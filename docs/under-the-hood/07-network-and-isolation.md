# Network and Isolation: Under the Hood

Companion to [Network and Isolation](../user-guide/07-network-and-isolation.md).

## Network modes

```python
Sandbox(image="ubuntu")  # MOUNTS_ONLY by default
```

```bash
# NetworkMode.MOUNTS_ONLY (default):
-netdev user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:8080-:8080,guestfwd=tcp:10.0.2.100:445-cmd:... \
-device virtio-net-device,netdev=net0
```

```python
Sandbox(image="ubuntu", network_mode=NetworkMode.FULL)
```

```bash
# NetworkMode.FULL:
-netdev user,id=net0,hostfwd=tcp:127.0.0.1:8080-:8080,guestfwd=tcp:10.0.2.100:445-cmd:... \
-device virtio-net-device,netdev=net0
```

```python
Sandbox(image="ubuntu", network_mode=NetworkMode.NONE)
```

```bash
# NetworkMode.NONE:
-nic none
```

The only difference between `MOUNTS_ONLY` and `FULL` is `restrict=on` vs no restrict.

## How `restrict=on` works

QEMU's SLIRP networking gives the guest a virtual NAT. The guest sees:

- `10.0.2.15` — its own IP
- `10.0.2.2` — the host (NAT gateway)
- `10.0.2.3` — DNS server

`restrict=on` blocks all outbound connections from the guest to the real network. But it does **not** block `hostfwd` or `guestfwd`. Those are internal QEMU tunnels, not real network connections. This is how file mounts work even in `MOUNTS_ONLY` mode. The guestfwd tunnel to the SMB server is unaffected by `restrict=on`.

## Port forwarding

```python
Sandbox(
    image="ubuntu",
    network_mode=NetworkMode.FULL,
    port_forwards=[PortForward(host=8080, guest=80), PortForward(host=8443, guest=443)],
)
```

```bash
-netdev user,id=net0,hostfwd=tcp:127.0.0.1:{agent_port}-:{agent_port},hostfwd=tcp:127.0.0.1:8080-:80,hostfwd=tcp:127.0.0.1:8443-:443,...
```

Each `PortForward(host, guest)` pair becomes a `hostfwd` rule. The agent port's `hostfwd` is always present (it's how `execute()` reaches the guest) and uses a dynamically allocated port.

All forwards bind to `127.0.0.1`. They are only accessible from the host, not from the network.

## Virtio network device

The device type depends on machine type:

| Machine | Device | Bus |
|---|---|---|
| `virt` (ARM64) | `virtio-net-device` | MMIO |
| `q35` (x86_64) | `virtio-net-pci` | PCI |

On x86_64 machines, an additional flag suppresses PXE boot:

```bash
-global virtio-net-pci.romfile=
```

This prevents QEMU from trying to load a PXE boot ROM, which would slow down boot and isn't needed.
