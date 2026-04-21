# Quicksand SMB

Pure-Python SMB3 server for [quicksand](https://github.com/microsoft/quicksand) host-guest directory mounts. Zero dependencies.

Runs in inetd mode (stdin/stdout). QEMU spawns a new process per guest connection via `guestfwd`. No TCP port is opened on the host.

## Usage

Not intended to be used directly. `quicksand-core` invokes it as a subprocess:

```
python -m quicksand_smb --config /path/to/shares.json
```

The share orchestration (add/remove shares, lifecycle) is managed by `quicksand-core`'s `SMBServer` ABC in `quicksand_core/host/smb.py`.

## License

MIT
