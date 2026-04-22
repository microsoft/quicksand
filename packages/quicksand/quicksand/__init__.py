"""
Quicksand is a VM harness for AI agents.

It gives agents a real computer to work with. They can install packages, run
scripts, browse the web, and manipulate files inside a sandboxed VM.

Quick start (pip install quicksand[ubuntu]):
    import asyncio
    from quicksand import Sandbox

    async def main():
        async with Sandbox(image="ubuntu") as sb:
            result = await sb.execute("ls -la /")
            print(result.stdout)

    asyncio.run(main())

Or with Alpine (pip install quicksand[alpine]):
    from quicksand import Sandbox

    async with Sandbox(image="alpine") as sb:
        result = await sb.execute("cat /etc/os-release")

Or with custom configuration:
    from quicksand import Sandbox

    async with Sandbox(image="ubuntu", memory="2G", cpus=4) as sb:
        result = await sb.execute("cat /etc/os-release")

Install extras:
    pip install quicksand[ubuntu]   # Bundled Ubuntu image (~341MB)
    pip install quicksand[alpine]   # Bundled Alpine image (~78MB, faster boot)
    pip install quicksand[dev]      # Build custom images from Dockerfiles
"""

# Re-export everything from quicksand-core
from quicksand_core import (
    OS,
    Accelerator,
    AcceleratorStatus,
    Architecture,
    BootTiming,
    ExecuteResult,
    Key,
    MachineType,
    Mount,
    MountType,
    NetworkMode,
    PlatformConfig,
    PortForward,
    RuntimeInfo,
    Sandbox,
    SandboxConfig,
    SandboxConfigParams,
    SaveManifest,
    detect_accelerator,
    ensure_runtime,
    get_accelerator,
    get_machine_type,
    get_platform_config,
    get_runtime,
    install_qemu,
    is_runtime_available,
)

from quicksand.cli.install import install


class _MissingOptionalSandbox:
    """Placeholder for optional sandbox types that aren't installed."""

    def __init__(self, install_extra: str, sandbox_type: str):
        self._install_extra = install_extra
        self._sandbox_type = sandbox_type

    def __call__(self, *args, **kwargs):
        raise ImportError(
            f"{self._sandbox_type} requires the '{self._install_extra}' extra.\n"
            f"Install with: pip install 'quicksand[{self._install_extra}]'"
        )

    def __repr__(self):
        return f"<{self._sandbox_type}: not installed>"


# Conditionally import image extras if available
try:
    from quicksand_ubuntu import UbuntuSandbox
except ImportError:
    UbuntuSandbox = _MissingOptionalSandbox("ubuntu", "UbuntuSandbox")  # type: ignore[misc, assignment]  # ty: ignore[invalid-assignment]

try:
    from quicksand_alpine import AlpineSandbox
except ImportError:
    AlpineSandbox = _MissingOptionalSandbox("alpine", "AlpineSandbox")  # type: ignore[misc, assignment]  # ty: ignore[invalid-assignment]

try:
    from quicksand_alpine_desktop import AlpineDesktopSandbox
except ImportError:
    AlpineDesktopSandbox = _MissingOptionalSandbox("alpine-desktop", "AlpineDesktopSandbox")  # type: ignore[misc, assignment]  # ty: ignore[invalid-assignment]

try:
    from quicksand_ubuntu_desktop import UbuntuDesktopSandbox
except ImportError:
    UbuntuDesktopSandbox = _MissingOptionalSandbox("ubuntu-desktop", "UbuntuDesktopSandbox")  # type: ignore[misc, assignment]  # ty: ignore[invalid-assignment]

__all__ = [  # noqa: RUF022
    # Core
    "Sandbox",
    "SandboxConfig",
    "SandboxConfigParams",
    "Mount",
    "MountType",
    "PortForward",
    "ExecuteResult",
    # Boot timing
    "BootTiming",
    # Save
    "SaveManifest",
    # Runtime management
    "get_runtime",
    "RuntimeInfo",
    "is_runtime_available",
    "get_machine_type",
    "ensure_runtime",
    "install_qemu",
    # Accelerator
    "get_accelerator",
    "detect_accelerator",
    "AcceleratorStatus",
    # Platform
    "get_platform_config",
    "PlatformConfig",
    # Types
    "Key",
    "Accelerator",
    "Architecture",
    "MachineType",
    "NetworkMode",
    "OS",
    # Install API
    "install",
    # Ubuntu extras (available with quicksand[ubuntu])
    "UbuntuSandbox",
    # Alpine extras (available with quicksand[alpine])
    "AlpineSandbox",
    # Alpine Desktop extras (available with quicksand[alpine-desktop])
    "AlpineDesktopSandbox",
    # Ubuntu Desktop extras (available with quicksand[ubuntu-desktop])
    "UbuntuDesktopSandbox",
]

__version__ = "0.1.0"
