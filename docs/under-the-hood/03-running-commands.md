# Running Commands: Under the Hood

Companion to [Running Commands](../user-guide/03-running-commands.md).

## Direct kernel boot

Instead of booting through BIOS/UEFI firmware (slow), Quicksand passes the kernel and initrd directly to QEMU:

```bash
qemu-system-aarch64 \
  -kernel /path/to/ubuntu-24.04-arm64.kernel \
  -initrd /path/to/ubuntu-24.04-arm64.initrd \
  -append "root=/dev/vda rw rootflags=rw console=ttyAMA0 rootfstype=ext4 quiet loglevel=0 raid=noautodetect quicksand_token=abc123 quicksand_port=8080"
```

This skips the entire firmware boot sequence and starts the Linux kernel immediately, saving several seconds.

## Agent token injection

The guest agent is a minimal Rust HTTP server baked into the initrd. It reads its auth token and port from the kernel command line (`/proc/cmdline`):

```
quicksand_token=abc123 quicksand_port=8080
```

These are injected via `-append` and are unique per sandbox instance. The token prevents other processes on the host from sending commands to the VM.

## Serial console

```bash
-serial stdio
```

The VM's serial port is connected to QEMU's stdin/stdout. Combined with `console=ttyAMA0` (ARM64) or `console=ttyS0` (x86_64) in the kernel command line, this lets Quicksand read kernel boot messages and detect when the guest agent is ready.

## Host-to-guest routing

```python
result = await sb.execute("ls /")
```

The `execute()` call sends an HTTP POST to the guest agent. But the guest is behind QEMU's NAT. It doesn't have a real IP on the host network. The connection is routed via a port forward in the `-netdev` flags:

```bash
-netdev user,id=net0,...,hostfwd=tcp:127.0.0.1:8080-:8080
```

This means `localhost:8080` on the host is forwarded to port `8080` inside the guest, where the agent is listening. The flow:

```
sb.execute("ls /")
  → HTTP POST http://127.0.0.1:8080/execute {"command": "ls /"}
    → QEMU hostfwd routes to guest:8080
      → Guest agent runs the command
      → Returns {"stdout": "bin\nboot\n...", "exit_code": 0}
```

## Streaming

```python
result = await sb.execute("apt install -y python3", on_stdout=callback)
```

Streaming uses the `/execute_stream` endpoint instead of `/execute`. The guest agent responds with Server-Sent Events (SSE). Each event contains one output chunk.

```
data: {"type": "stdout", "data": "Reading package lists..."}
data: {"type": "stdout", "data": "Building dependency tree..."}
data: {"type": "exit", "exit_code": 0}
```

The HTTP connection stays open until the command finishes. Callbacks fire for each chunk as it arrives.
