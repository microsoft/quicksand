# Running Commands

*See [Under the Hood: Running Commands](../under-the-hood/03-running-commands.md) for how commands reach the VM through the guest agent and QEMU's port forwarding.*

## Basic execution

```python
result = await sb.execute("ls /")
print(result.stdout)      # "bin\nboot\ndev\netc\n..."
print(result.stderr)      # "" (empty if no errors)
print(result.exit_code)   # 0
```

`execute()` runs a shell command inside the VM and waits for it to finish. It returns an `ExecuteResult` with `stdout`, `stderr`, and `exit_code`.

## Options

```python
result = await sb.execute(
    "make test",
    timeout=300,          # Max execution time in seconds (default: 30)
    cwd="/home/user/project",  # Working directory
    shell="/bin/bash",    # Shell to use (default: /bin/sh)
)
```

## Checking success

```python
result = await sb.execute("apt install -y nonexistent-package")
if result.exit_code != 0:
    print(f"Failed: {result.stderr}")
```

An `exit_code` of 0 means success. Anything else is a failure. The command itself never throws an exception on failure. You check `exit_code` instead.

## Streaming output

For long-running commands, you can get output as it arrives instead of waiting for the command to finish:

```python
def on_stdout(chunk: str):
    print(f"[stdout] {chunk}", end="")

def on_stderr(chunk: str):
    print(f"[stderr] {chunk}", end="")

result = await sb.execute(
    "apt install -y python3",
    on_stdout=on_stdout,
    on_stderr=on_stderr,
)
```

The callbacks receive chunks of output in real time over virtio-serial, or via
Server-Sent Events when using the HTTP fallback. The final `ExecuteResult` still
contains the complete stdout/stderr.

## Streaming stdin

Pass an async iterable of byte chunks to feed a running command incrementally:

```python
async def input_chunks():
    yield b"first line\n"
    yield b"second line\n"

result = await sb.execute(
    "cat",
    stdin=input_chunks(),
    on_stdout=lambda chunk: print(chunk, end=""),
)
```

The producer is advanced as the guest accepts input, rather than being buffered
in full. Output callbacks run concurrently, so the producer can wait for output
before supplying its next chunk. Finishing the iterable closes stdin (EOF).

For small inputs, `stdin` also accepts `bytes` or a UTF-8 string:

```python
result = await sb.execute("wc -c", stdin=b"hello")
result = await sb.execute("cat", stdin="hello\n")
result = await sb.execute("cat", stdin=b"")  # Send EOF immediately
```

Async iterables must yield `bytes`, including for binary data. Quicksand splits
large chunks automatically. Omitting `stdin` preserves the usual execution
behavior.

Input consumption stops when the command exits. Cancelling the call or reaching
its timeout terminates the stdin-enabled command's process group. Exceptions
from the input producer propagate to the caller after the command is cancelled.

Streaming stdin requires a guest image whose agent advertises the
`stdin_streaming` capability. Older images return an explicit error without
starting the command or consuming input. Update or rebuild the image with the
new guest agent; upgrading the host Python package alone is not sufficient.

## Running as separate users

Create guest OS accounts when multiple workloads should have separate home
directories and file ownership within one VM:

```python
alice = await sb.create_user("alice")
result = await alice.execute("cat > input.txt", stdin=b"hello\n")
print(alice.uid, alice.gid, alice.home)

# Equivalent explicit user selection:
result = await sb.execute("cat input.txt", user="alice")

await sb.delete_user("alice")  # Stops the user's processes and removes its home
```

User-scoped commands run with that account's UID, GID, supplementary groups, and
`HOME`, `USER`, and `LOGNAME`. The working directory defaults to the user's home;
an explicit `cwd` overrides it. `SandboxUser.execute()` accepts the same stdin
and output-streaming options as `Sandbox.execute()`.

Usernames must match `[a-z_][a-z0-9_-]*` and contain at most 32 characters.
Pass `remove_home=False` to `delete_user()` to keep the home directory. These are
ordinary OS accounts sharing one VM, not separate VM isolation boundaries.
Creating users requires an image containing the updated guest agent.

## Multi-step workflows

Commands run in independent shell sessions. There's no persistent shell state between calls. Use `&&` to chain commands, or write a script.

```python
# Each execute() is a fresh shell — cd doesn't persist
await sb.execute("cd /tmp")
result = await sb.execute("pwd")  # still "/" — not /tmp

# Use && to chain commands in one shell session
result = await sb.execute("cd /tmp && pwd")  # "/tmp"

# Or use cwd= for a working directory
result = await sb.execute("pwd", cwd="/tmp")  # "/tmp"
```

## Common patterns

**Install packages:**
```python
await sb.execute("apt update && apt install -y python3 git curl")
```

**Run a script from a mounted directory:**
```python
handle = await sb.mount("/host/scripts", "/mnt/scripts")
result = await sb.execute("bash /mnt/scripts/setup.sh")
```

**Check if something is installed:**
```python
result = await sb.execute("which python3")
installed = result.exit_code == 0
```

**Capture JSON output:**
```python
import json
result = await sb.execute("cat /etc/os-release | jq -R -s '.'")
data = json.loads(result.stdout)
```
