# Quicksand Alpine

This package bundles a pre-built Alpine Linux 3.21 VM image for the [quicksand](https://github.com/microsoft/quicksand) agent harness. No downloads required after installation.

Alpine is lightweight and boots quickly, making it ideal for AI agents that need fast sandbox startup.

## Why Alpine?

Alpine Linux is a lightweight distribution that offers:
- **Smaller image size**: ~75MB vs ~300MB for Ubuntu
- **Faster boot time**: Less to load means quicker startup
- **Minimal attack surface**: Only essential packages included
- **musl libc**: Smaller, simpler C library

Use Alpine when you need fast, lightweight sandboxes. Use Ubuntu when you need broader package compatibility or glibc-dependent software.

## Installation

Install quicksand CLI first:

```bash
pip install "git+ssh://git@github.com/microsoft/quicksand.git#subdirectory=packages/quicksand"
```

```bash
quicksand install alpine
```

This installs `quicksand` with the bundled Alpine image.

## Usage

### Simple (recommended)

```python
import asyncio
from quicksand import AlpineSandbox

async def main():
    async with AlpineSandbox() as sb:
        result = await sb.execute("cat /etc/os-release")
        print(result.stdout)

asyncio.run(main())
```

### With custom config

```python
from quicksand import AlpineSandbox

async with AlpineSandbox(memory="512M", cpus=2) as sb:
    result = await sb.execute("uname -a")
```

Or using `Sandbox` directly:

```python
from quicksand import Sandbox

async with Sandbox(image="alpine", memory="512M", cpus=2) as sb:
    result = await sb.execute("uname -a")
```

## What's Included

The Alpine 3.21 image includes:
- Python 3
- Bash shell
- curl, ca-certificates
- Networking tools (iproute2, iputils-ping)
- The quicksand agent (pre-installed)

## Installing Additional Packages

Alpine uses `apk` for package management:

```python
async with AlpineSandbox() as sb:
    # Install packages
    await sb.execute("apk add --no-cache git nodejs npm")

    # Use them
    result = await sb.execute("node --version")
    print(result.stdout)
```

## Package Size

The wheel is ~75MB, much smaller than Ubuntu (~300MB) because Alpine is a minimal distribution. This makes it faster to download and install.

## License

MIT
