"""Unit tests for QuicksandSMBTCPServer (the Windows loopback TCP SMB server).

These run on every platform — the server binds 127.0.0.1 and serves the
pure-Python SMB3 implementation over a socket, independent of any VM.
"""

from __future__ import annotations

import os
import socket
import struct
from unittest.mock import patch

import pytest
from quicksand_core.host.smb import (
    QuicksandSMBServer,
    QuicksandSMBTCPServer,
    WindowsSMBServer,
    create_smb_server,
)
from quicksand_smb._protocol import SMB2_MAGIC, Command


def _frame(data: bytes) -> bytes:
    """Add NetBIOS framing (4-byte big-endian length prefix)."""
    return struct.pack(">I", len(data)) + data


def _smb_header(command: int) -> bytes:
    """Build a minimal SMB2 sync request header."""
    return struct.pack(
        "<4sHHIHHIIQIIQ16s",
        SMB2_MAGIC,
        64,  # StructureSize
        0,  # CreditCharge
        0,  # Status
        command,
        1,  # CreditRequest
        0,  # Flags
        0,  # NextCommand
        0,  # MessageId
        0,  # Reserved/ProcessId
        0,  # TreeId
        0,  # SessionId
        b"\x00" * 16,  # Signature
    )


def _negotiate_frame() -> bytes:
    """A framed SMB2 NEGOTIATE request advertising dialect 3.0."""
    dialects = struct.pack("<H", 0x0300)
    body = struct.pack("<HHHHI16sIHH", 36, 1, 0, 0, 0, os.urandom(16), 0, 0, 0)
    body = body[:36] + dialects
    return _frame(_smb_header(Command.NEGOTIATE) + body)


def _read_status(data: bytes) -> int:
    """Extract NTSTATUS from a framed SMB2 response buffer."""
    length = struct.unpack_from(">I", data, 0)[0]
    payload = data[4 : 4 + length]
    return struct.unpack_from("<I", payload, 8)[0]


@pytest.fixture
def tcp_server():
    srv = QuicksandSMBTCPServer()
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


class TestLifecycle:
    def test_start_binds_loopback_port(self, tcp_server):
        assert tcp_server.port > 0
        # The port accepts connections while running.
        c = socket.create_connection(("127.0.0.1", tcp_server.port), timeout=3)
        c.close()

    def test_credentials_are_guest(self, tcp_server):
        assert tcp_server.credentials == ("guest", "")

    def test_get_guestfwd_cmd_is_none(self, tcp_server):
        # None routes mounts through the slirp gateway, not a guestfwd tunnel.
        assert tcp_server.get_guestfwd_cmd() is None

    def test_stop_closes_listener(self):
        srv = QuicksandSMBTCPServer()
        srv.start()
        port = srv.port
        srv.stop()
        assert srv.port == 0
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1)


class TestShareConfig:
    def test_add_and_remove_share(self, tcp_server, tmp_path):
        name = tcp_server.add_share(str(tmp_path), readonly=True)
        assert name.startswith("QUICKSAND_")
        shares = tcp_server.list_shares()
        assert len(shares) == 1
        assert shares[0]["host_path"] == str(tmp_path)
        assert shares[0]["readonly"] is True

        tcp_server.remove_share(name)
        assert tcp_server.list_shares() == []

    def test_add_share_creates_missing_dir(self, tcp_server, tmp_path):
        target = tmp_path / "made" / "bythserver"
        tcp_server.add_share(str(target), readonly=False)
        assert target.is_dir()


class TestServeSocket:
    def test_negotiate_round_trip(self, tcp_server, tmp_path):
        """A real client connection gets a successful NEGOTIATE response."""
        tcp_server.add_share(str(tmp_path), readonly=False)

        c = socket.create_connection(("127.0.0.1", tcp_server.port), timeout=5)
        try:
            c.sendall(_negotiate_frame())
            # Read the 4-byte length prefix, then the payload.
            header = b""
            while len(header) < 4:
                chunk = c.recv(4 - len(header))
                assert chunk, "server closed before responding"
                header += chunk
            length = struct.unpack(">I", header)[0]
            payload = b""
            while len(payload) < length:
                chunk = c.recv(length - len(payload))
                assert chunk, "truncated response"
                payload += chunk
        finally:
            c.close()

        assert payload[:4] == SMB2_MAGIC
        assert struct.unpack_from("<I", payload, 8)[0] == 0  # STATUS_SUCCESS

    def test_multiple_sequential_connections(self, tcp_server, tmp_path):
        """The listener keeps serving after a connection closes."""
        tcp_server.add_share(str(tmp_path), readonly=False)
        for _ in range(3):
            c = socket.create_connection(("127.0.0.1", tcp_server.port), timeout=5)
            try:
                c.sendall(_negotiate_frame())
                resp = c.recv(4096)
                assert resp[4:8] == SMB2_MAGIC
            finally:
                c.close()


class TestFactory:
    def test_non_windows_returns_guestfwd_server(self):
        with patch("quicksand_core.host.smb.sys.platform", "linux"):
            srv = create_smb_server()
        assert isinstance(srv, QuicksandSMBServer)
        assert not isinstance(srv, QuicksandSMBTCPServer)

    def test_windows_default_returns_tcp_server(self):
        with (
            patch("quicksand_core.host.smb.sys.platform", "win32"),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("QUICKSAND_WINDOWS_NATIVE_SMB", None)
            srv = create_smb_server()
        assert isinstance(srv, QuicksandSMBTCPServer)

    def test_windows_native_opt_in(self):
        with (
            patch("quicksand_core.host.smb.sys.platform", "win32"),
            patch.dict(os.environ, {"QUICKSAND_WINDOWS_NATIVE_SMB": "1"}, clear=False),
        ):
            srv = create_smb_server()
        assert isinstance(srv, WindowsSMBServer)
