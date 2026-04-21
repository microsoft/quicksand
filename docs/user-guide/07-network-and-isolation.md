# Network and Isolation

*See [Under the Hood: Network and Isolation](../under-the-hood/07-network-and-isolation.md) for how these modes translate to QEMU's SLIRP networking and `restrict=on`.*

All examples below assume:
```python
from quicksand import Sandbox, NetworkMode, PortForward
```

## Default: isolated

By default, the sandbox has no internet access:

```python
async with Sandbox(image="ubuntu") as sb:  # network_mode=MOUNTS_ONLY by default
    result = await sb.execute("curl https://example.com")
    # Fails — no internet access
```

The VM can still share files with the host (via mounts) and communicate with the guest agent. Only outbound internet connections are blocked.

## Enabling internet

```python
async with Sandbox(image="ubuntu", network_mode=NetworkMode.FULL) as sb:
    await sb.execute("apt update && apt install -y python3")
    await sb.execute("pip install requests")
    await sb.execute("curl https://api.example.com")
```

## Network modes

| Mode | Internet | File mounts (CIFS) | File mounts (9p) |
|------|----------|-------------------|------------------|
| `MOUNTS_ONLY` (default) | No | Yes | Yes |
| `FULL` | Yes | Yes | Yes |
| `NONE` | No | No | Yes (9p only) |

## Port forwarding

Expose a service running inside the VM to the host:

```python
async with Sandbox(
    image="ubuntu",
    network_mode=NetworkMode.FULL,
    port_forwards=[PortForward(host=8080, guest=80)],
) as sb:
    await sb.execute("apt install -y nginx && nginx")
    # Now accessible at http://localhost:8080 on the host
```

Multiple port forwards are supported:

```python
port_forwards=[
    PortForward(host=8080, guest=80),    # HTTP
    PortForward(host=8443, guest=443),   # HTTPS
    PortForward(host=5432, guest=5432),  # PostgreSQL
]
```

## Security boundary

The VM provides strong isolation:

- **Separate kernel.** The guest runs its own Linux kernel. A crash or exploit inside the VM doesn't affect the host.
- **No shared filesystem** by default. The guest can only see files you explicitly mount.
- **No network** by default. The guest can't reach the internet or the host network unless you opt in.
- **No root on host.** The entire VM runs as a normal user process. No Docker daemon, no admin privileges.

The agent can execute arbitrary code inside the VM, including installing packages, modifying system files, and running as root, all without any risk to the host. The point is to give the agent a computer it can't break out of.

## Common patterns

**Install dependencies then go offline:**
```python
# Phase 1: online
async with Sandbox(image="ubuntu", network_mode=NetworkMode.FULL, save="with-deps") as sb:
    await sb.execute("apt update && apt install -y python3 python3-pip")
    await sb.execute("pip install -r requirements.txt")

# Phase 2: offline (more secure — MOUNTS_ONLY by default)
async with Sandbox(image="with-deps") as sb:
    handle = await sb.mount("/host/code", "/mnt/code")
    await sb.execute("cd /mnt/code && python3 main.py")
```

**Run a web server and test it:**
```python
async with Sandbox(
    image="ubuntu",
    network_mode=NetworkMode.FULL,
    port_forwards=[PortForward(host=3000, guest=3000)],
) as sb:
    await sb.execute("cd /app && node server.js &")
    # Test from host: requests.get("http://localhost:3000")
```
