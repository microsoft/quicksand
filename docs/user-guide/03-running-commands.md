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

The callbacks receive chunks of output in real time via Server-Sent Events. The final `ExecuteResult` still contains the complete stdout/stderr.

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
