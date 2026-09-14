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

The guest agent is a minimal Rust server bundled in the VM image. It reads its auth token and port from the kernel command line (`/proc/cmdline`):

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

Quicksand normally sends length-prefixed JSON frames over a virtio-serial channel.
Request IDs allow command replies and output events to be routed independently.
This transport does not require guest networking.

The HTTP fallback sends a POST to the guest agent. The guest is behind QEMU's
NAT, so this connection is routed via a port forward in the `-netdev` flags:

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

Streaming uses the `execute_stream` method over virtio-serial, or the
`/execute_stream` endpoint over HTTP. HTTP responses use Server-Sent Events
(SSE); serial output events additionally carry the execution request's `id`.

```
data: {"stream": "stdout", "data": "Reading package lists..."}
data: {"stream": "stdout", "data": "Building dependency tree..."}
data: {"stream": "exit", "exit_code": 0}
```

The HTTP connection stays open until the command finishes. Callbacks fire for each chunk as it arrives.

## Incremental stdin

Authentication responses advertise `capabilities: ["stdin_streaming"]` when the
guest supports input streaming. The host requires this capability before
starting a command with stdin, so old agents cannot silently ignore the input.

The host adds a unique `stdin_id` to `execute_stream`. Once the process and its
input channel are ready, the agent sends `{"stream": "ready"}`. The host then
sends separate `stdin` requests (HTTP `POST /stdin` or serial method `stdin`):

```json
{"stdin_id": "<execution>", "data": "<base64-encoded bytes>"}
```

Each chunk contains at most 64 KiB of decoded input. The agent acknowledges it
after writing to the child pipe, and the host waits for that acknowledgement
before requesting more input. Exhausting the input iterable sends
`{"stdin_id": "<execution>", "eof": true}` to close the pipe.

Input and cancellation requests bypass the HTTP execution lock and remain
available during exclusive commands. They are not retried: replaying a write
after a lost acknowledgement could duplicate input. Output continues on the
original stream while input is being supplied.

Cancellation uses HTTP `POST /cancel` or serial method `cancel`, with the same
`stdin_id`. The agent terminates the execution's process group and releases its
input and exclusive-command state. Guest timeouts perform the same cleanup.

## Guest user identity

Both execution methods accept an optional `user`. The guest resolves the account
before launching a command, sets `HOME`, `USER`, and `LOGNAME`, and changes to its
home unless `cwd` is supplied. Before executing the shell, the child initializes
supplementary groups and drops its GID and UID. This also applies to commands
with streaming stdin; input is written to the already user-scoped child pipe.

The authenticated `create_user` and `delete_user` methods (or HTTP endpoints with
the same names) manage guest accounts. They serialize updates to the guest
account files. Deletion stops that user's processes and optionally removes the
home directory. Accounts share the guest kernel and filesystem; they do not
create additional VM isolation.
