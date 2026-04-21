"""Platform detection and configuration.

This package provides host-specific configuration including:
- Architecture detection (x86_64, ARM64)
- OS detection (Linux, macOS, Windows)
- Accelerator detection (KVM, HVF, WHPX)
"""

from . import arch
from . import os_ as os
from .arch import Architecture
from .os_ import (
    OS,
    Accelerator,
    AcceleratorStatus,
    BaseOSConfig,
    DarwinConfig,
    LinuxConfig,
    OSConfig,
    WindowsConfig,
)

__all__ = [
    "OS",
    "Accelerator",
    "AcceleratorStatus",
    "Architecture",
    "BaseOSConfig",
    "DarwinConfig",
    "LinuxConfig",
    "OSConfig",
    "WindowsConfig",
    "arch",
    "os",
]
