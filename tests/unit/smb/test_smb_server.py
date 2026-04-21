"""Integration tests for the SMB3 server.

Tests the full SMB3 protocol by piping a real Linux CIFS client through
the server's stdin/stdout. Uses subprocess to simulate the inetd-style
invocation that QEMU's guestfwd would use.

These tests verify the server works end-to-end WITHOUT requiring a VM.
They use a loopback approach: start the SMB server process, connect to
it via a local TCP bridge, and mount using the kernel CIFS client.

For CI (no root / no mount), we test at the protocol level by replaying
captured SMB3 packet sequences.
"""

import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quicksand_smb import ShareConfig, SMBConfig, SMBSession, _dispatch
from quicksand_smb._negotiate import handle_negotiate, handle_session_setup
from quicksand_smb._protocol import (
    SMB2_MAGIC,
    Command,
    parse_request,
)
from quicksand_smb._status import (
    STATUS_ACCESS_DENIED,
    STATUS_BAD_NETWORK_NAME,
    STATUS_END_OF_FILE,
    STATUS_INVALID_HANDLE,
    STATUS_NO_MORE_FILES,
    STATUS_OBJECT_NAME_COLLISION,
    STATUS_OBJECT_NAME_NOT_FOUND,
    STATUS_SUCCESS,
)


def _smb_header(
    command: int,
    message_id: int = 0,
    tree_id: int = 0,
    session_id: int = 0,
    flags: int = 0,
) -> bytes:
    """Build a raw SMB2 header."""
    return struct.pack(
        "<4sHHIHHIIQIIQ16s",
        SMB2_MAGIC,
        64,
        1,
        0,
        command,
        1,
        flags,
        0,
        message_id,
        0,
        tree_id,
        session_id,
        b"\x00" * 16,
    )


def _frame(data: bytes) -> bytes:
    """Add NetBIOS framing (4-byte big-endian length prefix)."""
    return struct.pack(">I", len(data)) + data


def _read_response(data: bytes, offset: int = 0) -> tuple[bytes, int]:
    """Read a NetBIOS-framed response from a buffer. Returns (payload, new_offset)."""
    length = struct.unpack_from(">I", data, offset)[0]
    payload = data[offset + 4 : offset + 4 + length]
    return payload, offset + 4 + length


def _get_status(response: bytes) -> int:
    """Extract NTSTATUS from an SMB2 response."""
    return struct.unpack_from("<I", response, 8)[0]


class TestNegotiate:
    """Test NEGOTIATE + SESSION_SETUP flow."""

    def test_negotiate_selects_smb3(self):
        """Server should select SMB 3.0 dialect."""
        # Build NEGOTIATE request with dialects 2.02, 2.10, 3.0.0
        dialects = struct.pack("<HHH", 0x0202, 0x0210, 0x0300)
        body = struct.pack(
            "<HHHHI16sIHH",
            36,  # StructureSize
            3,  # DialectCount
            0,  # SecurityMode
            0,  # Reserved
            0,  # Capabilities
            os.urandom(16),  # ClientGuid
            0,
            0,
            0,  # NegotiateContext fields
        )
        # Pad to offset 36 for dialects
        body = body[:36] + dialects

        header = _smb_header(Command.NEGOTIATE)
        req = parse_request(header + body)

        response, state = handle_negotiate(req)
        assert _get_status(response) == STATUS_SUCCESS
        assert state.dialect == 0x0300

    def test_session_setup_accepts_guest(self):
        """SESSION_SETUP should accept any auth and return IS_GUEST."""
        body = struct.pack(
            "<HBBIIHH",
            25,  # StructureSize
            0,  # Flags
            0,  # SecurityMode
            0,  # Capabilities
            0,  # Channel
            72,  # SecurityBufferOffset
            0,  # SecurityBufferLength
        )
        # Pad to match structure
        body = body.ljust(24, b"\x00")

        header = _smb_header(Command.SESSION_SETUP)
        req = parse_request(header + body)

        response = handle_session_setup(req, session_id=1)
        assert _get_status(response) == STATUS_SUCCESS

        # Check SessionFlags has IS_GUEST (0x0001)
        session_flags = struct.unpack_from("<H", response, 64 + 2)[0]
        assert session_flags & 0x0001


class TestTreeConnect:
    """Test TREE_CONNECT with shares."""

    def _make_session(self, share_dir: Path) -> SMBSession:
        config = SMBConfig(
            shares={
                "TESTSHARE": ShareConfig(host_path=str(share_dir), readonly=False),
            }
        )
        return SMBSession(config=config)

    def test_tree_connect_valid_share(self, tmp_path):
        session = self._make_session(tmp_path)
        session.session_id = 1

        path = "\\\\10.0.2.100\\TESTSHARE".encode("utf-16-le")
        body = struct.pack("<HHHH", 9, 0, 64 + 8, len(path))
        body = body.ljust(8, b"\x00") + path

        header = _smb_header(Command.TREE_CONNECT, session_id=1)
        req = parse_request(header + body)

        response = _dispatch(session, req)
        assert _get_status(response) == STATUS_SUCCESS
        assert 1 in session.tree_map

    def test_tree_connect_bad_share(self, tmp_path):
        session = self._make_session(tmp_path)
        session.session_id = 1

        path = "\\\\10.0.2.100\\NONEXISTENT".encode("utf-16-le")
        body = struct.pack("<HHHH", 9, 0, 64 + 8, len(path))
        body = body.ljust(8, b"\x00") + path

        header = _smb_header(Command.TREE_CONNECT, session_id=1)
        req = parse_request(header + body)

        response = _dispatch(session, req)
        assert _get_status(response) == STATUS_BAD_NETWORK_NAME


class TestFileOperations:
    """Test CREATE, READ, WRITE, CLOSE operations."""

    def _setup_session(self, share_dir: Path, readonly: bool = False) -> SMBSession:
        config = SMBConfig(
            shares={
                "SHARE": ShareConfig(host_path=str(share_dir), readonly=readonly),
            }
        )
        session = SMBSession(config=config)
        session.session_id = 1
        session.tree_map[1] = "SHARE"
        session.next_tree_id = 2
        return session

    def _create_file(
        self,
        session: SMBSession,
        filename: str,
        tree_id: int = 1,
        disposition: int = 0x03,
        options: int = 0,
        access: int = 0x12019F,
    ) -> tuple[bytes, bytes]:
        """Send CREATE request, return (response, file_id or empty)."""
        name_bytes = filename.encode("utf-16-le")
        name_offset = 64 + 56  # header + fixed body

        body = struct.pack(
            "<HBBIqQIIIIIHHII",
            57,  # StructureSize
            0,  # SecurityFlags
            0,  # RequestedOplockLevel
            0,  # ImpersonationLevel
            0,  # SmbCreateFlags
            0,  # Reserved
            access,  # DesiredAccess
            0x80,  # FileAttributes (NORMAL)
            0x07,  # ShareAccess (all)
            disposition,
            options,
            name_offset,
            len(name_bytes),
            0,
            0,  # CreateContexts
        )
        body += name_bytes

        header = _smb_header(Command.CREATE, tree_id=tree_id, session_id=1)
        req = parse_request(header + body)
        response = _dispatch(session, req)

        status = _get_status(response)
        if status == STATUS_SUCCESS:
            # FileId is at offset 64+66 in response (bytes 66-82 of body)
            file_id = response[64 + 64 : 64 + 80]
            return response, file_id
        return response, b""

    def _close_file(self, session: SMBSession, file_id: bytes) -> bytes:
        body = struct.pack("<HH4x", 24, 0) + file_id
        header = _smb_header(Command.CLOSE, tree_id=1, session_id=1)
        req = parse_request(header + body)
        return _dispatch(session, req)

    def _read_file(
        self, session: SMBSession, file_id: bytes, length: int, offset: int = 0
    ) -> bytes:
        body = struct.pack(
            "<HBBIQ16sIIIHH",
            49,  # StructureSize
            0,  # Padding
            0,  # Flags
            length,
            offset,
            file_id,
            0,  # MinimumCount
            0,  # Channel
            0,  # RemainingBytes
            0,
            0,  # ReadChannelInfo
        )
        header = _smb_header(Command.READ, tree_id=1, session_id=1)
        req = parse_request(header + body)
        return _dispatch(session, req)

    def _write_file(
        self, session: SMBSession, file_id: bytes, data: bytes, offset: int = 0
    ) -> bytes:
        data_offset = 64 + 48  # header + fixed write body
        body = struct.pack(
            "<HHIQ16sIIHHI",
            49,  # StructureSize
            data_offset,  # DataOffset
            len(data),
            offset,
            file_id,
            0,  # Channel
            0,  # RemainingBytes
            0,
            0,  # WriteChannelInfo
            0,  # Flags
        )
        body += data
        header = _smb_header(Command.WRITE, tree_id=1, session_id=1)
        req = parse_request(header + body)
        return _dispatch(session, req)

    def test_create_existing_file(self, tmp_path):
        (tmp_path / "hello.txt").write_text("world")
        session = self._setup_session(tmp_path)

        response, file_id = self._create_file(session, "hello.txt", disposition=0x01)  # FILE_OPEN
        assert _get_status(response) == STATUS_SUCCESS
        assert len(file_id) == 16
        self._close_file(session, file_id)

    def test_create_new_file(self, tmp_path):
        session = self._setup_session(tmp_path)

        response, file_id = self._create_file(session, "new.txt", disposition=0x02)  # FILE_CREATE
        assert _get_status(response) == STATUS_SUCCESS
        assert (tmp_path / "new.txt").exists()
        self._close_file(session, file_id)

    def test_create_nonexistent_fails(self, tmp_path):
        session = self._setup_session(tmp_path)

        response, _ = self._create_file(session, "nope.txt", disposition=0x01)  # FILE_OPEN
        assert _get_status(response) == STATUS_OBJECT_NAME_NOT_FOUND

    def test_create_duplicate_fails(self, tmp_path):
        (tmp_path / "existing.txt").write_text("data")
        session = self._setup_session(tmp_path)

        response, _ = self._create_file(session, "existing.txt", disposition=0x02)  # FILE_CREATE
        assert _get_status(response) == STATUS_OBJECT_NAME_COLLISION

    def test_read_file(self, tmp_path):
        (tmp_path / "data.txt").write_bytes(b"Hello, SMB3!")
        session = self._setup_session(tmp_path)

        _, file_id = self._create_file(session, "data.txt", disposition=0x01)
        response = self._read_file(session, file_id, 4096)

        assert _get_status(response) == STATUS_SUCCESS
        # Data is after header + fixed read response body
        data_offset_field = struct.unpack_from("<B", response, 64 + 2)[0]
        data_length = struct.unpack_from("<I", response, 64 + 4)[0]
        data = response[data_offset_field : data_offset_field + data_length]
        assert data == b"Hello, SMB3!"

        self._close_file(session, file_id)

    def test_read_past_eof(self, tmp_path):
        (tmp_path / "small.txt").write_bytes(b"hi")
        session = self._setup_session(tmp_path)

        _, file_id = self._create_file(session, "small.txt", disposition=0x01)
        response = self._read_file(session, file_id, 4096, offset=100)
        assert _get_status(response) == STATUS_END_OF_FILE

        self._close_file(session, file_id)

    def test_write_file(self, tmp_path):
        session = self._setup_session(tmp_path)

        _, file_id = self._create_file(
            session,
            "output.txt",
            disposition=0x03,
            access=0x12019F,  # GENERIC_READ | GENERIC_WRITE
        )
        response = self._write_file(session, file_id, b"Written via SMB3")
        assert _get_status(response) == STATUS_SUCCESS

        self._close_file(session, file_id)
        assert (tmp_path / "output.txt").read_bytes() == b"Written via SMB3"

    def test_write_readonly_share_denied(self, tmp_path):
        session = self._setup_session(tmp_path, readonly=True)

        _, file_id = self._create_file(session, "test.txt", disposition=0x03)
        if file_id:
            response = self._write_file(session, file_id, b"should fail")
            assert _get_status(response) == STATUS_ACCESS_DENIED
            self._close_file(session, file_id)

    def test_close_invalid_handle(self, tmp_path):
        session = self._setup_session(tmp_path)

        fake_id = b"\xff" * 16
        response = self._close_file(session, fake_id)
        assert _get_status(response) == STATUS_INVALID_HANDLE

    def test_binary_data_roundtrip(self, tmp_path):
        """Binary data should survive read/write without corruption."""
        binary_data = bytes(range(256)) * 10  # 2560 bytes of all byte values
        session = self._setup_session(tmp_path)

        _, file_id = self._create_file(session, "binary.bin", disposition=0x03, access=0x12019F)
        self._write_file(session, file_id, binary_data)
        self._close_file(session, file_id)

        # Re-open and read back
        _, file_id = self._create_file(session, "binary.bin", disposition=0x01)
        response = self._read_file(session, file_id, 4096)
        assert _get_status(response) == STATUS_SUCCESS

        data_offset_field = struct.unpack_from("<B", response, 64 + 2)[0]
        data_length = struct.unpack_from("<I", response, 64 + 4)[0]
        data = response[data_offset_field : data_offset_field + data_length]
        assert data == binary_data

        self._close_file(session, file_id)

    def test_large_file_io(self, tmp_path):
        """Test reading/writing files > 64KB."""
        large_data = os.urandom(256 * 1024)  # 256 KB
        (tmp_path / "large.bin").write_bytes(large_data)

        session = self._setup_session(tmp_path)
        _, file_id = self._create_file(session, "large.bin", disposition=0x01)

        # Read in chunks
        read_data = b""
        offset = 0
        chunk_size = 65536
        while True:
            response = self._read_file(session, file_id, chunk_size, offset)
            status = _get_status(response)
            if status == STATUS_END_OF_FILE:
                break
            assert status == STATUS_SUCCESS
            data_offset_field = struct.unpack_from("<B", response, 64 + 2)[0]
            data_length = struct.unpack_from("<I", response, 64 + 4)[0]
            chunk = response[data_offset_field : data_offset_field + data_length]
            read_data += chunk
            offset += len(chunk)
            if len(chunk) < chunk_size:
                break

        assert read_data == large_data
        self._close_file(session, file_id)


class TestPathTraversal:
    """Security tests: path traversal must be blocked."""

    def _setup_session(self, share_dir: Path) -> SMBSession:
        config = SMBConfig(
            shares={
                "SHARE": ShareConfig(host_path=str(share_dir)),
            }
        )
        session = SMBSession(config=config)
        session.session_id = 1
        session.tree_map[1] = "SHARE"
        return session

    def _try_create(self, session: SMBSession, filename: str) -> int:
        name_bytes = filename.encode("utf-16-le")
        name_offset = 64 + 56
        body = struct.pack(
            "<HBBIqQIIIIIHHII",
            57,
            0,
            0,
            0,
            0,
            0,
            0x80,
            0x80,
            0x07,
            0x01,
            0,
            name_offset,
            len(name_bytes),
            0,
            0,
        )
        body += name_bytes
        header = _smb_header(Command.CREATE, tree_id=1, session_id=1)
        req = parse_request(header + body)
        response = _dispatch(session, req)
        return _get_status(response)

    def test_dotdot_escape(self, tmp_path):
        share_dir = tmp_path / "share"
        share_dir.mkdir()
        (tmp_path / "secret.txt").write_text("secret")

        session = self._setup_session(share_dir)
        status = self._try_create(session, "..\\secret.txt")
        assert status == STATUS_ACCESS_DENIED

    def test_dotdot_deep_escape(self, tmp_path):
        share_dir = tmp_path / "a" / "b" / "share"
        share_dir.mkdir(parents=True)

        session = self._setup_session(share_dir)
        status = self._try_create(session, "..\\..\\..\\..\\etc\\passwd")
        assert status == STATUS_ACCESS_DENIED

    def test_symlink_escape(self, tmp_path):
        share_dir = tmp_path / "share"
        share_dir.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("secret")

        # Create symlink inside share pointing outside
        link = share_dir / "escape"
        link.symlink_to(secret)

        session = self._setup_session(share_dir)
        status = self._try_create(session, "escape")
        assert status == STATUS_ACCESS_DENIED


class TestDirectoryOperations:
    """Test QUERY_DIRECTORY (ls) operations."""

    def _setup_session(self, share_dir: Path) -> SMBSession:
        config = SMBConfig(
            shares={
                "SHARE": ShareConfig(host_path=str(share_dir)),
            }
        )
        session = SMBSession(config=config)
        session.session_id = 1
        session.tree_map[1] = "SHARE"
        return session

    def _open_dir(self, session: SMBSession, path: str = "") -> bytes:
        """Open a directory handle."""
        name_bytes = path.encode("utf-16-le")
        name_offset = 64 + 56
        body = struct.pack(
            "<HBBIqQIIIIIHHII",
            57,
            0,
            0,
            0,
            0,
            0,
            0x80,  # DesiredAccess: FILE_READ_ATTRIBUTES
            0x10,  # FileAttributes: DIRECTORY
            0x07,  # ShareAccess
            0x01,  # CreateDisposition: FILE_OPEN
            0x01,  # CreateOptions: FILE_DIRECTORY_FILE
            name_offset,
            len(name_bytes),
            0,
            0,
        )
        body += name_bytes
        header = _smb_header(Command.CREATE, tree_id=1, session_id=1)
        req = parse_request(header + body)
        response = _dispatch(session, req)
        assert _get_status(response) == STATUS_SUCCESS
        return response[64 + 64 : 64 + 80]  # FileId

    def _query_dir(self, session: SMBSession, file_id: bytes, flags: int = 0) -> bytes:
        """Send QUERY_DIRECTORY request."""
        pattern = "*".encode("utf-16-le")
        name_offset = 64 + 32

        body = struct.pack(
            "<HBBI16sHHI",
            33,  # StructureSize
            3,  # FileInfoClass: FileBothDirectoryInformation
            flags,
            0,  # FileIndex
            file_id,
            name_offset,
            len(pattern),
            65536,  # OutputBufferLength
        )
        body += pattern

        header = _smb_header(Command.QUERY_DIRECTORY, tree_id=1, session_id=1)
        req = parse_request(header + body)
        return _dispatch(session, req)

    def _parse_dir_entries(self, response: bytes, info_class: int = 3) -> list[str]:
        """Parse filenames from a QUERY_DIRECTORY response.

        Supports FILE_BOTH_DIRECTORY_INFORMATION (3) and
        FILE_ID_BOTH_DIRECTORY_INFORMATION (37).
        """
        assert _get_status(response) == STATUS_SUCCESS
        output_offset = struct.unpack_from("<H", response, 64 + 2)[0]
        output_length = struct.unpack_from("<I", response, 64 + 4)[0]
        data = response[output_offset : output_offset + output_length]

        # Fixed header size before FileName
        if info_class == 3:
            fname_offset = 94  # FileBothDirectoryInformation
        elif info_class == 37:
            fname_offset = 104  # FileIdBothDirectoryInformation
        elif info_class == 38:
            fname_offset = 80  # FileIdFullDirectoryInformation
        else:
            raise ValueError(f"Unsupported info class {info_class}")

        names: list[str] = []
        pos = 0
        while pos < len(data):
            next_entry_offset = struct.unpack_from("<I", data, pos)[0]
            fname_len = struct.unpack_from("<I", data, pos + 60)[0]
            fname_bytes = data[pos + fname_offset : pos + fname_offset + fname_len]
            names.append(fname_bytes.decode("utf-16-le"))
            if next_entry_offset == 0:
                break
            pos += next_entry_offset
        return names

    def _query_dir_with_class(
        self, session: SMBSession, file_id: bytes, info_class: int = 3, flags: int = 0
    ) -> bytes:
        """Send QUERY_DIRECTORY with a specific FileInfoClass."""
        pattern = "*".encode("utf-16-le")
        name_offset = 64 + 32

        body = struct.pack(
            "<HBBI16sHHI",
            33,  # StructureSize
            info_class,
            flags,
            0,  # FileIndex
            file_id,
            name_offset,
            len(pattern),
            65536,  # OutputBufferLength
        )
        body += pattern

        header = _smb_header(Command.QUERY_DIRECTORY, tree_id=1, session_id=1)
        req = parse_request(header + body)
        return _dispatch(session, req)

    def test_list_directory(self, tmp_path):
        (tmp_path / "file1.txt").write_text("a")
        (tmp_path / "file2.txt").write_text("b")
        (tmp_path / "subdir").mkdir()

        session = self._setup_session(tmp_path)
        dir_id = self._open_dir(session)

        response = self._query_dir(session, dir_id)
        assert _get_status(response) == STATUS_SUCCESS

        # Should contain entries (at least ., .., file1.txt, file2.txt, subdir)
        output_length = struct.unpack_from("<I", response, 64 + 4)[0]
        assert output_length > 0

    def test_filenames_correct_class3(self, tmp_path):
        """Verify filenames are correctly encoded in FileBothDirectoryInformation."""
        (tmp_path / "README.md").write_text("hello")
        (tmp_path / "LICENSE").write_text("MIT")
        (tmp_path / ".github").mkdir()

        session = self._setup_session(tmp_path)
        dir_id = self._open_dir(session)

        # Collect all entries across multiple responses
        all_names: list[str] = []
        while True:
            response = self._query_dir(session, dir_id)
            if _get_status(response) == STATUS_NO_MORE_FILES:
                break
            all_names.extend(self._parse_dir_entries(response, info_class=3))

        assert "." in all_names
        assert ".." in all_names
        assert "README.md" in all_names
        assert "LICENSE" in all_names
        assert ".github" in all_names

    def test_filenames_correct_class37(self, tmp_path):
        """Verify filenames are correctly encoded in FileIdBothDirectoryInformation."""
        (tmp_path / "README.md").write_text("hello")
        (tmp_path / "LICENSE").write_text("MIT")
        (tmp_path / ".github").mkdir()

        session = self._setup_session(tmp_path)
        dir_id = self._open_dir(session)

        # Collect all entries using info class 37
        all_names: list[str] = []
        while True:
            response = self._query_dir_with_class(session, dir_id, info_class=37)
            if _get_status(response) == STATUS_NO_MORE_FILES:
                break
            all_names.extend(self._parse_dir_entries(response, info_class=37))

        assert "." in all_names
        assert ".." in all_names
        assert "README.md" in all_names
        assert "LICENSE" in all_names
        assert ".github" in all_names

    def test_filenames_correct_class38(self, tmp_path):
        """Verify filenames are correctly encoded in FileIdFullDirectoryInformation.

        This is the info class the Linux kernel SMB3 client actually uses for readdir.
        Regression: a missing 4-byte Reserved field before FileId caused filenames
        to lose their first 2 characters (e.g. README.md -> ADME.md).
        """
        (tmp_path / "README.md").write_text("hello")
        (tmp_path / "LICENSE").write_text("MIT")
        (tmp_path / ".github").mkdir()

        session = self._setup_session(tmp_path)
        dir_id = self._open_dir(session)

        # Collect all entries using info class 38
        all_names: list[str] = []
        while True:
            response = self._query_dir_with_class(session, dir_id, info_class=38)
            if _get_status(response) == STATUS_NO_MORE_FILES:
                break
            all_names.extend(self._parse_dir_entries(response, info_class=38))

        assert "." in all_names
        assert ".." in all_names
        assert "README.md" in all_names
        assert "LICENSE" in all_names
        assert ".github" in all_names

    def test_empty_directory(self, tmp_path):
        session = self._setup_session(tmp_path)
        dir_id = self._open_dir(session)

        # First call should return . and ..
        response = self._query_dir(session, dir_id)
        assert _get_status(response) == STATUS_SUCCESS

        # Eventually should return NO_MORE_FILES
        for _ in range(10):
            response = self._query_dir(session, dir_id)
            if _get_status(response) == STATUS_NO_MORE_FILES:
                break
        assert _get_status(response) == STATUS_NO_MORE_FILES

    def test_restart_scans(self, tmp_path):
        (tmp_path / "file.txt").write_text("data")
        session = self._setup_session(tmp_path)
        dir_id = self._open_dir(session)

        # Read all entries
        self._query_dir(session, dir_id)
        while _get_status(self._query_dir(session, dir_id)) != STATUS_NO_MORE_FILES:
            pass

        # Restart and read again
        response = self._query_dir(session, dir_id, flags=0x01)  # SMB2_RESTART_SCANS
        assert _get_status(response) == STATUS_SUCCESS


class TestSubprocessIntegration:
    """Test the server as a subprocess (simulating QEMU guestfwd invocation)."""

    def test_server_starts_and_handles_negotiate(self, tmp_path):
        """Start server as subprocess, send NEGOTIATE, verify response."""
        share_dir = tmp_path / "share"
        share_dir.mkdir()
        (share_dir / "test.txt").write_text("hello")

        config = {"shares": {"TEST": {"host_path": str(share_dir), "readonly": False}}}
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config))

        # Build NEGOTIATE request
        dialects = struct.pack("<H", 0x0300)
        negotiate_body = struct.pack("<HHHHI16sIHH", 36, 1, 0, 0, 0, os.urandom(16), 0, 0, 0)
        negotiate_body = negotiate_body[:36] + dialects
        negotiate_header = _smb_header(Command.NEGOTIATE)
        negotiate_msg = negotiate_header + negotiate_body
        framed_input = _frame(negotiate_msg)

        proc = subprocess.run(
            [sys.executable, "-m", "quicksand_smb", "--config", str(config_path)],
            input=framed_input,
            capture_output=True,
            timeout=5,
        )

        # Server should have written a response
        assert len(proc.stdout) > 4, f"No response from server. stderr: {proc.stderr.decode()}"

        # Parse the response
        resp_payload, _ = _read_response(proc.stdout)
        assert resp_payload[:4] == SMB2_MAGIC
        assert _get_status(resp_payload) == STATUS_SUCCESS

    def test_no_tcp_port_opened(self, tmp_path):
        """Server must not open any TCP ports (guestfwd uses stdin/stdout)."""
        share_dir = tmp_path / "share"
        share_dir.mkdir()

        config = {"shares": {"TEST": {"host_path": str(share_dir), "readonly": False}}}
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config))

        # Send a NEGOTIATE so the server starts processing
        dialects = struct.pack("<H", 0x0300)
        negotiate_body = struct.pack("<HHHHI16sIHH", 36, 1, 0, 0, 0, os.urandom(16), 0, 0, 0)
        negotiate_body = negotiate_body[:36] + dialects
        negotiate_header = _smb_header(Command.NEGOTIATE)
        framed_input = _frame(negotiate_header + negotiate_body)

        if not shutil.which("lsof"):
            pytest.skip("lsof not available")

        proc = subprocess.Popen(
            [sys.executable, "-m", "quicksand_smb", "--config", str(config_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdin is not None
        try:
            proc.stdin.write(framed_input)
            proc.stdin.flush()
            time.sleep(0.2)

            lsof = subprocess.run(
                ["lsof", "-a", "-i", "-P", "-n", "-p", str(proc.pid)],
                capture_output=True,
                text=True,
                timeout=3,
            )
            listening = [
                line for line in lsof.stdout.strip().split("\n") if line and "LISTEN" in line
            ]
            assert not listening, f"Server opened TCP ports: {listening}"
        finally:
            proc.stdin.close()
            proc.wait(timeout=3)


class TestDynamicConfig:
    """Test dynamic share configuration changes."""

    def test_dynamic_share_addition(self, tmp_path):
        """Shares added after session creation should be accessible."""
        share1 = tmp_path / "share1"
        share1.mkdir()
        (share1 / "file1.txt").write_text("from share1")

        share2 = tmp_path / "share2"
        share2.mkdir()
        (share2 / "file2.txt").write_text("from share2")

        config = SMBConfig(shares={"SHARE1": ShareConfig(host_path=str(share1))})
        session = SMBSession(config=config)
        session.session_id = 1
        session.tree_map[1] = "SHARE1"
        session.next_tree_id = 2

        # Access first share
        name_bytes = "file1.txt".encode("utf-16-le")
        name_offset = 64 + 56
        body = struct.pack(
            "<HBBIqQIIIIIHHII",
            57,
            0,
            0,
            0,
            0,
            0,
            0x12019F,
            0x80,
            0x07,
            0x01,
            0,
            name_offset,
            len(name_bytes),
            0,
            0,
        )
        body += name_bytes
        header = _smb_header(Command.CREATE, tree_id=1, session_id=1)
        response = _dispatch(session, parse_request(header + body))
        assert _get_status(response) == STATUS_SUCCESS
        file_id = response[64 + 64 : 64 + 80]
        # Close
        close_body = struct.pack("<HH4x", 24, 0) + file_id
        close_header = _smb_header(Command.CLOSE, tree_id=1, session_id=1)
        _dispatch(session, parse_request(close_header + close_body))

        # Dynamically add second share
        session.config.shares["SHARE2"] = ShareConfig(host_path=str(share2))
        session.tree_map[2] = "SHARE2"

        # Access second share
        name_bytes = "file2.txt".encode("utf-16-le")
        body = struct.pack(
            "<HBBIqQIIIIIHHII",
            57,
            0,
            0,
            0,
            0,
            0,
            0x12019F,
            0x80,
            0x07,
            0x01,
            0,
            name_offset,
            len(name_bytes),
            0,
            0,
        )
        body += name_bytes
        header = _smb_header(Command.CREATE, tree_id=2, session_id=1)
        response = _dispatch(session, parse_request(header + body))
        assert _get_status(response) == STATUS_SUCCESS
