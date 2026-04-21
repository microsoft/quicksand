# Quicksand Ubuntu

This package bundles a pre-built Ubuntu 24.04 VM image for the [quicksand](https://github.com/microsoft/quicksand) agent harness. No downloads required after installation.

Ubuntu is ideal for AI agents that need a full Linux environment with the apt package manager and broad software compatibility.

## Installation

Install quicksand CLI first:

```bash
pip install "git+ssh://git@github.com/microsoft/quicksand.git#subdirectory=packages/quicksand"
```

```bash
quicksand install ubuntu
```

This installs `quicksand` with the bundled Ubuntu 24.04 image.

## Usage

### Simple (recommended)

```python
import asyncio
from quicksand import UbuntuSandbox

async def main():
    async with UbuntuSandbox() as sb:
        result = await sb.execute("cat /etc/os-release")
        print(result.stdout)

asyncio.run(main())
```

### With custom config

```python
from quicksand import UbuntuSandbox

async with UbuntuSandbox(memory="2G", cpus=4) as sb:
    result = await sb.execute("uname -a")
```

Or using `Sandbox` directly:

```python
from quicksand import Sandbox

async with Sandbox(image="ubuntu", memory="2G", cpus=4) as sb:
    result = await sb.execute("uname -a")
```

## What's Included

The Ubuntu 24.04 image includes:
- Python 3
- Bash, curl, ca-certificates
- Networking tools
- The quicksand agent (pre-installed and configured)

## Package Size

The wheel is ~300MB because it includes the full VM image. This is intentional - it eliminates runtime downloads and ensures reproducible environments.

## License

MIT
