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

`restrict=on` blocks all outbound connections from the guest to the real network. But it does **not** block `hostfwd` or `guestfwd`. Those are internal QEMU tunnels, not real network connections. This is how file mounts work even in `MOUNTS_ONLY` mode. The guestfwd tunnel to the SMB server is unaffected by `restrict=on` (on Windows the tunnel relays to the in-process loopback TCP listener rather than a stdin/stdout child).

## Port forwarding

```python
Sandbox(
    image="ubuntu",
    network_mode=NetworkMode.FULL,
    port_forwards=[Forward(Host(8080), Guest(80)), Forward(Host("0.0.0.0", 8443), Guest(443))],
)
```

```bash
-netdev user,id=net0,hostfwd=tcp:127.0.0.1:{agent_port}-:{agent_port},hostfwd=tcp:127.0.0.1:8080-:80,hostfwd=tcp:0.0.0.0:8443-:443,...
```

Each `Forward(Host, Guest)` becomes a `hostfwd` rule of the form `hostfwd=tcp:{host.address}:{host.port}-{guest.address}:{guest.port}`. The host address is the interface QEMU listens on and defaults to `127.0.0.1`. The guest address is left empty by default, which QEMU resolves to the guest's own IP (`10.0.2.15`). The agent port's `hostfwd` is always present (it's how `execute()` reaches the guest) and uses a dynamically allocated port.

## Guest forwarding

```python
Sandbox(
    image="ubuntu",
    port_forwards=[Forward(Guest(3128), Host(8080))],
)
```

```bash
-netdev user,id=net0,restrict=on,...,guestfwd=tcp:10.0.2.101:3128-cmd:/path/to/python /path/to/_tcp_relay.py 127.0.0.1 8080
```

Each `Forward(Guest, Host)` becomes a `guestfwd` rule on the same virtual network the SMB mount tunnel uses. When the guest connects to `10.0.2.101:3128`, QEMU spawns one `_tcp_relay.py` process for that connection and pipes the guest's TCP stream through the relay's stdin/stdout to a fresh host-side connection to `127.0.0.1:8080`. When either side closes, the relay exits. This is the same `cmd:` relay that carries CIFS traffic to the in-process SMB server on Windows. Because the relay does an ordinary `connect()`, the host endpoint may be a hostname or an address on another machine.

QEMU also accepts a chardev target (`guestfwd=tcp:10.0.2.101:3128-tcp:127.0.0.1:8080`), which avoids the per-connection process. Quicksand does not use it because a chardev is a single stream: every guest connection to that address is multiplexed onto one host connection. In testing, eight concurrent requests through a chardev target produced a duplicated response and a dropped one, and the stream was left unusable afterwards. The per-connection relay costs one short-lived Python process per guest connection and in exchange gives ordinary TCP semantics.

`restrict=on` does not affect `guestfwd`, so guest-to-host forwards work in `MOUNTS_ONLY` mode. The guest address is validated to sit inside `10.0.2.0/24` and away from the addresses slirp reserves (`10.0.2.2`, `10.0.2.3`, `10.0.2.15`) and the SMB tunnel (`10.0.2.100:445`).

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
