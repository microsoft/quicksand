"""NEGOTIATE and SESSION_SETUP command handlers.

NEGOTIATE establishes the SMB3 dialect and server capabilities.
SESSION_SETUP handles NTLMSSP authentication (accepts any credentials).

The Linux CIFS client always performs NTLMSSP exchange even with sec=none:
  1. Client sends NTLMSSP_NEGOTIATE → server replies NTLMSSP_CHALLENGE
  2. Client sends NTLMSSP_AUTH     → server replies SUCCESS

Reference: [MS-SMB2] Sections 2.2.3-2.2.6, [MS-NLMP].
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

from ._protocol import SMBRequest, build_response_header
from ._status import STATUS_MORE_PROCESSING_REQUIRED, STATUS_SUCCESS


@dataclass
class NegotiateState:
    """Stored negotiate parameters for FSCTL_VALIDATE_NEGOTIATE_INFO replay."""

    server_guid: bytes  # 16 bytes
    dialect: int
    security_mode: int
    capabilities: int
    max_transact_size: int
    max_read_size: int
    max_write_size: int


# Dialect values
SMB_300 = 0x0300
SMB_302 = 0x0302
SMB_311 = 0x0311
SMB_210 = 0x0210
SMB_202 = 0x0202

# Preferred dialects in order
SUPPORTED_DIALECTS = (SMB_311, SMB_302, SMB_300, SMB_210, SMB_202)

# Capabilities: LARGE_MTU enables multi-credit requests (required for SMB 3.0)
SERVER_CAPABILITIES = 0x00000004  # SMB2_GLOBAL_CAP_LARGE_MTU

# SecurityMode: signing enabled (MUST be set per MS-SMB2 3.3.5.4)
SERVER_SECURITY_MODE = 0x0001  # SMB2_NEGOTIATE_SIGNING_ENABLED

# Transfer sizes
MAX_TRANSACT_SIZE = 1 * 1024 * 1024  # 1 MB
MAX_READ_SIZE = 1 * 1024 * 1024
MAX_WRITE_SIZE = 1 * 1024 * 1024

# NTLMSSP constants
NTLMSSP_SIGNATURE = b"NTLMSSP\x00"
NTLMSSP_NEGOTIATE = 1
NTLMSSP_CHALLENGE = 2
NTLMSSP_AUTH = 3

# SPNEGO OIDs (for wrapping NTLMSSP tokens)
# These are the minimal ASN.1/DER bytes for SPNEGO negotiation
_SPNEGO_OID = b"\x06\x06\x2b\x06\x01\x05\x05\x02"  # 1.3.6.1.5.5.2
_NTLMSSP_OID = b"\x06\x0a\x2b\x06\x01\x04\x01\x82\x37\x02\x02\x0a"  # 1.3.6.1.4.1.311.2.2.10


def handle_negotiate(req: SMBRequest) -> tuple[bytes, NegotiateState]:
    """Handle SMB2 NEGOTIATE request. Returns (response_bytes, state)."""
    payload = req.payload

    # Parse NEGOTIATE request
    _struct_size, dialect_count, _sec_mode, _reserved, _caps = struct.unpack_from("<HHHHI", payload)

    dialects_offset = 36  # relative to payload start
    dialects = []
    for i in range(dialect_count):
        d = struct.unpack_from("<H", payload, dialects_offset + i * 2)[0]
        dialects.append(d)

    # Select best supported dialect
    selected = None
    for d in SUPPORTED_DIALECTS:
        if d in dialects:
            selected = d
            break
    if selected is None:
        selected = dialects[0] if dialects else SMB_202

    server_guid = os.urandom(16)

    state = NegotiateState(
        server_guid=server_guid,
        dialect=selected,
        security_mode=SERVER_SECURITY_MODE,
        capabilities=SERVER_CAPABILITIES,
        max_transact_size=MAX_TRANSACT_SIZE,
        max_read_size=MAX_READ_SIZE,
        max_write_size=MAX_WRITE_SIZE,
    )

    # Build SPNEGO negTokenInit with NTLMSSP OID for the security buffer.
    # This tells the client we support NTLMSSP authentication.
    security_buffer = _build_spnego_neg_token_init()
    security_offset = 128  # 64 (header) + 64 (negotiate response body)

    body = struct.pack(
        "<H"  # StructureSize (65)
        "H"  # SecurityMode
        "H"  # DialectRevision
        "H"  # NegotiateContextCount (0 for non-3.1.1)
        "16s"  # ServerGuid
        "I"  # Capabilities
        "I"  # MaxTransactSize
        "I"  # MaxReadSize
        "I"  # MaxWriteSize
        "Q"  # SystemTime (FILETIME)
        "Q"  # ServerStartTime (FILETIME)
        "H"  # SecurityBufferOffset
        "H"  # SecurityBufferLength
        "I",  # NegotiateContextOffset (0)
        65,  # StructureSize
        SERVER_SECURITY_MODE,
        selected,
        0,  # NegotiateContextCount
        server_guid,
        SERVER_CAPABILITIES,
        MAX_TRANSACT_SIZE,
        MAX_READ_SIZE,
        MAX_WRITE_SIZE,
        _current_filetime(),
        0,  # ServerStartTime
        security_offset,
        len(security_buffer),
        0,  # NegotiateContextOffset
    )

    header = build_response_header(req.header, STATUS_SUCCESS, len(body) + len(security_buffer))
    return header + body + security_buffer, state


def handle_session_setup(req: SMBRequest, session_id: int) -> bytes:
    """Handle SESSION_SETUP request with NTLMSSP exchange.

    The Linux CIFS client always sends NTLMSSP tokens even with sec=none.
    Round 1: NTLMSSP_NEGOTIATE → reply with NTLMSSP_CHALLENGE + MORE_PROCESSING_REQUIRED
    Round 2: NTLMSSP_AUTH → reply with SUCCESS
    """
    payload = req.payload

    # Parse the security buffer from the request
    # SESSION_SETUP request body:
    # StructureSize(25)(2), Flags(1), SecurityMode(1), Capabilities(4),
    # Channel(4), SecurityBufferOffset(2), SecurityBufferLength(2),
    # PreviousSessionId(8)
    if len(payload) >= 24:
        sec_offset = struct.unpack_from("<H", payload, 12)[0]
        sec_length = struct.unpack_from("<H", payload, 14)[0]
        sec_start = sec_offset - 64  # offset from header start
        if sec_start >= 0 and sec_length > 0 and sec_start + sec_length <= len(payload):
            sec_blob = payload[sec_start : sec_start + sec_length]
        else:
            sec_blob = b""
    else:
        sec_blob = b""

    # Extract NTLMSSP token from SPNEGO wrapper (or raw NTLMSSP)
    ntlmssp_token = _extract_ntlmssp(sec_blob)

    if ntlmssp_token and len(ntlmssp_token) >= 12:
        msg_type = struct.unpack_from("<I", ntlmssp_token, 8)[0]
    else:
        msg_type = 0

    if msg_type == NTLMSSP_NEGOTIATE:
        # Round 1: Send back raw NTLMSSP_CHALLENGE (no SPNEGO wrapper).
        # The Linux kernel SMB2 client uses RawNTLMSSP — it sends and expects
        # bare NTLMSSP tokens in SESSION_SETUP, not SPNEGO-wrapped ones.
        # SPNEGO is only used in the NEGOTIATE response.
        security_buffer = _build_ntlmssp_challenge()
        status = STATUS_MORE_PROCESSING_REQUIRED
    else:
        # Round 2 (NTLMSSP_AUTH) or unknown: Accept unconditionally
        security_buffer = b""
        status = STATUS_SUCCESS

    security_offset = 72  # 64 (header) + 8 (fixed session setup response body)

    body = struct.pack(
        "<H"  # StructureSize (9)
        "H"  # SessionFlags
        "H"  # SecurityBufferOffset
        "H",  # SecurityBufferLength
        9,
        0x0001 if status == STATUS_SUCCESS else 0,  # IS_GUEST on final success
        security_offset,
        len(security_buffer),
    )

    header = build_response_header(
        req.header,
        status,
        len(body) + len(security_buffer),
        session_id=session_id,
    )
    return header + body + security_buffer


# ---------------------------------------------------------------------------
# NTLMSSP helpers
# ---------------------------------------------------------------------------


def _build_ntlmssp_challenge() -> bytes:
    """Build an NTLMSSP_CHALLENGE message.

    The Linux kernel CIFS client validates the challenge strictly:
    - NegotiateFlags must include EXTENDED_SESSIONSECURITY
    - TargetInfo must be present (with at least MsvAvEOL terminator)
    - All offsets must point within the message

    Structure (48 bytes fixed + variable):
      Signature(8), MessageType(4),
      TargetNameLen(2), TargetNameMaxLen(2), TargetNameOffset(4),
      NegotiateFlags(4), ServerChallenge(8), Reserved(8),
      TargetInfoLen(2), TargetInfoMaxLen(2), TargetInfoOffset(4)
      [TargetName bytes] [TargetInfo bytes]
    """
    server_challenge = os.urandom(8)

    flags = (
        0x00000001  # NEGOTIATE_UNICODE
        | 0x00000002  # NEGOTIATE_OEM
        | 0x00000004  # REQUEST_TARGET
        | 0x00000010  # NEGOTIATE_SIGN
        | 0x00000020  # NEGOTIATE_SEAL
        | 0x00000200  # NEGOTIATE_NTLM
        | 0x00008000  # NEGOTIATE_ALWAYS_SIGN
        | 0x00080000  # NEGOTIATE_EXTENDED_SESSIONSECURITY
        | 0x00800000  # NEGOTIATE_TARGET_INFO
        | 0x02000000  # NEGOTIATE_128
        | 0x20000000  # NEGOTIATE_KEY_EXCH
    )

    # Target name: "QUICKSAND" in UTF-16LE
    target_name = "QUICKSAND".encode("utf-16-le")

    # TargetInfo: MsvAvNbDomainName + MsvAvEOL terminator
    # MsvAvNbDomainName (AvId=2): domain name "QUICKSAND" in UTF-16LE
    domain = "QUICKSAND".encode("utf-16-le")
    target_info = struct.pack("<HH", 2, len(domain)) + domain
    # MsvAvEOL terminator (AvId=0, AvLen=0)
    target_info += struct.pack("<HH", 0, 0)

    # Fixed header is 48 bytes; variable data follows
    fixed_size = 48
    target_name_offset = fixed_size
    target_info_offset = fixed_size + len(target_name)

    msg = struct.pack(
        "<8sI"  # Signature + MessageType
        "HHI"  # TargetNameLen, TargetNameMaxLen, TargetNameOffset
        "I"  # NegotiateFlags
        "8s"  # ServerChallenge
        "8s"  # Reserved
        "HHI",  # TargetInfoLen, TargetInfoMaxLen, TargetInfoOffset
        NTLMSSP_SIGNATURE,
        NTLMSSP_CHALLENGE,
        len(target_name),
        len(target_name),
        target_name_offset,
        flags,
        server_challenge,
        b"\x00" * 8,
        len(target_info),
        len(target_info),
        target_info_offset,
    )
    return msg + target_name + target_info


def _extract_ntlmssp(blob: bytes) -> bytes | None:
    """Extract the NTLMSSP token from a SPNEGO wrapper or raw NTLMSSP blob."""
    if not blob:
        return None

    # Raw NTLMSSP (starts with "NTLMSSP\0")
    if blob[:8] == NTLMSSP_SIGNATURE:
        return blob

    # SPNEGO wrapped — search for NTLMSSP signature within the blob
    idx = blob.find(NTLMSSP_SIGNATURE)
    if idx >= 0:
        return blob[idx:]

    return None


def _build_spnego_neg_token_init() -> bytes:
    """Build a minimal SPNEGO negTokenInit offering NTLMSSP.

    Structure per RFC 4178:
      APPLICATION[0] IMPLICIT SEQUENCE {
        OID(1.3.6.1.5.5.2),        -- SPNEGO
        [0] NegTokenInit SEQUENCE {
          [0] MechTypeList SEQUENCE {
            OID(NTLMSSP)
          }
        }
      }
    """
    # MechTypeList: SEQUENCE { OID(NTLMSSP) }
    mech_types = b"\x30" + bytes([len(_NTLMSSP_OID)]) + _NTLMSSP_OID
    # mechTypes [0] MechTypeList
    mech_type_ctx = b"\xa0" + bytes([len(mech_types)]) + mech_types
    # NegTokenInit SEQUENCE { mechTypes }
    neg_token_init_seq = b"\x30" + _der_length(len(mech_type_ctx)) + mech_type_ctx
    # [0] EXPLICIT NegTokenInit
    neg_token_init = b"\xa0" + _der_length(len(neg_token_init_seq)) + neg_token_init_seq
    # APPLICATION[0] is IMPLICIT SEQUENCE — no extra 0x30 wrapper
    inner = _SPNEGO_OID + neg_token_init
    return b"\x60" + _der_length(len(inner)) + inner


def _build_spnego_neg_token_resp(ntlmssp_token: bytes) -> bytes:
    """Build a SPNEGO negTokenResp wrapping an NTLMSSP token.

    Structure per RFC 4178:
      [1] NegTokenResp SEQUENCE {
        [0] negState ENUMERATED { accept-incomplete(1) },
        [1] supportedMech OID (NTLMSSP),
        [2] responseToken OCTET STRING (NTLMSSP_CHALLENGE)
      }
    """
    # negState [0] ENUMERATED { accept-incomplete(1) }
    neg_state = b"\xa0\x03\x0a\x01\x01"
    # supportedMech [1] OID (NTLMSSP)
    supported_mech = b"\xa1" + bytes([len(_NTLMSSP_OID) + 0]) + _NTLMSSP_OID
    # responseToken [2] OCTET STRING
    octet_string = b"\x04" + _der_length(len(ntlmssp_token)) + ntlmssp_token
    response_token = b"\xa2" + _der_length(len(octet_string)) + octet_string
    # NegTokenResp SEQUENCE
    seq_content = neg_state + supported_mech + response_token
    seq = b"\x30" + _der_length(len(seq_content)) + seq_content
    # context [1]
    return b"\xa1" + _der_length(len(seq)) + seq


def _der_length(length: int) -> bytes:
    """Encode an ASN.1 DER length."""
    if length < 0x80:
        return bytes([length])
    elif length < 0x100:
        return bytes([0x81, length])
    else:
        return bytes([0x82, (length >> 8) & 0xFF, length & 0xFF])


def _current_filetime() -> int:
    """Return current time as Windows FILETIME (100ns since 1601-01-01)."""
    import time

    return int((time.time() + 11644473600) * 10_000_000)
