"""TREE_CONNECT and TREE_DISCONNECT handlers.

TREE_CONNECT maps a UNC share path (e.g., \\\\10.0.2.100\\SHARE_NAME) to a
host directory. TREE_DISCONNECT releases the mapping.

Reference: [MS-SMB2] Sections 2.2.9-2.2.10.
"""

from __future__ import annotations

import struct

from ._protocol import SMBRequest, build_error_response, build_response_header
from ._status import STATUS_BAD_NETWORK_NAME, STATUS_SUCCESS

# Share type constants
SMB2_SHARE_TYPE_DISK = 0x01
SMB2_SHARE_TYPE_PIPE = 0x02


def handle_tree_connect(
    req: SMBRequest,
    shares: dict[str, dict],
    tree_map: dict[int, str],
    next_tree_id: int,
) -> tuple[bytes, int | None]:
    """Handle TREE_CONNECT. Returns (response_bytes, new_tree_id or None on failure)."""
    payload = req.payload

    # TREE_CONNECT request body:
    # StructureSize(9), Reserved/Flags(2), PathOffset(2), PathLength(2)
    _struct_size, _flags, path_offset, path_length = struct.unpack_from("<HHHH", payload)

    # PathOffset is from start of SMB header. Payload starts at byte 64.
    path_start = path_offset - 64
    if path_start < 0 or path_start + path_length > len(payload):
        return build_error_response(req.header, STATUS_BAD_NETWORK_NAME), None

    path_bytes = payload[path_start : path_start + path_length]
    path_str = path_bytes.decode("utf-16-le", errors="replace")

    # Extract share name: \\server\SHARE_NAME → SHARE_NAME
    parts = path_str.replace("\\", "/").strip("/").split("/")
    share_name = parts[-1] if parts else ""

    # Case-insensitive lookup. Also accept IPC$ (required by CIFS clients).
    is_ipc = share_name.upper() == "IPC$"

    share_info = None
    if not is_ipc:
        for name, info in shares.items():
            if name.upper() == share_name.upper():
                share_info = info
                share_name = name
                break

        if share_info is None:
            return build_error_response(req.header, STATUS_BAD_NETWORK_NAME), None

    tree_id = next_tree_id
    tree_map[tree_id] = "IPC$" if is_ipc else share_name

    # TREE_CONNECT response:
    # StructureSize(16), ShareType(1), Reserved(1), ShareFlags(4),
    # Capabilities(4), MaximalAccess(4)
    share_type = SMB2_SHARE_TYPE_PIPE if is_ipc else SMB2_SHARE_TYPE_DISK
    body = struct.pack(
        "<H"  # StructureSize (16)
        "B"  # ShareType
        "B"  # Reserved
        "I"  # ShareFlags
        "I"  # Capabilities
        "I",  # MaximalAccess
        16,
        share_type,
        0,  # Reserved
        0,  # ShareFlags (no caching, no DFS)
        0,  # Capabilities
        0x001F01FF,  # MaximalAccess: FILE_ALL_ACCESS
    )

    header = build_response_header(req.header, STATUS_SUCCESS, len(body), tree_id=tree_id)
    return header + body, tree_id


def handle_tree_disconnect(req: SMBRequest, tree_map: dict[int, str]) -> bytes:
    """Handle TREE_DISCONNECT. Returns response bytes."""
    tree_map.pop(req.header.tree_id, None)

    # TREE_DISCONNECT response: StructureSize(4), Reserved(2)
    body = struct.pack("<HH", 4, 0)
    header = build_response_header(req.header, STATUS_SUCCESS, len(body))
    return header + body
