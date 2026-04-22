"""
Quicksand Core provides the VM sandbox implementation that powers the quicksand
agent harness. Includes save/load, file sharing, and cross-platform support.

For the user-facing package, use `quicksand` instead.
"""

import logging

# Re-export _types as types for external use (internal code uses _types to avoid shadowing stdlib)
from . import _types as types
from ._types import (
    BaseImageInfo,
    BootTiming,
    ImageProvider,
    Key,
    Mount,
    MountHandle,
    MountType,
    NetworkMode,
    PortForward,
    QuicksandGuestAgentMethod,
    ResolvedImage,
    SaveManifest,
)
from .host import (
    OS,
    Accelerator,
    AcceleratorStatus,
    Architecture,
    BaseOSConfig,
    DarwinConfig,
    LinuxConfig,
    OSConfig,
    WindowsConfig,
)
from .qemu.arch import (
    ArchitectureConfig,
    ARM64Config,
    BaseArchitectureConfig,
    MachineType,
    X86_64Config,
)
from .qemu.installer import ensure_runtime, install_qemu
from .qemu.platform import (
    PlatformConfig,
    RuntimeInfo,
    detect_accelerator,
    get_accelerator,
    get_machine_type,
    get_platform_config,
    get_runtime,
    is_runtime_available,
)
from .sandbox import ExecuteResult, Sandbox, SandboxConfig, SandboxConfigParams

__all__ = [  # noqa: RUF022
    # Types module alias
    "types",
    # Core
    "Sandbox",
    "SandboxConfig",
    "SandboxConfigParams",
    "Mount",
    "MountHandle",
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
    "PlatformConfig",
    "get_platform_config",
    # Architecture configs
    "ArchitectureConfig",
    "BaseArchitectureConfig",
    "X86_64Config",
    "ARM64Config",
    # OS configs
    "OSConfig",
    "BaseOSConfig",
    "LinuxConfig",
    "DarwinConfig",
    "WindowsConfig",
    # Types
    "Accelerator",
    "QuicksandGuestAgentMethod",
    "Architecture",
    "BaseImageInfo",
    "ImageProvider",
    "ResolvedImage",
    "MachineType",
    "Key",
    "NetworkMode",
    "OS",
]

__version__ = "0.1.0"

logging.getLogger("quicksand").addHandler(logging.NullHandler())
