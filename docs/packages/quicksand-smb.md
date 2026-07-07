# Quicksand SMB

Pure-Python SMB3 server for [quicksand](https://github.com/microsoft/quicksand) host-guest directory mounts. Zero dependencies.

Supports two transports. On macOS and Linux it runs in inetd mode (stdin/stdout) — QEMU spawns a new process per guest connection via `guestfwd`, and no TCP port is opened on the host. On Windows, `quicksand-core` serves it in-process via `serve_socket` on a loopback-only TCP listener, so no Administrator rights are required.

## Usage

Not intended to be used directly. `quicksand-core` invokes it as a subprocess:

```
python -m quicksand_smb --config /path/to/shares.json
```

The share orchestration (add/remove shares, lifecycle) is managed by `quicksand-core`'s `SMBServer` ABC in `quicksand_core/host/smb.py`.

## License

MIT
