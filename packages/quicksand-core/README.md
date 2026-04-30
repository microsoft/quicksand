# Quicksand Core

This package provides the core implementation for the [quicksand](https://github.com/microsoft/quicksand) VM harness.

It includes the abstractions for running VMs that AI agents can interact with, including command execution, file operations, and state checkpointing. Most users should install `quicksand` instead, which includes pre-built images.

## Installation

For most users, install the main package:

```bash
pip install 'quick-sandbox[qemu,ubuntu]'
```

For core-only (no bundled images):

```bash
pip install quick-sandbox
```

## Core Exports

This package exports the core building blocks:

```python
from quicksand_core import (
    # Main classes
    Sandbox,
    Mount,
    ExecuteResult,
    # Save support
    SaveManifest,
    # Image resolution
    ResolvedImage,
    # Runtime management
    get_runtime,
    RuntimeInfo,
    is_runtime_available,
    # Accelerator detection
    get_accelerator,
    detect_accelerator,
    AcceleratorStatus,
    Accelerator,
    # Platform configuration
    get_platform_config,
    PlatformConfig,
    # Architecture/OS types
    Architecture,
    MachineType,
    OS,
)
```

## Usage with Custom Image

```python
import asyncio
from quicksand_core import Sandbox

async def main():
    async with Sandbox(image="your-image-name", memory="1G", cpus=2) as sb:
        result = await sb.execute("cat /etc/os-release")
        print(result.stdout)

asyncio.run(main())
```

## Features

- **Real VM isolation**: Hypervisor-level isolation (KVM, HVF, WHPX)
- **Cross-platform**: Linux, macOS, Windows
- **Platform abstraction**: Automatic detection of accelerators and machine types
- **Save and load**: Save VM disk state to a directory and load it on any machine
- **File sharing**: CIFS mounts via `quicksand-smb` (pure-Python SMB3 server, invoked as a subprocess via QEMU guestfwd)
- **Performance optimizations**:
  - io_uring disk AIO (~50% lower latency on Linux)
  - IOThreads for better concurrent disk I/O (all platforms)

## For Most Users

Install `quicksand` with a bundled image for zero-configuration usage:

```bash
pip install 'quick-sandbox[qemu,ubuntu]'
```

```python
import asyncio
from quicksand import UbuntuSandbox

async def main():
    async with UbuntuSandbox() as sb:
        result = await sb.execute("ls -la /")
        print(result.stdout)

asyncio.run(main())
```

## License

MIT
