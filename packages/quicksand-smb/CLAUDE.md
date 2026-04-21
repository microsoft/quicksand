# quicksand-smb

Pure-Python SMB3 server for quicksand host-guest directory mounting.

## Overview

An inetd-style SMB3 file server that reads/writes on stdin/stdout. QEMU's `guestfwd=cmd:` spawns one instance per guest TCP connection. No TCP port is opened on the host, eliminating the attack surface that Samba's smbd had.

## Acceptance Criteria

- [x] `mount -t cifs //10.0.2.100/SHARE /mnt -o sec=none,vers=3.0` succeeds from Ubuntu guest
- [x] `ls`, `cat`, `stat` work on mounted files (read path)
- [x] `echo "data" > file`, `mkdir`, `rm`, `mv` work (write path)
- [x] No TCP ports opened on host during operation
- [x] Path traversal attacks blocked (symlink escapes, `../` escapes)
- [x] Dynamic mounts work (add shares after VM boot via config file reload)
- [x] Read-only shares enforce read-only (writes fail with permission denied)
- [x] Multiple concurrent shares work
- [x] Binary files transfer correctly (no newline translation)
- [ ] Large file I/O works (files > 1MB) — tested at protocol level, not yet in VM
- [ ] Alpine guest mount validation

## Key Protocol Details

### NTLMSSP Authentication (sec=none still requires it)

The Linux kernel CIFS client **always** performs a full NTLMSSP exchange, even with `sec=none`:

1. **NEGOTIATE**: Server sends SPNEGO `negTokenInit` offering NTLMSSP OID
2. **SESSION_SETUP round 1**: Client sends raw NTLMSSP_NEGOTIATE → Server sends **raw NTLMSSP_CHALLENGE** (NOT SPNEGO-wrapped)
3. **SESSION_SETUP round 2**: Client sends raw NTLMSSP_AUTH → Server returns STATUS_SUCCESS

**Critical**: The SMB2/3 kernel code uses `RawNTLMSSP` — it does NOT unwrap SPNEGO from SESSION_SETUP responses. SPNEGO is only used in the NEGOTIATE response. Wrapping the NTLMSSP_CHALLENGE in SPNEGO causes `error(22): Invalid argument`.

### SecurityMode MUST be non-zero

Per MS-SMB2 Section 3.3.5.4, the NEGOTIATE response **MUST** have `SMB2_NEGOTIATE_SIGNING_ENABLED (0x0001)` set. Setting SecurityMode=0 causes the client to silently disconnect after NEGOTIATE.

### IPC$ Share Required

The CIFS client always connects to `IPC$` before the real share (for IOCTL validation). The server must accept `IPC$` as a valid share with `ShareType=PIPE (0x02)`.

### Compound Related Operations

The CIFS client sends compound requests (CREATE+QUERY_INFO+CLOSE) with `SMB2_FLAGS_RELATED_OPERATIONS (0x04)`. In related operations, the FileId in QUERY_INFO/CLOSE is `0xFFFFFFFF...` meaning "use the FileId from the previous CREATE response". The server must track the last FileId and substitute it.

### FILE_ALL_INFORMATION Struct Alignment

The composite `FileAllInformation` response requires exact field sizes:
- `FileBasicInformation`: **40 bytes** (4 timestamps + attributes + 4-byte reserved)
- `FileStandardInformation`: **24 bytes** (2 sizes + nlinks + 2 bools + 2-byte reserved)

Missing the reserved padding fields causes `get root inode failed`.

### FSCTL_VALIDATE_NEGOTIATE_INFO

The IOCTL response body is **48 bytes** (not 44 — includes a Reserved2 field). The OutputOffset must account for the full 48-byte body. Off-by-4 causes `buffer length N smaller than minimum size 28`.

### QUERY_DIRECTORY Info Classes and Reserved Fields

The Linux kernel CIFS client uses `FILE_ID_FULL_DIRECTORY_INFORMATION` (info class 38) for readdir — NOT class 3 or 37 as you might expect. Every directory info struct has reserved/padding fields that must be included or the FileName offset shifts and filenames appear truncated.

Critical struct sizes (fixed header before FileName):
- `FILE_DIRECTORY_INFORMATION` (1): **64 bytes**
- `FILE_BOTH_DIRECTORY_INFORMATION` (3): **94 bytes** (includes ShortNameLength(1) + Reserved(1) + ShortName(24))
- `FILE_ID_BOTH_DIRECTORY_INFORMATION` (37): **104 bytes** (adds Reserved2(2) + FileId(8))
- `FILE_ID_FULL_DIRECTORY_INFORMATION` (38): **80 bytes** (includes EaSize(4) + **Reserved(4)** + FileId(8))

The Reserved(4) field in class 38 between EaSize and FileId is easy to miss — omitting it shifts FileName by 4 bytes (2 UTF-16LE chars), causing filenames like `README.md` to appear as `ADME.md`.

### Dynamic Mount Config Reload

The CIFS client reuses existing TCP connections for new mounts. The server process reads the config file once at startup, so `reload_config()` must re-read the config file on each TREE_CONNECT to pick up dynamically added shares.

## Mandatory Verification Process

When adding or modifying any SMB struct (directory info, file info, IOCTL, etc.):

1. **Field-by-field spec check**: Open the MS-FSCC or MS-SMB2 spec section for the struct. List every field and its size. Compare against the `struct.pack` format string. Count total bytes with `struct.calcsize()`. Reserved/padding fields with value 0 are still required on the wire.
2. **Integration test**: Every struct that reaches the guest kernel MUST be validated by an integration test running in the real sandbox — not just a unit test. Unit test parsers can silently share the same offset bugs as the server (this has happened: see FILE_ID_FULL_DIRECTORY_INFORMATION below).
3. **Verify the actual info class**: Add debug logging (`logger.debug`) to confirm which info class/level the kernel actually requests. Do not assume — the kernel may use a different class than expected (e.g., it uses class 38, not 37, for readdir).

This project has been bitten THREE times by missing Reserved/padding fields in SMB structs. Every zero-valued field in the spec exists for a reason — the client-side struct layout depends on it.

## Gotchas

### Compound Requests
The Linux CIFS client batches requests (e.g., CREATE+QUERY_INFO+CLOSE). The `NextCommand` field in the SMB2 header links them. Must split and process sequentially, then chain responses with `NextCommand` offsets.

### FSCTL_QUERY_NETWORK_INTERFACE_INFO
The CIFS client sends this IOCTL on the IPC$ tree. Return `STATUS_NOT_SUPPORTED` — the client gracefully falls back to single-channel.

### UTF-16LE Everywhere
All SMB3 paths and filenames are UTF-16LE encoded. Length fields are in bytes, not characters. Paths use backslash separators. No null terminators in wire format.

### Windows FILETIME
Timestamps are 100-nanosecond intervals since January 1, 1601. Convert from Unix: `filetime = int((unix_time + 11644473600) * 10_000_000)`.

### Binary stdin/stdout
QEMU's guestfwd passes raw TCP bytes via stdin/stdout. Must use `os.read()`/`os.write()` on raw file descriptors — NOT `sys.stdin.readline()` or print(). No buffering, no newline translation.

### Credit System
SMB3 uses credits for flow control. Grant generously (256 per response) to avoid stalls.

## Previous Failed Attempts

### Samba smbd (abandoned)
Used bundled Samba `smbd` binary listening on localhost TCP with anonymous access.
- **Problem 1**: No password auth without root (smbd needs PAM/root for smbpasswd)
- **Problem 2**: Different local users could port-scan, enumerate shares via IPC$/srvsvc
- **Problem 3**: 50MB+ binary dependency per platform
- **Problem 4**: MD4 hash needed for NTLM auth, unavailable on OpenSSL 3.0+

### smbpasswd auth approach (abandoned)
Attempted to write smbpasswd file directly. NT hash requires MD4(UTF-16LE(password)), but MD4 is unavailable in Python 3.13+ / OpenSSL 3.0+. Decided auth was unnecessary with the guestfwd approach.

### SPNEGO-wrapped NTLMSSP_CHALLENGE (failed)
Wrapped the NTLMSSP_CHALLENGE in a SPNEGO negTokenResp for SESSION_SETUP response. The Linux kernel SMB2 code does NOT support SPNEGO in session setup — it expects raw NTLMSSP tokens.

### SecurityMode=0 (failed)
Set SecurityMode=0x0000 in NEGOTIATE response. MS-SMB2 spec requires SIGNING_ENABLED (0x0001) to be set. Client silently disconnects.

### Missing Reserved field in FILE_ID_FULL_DIRECTORY_INFORMATION (fixed 2026-03)
- **Symptom**: All filenames in `ls` were truncated by their first 2 characters (e.g. `README.md` → `ADME.md`)
- **Root cause**: `_build_dir_entry` for info class 38 was missing a 4-byte `Reserved` field between `EaSize` and `FileId` (MS-FSCC 2.3.6). Fixed header was 76 bytes instead of 80, shifting FileName by 4 bytes (2 UTF-16LE chars).
- **How it was missed**: Unit tests parsed entries using our own offset constants, which had the same bug. Only caught by running integration tests against the real sandbox kernel.
- **Lesson**: Always validate directory listing structs with an integration test (`ls` in guest), not just protocol-level parsing. Unit test parsers can share the same wrong offsets as the server.

## Useful Resources

- [MS-SMB2](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-smb2/) — The authoritative protocol specification
- [MS-NLMP](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-nlmp/) — NTLMSSP authentication
- [MS-FSCC](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fscc/) — File system control codes and info classes
- [Linux kernel fs/smb/client/](https://github.com/torvalds/linux/tree/master/fs/smb/client) — The CIFS client source (sess.c, smb2pdu.c)
- [RFC 4178](https://tools.ietf.org/html/rfc4178) — SPNEGO (used in NEGOTIATE response only)

## Changelog

### 0.1.0 (initial)
- Pure-Python SMB3 server with inetd/stdio transport
- NEGOTIATE with SPNEGO negTokenInit, NTLMSSP authentication
- SESSION_SETUP with raw NTLMSSP (NEGOTIATE → CHALLENGE → AUTH)
- TREE_CONNECT/DISCONNECT (including IPC$ support)
- CREATE, CLOSE, READ, WRITE, FLUSH
- QUERY_INFO (file + filesystem), QUERY_DIRECTORY, SET_INFO
- IOCTL (FSCTL_VALIDATE_NEGOTIATE_INFO, FSCTL_QUERY_NETWORK_INTERFACE_INFO stub)
- Compound related operations support
- Dynamic mount config reload on TREE_CONNECT
- Path traversal protection
- No external dependencies
