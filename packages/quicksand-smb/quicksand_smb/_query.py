"""QUERY_INFO, QUERY_DIRECTORY, and SET_INFO command handlers.

Implements file/filesystem metadata queries and directory enumeration.

Reference: [MS-SMB2] Sections 2.2.37-2.2.42 and [MS-FSCC].
"""

from __future__ import annotations

import logging
import os
import struct
from pathlib import Path

from ._files import (
    FILE_ATTRIBUTE_ARCHIVE,
    FILE_ATTRIBUTE_DIRECTORY,
    HandleInfo,
    HandleManager,
    _filetime,
    _filetime_now,
)
from ._protocol import SMBRequest, build_error_response, build_response_header
from ._status import (
    STATUS_ACCESS_DENIED,
    STATUS_BUFFER_OVERFLOW,
    STATUS_BUFFER_TOO_SMALL,
    STATUS_INVALID_HANDLE,
    STATUS_INVALID_PARAMETER,
    STATUS_NO_MORE_FILES,
    STATUS_NOT_SUPPORTED,
    STATUS_OBJECT_NAME_NOT_FOUND,
    STATUS_SUCCESS,
)

logger = logging.getLogger("quicksand.smb.query")

# InfoType values
SMB2_0_INFO_FILE = 0x01
SMB2_0_INFO_FILESYSTEM = 0x02

# FileInfoClass values
FILE_BASIC_INFORMATION = 4
FILE_STANDARD_INFORMATION = 5
FILE_INTERNAL_INFORMATION = 6
FILE_EA_INFORMATION = 7
FILE_ACCESS_INFORMATION = 8
FILE_POSITION_INFORMATION = 14
FILE_MODE_INFORMATION = 16
FILE_ALIGNMENT_INFORMATION = 17
FILE_ALL_INFORMATION = 18
FILE_NETWORK_OPEN_INFORMATION = 34
FILE_ATTRIBUTE_TAG_INFORMATION = 35
FILE_STREAM_INFORMATION = 22
FILE_COMPRESSION_INFORMATION = 28

# FsInfoClass values
FS_VOLUME_INFORMATION = 1
FS_SIZE_INFORMATION = 3
FS_DEVICE_INFORMATION = 4
FS_ATTRIBUTE_INFORMATION = 5
FS_FULL_SIZE_INFORMATION = 7
FS_SECTOR_SIZE_INFORMATION = 11

# FileInfoClass for directory queries
FILE_DIRECTORY_INFORMATION = 1
FILE_BOTH_DIRECTORY_INFORMATION = 3
FILE_ID_BOTH_DIRECTORY_INFORMATION = 37
FILE_ID_FULL_DIRECTORY_INFORMATION = 38
FILE_NAMES_INFORMATION = 12

# QUERY_DIRECTORY flags
SMB2_RESTART_SCANS = 0x01
SMB2_RETURN_SINGLE_ENTRY = 0x02
SMB2_INDEX_SPECIFIED = 0x04

# SET_INFO FileInfoClass
FILE_DISPOSITION_INFORMATION = 13
FILE_RENAME_INFORMATION = 10
FILE_END_OF_FILE_INFORMATION = 20
FILE_ALLOCATION_INFORMATION = 19


def _build_basic_info(st: os.stat_result, is_dir: bool) -> bytes:
    """FileBasicInformation: timestamps + attributes (40 bytes)."""
    attrs = FILE_ATTRIBUTE_DIRECTORY if is_dir else FILE_ATTRIBUTE_ARCHIVE
    return struct.pack(
        "<QQQQII",
        _filetime(st.st_ctime),  # CreationTime
        _filetime(st.st_atime),  # LastAccessTime
        _filetime(st.st_mtime),  # LastWriteTime
        _filetime(st.st_mtime),  # ChangeTime
        attrs,  # FileAttributes
        0,  # Reserved (padding to 40 bytes)
    )


def _build_standard_info(st: os.stat_result, is_dir: bool) -> bytes:
    """FileStandardInformation: sizes + link count + delete/dir flags (24 bytes)."""
    alloc_size = (st.st_size + 4095) // 4096 * 4096 if not is_dir else 0
    eof = st.st_size if not is_dir else 0
    return struct.pack(
        "<QQI??H",
        alloc_size,
        eof,
        st.st_nlink,
        False,  # DeletePending
        is_dir,  # Directory
        0,  # Reserved (padding to 24 bytes)
    )


def _build_internal_info(st: os.stat_result) -> bytes:
    """FileInternalInformation: inode number."""
    return struct.pack("<Q", st.st_ino)


def _build_ea_info() -> bytes:
    """FileEaInformation: EA size (always 0)."""
    return struct.pack("<I", 0)


def _build_access_info() -> bytes:
    """FileAccessInformation: granted access mask."""
    return struct.pack("<I", 0x001F01FF)  # FILE_ALL_ACCESS


def _build_position_info() -> bytes:
    """FilePositionInformation."""
    return struct.pack("<Q", 0)


def _build_mode_info() -> bytes:
    """FileModeInformation."""
    return struct.pack("<I", 0)


def _build_alignment_info() -> bytes:
    """FileAlignmentInformation."""
    return struct.pack("<I", 0)  # FILE_BYTE_ALIGNMENT


def _build_name_info(info: HandleInfo, share_root: Path) -> bytes:
    """FileNameInformation: relative path within share."""
    try:
        rel = info.path.relative_to(share_root)
        name = "\\" + str(rel).replace("/", "\\")
    except ValueError:
        name = "\\"
    name_bytes = name.encode("utf-16-le")
    return struct.pack("<I", len(name_bytes)) + name_bytes


def _build_network_open_info(st: os.stat_result, is_dir: bool) -> bytes:
    """FileNetworkOpenInformation."""
    attrs = FILE_ATTRIBUTE_DIRECTORY if is_dir else FILE_ATTRIBUTE_ARCHIVE
    alloc_size = (st.st_size + 4095) // 4096 * 4096 if not is_dir else 0
    eof = st.st_size if not is_dir else 0
    return struct.pack(
        "<QQQQQQI4x",  # 4x for Reserved
        _filetime(st.st_ctime),
        _filetime(st.st_atime),
        _filetime(st.st_mtime),
        _filetime(st.st_mtime),
        alloc_size,
        eof,
        attrs,
    )


def _build_attribute_tag_info(is_dir: bool) -> bytes:
    """FileAttributeTagInformation."""
    attrs = FILE_ATTRIBUTE_DIRECTORY if is_dir else FILE_ATTRIBUTE_ARCHIVE
    return struct.pack("<II", attrs, 0)  # ReparseTag = 0


def handle_query_info(
    req: SMBRequest,
    handles: HandleManager,
    shares: dict[str, dict],
    tree_map: dict[int, str],
) -> bytes:
    """Handle QUERY_INFO request."""
    payload = req.payload

    # QUERY_INFO request: StructureSize(41), InfoType(1), FileInfoClass(1),
    # OutputBufferLength(4), InputBufferOffset(2), Reserved(2),
    # InputBufferLength(4), AdditionalInformation(4), Flags(4), FileId(16)
    if len(payload) < 40:
        return build_error_response(req.header, STATUS_INVALID_PARAMETER)

    _struct_size, info_type, file_info_class, output_buffer_length = struct.unpack_from(
        "<HBBI", payload
    )
    file_id = payload[24:40]

    import logging

    logging.getLogger("quicksand.smb").debug(
        "QUERY_INFO: type=%d class=%d buflen=%d fid=%s",
        info_type,
        file_info_class,
        output_buffer_length,
        file_id.hex(),
    )

    info = handles.get(file_id)
    if info is None:
        return build_error_response(req.header, STATUS_INVALID_HANDLE)

    try:
        st = info.path.stat()
    except OSError:
        return build_error_response(req.header, STATUS_OBJECT_NAME_NOT_FOUND)

    # Get share root for name info
    share_name = tree_map.get(info.tree_id)
    share_root = (
        Path(shares[share_name]["host_path"])
        if share_name and share_name in shares
        else info.path.parent
    )

    if info_type == SMB2_0_INFO_FILE:
        output = _handle_file_info(file_info_class, st, info, share_root)
    elif info_type == SMB2_0_INFO_FILESYSTEM:
        output = _handle_fs_info(file_info_class, info.path)
    else:
        output = None

    if output is None:
        return build_error_response(req.header, STATUS_NOT_SUPPORTED)

    if len(output) > output_buffer_length:
        # Truncate and return BUFFER_OVERFLOW for some info classes
        output = output[:output_buffer_length]
        status = STATUS_BUFFER_OVERFLOW
    else:
        status = STATUS_SUCCESS

    # QUERY_INFO response: StructureSize(9), OutputBufferOffset(2),
    # OutputBufferLength(4), Output(variable)
    output_offset = 64 + 8  # header + fixed body
    body = struct.pack(
        "<HHI",
        9,  # StructureSize
        output_offset,
        len(output),
    )

    header = build_response_header(req.header, status, len(body) + len(output))
    return header + body + output


def _handle_file_info(
    info_class: int,
    st: os.stat_result,
    info: HandleInfo,
    share_root: Path,
) -> bytes | None:
    """Build response data for SMB2_0_INFO_FILE queries."""
    if info_class == FILE_BASIC_INFORMATION:
        return _build_basic_info(st, info.is_dir)
    elif info_class == FILE_STANDARD_INFORMATION:
        return _build_standard_info(st, info.is_dir)
    elif info_class == FILE_INTERNAL_INFORMATION:
        return _build_internal_info(st)
    elif info_class == FILE_EA_INFORMATION:
        return _build_ea_info()
    elif info_class == FILE_ACCESS_INFORMATION:
        return _build_access_info()
    elif info_class == FILE_POSITION_INFORMATION:
        return _build_position_info()
    elif info_class == FILE_MODE_INFORMATION:
        return _build_mode_info()
    elif info_class == FILE_ALIGNMENT_INFORMATION:
        return _build_alignment_info()
    elif info_class == FILE_ALL_INFORMATION:
        # Composite: Basic(40) + Standard(24) + Internal(8) + EA(4) +
        # Access(4) + Position(8) + Mode(4) + Alignment(4) + Name(4+var)
        # Total fixed: 100 bytes before FileName variable part
        data = _build_basic_info(st, info.is_dir)  # 40
        data += _build_standard_info(st, info.is_dir)  # 24
        data += _build_internal_info(st)  # 8
        data += _build_ea_info()  # 4
        data += _build_access_info()  # 4
        data += _build_position_info()  # 8
        data += _build_mode_info()  # 4
        data += _build_alignment_info()  # 4
        data += _build_name_info(info, share_root)  # 4 + name bytes
        return data
    elif info_class == FILE_NETWORK_OPEN_INFORMATION:
        return _build_network_open_info(st, info.is_dir)
    elif info_class == FILE_ATTRIBUTE_TAG_INFORMATION:
        return _build_attribute_tag_info(info.is_dir)
    elif info_class == FILE_STREAM_INFORMATION:
        # Return a single default data stream
        stream_name = "::$DATA".encode("utf-16-le")
        return (
            struct.pack(
                "<IIQQ",
                0,  # NextEntryOffset
                len(stream_name),
                st.st_size,
                (st.st_size + 4095) // 4096 * 4096,
            )
            + stream_name
        )
    elif info_class == FILE_COMPRESSION_INFORMATION:
        return struct.pack("<QHBxI", st.st_size, 0, 0, 0)
    return None


def _handle_fs_info(info_class: int, path: Path) -> bytes | None:
    """Build response data for SMB2_0_INFO_FILESYSTEM queries."""
    try:
        svfs = os.statvfs(str(path))
    except OSError:
        svfs = None

    if info_class == FS_VOLUME_INFORMATION:
        label = "quicksand".encode("utf-16-le")
        return (
            struct.pack(
                "<QI?xI",
                _filetime_now(),  # VolumeCreationTime
                0x12345678,  # VolumeSerialNumber
                False,  # SupportsObjects
                len(label),  # VolumeLabelLength
            )
            + label
        )

    elif info_class == FS_SIZE_INFORMATION:
        if svfs is None:
            return struct.pack("<QQIi", 0, 0, 4096, 1)
        total_units = svfs.f_blocks
        avail_units = svfs.f_bavail
        return struct.pack("<QQIi", total_units, avail_units, svfs.f_frsize, 1)

    elif info_class == FS_FULL_SIZE_INFORMATION:
        if svfs is None:
            return struct.pack("<QQQIi", 0, 0, 0, 4096, 1)
        return struct.pack(
            "<QQQIi",
            svfs.f_blocks,  # TotalAllocationUnits
            svfs.f_bavail,  # CallerAvailableAllocationUnits
            svfs.f_bfree,  # ActualAvailableAllocationUnits
            svfs.f_frsize,  # BytesPerSector (use fragment size)
            1,  # SectorsPerAllocationUnit
        )

    elif info_class == FS_DEVICE_INFORMATION:
        return struct.pack(
            "<II",
            0x00000007,  # FILE_DEVICE_DISK
            0x00000020,  # FILE_DEVICE_IS_MOUNTED
        )

    elif info_class == FS_ATTRIBUTE_INFORMATION:
        # FILE_CASE_PRESERVED_NAMES | FILE_UNICODE_ON_DISK
        fs_name = "quicksand".encode("utf-16-le")
        return (
            struct.pack(
                "<III",
                0x00000002 | 0x00000004,  # FileSystemAttributes
                255,  # MaximumComponentNameLength
                len(fs_name),  # FileSystemNameLength
            )
            + fs_name
        )

    elif info_class == FS_SECTOR_SIZE_INFORMATION:
        return struct.pack(
            "<IIIII",
            4096,  # LogicalBytesPerSector
            4096,  # PhysicalBytesPerSectorForAtomicity
            4096,  # PhysicalBytesPerSectorForPerformance
            4096,  # FileSystemEffectivePhysicalBytesPerSectorForAtomicity
            0,  # Flags
        )

    return None


# ---------------------------------------------------------------------------
# QUERY_DIRECTORY
# ---------------------------------------------------------------------------


def _build_dir_entry(
    entry: os.DirEntry | None,
    info_class: int,
    next_offset: int = 0,
    root_path: Path | None = None,
    is_dot: str | None = None,
) -> bytes | None:
    """Build a single directory entry in the requested format.

    `is_dot` should be "." or ".." for the special entries, None for regular.
    """
    if is_dot:
        name = is_dot
        if root_path:
            try:
                st = root_path.stat()
            except OSError:
                return None
            is_dir = root_path.is_dir()
        else:
            return None
    elif entry is not None:
        name = entry.name
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            return None
        is_dir = entry.is_dir(follow_symlinks=False)
    else:
        return None

    name_bytes = name.encode("utf-16-le")
    attrs = FILE_ATTRIBUTE_DIRECTORY if is_dir else FILE_ATTRIBUTE_ARCHIVE
    alloc_size = (st.st_size + 4095) // 4096 * 4096 if not is_dir else 0
    eof = st.st_size if not is_dir else 0

    if info_class in (FILE_BOTH_DIRECTORY_INFORMATION, FILE_ID_BOTH_DIRECTORY_INFORMATION):
        # FileBothDirectoryInformation:
        # NextEntryOffset(4), FileIndex(4), CreationTime(8), LastAccessTime(8),
        # LastWriteTime(8), ChangeTime(8), EndOfFile(8), AllocationSize(8),
        # FileAttributes(4), FileNameLength(4), EaSize(4), ShortNameLength(1),
        # Reserved(1), ShortName(24), FileName(variable)
        fixed = struct.pack(
            "<II"  # NextEntryOffset, FileIndex
            "QQQQ"  # timestamps
            "QQ"  # EOF, AllocationSize
            "II"  # FileAttributes, FileNameLength
            "I"  # EaSize
            "Bx"  # ShortNameLength, Reserved
            "24s",  # ShortName (empty)
            0,  # NextEntryOffset (patched later)
            0,  # FileIndex
            _filetime(st.st_ctime),
            _filetime(st.st_atime),
            _filetime(st.st_mtime),
            _filetime(st.st_mtime),
            eof,
            alloc_size,
            attrs,
            len(name_bytes),
            0,  # EaSize
            0,  # ShortNameLength
            b"\x00" * 24,  # ShortName
        )
        if info_class == FILE_ID_BOTH_DIRECTORY_INFORMATION:
            # Additional: Reserved2(2), FileId(8) before FileName
            fixed += struct.pack("<HQ", 0, st.st_ino)

        entry_data = fixed + name_bytes

    elif info_class == FILE_DIRECTORY_INFORMATION:
        fixed = struct.pack(
            "<IIQQQQQQII",
            0,
            0,
            _filetime(st.st_ctime),
            _filetime(st.st_atime),
            _filetime(st.st_mtime),
            _filetime(st.st_mtime),
            eof,
            alloc_size,
            attrs,
            len(name_bytes),
        )
        entry_data = fixed + name_bytes

    elif info_class == FILE_NAMES_INFORMATION:
        fixed = struct.pack("<III", 0, 0, len(name_bytes))
        entry_data = fixed + name_bytes

    elif info_class == FILE_ID_FULL_DIRECTORY_INFORMATION:
        fixed = struct.pack(
            "<II"
            "QQQQ"
            "QQ"
            "II"
            "I"  # EaSize
            "I"  # Reserved
            "Q",  # FileId
            0,
            0,
            _filetime(st.st_ctime),
            _filetime(st.st_atime),
            _filetime(st.st_mtime),
            _filetime(st.st_mtime),
            eof,
            alloc_size,
            attrs,
            len(name_bytes),
            0,  # EaSize
            0,  # Reserved
            st.st_ino,
        )
        entry_data = fixed + name_bytes

    else:
        return None

    # Pad to 8-byte alignment
    padded_len = (len(entry_data) + 7) & ~7
    entry_data = entry_data.ljust(padded_len, b"\x00")

    # Set NextEntryOffset in first 4 bytes
    if next_offset != 0:
        struct.pack_into("<I", bytearray(entry_data), 0, len(entry_data))
        entry_data = bytes(bytearray(entry_data)[:4]) + entry_data[4:]
        # Actually, let's do this properly
        ba = bytearray(entry_data)
        struct.pack_into("<I", ba, 0, len(ba))
        entry_data = bytes(ba)

    return entry_data


def handle_query_directory(
    req: SMBRequest,
    handles: HandleManager,
    shares: dict[str, dict],
    tree_map: dict[int, str],
) -> bytes:
    """Handle QUERY_DIRECTORY request."""
    payload = req.payload

    # QUERY_DIRECTORY request: StructureSize(33), FileInfoClass(1),
    # Flags(1), FileIndex(4), FileId(16), FileNameOffset(2),
    # FileNameLength(2), OutputBufferLength(4)
    if len(payload) < 32:
        return build_error_response(req.header, STATUS_INVALID_PARAMETER)

    _struct_size, info_class, flags, _file_index = struct.unpack_from("<HBBI", payload)
    file_id = payload[8:24]
    name_offset, name_length, output_buffer_length = struct.unpack_from("<HHI", payload, 24)
    logger.debug("QUERY_DIRECTORY: info_class=%d flags=0x%x", info_class, flags)

    info = handles.get(file_id)
    if info is None:
        return build_error_response(req.header, STATUS_INVALID_HANDLE)

    if not info.is_dir:
        return build_error_response(req.header, STATUS_INVALID_PARAMETER)

    # Parse search pattern
    pattern_start = name_offset - 64
    if pattern_start >= 0 and name_length > 0 and pattern_start + name_length <= len(payload):
        payload[pattern_start : pattern_start + name_length].decode("utf-16-le", errors="replace")
    else:
        pass

    restart = bool(flags & SMB2_RESTART_SCANS)
    single_entry = bool(flags & SMB2_RETURN_SINGLE_ENTRY)

    # Build or reset directory listing
    if info.dir_entries is None or restart:
        try:
            entries = list(os.scandir(str(info.path)))
        except OSError:
            return build_error_response(req.header, STATUS_OBJECT_NAME_NOT_FOUND)

        # Prepend . and ..
        info.dir_entries = ["..", "."] + [e.name for e in entries]
        info._scandir_cache = {e.name: e for e in entries}
        info.dir_offset = 0

    if info.dir_offset >= len(info.dir_entries):
        return build_error_response(req.header, STATUS_NO_MORE_FILES)

    # Build entries fitting in output buffer
    result_entries: list[bytes] = []
    total_size = 0

    while info.dir_offset < len(info.dir_entries):
        entry_name = info.dir_entries[info.dir_offset]

        if entry_name in (".", ".."):
            entry_data = _build_dir_entry(None, info_class, root_path=info.path, is_dot=entry_name)
        else:
            scandir_entry = info._scandir_cache.get(entry_name) if info._scandir_cache else None
            if scandir_entry is None:
                info.dir_offset += 1
                continue
            entry_data = _build_dir_entry(scandir_entry, info_class)

        if entry_data is None:
            info.dir_offset += 1
            continue

        if total_size + len(entry_data) > output_buffer_length:
            if not result_entries:
                # First entry doesn't fit — buffer too small
                return build_error_response(req.header, STATUS_BUFFER_TOO_SMALL)
            break

        result_entries.append(entry_data)
        total_size += len(entry_data)
        info.dir_offset += 1

        if single_entry:
            break

    if not result_entries:
        return build_error_response(req.header, STATUS_NO_MORE_FILES)

    # Chain entries: set NextEntryOffset for all except last
    output = bytearray()
    for i, entry in enumerate(result_entries):
        ba = bytearray(entry)
        if i < len(result_entries) - 1:
            struct.pack_into("<I", ba, 0, len(ba))  # NextEntryOffset = own length
        else:
            struct.pack_into("<I", ba, 0, 0)  # Last entry: 0
        output.extend(ba)

    output = bytes(output)

    # QUERY_DIRECTORY response: StructureSize(9), OutputBufferOffset(2),
    # OutputBufferLength(4), Output(variable)
    output_offset = 64 + 8  # header + fixed body
    body = struct.pack(
        "<HHI",
        9,
        output_offset,
        len(output),
    )

    header = build_response_header(req.header, STATUS_SUCCESS, len(body) + len(output))
    return header + body + output


# ---------------------------------------------------------------------------
# SET_INFO
# ---------------------------------------------------------------------------


def handle_set_info(
    req: SMBRequest,
    handles: HandleManager,
) -> bytes:
    """Handle SET_INFO request."""
    payload = req.payload

    # SET_INFO request: StructureSize(33), InfoType(1), FileInfoClass(1),
    # BufferLength(4), BufferOffset(2), Reserved(2),
    # AdditionalInformation(4), FileId(16)
    if len(payload) < 32:
        return build_error_response(req.header, STATUS_INVALID_PARAMETER)

    _struct_size, info_type, file_info_class, buffer_length = struct.unpack_from("<HBBI", payload)
    buffer_offset = struct.unpack_from("<H", payload, 8)[0]
    file_id = payload[16:32]

    info = handles.get(file_id)
    if info is None:
        return build_error_response(req.header, STATUS_INVALID_HANDLE)

    # Extract the input buffer
    buf_start = buffer_offset - 64
    if buf_start < 0:
        buf_start = 32
    buffer_data = payload[buf_start : buf_start + buffer_length]

    if info_type == SMB2_0_INFO_FILE:
        result = _handle_set_file_info(file_info_class, buffer_data, info)
    else:
        result = STATUS_NOT_SUPPORTED

    if result != STATUS_SUCCESS:
        return build_error_response(req.header, result)

    # SET_INFO response: StructureSize(2)
    body = struct.pack("<H", 2)
    header = build_response_header(req.header, STATUS_SUCCESS, len(body))
    return header + body


def _handle_set_file_info(
    info_class: int,
    data: bytes,
    info: HandleInfo,
) -> int:
    """Apply SET_INFO for file info classes. Returns NTSTATUS."""
    if info.readonly:
        return STATUS_ACCESS_DENIED

    if info_class == FILE_BASIC_INFORMATION:
        # CreationTime(8), LastAccessTime(8), LastWriteTime(8), ChangeTime(8), FileAttributes(4)
        if len(data) < 36:
            return STATUS_INVALID_PARAMETER
        _creation, last_access, last_write, _change, _attrs = struct.unpack_from("<QQQQI", data)
        try:
            # Convert FILETIME to Unix time (only if non-zero)
            atime = (last_access / 10_000_000 - 11644473600) if last_access else None
            mtime = (last_write / 10_000_000 - 11644473600) if last_write else None
            if atime is not None or mtime is not None:
                cur_st = info.path.stat()
                os.utime(
                    str(info.path),
                    (atime or cur_st.st_atime, mtime or cur_st.st_mtime),
                )
        except OSError:
            pass  # Best effort
        return STATUS_SUCCESS

    elif info_class == FILE_DISPOSITION_INFORMATION:
        if len(data) < 1:
            return STATUS_INVALID_PARAMETER
        delete_pending = struct.unpack_from("<B", data)[0]
        info.delete_on_close = bool(delete_pending)
        return STATUS_SUCCESS

    elif info_class == FILE_END_OF_FILE_INFORMATION:
        if len(data) < 8:
            return STATUS_INVALID_PARAMETER
        new_size = struct.unpack_from("<Q", data)[0]
        try:
            if info.fd >= 0:
                os.ftruncate(info.fd, new_size)
        except OSError:
            return STATUS_ACCESS_DENIED
        return STATUS_SUCCESS

    elif info_class == FILE_ALLOCATION_INFORMATION:
        # Can treat same as end-of-file for our purposes
        if len(data) < 8:
            return STATUS_INVALID_PARAMETER
        return STATUS_SUCCESS  # No-op

    elif info_class == FILE_RENAME_INFORMATION:
        # ReplaceIfExists(1), Reserved(7), RootDirectory(8),
        # FileNameLength(4), FileName(variable)
        if len(data) < 20:
            return STATUS_INVALID_PARAMETER
        replace_if_exists = struct.unpack_from("<B", data)[0]
        name_length = struct.unpack_from("<I", data, 16)[0]
        name_bytes = data[20 : 20 + name_length]
        new_name = name_bytes.decode("utf-16-le", errors="replace")
        # The name might be a full path like \newname or just newname
        new_name = new_name.lstrip("\\").replace("\\", "/")
        new_path = info.path.parent / new_name

        try:
            if new_path.exists() and not replace_if_exists:
                from ._status import STATUS_OBJECT_NAME_COLLISION

                return STATUS_OBJECT_NAME_COLLISION
            os.rename(str(info.path), str(new_path))
            info.path = new_path
        except OSError:
            return STATUS_ACCESS_DENIED
        return STATUS_SUCCESS

    return STATUS_NOT_SUPPORTED
