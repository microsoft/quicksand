# Quicksand: User Guide

Quicksand gives you a full Linux virtual machine from Python. Not a container and not a shell. It's a real VM with its own kernel, filesystem, package manager, and optionally a graphical desktop. You can install software, browse the web, run tests, edit files, take screenshots, and drive the mouse and keyboard, all through a Python async API.

The key capabilities:

- **Run commands.** Execute shell commands and get stdout/stderr/exit code.
- **Share files.** Mount host directories into the VM, at boot or on the fly.
- **Save and rollback.** Checkpoint before risky operations, revert on failure, save progress to disk.
- **Control a desktop.** Take screenshots, type text, click buttons, move the mouse.
- **Network isolation.** The VM is isolated by default, with opt-in internet access.

Everything starts from a `Sandbox`:

```python
from quicksand import Sandbox

async with Sandbox(image="ubuntu") as sb:
    result = await sb.execute("echo hello from inside the VM")
    print(result.stdout)  # "hello from inside the VM"
```

The VM boots in ~2-3 seconds. When the `async with` block exits, it's gone.

For the QEMU machinery underneath, see [Under the Hood](../under-the-hood/).
