# Installation

*See [Under the Hood: Installation](../under-the-hood/01-installation.md) for how these packages map to QEMU binaries and disk images.*

## Install quicksand

```bash
pip install 'quick-sandbox[qemu,alpine,ubuntu]'
```

This installs the core Python library, CLI, QEMU, and image packages. On platforms
with a compatible bundled QEMU wheel, no system QEMU installation is needed.

Large image packages are published to PyPI as small stubs. On first use, they
download the full platform-specific wheel from the
[Quicksand index](https://microsoft.github.io/quicksand/simple/). Both automatic
downloads and `quicksand install` require `pip` in that Python environment
(`uv venv --seed` when using uv). Use the CLI to fetch images before first use. Set
`QUICKSAND_AUTO_INSTALL=0` to disable automatic image downloads.

To declare it as a dependency in your `pyproject.toml`:

```toml
[project]
dependencies = [
    "quick-sandbox[qemu,alpine,ubuntu]",
]
```

## Install QEMU and an image

Use the `quicksand install` CLI to download bundled QEMU binaries and images.
See [Requirements](#requirements) for platform compatibility.

```bash
quicksand install qemu      # Bundled QEMU (~15MB on macOS ARM64)
quicksand install ubuntu     # Ubuntu 24.04 headless (~309-357 MB)
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

| | macOS ARM64 | Linux ARM64 | Linux x86_64 | Windows x86_64 | Windows ARM64 |
|---|---|---|---|---|---|
| Download | 15 MB | 16 MB | 16 MB | 44 MB | 35 MB |

### Images

| Image | Depends on | Display | ARM64 download | Boot p50 | Boot p95 |
|-------|------------|---------|----------------|----------|----------|
| `alpine` | — | No | 82 MB | 0.37s | 0.45s |
| `ubuntu` | — | No | 357 MB | 0.88s | 0.91s |
| `alpine-desktop` | `alpine` | Yes | 346 MB | 0.47s | 0.54s |
| `ubuntu-desktop` | `ubuntu` | Yes | 273 MB | 0.90s | 0.96s |
| `quicksand-agent` | `ubuntu` | No | 306 MB | 0.91s | 0.92s |
| `quicksand-cua` | `quicksand-agent` | No | 489 MB | 0.85s | 1.01s |

Download sizes are rounded decimal MB for the September 14, 2026 releases and
exclude dependencies. Boot times are historical measurements on macOS ARM64
(Apple M3 Max, HVF) with `quicksand benchmark -n 5`, not a new benchmark of these
releases. First boot is slower due to cold cache.

`quicksand install` accepts multiple names, for example
`quicksand install qemu ubuntu alpine`.

Alpine is smaller and boots faster. Ubuntu has a larger package ecosystem. Desktop images are overlays on their base image and add a graphical environment for screenshot/keyboard/mouse interaction. The agent sandbox images are pre-configured agent environments with Python 3.12, browser automation tools, and common AI agent dependencies.

## Verify the installation

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

Use native ARM64 Python on Windows ARM64. The current Windows QEMU wheels each
contain one architecture; emulated x86_64 Python selects the x64 wheel rather
than the ARM64 binaries needed by the host. Linux quicksand-qemu 0.5.12 wheels require
glibc 2.38 or newer; use a compatible system QEMU on older Linux distributions.

## Using system QEMU

If you already have QEMU installed, Quicksand will find it on `PATH` as a fallback. But the bundled QEMU (`quicksand install qemu`) is recommended. It's tested and includes the right firmware files.
