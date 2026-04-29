# Installation

*See [Under the Hood: Installation](../under-the-hood/01-installation.md) for how these packages map to QEMU binaries and disk images.*

## Install quicksand

```bash
pip install 'quick-sandbox[qemu,alpine,ubuntu]'
```

This installs the core Python library, CLI, QEMU, and VM images. No native dependencies needed.

To declare it as a dependency in your `pyproject.toml`:

```toml
[project]
dependencies = [
    "quick-sandbox[qemu,alpine,ubuntu]",
]
```

## Install QEMU and an image

Quicksand bundles its own QEMU, so no system install is needed. Use the `quicksand install` CLI to download platform-specific binaries and images.

```bash
quicksand install qemu      # Bundled QEMU (~15MB on macOS ARM64)
quicksand install ubuntu     # Ubuntu 24.04 headless (~340MB)
```

That's enough to start using Quicksand:

```python
from quicksand import Sandbox

async with Sandbox(image="ubuntu") as sb:
    result = await sb.execute("echo hello")
    print(result.stdout)
```

## Available packages

### QEMU (`quicksand install qemu`)

| | macOS ARM64 | Linux ARM64 | Linux x86_64 | Windows x86_64 |
|---|---|---|---|---|
| Download | 14 MB | 15 MB | 15 MB | 43 MB |
| On disk | 58 MB | 60 MB | 53 MB | 124 MB |

### Images

| Image | Depends on | Display | ARM64 download | ARM64 on disk | Boot p50 | Boot p95 |
|-------|------------|---------|----------------|---------------|----------|----------|
| `alpine` | — | No | 73 MB | 74 MB | 0.37s | 0.45s |
| `ubuntu` | — | No | 341 MB | 346 MB | 0.88s | 0.91s |
| `alpine-desktop` | `alpine` | Yes | 287 MB | 290 MB | 0.47s | 0.54s |
| `ubuntu-desktop` | `ubuntu` | Yes | 252 MB | 257 MB | 0.90s | 0.96s |
| `quicksand-agent` | `ubuntu` | No | ~304 MB | ~308 MB | 0.91s | 0.92s |
| `quicksand-cua` | `quicksand-agent` | No | ~445 MB | ~450 MB | 0.85s | 1.01s |

Boot times measured on macOS ARM64 (Apple M3 Max, HVF) with `quicksand benchmark -n 5`. First boot is slower due to cold cache.

`quicksand install all` installs QEMU and all images.

Alpine is smaller and boots faster. Ubuntu has a larger package ecosystem. Desktop images are overlays on their base image and add a graphical environment for screenshot/keyboard/mouse interaction. The agent sandbox images are pre-configured agent environments with Python 3.12, browser automation tools, and common AI agent dependencies.

## Verify the installation

```bash
quicksand images list    # Shows installed images
```

```python
import asyncio
from quicksand import Sandbox

async def main():
    async with Sandbox(image="ubuntu") as sb:
        result = await sb.execute("uname -a")
        print(result.stdout)

asyncio.run(main())
```

If this prints a Linux kernel version, everything is working.

## Requirements

- **Python 3.11+**
- **macOS, Linux, or Windows.** Hardware acceleration is auto-detected (HVF on macOS, KVM on Linux, WHPX on Windows). Software emulation (TCG) is used as a fallback but is much slower.
- **No root/admin.** QEMU runs as a normal user process.
- **No Docker.** Quicksand is not container-based (Docker is only needed for *building* new images, not running them).

## Using system QEMU

If you already have QEMU installed, Quicksand will find it on `PATH` as a fallback. But the bundled QEMU (`quicksand install qemu`) is recommended. It's tested and includes the right firmware files.
