"""QEMU-specific subsystems: overlay, process, save, and platform configuration."""

from .overlay import OverlayManager
from .process import VMProcessManager
from .qmp import QMPClient

__all__ = [
    "OverlayManager",
    "QMPClient",
    "VMProcessManager",
]
