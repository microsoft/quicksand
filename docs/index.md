---
layout: home

hero:
  name: Quicksand
  # text: Full Linux VMs from Python
  tagline: <span>Launch, control, and snapshot <a href="https://www.qemu.org">QEMU</a> virtual machines through an async Python API. No root, no Docker, no cloud. Just <code>pip install quicksand</code></span>
  actions:
    - theme: brand
      text: Get Started
      link: /user-guide/01-installation
    - theme: alt
      text: Go Under the Hood
      link: /under-the-hood/

features:
  - title: ⚡ Run Commands
    details: Execute shell commands inside a VM and get stdout, stderr, and exit code back.
    link: /user-guide/03-running-commands
  - title: 💾 Save & Rollback
    details: Checkpoint before risky operations and revert on failure. Save progress to disk and resume later.
    link: /user-guide/05-save-and-rollback
  - title: 🖥️ Desktop Control
    details: Take screenshots, type text, click buttons, and move the mouse through a full graphical desktop.
    link: /user-guide/06-desktop-control
  - title: 🔒 Network Isolation
    details: VMs are isolated by default. Opt in to internet access and port forwarding when you need it.
    link: /user-guide/07-network-and-isolation
  - title: 📁 File Sharing
    details: Mount host directories into the VM at boot or on the fly. CIFS and 9p protocols supported.
    link: /user-guide/04-file-exchange
  - title: 🌍 Cross-Platform
    details: Works on macOS, Linux, and Windows with hardware acceleration. No system dependencies.
    link: /user-guide/08-performance
---

## Example: Ubuntu Hello, World

Install quicksand with QEMU and an Ubuntu VM image. Everything you need in one line.

```bash
pip install 'quicksand[qemu,ubuntu]'
```

Launch a sandbox, install a package, and use it. All inside an isolated VM that boots in ~2 seconds.

```python
import asyncio
from quicksand import Sandbox, Mount, NetworkMode

async def main():
    # Each sandbox is a real Ubuntu VM with its own kernel
    async with Sandbox(
        image="ubuntu",
        network_mode=NetworkMode.FULL,  # Enable internet for apt
        mounts=[Mount(".", "/mnt/workspace")],  # Share your project into the VM
    ) as sb:
        # Full apt ecosystem — install anything you'd install on a real machine
        await sb.execute("apt-get update && apt-get install -y figlet")
        # Your files are right there inside the VM
        await sb.execute("ls /mnt/workspace")
        # Run it just like you would in a terminal
        result = await sb.execute("figlet Quicksand")
        print(result.stdout)
    # VM is gone — nothing left on the host

asyncio.run(main())
```

```
 ____            _        _
|  _ \ ___  _ __| |_ __ _| |__   _____  __
| |_) / _ \| '__| __/ _` | '_ \ / _ \ \/ /
|  __/ (_) | |  | || (_| | |_) | (_) >  <
|_|   \___/|_|   \__\__,_|_.__/ \___/_/\_\
```

That's it. No Docker, no root, no cloud account. Just a normal user process with hypervisor-level isolation.
