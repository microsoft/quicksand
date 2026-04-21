"""IOCTL command handler.

The critical IOCTL is FSCTL_VALIDATE_NEGOTIATE_INFO — the SMB3 client sends
this to verify the negotiate wasn't tampered with. We replay stored negotiate
parameters. All other IOCTLs return STATUS_NOT_SUPPORTED.

Reference: [MS-SMB2] Section 2.2.31-2.2.32 and [MS-FSA].
"""

from __future__ import annotations

import struct

from ._negotiate import NegotiateState
from ._protocol import SMBRequest, build_error_response, build_response_header
from ._status import STATUS_NOT_SUPPORTED, STATUS_SUCCESS

# IOCTL codes
FSCTL_VALIDATE_NEGOTIATE_INFO = 0x00140204
FSCTL_PIPE_WAIT = 0x00110018
FSCTL_PIPE_TRANSCEIVE = 0x0011C017
FSCTL_DFS_GET_REFERRALS = 0x00060194
FSCTL_SRV_ENUMERATE_SNAPSHOTS = 0x00144064
FSCTL_SRV_REQUEST_RESUME_KEY = 0x00140078
FSCTL_SRV_COPYCHUNK = 0x001440F2
FSCTL_QUERY_NETWORK_INTERFACE_INFO = 0x001401FC


def handle_ioctl(req: SMBRequest, negotiate_state: NegotiateState | None) -> bytes:
    """Handle IOCTL request."""
    payload = req.payload

    # IOCTL request body:
    # StructureSize(57), Reserved/Reserved2(2), CtlCode(4), FileId(16),
    # InputOffset(4), InputCount(4), MaxInputResponse(4),
    # OutputOffset(4), OutputCount(4), MaxOutputResponse(4), Flags(4)
    if len(payload) < 56:
        return build_error_response(req.header, STATUS_NOT_SUPPORTED)

    _struct_size, _reserved, ctl_code = struct.unpack_from("<HHI", payload)
    # FileId at offset 8 (16 bytes), InputOffset at 24, InputCount at 28
    input_offset, input_count = struct.unpack_from("<II", payload, 24)
    _max_input, _output_offset, _output_count, _max_output = struct.unpack_from(
        "<IIII", payload, 32
    )

    if ctl_code == FSCTL_VALIDATE_NEGOTIATE_INFO:
        return _handle_validate_negotiate(req, payload, input_offset, input_count, negotiate_state)

    return build_error_response(req.header, STATUS_NOT_SUPPORTED)


def _handle_validate_negotiate(
    req: SMBRequest,
    payload: bytes,
    input_offset: int,
    input_count: int,
    state: NegotiateState | None,
) -> bytes:
    """Handle FSCTL_VALIDATE_NEGOTIATE_INFO.

    Client sends: Capabilities(4), Guid(16), SecurityMode(2), DialectCount(2), Dialects(2*N)
    Server responds: Capabilities(4), Guid(16), SecurityMode(2), Dialect(2)
    """
    if state is None:
        return build_error_response(req.header, STATUS_NOT_SUPPORTED)

    # Build the validate negotiate info response
    output_data = struct.pack(
        "<I16sHH",
        state.capabilities,
        state.server_guid,
        state.security_mode,
        state.dialect,
    )

    # IOCTL response body:
    # StructureSize(49), Reserved/Reserved2(2), CtlCode(4), FileId(16),
    # InputOffset(4), InputCount(4), OutputOffset(4), OutputCount(4), Flags(4)
    file_id = payload[8:24]  # echo back the FileId from request
    output_offset = 64 + 48  # header(64) + fixed ioctl response body(48)

    body = struct.pack(
        "<HH"  # StructureSize(49), Reserved
        "I"  # CtlCode
        "16s"  # FileId
        "I"  # InputOffset
        "I"  # InputCount
        "I"  # OutputOffset
        "I"  # OutputCount
        "I"  # Flags
        "I",  # Reserved2
        49,
        0,
        FSCTL_VALIDATE_NEGOTIATE_INFO,
        file_id,
        0,  # InputOffset (no input in response)
        0,  # InputCount
        output_offset,
        len(output_data),
        0,  # Flags
        0,  # Reserved2
    )

    header = build_response_header(req.header, STATUS_SUCCESS, len(body) + len(output_data))
    return header + body + output_data
