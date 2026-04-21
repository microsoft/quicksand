"""quicksand-smb: Pure-Python SMB3 server for host-guest directory mounts.

This server runs in inetd mode (stdin/stdout) and is spawned per-connection
by QEMU's guestfwd mechanism. No TCP listener, no auth, no external deps.

Usage:
    python -m quicksand_smb --config /path/to/config.json

Public API:
    SMBConfig     — share configuration
    serve_stdio   — main server loop (reads stdin, writes stdout)
"""

from __future__ import annotations

import json
import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path

from ._files import (
    HandleManager,
    handle_close,
    handle_create,
    handle_flush,
    handle_read,
    handle_write,
)
from ._ioctl import handle_ioctl
from ._negotiate import NegotiateState, handle_negotiate, handle_session_setup
from ._protocol import (
    Command,
    SMBRequest,
    _init_io,
    build_error_response,
    parse_request,
    read_frame,
    split_compound,
    write_frame,
)
from ._query import handle_query_directory, handle_query_info, handle_set_info
from ._status import STATUS_NOT_IMPLEMENTED, STATUS_SUCCESS
from ._tree import handle_tree_connect, handle_tree_disconnect

__version__ = "0.1.0"

logger = logging.getLogger("quicksand.smb")


@dataclass
class ShareConfig:
    """Configuration for a single SMB share."""

    host_path: str
    readonly: bool = False


@dataclass
class SMBConfig:
    """Configuration for the SMB3 server."""

    shares: dict[str, ShareConfig] = field(default_factory=dict)

    @classmethod
    def from_json_file(cls, path: str) -> SMBConfig:
        with open(path) as f:
            data = json.load(f)
        shares = {}
        for name, info in data.get("shares", {}).items():
            if isinstance(info, dict):
                shares[name] = ShareConfig(
                    host_path=info["host_path"],
                    readonly=info.get("readonly", False),
                )
            else:
                shares[name] = ShareConfig(host_path=str(info))
        return cls(shares=shares)

    def to_dict(self) -> dict:
        return {
            "shares": {
                name: {"host_path": sc.host_path, "readonly": sc.readonly}
                for name, sc in self.shares.items()
            }
        }


@dataclass
class SMBSession:
    """Per-connection server state."""

    config: SMBConfig
    config_path: str | None = None  # path to reload config from for dynamic mounts
    negotiate_state: NegotiateState | None = None
    session_id: int = 0
    next_tree_id: int = 1
    tree_map: dict[int, str] = field(default_factory=dict)  # tree_id → share_name
    handles: HandleManager = field(default_factory=HandleManager)

    def reload_config(self) -> None:
        """Reload shares from config file (picks up dynamic mounts)."""
        if self.config_path:
            self.config = SMBConfig.from_json_file(self.config_path)

    @property
    def shares_dict(self) -> dict[str, dict]:
        """Return shares as plain dicts for internal use."""
        return {
            name: {"host_path": sc.host_path, "readonly": sc.readonly}
            for name, sc in self.config.shares.items()
        }


def _dispatch(session: SMBSession, req: SMBRequest) -> bytes:
    """Dispatch a single SMB request to the appropriate handler."""
    cmd = req.header.command
    try:
        cmd_name = Command(cmd).name
    except ValueError:
        cmd_name = f"0x{cmd:04X}"
    logger.debug(
        "CMD=%s mid=%d tid=%d sid=%d payload=%d bytes",
        cmd_name,
        req.header.message_id,
        req.header.tree_id,
        req.header.session_id,
        len(req.payload),
    )

    if cmd == Command.NEGOTIATE:
        response, state = handle_negotiate(req)
        session.negotiate_state = state
        return response

    elif cmd == Command.SESSION_SETUP:
        session.session_id = 1
        return handle_session_setup(req, session.session_id)

    elif cmd == Command.LOGOFF:
        body = struct.pack("<HH", 4, 0)
        from ._protocol import build_response_header

        return build_response_header(req.header, STATUS_SUCCESS, len(body)) + body

    elif cmd == Command.TREE_CONNECT:
        # Reload config to pick up dynamically added shares
        session.reload_config()
        response, tree_id = handle_tree_connect(
            req, session.shares_dict, session.tree_map, session.next_tree_id
        )
        if tree_id is not None:
            session.next_tree_id = tree_id + 1
        return response

    elif cmd == Command.TREE_DISCONNECT:
        return handle_tree_disconnect(req, session.tree_map)

    elif cmd == Command.CREATE:
        share_name = session.tree_map.get(req.header.tree_id)
        if share_name is None or share_name not in session.shares_dict:
            from ._status import STATUS_NETWORK_NAME_DELETED

            return build_error_response(req.header, STATUS_NETWORK_NAME_DELETED)
        share = session.shares_dict[share_name]
        return handle_create(
            req,
            Path(share["host_path"]),
            share["readonly"],
            session.handles,
            req.header.tree_id,
        )

    elif cmd == Command.CLOSE:
        return handle_close(req, session.handles)

    elif cmd == Command.READ:
        return handle_read(req, session.handles)

    elif cmd == Command.WRITE:
        return handle_write(req, session.handles)

    elif cmd == Command.FLUSH:
        return handle_flush(req, session.handles)

    elif cmd == Command.QUERY_INFO:
        return handle_query_info(req, session.handles, session.shares_dict, session.tree_map)

    elif cmd == Command.SET_INFO:
        return handle_set_info(req, session.handles)

    elif cmd == Command.QUERY_DIRECTORY:
        return handle_query_directory(req, session.handles, session.shares_dict, session.tree_map)

    elif cmd == Command.IOCTL:
        return handle_ioctl(req, session.negotiate_state)

    elif cmd == Command.ECHO:
        body = struct.pack("<HH", 4, 0)
        from ._protocol import build_response_header

        return build_response_header(req.header, STATUS_SUCCESS, len(body)) + body

    elif cmd in (Command.LOCK, Command.CHANGE_NOTIFY, Command.CANCEL):
        return build_error_response(req.header, STATUS_NOT_IMPLEMENTED)

    else:
        return build_error_response(req.header, STATUS_NOT_IMPLEMENTED)


def serve_stdio(config: SMBConfig, config_path: str | None = None) -> None:
    """Main server loop: read SMB frames from stdin, dispatch, write responses to stdout.

    Runs until stdin is closed (QEMU terminates the connection).
    """
    _init_io()
    session = SMBSession(config=config, config_path=config_path)

    while True:
        try:
            raw = read_frame()
        except EOFError:
            break

        logger.debug("Frame: %d bytes, hex=%s", len(raw), raw.hex())

        # SMB1 negotiate: some clients send \xFFSMB first to discover SMB2.
        # Respond with SMB2 negotiate error to force protocol upgrade.
        if raw[:4] == b"\xffSMB":
            logger.debug("Received SMB1 negotiate, sending SMB2 upgrade response")
            # SMB2 negotiate response with dialect 0x02FF (wildcard) tells
            # the client to retry with SMB2. But the simplest approach is
            # to just send back a minimal SMB2 negotiate response.
            # The Linux CIFS client with vers=3.0 should NOT send SMB1,
            # but vers=default might. Log and continue.
            continue

        # Handle compound requests
        messages = split_compound(raw)

        responses: list[bytes] = []
        last_file_id: bytes = b"\x00" * 16
        last_tree_id: int = 0
        for msg_data in messages:
            try:
                req = parse_request(msg_data)

                # Compound related operations: inherit FileId/TreeId from
                # the previous response in the chain
                if req.header.flags & 0x04:  # SMB2_FLAGS_RELATED_OPERATIONS
                    if req.header.tree_id == 0:
                        req.header.tree_id = last_tree_id
                    # Replace sentinel FileId (0xFFFF...) in payload
                    sentinel = b"\xff" * 16
                    if sentinel in req.payload:
                        req = SMBRequest(
                            header=req.header,
                            payload=req.payload.replace(sentinel, last_file_id, 1),
                            raw=req.raw,
                        )

                resp = _dispatch(session, req)
                responses.append(resp)

                # Track FileId from CREATE responses for compound chains
                if req.header.command == Command.CREATE and len(resp) >= 64 + 82:
                    status = struct.unpack_from("<I", resp, 8)[0]
                    if status == 0:
                        last_file_id = resp[64 + 64 : 64 + 80]
                last_tree_id = struct.unpack_from("<I", resp, 36)[0]
            except Exception as e:
                logger.error("Error processing SMB request: %s", e, exc_info=True)
                try:
                    from ._protocol import parse_header

                    hdr = parse_header(msg_data)
                    from ._status import STATUS_UNSUCCESSFUL

                    responses.append(build_error_response(hdr, STATUS_UNSUCCESSFUL))
                except Exception:
                    break

        # Write compound response
        if len(responses) == 1:
            write_frame(responses[0])
        elif responses:
            # Chain responses: set NextCommand in headers
            parts = []
            for i, resp in enumerate(responses):
                if i < len(responses) - 1:
                    # Pad to 8-byte alignment and set NextCommand
                    padded_len = (len(resp) + 7) & ~7
                    resp = resp.ljust(padded_len, b"\x00")
                    # NextCommand is at offset 20 in SMB2 header
                    ba = bytearray(resp)
                    struct.pack_into("<I", ba, 20, padded_len)
                    resp = bytes(ba)
                parts.append(resp)
            write_frame(b"".join(parts))

    # Cleanup
    session.handles.close_all()
