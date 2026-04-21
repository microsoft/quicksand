"""Unit tests for SMB3 protocol framing and header parsing."""

import struct

from quicksand_smb._protocol import (
    SMB2_HEADER_SIZE,
    SMB2_MAGIC,
    Command,
    SMBHeader,
    build_error_response,
    build_response_header,
    parse_header,
    parse_request,
    split_compound,
)
from quicksand_smb._status import STATUS_NOT_SUPPORTED, STATUS_SUCCESS


def _build_test_header(
    command: int = Command.NEGOTIATE,
    message_id: int = 0,
    tree_id: int = 0,
    session_id: int = 0,
    flags: int = 0,
    next_command: int = 0,
) -> bytes:
    """Build a minimal SMB2 header for testing."""
    return struct.pack(
        "<4sHHIHHIIQIIQ16s",
        SMB2_MAGIC,
        64,  # StructureSize
        1,  # CreditCharge
        0,  # Status
        command,
        1,  # CreditRequest
        flags,
        next_command,
        message_id,
        0,  # Reserved
        tree_id,
        session_id,
        b"\x00" * 16,  # Signature
    )


class TestHeaderParsing:
    def test_parse_negotiate_header(self):
        data = _build_test_header(command=Command.NEGOTIATE, message_id=42)
        hdr = parse_header(data)
        assert hdr.command == Command.NEGOTIATE
        assert hdr.message_id == 42
        assert hdr.tree_id == 0
        assert hdr.session_id == 0

    def test_parse_header_with_tree_and_session(self):
        data = _build_test_header(tree_id=5, session_id=99)
        hdr = parse_header(data)
        assert hdr.tree_id == 5
        assert hdr.session_id == 99

    def test_parse_request(self):
        header = _build_test_header()
        payload = b"\x01\x02\x03\x04"
        req = parse_request(header + payload)
        assert req.header.command == Command.NEGOTIATE
        assert req.payload == payload
        assert len(req.raw) == SMB2_HEADER_SIZE + 4

    def test_bad_magic_raises(self):
        data = b"\x00" * 64
        try:
            parse_header(data)
            raise AssertionError("Should have raised ValueError")
        except ValueError as e:
            assert "Bad SMB2 magic" in str(e)


class TestResponseBuilding:
    def test_build_response_header_size(self):
        req_hdr = SMBHeader(
            credit_charge=1,
            status=0,
            command=Command.NEGOTIATE,
            credit_request=1,
            flags=0,
            next_command=0,
            message_id=0,
            tree_id=0,
            session_id=0,
        )
        resp = build_response_header(req_hdr, STATUS_SUCCESS, 100)
        assert len(resp) == SMB2_HEADER_SIZE

        # Check magic
        assert resp[:4] == SMB2_MAGIC

        # Check server-to-redir flag is set
        flags = struct.unpack_from("<I", resp, 16)[0]
        assert flags & 0x01

    def test_build_error_response(self):
        req_hdr = SMBHeader(
            credit_charge=1,
            status=0,
            command=Command.CREATE,
            credit_request=1,
            flags=0,
            next_command=0,
            message_id=5,
            tree_id=1,
            session_id=1,
        )
        resp = build_error_response(req_hdr, STATUS_NOT_SUPPORTED)
        assert len(resp) == SMB2_HEADER_SIZE + 8  # header + error body

        # Check status in header
        status = struct.unpack_from("<I", resp, 8)[0]
        assert status == STATUS_NOT_SUPPORTED

    def test_response_preserves_message_id(self):
        req_hdr = SMBHeader(
            credit_charge=1,
            status=0,
            command=Command.READ,
            credit_request=1,
            flags=0,
            next_command=0,
            message_id=42,
            tree_id=3,
            session_id=7,
        )
        resp = build_response_header(req_hdr, STATUS_SUCCESS, 0)
        msg_id = struct.unpack_from("<Q", resp, 24)[0]
        assert msg_id == 42


class TestCompoundSplitting:
    def test_single_message(self):
        msg = _build_test_header() + b"\x00" * 10
        parts = split_compound(msg)
        assert len(parts) == 1
        assert parts[0] == msg

    def test_two_messages(self):
        # First message has NextCommand pointing to second
        msg1_size = 80  # 64 header + 16 payload, 8-byte aligned
        msg1 = _build_test_header(command=Command.CREATE, next_command=msg1_size)
        msg1 += b"\x00" * (msg1_size - SMB2_HEADER_SIZE)

        msg2 = _build_test_header(command=Command.CLOSE)
        msg2 += b"\x00" * 8

        combined = msg1 + msg2
        parts = split_compound(combined)
        assert len(parts) == 2
        assert len(parts[0]) == msg1_size
        assert parts[1][:4] == SMB2_MAGIC
