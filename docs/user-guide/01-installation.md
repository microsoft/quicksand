# Installation

*See [Under the Hood: Installation](../under-the-hood/01-installation.md) for how these packages map to QEMU binaries and disk images.*

::: warning Not yet released to PyPI
Quicksand is not yet on PyPI. Once released, installation will be `pip install quicksand[qemu,ubuntu]`. For now, install from GitHub or Azure DevOps Artifacts as shown below.
:::

## Install quicksand

```bash
pip install git+ssh://git@github.com/microsoft/quicksand#subdirectory=packages/quicksand
```

This installs the core Python library and CLI. No native dependencies yet. QEMU and VM images are installed separately.

To declare it as a dependency in your `pyproject.toml`:

```toml
[project]
dependencies = [
    "quicksand",
]

[tool.uv.sources]
quicksand = { git = "ssh://git@github.com/microsoft/quicksand", subdirectory = "packages/quicksand" }
```

To pin to a specific release, use a `quicksand/vX.Y.Z` tag:

```toml
[tool.uv.sources]
quicksand = { git = "ssh://git@github.com/microsoft/quicksand", subdirectory = "packages/quicksand", tag = "quicksand/v0.9.0" }
```

### Install from Azure DevOps Artifacts

First, install `keyring` with the Azure Artifacts backend so `uv`/`pip` can authenticate automatically:

```bash
uv tool install keyring --with artifacts-keyring
```

Then install quicksand from the feed:

```bash
uv pip install \
  --index-url https://VssSessionToken@pkgs.dev.azure.com/msraif/_packaging/packages/pypi/simple/ \
  --keyring-provider subprocess \
  'quicksand[qemu,alpine,ubuntu]'
```

With this method QEMU and images are included as extras. No separate `quicksand install` step needed.

To declare the feed as a dependency source in your own `pyproject.toml`:

```toml
[tool.uv]
keyring-provider = "subprocess"

[[tool.uv.index]]
name = "pypi"
url = "https://pypi.org/simple"
default = true

[[tool.uv.index]]
name = "azure-msraif"
url = "https://VssSessionToken@pkgs.dev.azure.com/msraif/_packaging/packages/pypi/simple/"

[project]
dependencies = [
    "quicksand[qemu,alpine,ubuntu]",
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
| `aif-agent-sandbox` | `ubuntu` | No | ~304 MB | ~308 MB | 0.91s | 0.92s |
| `aif-cua-agent-sandbox` | `aif-agent-sandbox` | No | ~445 MB | ~450 MB | 0.85s | 1.01s |

Boot times measured on macOS ARM64 (Apple M3 Max, HVF) with `quicksand benchmark -n 5`. First boot is slower due to cold cache.

`quicksand install all` installs QEMU and all images.

Alpine is smaller and boots faster. Ubuntu has a larger package ecosystem. Desktop images are overlays on their base image and add a graphical environment for screenshot/keyboard/mouse interaction. The `aif-*` images are pre-configured agent environments with Python 3.12, browser automation tools, and common AI agent dependencies.

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
