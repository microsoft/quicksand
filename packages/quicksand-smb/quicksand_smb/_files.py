"""CREATE, CLOSE, READ, WRITE, and FLUSH command handlers.

Manages file handles (FileId ↔ OS file descriptor) and performs I/O.
Path traversal is checked on every CREATE to prevent sandbox escapes.

Reference: [MS-SMB2] Sections 2.2.13-2.2.20.
"""

from __future__ import annotations

import contextlib
import os
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

from ._protocol import SMBRequest, build_error_response, build_response_header
from ._status import (
    STATUS_ACCESS_DENIED,
    STATUS_END_OF_FILE,
    STATUS_FILE_IS_A_DIRECTORY,
    STATUS_INVALID_HANDLE,
    STATUS_INVALID_PARAMETER,
    STATUS_NO_SUCH_FILE,
    STATUS_NOT_A_DIRECTORY,
    STATUS_OBJECT_NAME_COLLISION,
    STATUS_OBJECT_NAME_NOT_FOUND,
    STATUS_OBJECT_PATH_NOT_FOUND,
    STATUS_SUCCESS,
)

# CreateDisposition values
FILE_SUPERSEDE = 0x00000000
FILE_OPEN = 0x00000001
FILE_CREATE = 0x00000002
FILE_OPEN_IF = 0x00000003
FILE_OVERWRITE = 0x00000004
FILE_OVERWRITE_IF = 0x00000005

# CreateOptions flags
FILE_DIRECTORY_FILE = 0x00000001
FILE_NON_DIRECTORY_FILE = 0x00000040
FILE_DELETE_ON_CLOSE = 0x00001000

# File attribute constants
FILE_ATTRIBUTE_READONLY = 0x00000001
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_ARCHIVE = 0x00000020
FILE_ATTRIBUTE_NORMAL = 0x00000080

# DesiredAccess flags (simplified)
FILE_READ_DATA = 0x00000001
FILE_WRITE_DATA = 0x00000002
FILE_APPEND_DATA = 0x00000004
FILE_READ_ATTRIBUTES = 0x00000080
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
GENERIC_ALL = 0x10000000
DELETE = 0x00010000
MAXIMUM_ALLOWED = 0x02000000

# CreateAction values (returned in response)
FILE_OPENED = 0x00000001
FILE_CREATED = 0x00000002
FILE_OVERWRITTEN = 0x00000003


@dataclass
class HandleInfo:
    """State for an open file handle."""

    fd: int  # OS file descriptor (-1 for directories)
    path: Path
    is_dir: bool
    tree_id: int
    readonly: bool
    delete_on_close: bool = False
    dir_entries: list | None = None  # cached entry names for QUERY_DIRECTORY
    dir_offset: int = 0
    _scandir_cache: dict | None = None  # name → DirEntry cache for QUERY_DIRECTORY


@dataclass
class HandleManager:
    """Maps 16-byte FileIds to HandleInfo."""

    handles: dict[bytes, HandleInfo] = field(default_factory=dict)
    _counter: int = 0

    def allocate(self, info: HandleInfo) -> bytes:
        """Allocate a new FileId and store the handle."""
        self._counter += 1
        # FileId: 8 bytes persistent + 8 bytes volatile
        file_id = struct.pack("<QQ", 0, self._counter)
        self.handles[file_id] = info
        return file_id

    def get(self, file_id: bytes) -> HandleInfo | None:
        return self.handles.get(file_id)

    def remove(self, file_id: bytes) -> HandleInfo | None:
        return self.handles.pop(file_id, None)

    def close_all(self) -> None:
        for info in self.handles.values():
            if info.fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(info.fd)
        self.handles.clear()


def _resolve_path(share_root: Path, filename: str) -> Path | None:
    """Resolve a guest-requested filename to a host path, with traversal checks.

    Returns None if the path escapes the share root.
    """
    # Normalize: strip leading separators, convert backslash
    filename = filename.replace("\\", "/").lstrip("/")
    if not filename:
        return share_root

    target = (share_root / filename).resolve()
    share_resolved = share_root.resolve()

    # Verify the resolved path is within the share
    try:
        target.relative_to(share_resolved)
    except ValueError:
        return None

    return target


def _stat_to_attrs(st: os.stat_result, is_dir: bool) -> int:
    """Convert os.stat mode to SMB file attributes."""
    attrs = 0
    if is_dir:
        attrs |= FILE_ATTRIBUTE_DIRECTORY
    else:
        attrs |= FILE_ATTRIBUTE_ARCHIVE
    if not os.access(str(is_dir), os.W_OK):
        attrs |= FILE_ATTRIBUTE_READONLY
    return attrs or FILE_ATTRIBUTE_NORMAL


def _filetime(t: float) -> int:
    """Convert Unix timestamp to Windows FILETIME."""
    return int((t + 11644473600) * 10_000_000)


def _filetime_now() -> int:
    return _filetime(time.time())


def handle_create(
    req: SMBRequest,
    share_root: Path,
    share_readonly: bool,
    handles: HandleManager,
    tree_id: int,
) -> bytes:
    """Handle CREATE (open/create file or directory)."""
    payload = req.payload

    # CREATE request body (57 bytes fixed):
    # StructureSize(57)(2), SecurityFlags(1), RequestedOplockLevel(1),
    # ImpersonationLevel(4), SmbCreateFlags(8), Reserved(8),
    # DesiredAccess(4), FileAttributes(4), ShareAccess(4),
    # CreateDisposition(4), CreateOptions(4),
    # NameOffset(2), NameLength(2),
    # CreateContextsOffset(4), CreateContextsLength(4)
    if len(payload) < 56:
        return build_error_response(req.header, STATUS_INVALID_PARAMETER)

    (
        _struct_size,
        _sec_flags,
        _oplock_level,
        _impersonation,
        _smb_create_flags,
        _reserved,
        desired_access,
        _file_attributes,
        _share_access,
        create_disposition,
        create_options,
        name_offset,
        name_length,
        _ctx_offset,
        _ctx_length,
    ) = struct.unpack_from("<HBBIqQIIIIIHHII", payload)

    # Extract filename
    name_start = name_offset - 64  # offset from header start
    if name_start < 0:
        name_start = 56  # fallback: right after fixed part
    if name_length > 0 and name_start + name_length <= len(payload):
        filename = payload[name_start : name_start + name_length].decode(
            "utf-16-le", errors="replace"
        )
    else:
        filename = ""

    # Resolve and validate path
    target = _resolve_path(share_root, filename)
    if target is None:
        return build_error_response(req.header, STATUS_ACCESS_DENIED)

    # Check readonly
    wants_write = desired_access & (
        FILE_WRITE_DATA | FILE_APPEND_DATA | GENERIC_WRITE | GENERIC_ALL | DELETE
    )
    if share_readonly and wants_write and create_disposition not in (FILE_OPEN,):
        return build_error_response(req.header, STATUS_ACCESS_DENIED)

    is_dir_request = bool(create_options & FILE_DIRECTORY_FILE)
    is_nondir_request = bool(create_options & FILE_NON_DIRECTORY_FILE)
    delete_on_close = bool(create_options & FILE_DELETE_ON_CLOSE)

    target_exists = target.exists()
    target_is_dir = target.is_dir() if target_exists else False

    # Validate directory constraints
    if target_exists:
        if is_dir_request and not target_is_dir:
            return build_error_response(req.header, STATUS_NOT_A_DIRECTORY)
        if is_nondir_request and target_is_dir:
            return build_error_response(req.header, STATUS_FILE_IS_A_DIRECTORY)

    # Handle CreateDisposition
    action = FILE_OPENED
    try:
        if create_disposition == FILE_OPEN:
            if not target_exists:
                return build_error_response(req.header, STATUS_OBJECT_NAME_NOT_FOUND)
        elif create_disposition == FILE_CREATE:
            if target_exists:
                return build_error_response(req.header, STATUS_OBJECT_NAME_COLLISION)
            if is_dir_request:
                target.mkdir(parents=False, exist_ok=False)
            else:
                target.touch(exist_ok=False)
            action = FILE_CREATED
        elif create_disposition == FILE_OPEN_IF:
            if not target_exists:
                if is_dir_request:
                    target.mkdir(parents=False, exist_ok=True)
                else:
                    target.touch(exist_ok=True)
                action = FILE_CREATED
        elif create_disposition in (FILE_OVERWRITE, FILE_OVERWRITE_IF):
            if not target_exists:
                if create_disposition == FILE_OVERWRITE:
                    return build_error_response(req.header, STATUS_OBJECT_NAME_NOT_FOUND)
                target.touch(exist_ok=True)
                action = FILE_CREATED
            else:
                if not target_is_dir:
                    target.write_bytes(b"")  # truncate
                action = FILE_OVERWRITTEN
        elif create_disposition == FILE_SUPERSEDE:
            if target_exists and not target_is_dir:
                target.write_bytes(b"")
            elif not target_exists:
                if is_dir_request:
                    target.mkdir(parents=False, exist_ok=True)
                else:
                    target.touch(exist_ok=True)
            action = FILE_CREATED if not target_exists else FILE_OVERWRITTEN
    except PermissionError:
        return build_error_response(req.header, STATUS_ACCESS_DENIED)
    except FileNotFoundError:
        return build_error_response(req.header, STATUS_OBJECT_PATH_NOT_FOUND)
    except OSError:
        return build_error_response(req.header, STATUS_NO_SUCH_FILE)

    # Open file descriptor
    fd = -1
    is_dir = target.is_dir()
    if not is_dir:
        flags = os.O_RDONLY
        if not share_readonly and wants_write:
            flags = os.O_RDWR
        try:
            fd = os.open(str(target), flags)
        except PermissionError:
            return build_error_response(req.header, STATUS_ACCESS_DENIED)
        except FileNotFoundError:
            return build_error_response(req.header, STATUS_OBJECT_NAME_NOT_FOUND)
        except OSError:
            return build_error_response(req.header, STATUS_NO_SUCH_FILE)

    handle_info = HandleInfo(
        fd=fd,
        path=target,
        is_dir=is_dir,
        tree_id=tree_id,
        readonly=share_readonly,
        delete_on_close=delete_on_close,
    )
    file_id = handles.allocate(handle_info)

    # Stat for response
    try:
        st = target.stat()
    except OSError:
        st = None

    creation_time = _filetime(st.st_ctime) if st else _filetime_now()
    last_access_time = _filetime(st.st_atime) if st else _filetime_now()
    last_write_time = _filetime(st.st_mtime) if st else _filetime_now()
    change_time = last_write_time
    file_attrs = FILE_ATTRIBUTE_DIRECTORY if is_dir else FILE_ATTRIBUTE_ARCHIVE
    alloc_size = ((st.st_size + 4095) // 4096 * 4096) if st else 0
    end_of_file = st.st_size if st and not is_dir else 0

    # CREATE response (89 bytes fixed):
    # StructureSize(89), OplockLevel(1), Flags(1), CreateAction(4),
    # CreationTime(8), LastAccessTime(8), LastWriteTime(8), ChangeTime(8),
    # AllocationSize(8), EndOfFile(8), FileAttributes(4), Reserved2(4),
    # FileId(16), CreateContextsOffset(4), CreateContextsLength(4)
    body = struct.pack(
        "<H"  # StructureSize (89)
        "B"  # OplockLevel (none)
        "B"  # Flags
        "I"  # CreateAction
        "Q"  # CreationTime
        "Q"  # LastAccessTime
        "Q"  # LastWriteTime
        "Q"  # ChangeTime
        "Q"  # AllocationSize
        "Q"  # EndOfFile
        "I"  # FileAttributes
        "I"  # Reserved2
        "16s"  # FileId
        "I"  # CreateContextsOffset
        "I",  # CreateContextsLength
        89,
        0,  # OplockLevel: none
        0,  # Flags
        action,
        creation_time,
        last_access_time,
        last_write_time,
        change_time,
        alloc_size,
        end_of_file,
        file_attrs,
        0,  # Reserved2
        file_id,
        0,  # No create contexts
        0,
    )

    header = build_response_header(req.header, STATUS_SUCCESS, len(body))
    return header + body


def handle_close(req: SMBRequest, handles: HandleManager) -> bytes:
    """Handle CLOSE request."""
    payload = req.payload

    # CLOSE request: StructureSize(24), Flags(2), Reserved(4), FileId(16)
    if len(payload) < 24:
        return build_error_response(req.header, STATUS_INVALID_PARAMETER)

    _struct_size, flags = struct.unpack_from("<HH", payload)
    file_id = payload[8:24]

    info = handles.remove(file_id)
    if info is None:
        return build_error_response(req.header, STATUS_INVALID_HANDLE)

    # Close the fd
    if info.fd >= 0:
        with contextlib.suppress(OSError):
            os.close(info.fd)

    # Delete on close
    if info.delete_on_close:
        try:
            if info.is_dir:
                info.path.rmdir()
            else:
                info.path.unlink()
        except OSError:
            pass

    # Stat for response (if flags request it)
    if flags & 0x0001 and info.path.exists():  # SMB2_CLOSE_FLAG_POSTQUERY_ATTRIB
        st = info.path.stat()
        response_flags = 0x0001
        creation_time = _filetime(st.st_ctime)
        last_access_time = _filetime(st.st_atime)
        last_write_time = _filetime(st.st_mtime)
        change_time = last_write_time
        alloc_size = (st.st_size + 4095) // 4096 * 4096
        end_of_file = st.st_size
        file_attrs = FILE_ATTRIBUTE_DIRECTORY if info.is_dir else FILE_ATTRIBUTE_ARCHIVE
    else:
        response_flags = 0
        creation_time = last_access_time = last_write_time = change_time = 0
        alloc_size = end_of_file = 0
        file_attrs = 0

    # CLOSE response: StructureSize(60), Flags(2), Reserved(4),
    # CreationTime(8), LastAccessTime(8), LastWriteTime(8), ChangeTime(8),
    # AllocationSize(8), EndOfFile(8), FileAttributes(4)
    body = struct.pack(
        "<HHI"  # StructureSize, Flags, Reserved
        "QQQQ"  # timestamps
        "QQ"  # sizes
        "I",  # FileAttributes
        60,
        response_flags,
        0,
        creation_time,
        last_access_time,
        last_write_time,
        change_time,
        alloc_size,
        end_of_file,
        file_attrs,
    )

    header = build_response_header(req.header, STATUS_SUCCESS, len(body))
    return header + body


def handle_read(req: SMBRequest, handles: HandleManager) -> bytes:
    """Handle READ request."""
    payload = req.payload

    # READ request: StructureSize(49), Padding(1), Flags(1), Length(4),
    # Offset(8), FileId(16), MinimumCount(4), Channel(4),
    # RemainingBytes(4), ReadChannelInfoOffset(2), ReadChannelInfoLength(2)
    if len(payload) < 48:
        return build_error_response(req.header, STATUS_INVALID_PARAMETER)

    _struct_size, _padding, _flags, length, offset = struct.unpack_from("<HBBIQ", payload)
    file_id = payload[16:32]

    info = handles.get(file_id)
    if info is None:
        return build_error_response(req.header, STATUS_INVALID_HANDLE)

    if info.is_dir:
        return build_error_response(req.header, STATUS_FILE_IS_A_DIRECTORY)

    try:
        data = os.pread(info.fd, length, offset)
    except OSError:
        return build_error_response(req.header, STATUS_END_OF_FILE)

    if not data:
        return build_error_response(req.header, STATUS_END_OF_FILE)

    # READ response: StructureSize(17), DataOffset(1), Reserved(4),
    # DataLength(4), DataRemaining(4), Reserved2(4), Data(variable)
    data_offset = 64 + 16  # header + fixed body (16 bytes, StructureSize=17)

    body = struct.pack(
        "<H"  # StructureSize (17)
        "B"  # DataOffset
        "x"  # Reserved (1 byte padding)
        "I"  # DataLength
        "I"  # DataRemaining
        "I",  # Reserved2
        17,
        data_offset,
        len(data),
        0,  # DataRemaining
        0,  # Reserved2
    )

    header = build_response_header(req.header, STATUS_SUCCESS, len(body) + len(data))
    return header + body + data


def handle_write(req: SMBRequest, handles: HandleManager) -> bytes:
    """Handle WRITE request."""
    payload = req.payload

    # WRITE request: StructureSize(49), DataOffset(2), Length(4),
    # Offset(8), FileId(16), Channel(4), RemainingBytes(4),
    # WriteChannelInfoOffset(2), WriteChannelInfoLength(2), Flags(4)
    if len(payload) < 48:
        return build_error_response(req.header, STATUS_INVALID_PARAMETER)

    _struct_size, data_offset_field, length, offset = struct.unpack_from("<HHIQ", payload)
    file_id = payload[16:32]

    info = handles.get(file_id)
    if info is None:
        return build_error_response(req.header, STATUS_INVALID_HANDLE)

    if info.is_dir:
        return build_error_response(req.header, STATUS_FILE_IS_A_DIRECTORY)

    if info.readonly:
        return build_error_response(req.header, STATUS_ACCESS_DENIED)

    # Data starts at data_offset_field relative to header start
    data_start = data_offset_field - 64
    if data_start < 0:
        data_start = 48  # fallback after fixed part
    write_data = payload[data_start : data_start + length]

    try:
        written = os.pwrite(info.fd, write_data, offset)
    except OSError:
        return build_error_response(req.header, STATUS_ACCESS_DENIED)

    # WRITE response: StructureSize(17), Reserved(2), Count(4),
    # Remaining(4), WriteChannelInfoOffset(2), WriteChannelInfoLength(2)
    body = struct.pack(
        "<H"  # StructureSize (17)
        "H"  # Reserved
        "I"  # Count
        "I"  # Remaining
        "H"  # WriteChannelInfoOffset
        "H",  # WriteChannelInfoLength
        17,
        0,
        written,
        0,
        0,
        0,
    )

    header = build_response_header(req.header, STATUS_SUCCESS, len(body))
    return header + body


def handle_flush(req: SMBRequest, handles: HandleManager) -> bytes:
    """Handle FLUSH request."""
    payload = req.payload

    if len(payload) < 24:
        return build_error_response(req.header, STATUS_INVALID_PARAMETER)

    # FLUSH request: StructureSize(24), Reserved1(2), Reserved2(4), FileId(16)
    file_id = payload[8:24]

    info = handles.get(file_id)
    if info is None:
        return build_error_response(req.header, STATUS_INVALID_HANDLE)

    if info.fd >= 0:
        with contextlib.suppress(OSError):
            os.fsync(info.fd)

    # FLUSH response: StructureSize(4), Reserved(2)
    body = struct.pack("<HH", 4, 0)
    header = build_response_header(req.header, STATUS_SUCCESS, len(body))
    return header + body
