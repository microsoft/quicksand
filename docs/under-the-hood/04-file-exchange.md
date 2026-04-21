# File Exchange: Under the Hood

Companion to [File Exchange](../user-guide/04-file-exchange.md).

## CIFS mounts (default)

```python
Sandbox(
    image="ubuntu",
    mounts=[Mount("/host/code", "/mnt/code")],
)
```

CIFS mounts don't add any QEMU flags for the mount itself. Instead, they work through QEMU's `guestfwd` mechanism, which tunnels a TCP connection from the guest to a host process:

```bash
-netdev user,id=net0,...,guestfwd=tcp:10.0.2.100:445-cmd:/path/to/python -m quicksand_smb --inetd
```

`guestfwd` tells QEMU: "when the guest connects to `10.0.2.100:445`, spawn this command and pipe the TCP stream to its stdin/stdout." The command is Quicksand's pure-Python SMB3 server running in inetd mode. It runs one instance per connection with no listening port on the host.

The guest agent then mounts the share:

```bash
mount -t cifs //10.0.2.100/QUICKSAND0 /mnt/code \
  -o username=guest,password=,sec=none,vers=3.0,port=445
```

Each mount gets its own SMB share name (`QUICKSAND0`, `QUICKSAND1`, ...). The SMB server maps each share name to the corresponding host directory.

## Hot-mounts

```python
handle = await sb.mount("/host/project", "/mnt/project")
```

Hot-mounts use the same CIFS mechanism. The SMB server is already running (started at boot via guestfwd), so adding a mount just:

1. Registers a new share name in the running SMB server
2. Sends a `mount -t cifs` command to the guest via the agent

No QEMU restart or new flags needed.

## 9p mounts

```python
Sandbox(
    image="ubuntu",
    network_mode=NetworkMode.NONE,
    mounts=[Mount("/host/data", "/mnt/data", type="9p")],
)
```

9p mounts add QEMU flags at launch time:

```bash
-fsdev local,id=pb_fs_0,path=/host/data,security_model=none \
-device virtio-9p-pci,id=pb_9p_0,fsdev=pb_fs_0,mount_tag=pb9p0
```

| Flag | Purpose |
|---|---|
| `-fsdev local,...` | Expose a host directory to QEMU via the Plan 9 protocol |
| `security_model=none` | No UID/GID mapping — files appear as-is |
| `-device virtio-9p-pci,...` | Attach the filesystem as a virtio device the guest can mount |
| `mount_tag=pb9p0` | Tag used by the guest `mount -t 9p pb9p0 /mnt/data` command |

On MMIO machines such as `microvm` (x86_64+KVM) and `virt` (ARM64), the device type is `virtio-9p-device` instead of `virtio-9p-pci`.

Read-only 9p mounts add `,readonly=on` to the `-fsdev` flags.

## Why CIFS is the default

| | CIFS | 9p |
|---|---|---|
| Hot-mountable at runtime | Yes (share added to running SMB server) | No (requires QEMU flags at launch) |
| Works with `NetworkMode.NONE` | No (needs guestfwd, which needs a netdev) | Yes (uses virtio device, no network) |
| Host dependency | Pure-Python SMB server (bundled) | QEMU built-in |

CIFS is default because hot-mounting is the common case. 9p exists for the `NetworkMode.NONE` scenario where no network stack is configured at all.
