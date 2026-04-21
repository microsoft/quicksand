"""SMB3 binary protocol: NetBIOS framing, header parsing, and command dispatch.

All SMB3 messages are prefixed with a 4-byte NetBIOS session service header
(big-endian length), followed by the SMB3 header (64 bytes) and command payload.

Reference: [MS-SMB2] Sections 2.2.1 (header) and 2.2.3 (negotiate).
"""

from __future__ import annotations

import os
import struct
import sys
from dataclasses import dataclass
from enum import IntEnum

# SMB2/3 magic: 0xFE 'S' 'M' 'B'
SMB2_MAGIC = b"\xfeSMB"

# Header flags
SMB2_FLAGS_SERVER_TO_REDIR = 0x00000001
SMB2_FLAGS_RELATED_OPERATIONS = 0x00000004

# Header size
SMB2_HEADER_SIZE = 64

# Default credits to grant per response
DEFAULT_CREDITS = 256


class Command(IntEnum):
    NEGOTIATE = 0x0000
    SESSION_SETUP = 0x0001
    LOGOFF = 0x0002
    TREE_CONNECT = 0x0003
    TREE_DISCONNECT = 0x0004
    CREATE = 0x0005
    CLOSE = 0x0006
    FLUSH = 0x0007
    READ = 0x0008
    WRITE = 0x0009
    LOCK = 0x000A
    IOCTL = 0x000B
    CANCEL = 0x000C
    ECHO = 0x000D
    QUERY_DIRECTORY = 0x000E
    CHANGE_NOTIFY = 0x000F
    QUERY_INFO = 0x0010
    SET_INFO = 0x0011


@dataclass
class SMBHeader:
    """Parsed SMB2/3 sync header."""

    credit_charge: int
    status: int
    command: int
    credit_request: int
    flags: int
    next_command: int
    message_id: int
    tree_id: int
    session_id: int


@dataclass
class SMBRequest:
    """A single SMB request: header + raw payload (everything after header)."""

    header: SMBHeader
    payload: bytes  # bytes after the 64-byte header
    raw: bytes  # full raw message including header


# ---------------------------------------------------------------------------
# I/O: read/write on stdin/stdout in binary mode
# ---------------------------------------------------------------------------

_stdin_fd: int = -1
_stdout_fd: int = -1


def _init_io() -> None:
    """Grab raw file descriptors for binary I/O."""
    global _stdin_fd, _stdout_fd
    _stdin_fd = sys.stdin.buffer.fileno()
    _stdout_fd = sys.stdout.buffer.fileno()


def _read_exactly(n: int) -> bytes:
    """Read exactly n bytes from stdin, or raise EOFError."""
    buf = bytearray()
    while len(buf) < n:
        chunk = os.read(_stdin_fd, n - len(buf))
        if not chunk:
            raise EOFError("stdin closed")
        buf.extend(chunk)
    return bytes(buf)


def read_frame() -> bytes:
    """Read one NetBIOS-framed SMB message from stdin."""
    length_bytes = _read_exactly(4)
    length = struct.unpack(">I", length_bytes)[0]
    return _read_exactly(length)


def write_frame(data: bytes) -> None:
    """Write one NetBIOS-framed SMB message to stdout."""
    header = struct.pack(">I", len(data))
    os.write(_stdout_fd, header + data)


# ---------------------------------------------------------------------------
# Header parsing / building
# ---------------------------------------------------------------------------

# SMB2 sync header layout (64 bytes):
#   4s  ProtocolId        (\xfeSMB)
#   H   StructureSize     (64)
#   H   CreditCharge
#   I   Status
#   H   Command
#   H   CreditRequest/Response
#   I   Flags
#   I   NextCommand
#   Q   MessageId
#   I   Reserved (sync: ProcessId)
#   I   TreeId
#   Q   SessionId
#   16s Signature
_HEADER_FMT = "<4sHHIHHIIQIIQ16s"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)  # 64
assert _HEADER_SIZE == SMB2_HEADER_SIZE


def parse_header(data: bytes) -> SMBHeader:
    """Parse a 64-byte SMB2/3 sync header."""
    (
        magic,
        _struct_size,
        credit_charge,
        status,
        command,
        credit_request,
        flags,
        next_command,
        message_id,
        _reserved,
        tree_id,
        session_id,
        _signature,
    ) = struct.unpack_from(_HEADER_FMT, data)
    if magic != SMB2_MAGIC:
        raise ValueError(f"Bad SMB2 magic: {magic!r}")
    return SMBHeader(
        credit_charge=credit_charge,
        status=status,
        command=command,
        credit_request=credit_request,
        flags=flags,
        next_command=next_command,
        message_id=message_id,
        tree_id=tree_id,
        session_id=session_id,
    )


def build_response_header(
    req: SMBHeader,
    status: int,
    payload_size: int,
    session_id: int | None = None,
    tree_id: int | None = None,
) -> bytes:
    """Build a 64-byte SMB3 response header."""
    return struct.pack(
        _HEADER_FMT,
        SMB2_MAGIC,
        64,  # StructureSize
        req.credit_charge or 1,
        status,
        req.command,
        DEFAULT_CREDITS,  # CreditResponse
        SMB2_FLAGS_SERVER_TO_REDIR | (req.flags & SMB2_FLAGS_RELATED_OPERATIONS),
        0,  # NextCommand (set later for compound)
        req.message_id,
        0,  # Reserved
        tree_id if tree_id is not None else req.tree_id,
        session_id if session_id is not None else req.session_id,
        b"\x00" * 16,  # Signature (no signing)
    )


def build_error_response(req: SMBHeader, status: int) -> bytes:
    """Build a minimal error response (header + 9-byte error body)."""
    # SMB2 ERROR Response: StructureSize(9), ErrorContextCount(0), Reserved(0),
    # ByteCount(0), ErrorData(empty)
    header = build_response_header(req, status, 9)
    body = struct.pack("<HBxI", 9, 0, 0)  # StructureSize=9, ErrorContextCount=0, ByteCount=0
    return header + body


# ---------------------------------------------------------------------------
# Compound request splitting
# ---------------------------------------------------------------------------


def split_compound(data: bytes) -> list[bytes]:
    """Split a compound SMB message into individual requests."""
    messages: list[bytes] = []
    offset = 0
    while offset < len(data):
        if offset + SMB2_HEADER_SIZE > len(data):
            break
        next_command = struct.unpack_from("<I", data, offset + 20)[0]  # NextCommand at offset 20
        if next_command == 0:
            messages.append(data[offset:])
            break
        messages.append(data[offset : offset + next_command])
        offset += next_command
    return messages


def parse_request(data: bytes) -> SMBRequest:
    """Parse raw message bytes into an SMBRequest."""
    header = parse_header(data)
    payload = data[SMB2_HEADER_SIZE:]
    return SMBRequest(header=header, payload=payload, raw=data)
