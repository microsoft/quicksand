# Network and Isolation

*See [Under the Hood: Network and Isolation](../under-the-hood/07-network-and-isolation.md) for how these modes translate to QEMU's SLIRP networking and `restrict=on`.*

All examples below assume:
```python
from quicksand import Sandbox, NetworkMode, Forward, Host, Guest
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

`port_forwards` is a list of `Forward(src, dst)` objects. Each one connects a `Host` endpoint to a `Guest` endpoint, and the order decides the direction. An endpoint is a port with an optional address, written whichever way reads best:

```python
Host(8080)                    # port only, address defaults to 127.0.0.1
Host("0.0.0.0", 8080)         # address and port
Host("0.0.0.0:8080")          # the same, as one string
Host(port=8080, address="0.0.0.0")
```

`Guest` takes the same forms. Its address is optional too and defaults to the right value for its side of the forward.

> `PortForward(host=..., guest=...)` is a deprecated alias for `Forward(Host(...), Guest(...))`. It still works but emits a `DeprecationWarning`.

### Host to guest: reach the guest from the host

Expose a service running inside the VM on a host port:

```python
async with Sandbox(
    image="ubuntu",
    network_mode=NetworkMode.FULL,
    port_forwards=[Forward(Host(8080), Guest(80))],
) as sb:
    await sb.execute("apt install -y nginx && nginx")
    # Now accessible at http://localhost:8080 on the host
```

Multiple forwards are supported:

```python
port_forwards=[
    Forward(Host(8080), Guest(80)),      # HTTP
    Forward(Host(8443), Guest(443)),     # HTTPS
    Forward(Host(5432), Guest(5432)),    # PostgreSQL
]
```

By default the host side binds `127.0.0.1`, so the port is reachable only from the host itself. Set `Host("0.0.0.0", 8080)` to make it reachable from other machines on your network. The guest address almost never needs setting, since the VM has a single IP that QEMU fills in.

### Guest to host: reach the host from the guest

Expose a service running on the host to code inside the VM. This works even in the default `MOUNTS_ONLY` mode, where every other outbound connection is blocked, so it is the way to offer a sandbox a carefully chosen slice of the outside world such as an HTTP proxy or one of your own APIs:

```python
async with Sandbox(
    image="ubuntu",  # MOUNTS_ONLY by default: no internet
    port_forwards=[Forward(Guest(3128), Host(8080))],
) as sb:
    # Inside the VM, 10.0.2.101:3128 is the host's 127.0.0.1:8080
    await sb.execute("curl -x http://10.0.2.101:3128 https://example.com")
```

The guest dials `Guest.address:Guest.port`. The address defaults to `10.0.2.101`, a virtual address on the VM's private network. The host side defaults to `127.0.0.1` and may be any address or hostname the host can reach, which turns the forward into a pinhole from an offline sandbox to exactly one remote service:

```python
port_forwards=[
    Forward(Guest(3128), Host(8080)),                     # proxy on the host
    Forward(Guest(80), Host(5000)),                       # your API
    Forward(Guest("10.0.2.102", 80), Host(5001)),         # a second API on guest port 80
    Forward(Guest(5432), Host("db.internal", 5432)),      # relayed onward to one remote host
]
```

Each guest connection becomes its own connection on the host, so concurrent requests and keep-alive sessions behave normally. When the guest is the source its address must be inside `10.0.2.0/24` and may not be the gateway (`10.0.2.2`), the DNS forwarder (`10.0.2.3`), the guest itself (`10.0.2.15`), or the mount tunnel (`10.0.2.100:445`). Guest-to-host forwards need a network device, so they are rejected with `NetworkMode.NONE`.

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

**Allow internet access only through a proxy you control:**
```python
# Run an HTTP proxy on the host (127.0.0.1:8080) that enforces an allowlist,
# then hand the offline sandbox just that proxy.
async with Sandbox(
    image="ubuntu",
    port_forwards=[Forward(Guest(3128), Host(8080))],
) as sb:
    await sb.execute("export https_proxy=http://10.0.2.101:3128; pip install requests")
```

**Run a web server and test it:**
```python
async with Sandbox(
    image="ubuntu",
    network_mode=NetworkMode.FULL,
    port_forwards=[Forward(Host(3000), Guest(3000))],
) as sb:
    await sb.execute("cd /app && node server.js &")
    # Test from host: requests.get("http://localhost:3000")
```
