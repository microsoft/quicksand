"""QEMU architecture-specific configuration.

Provides machine types and virtio device names for each supported CPU architecture.

Machine types:
- x86_64: q35 (default) or microvm (with KVM for ~4x faster boot)
- ARM64: virt

Device types vary by machine:
- PCI machines (q35): virtio-blk-pci, virtio-net-pci
- MMIO machines (virt, microvm): virtio-blk-device, virtio-net-device
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from ..host.arch import Architecture, _detect_architecture


class MachineType(StrEnum):
    """QEMU machine types."""

    VIRT = "virt"
    Q35 = "q35"
    MICROVM = "microvm"


class BaseArchitectureConfig(ABC):
    """Abstract base class for architecture-specific QEMU configuration."""

    @property
    @abstractmethod
    def arch_type(self) -> Architecture: ...

    @property
    @abstractmethod
    def machine_type(self) -> MachineType: ...

    @property
    @abstractmethod
    def qemu_suffix(self) -> str: ...

    @property
    @abstractmethod
    def console_device(self) -> str: ...

    @property
    @abstractmethod
    def virtio_net_device(self) -> str: ...

    @property
    @abstractmethod
    def virtio_blk_device(self) -> str: ...

    @property
    @abstractmethod
    def virtio_gpu_device(self) -> str: ...

    @property
    @abstractmethod
    def default_cpu_model(self) -> str: ...

    def build_cpu_args(self, has_hw_accel: bool) -> list[str]:
        """Build QEMU -cpu arguments."""
        if has_hw_accel:
            return ["-cpu", "host"]
        elif self.default_cpu_model:
            return ["-cpu", self.default_cpu_model]
        return []


class X86_64Config(BaseArchitectureConfig):
    """QEMU configuration for x86_64 (amd64)."""

    @property
    def arch_type(self) -> Architecture:
        return Architecture.X86_64

    @property
    def machine_type(self) -> MachineType:
        return MachineType.Q35

    @property
    def qemu_suffix(self) -> str:
        return "x86_64"

    @property
    def console_device(self) -> str:
        return "ttyS0"

    @property
    def virtio_net_device(self) -> str:
        return "virtio-net-pci"

    @property
    def virtio_blk_device(self) -> str:
        return "virtio-blk-pci"

    @property
    def virtio_gpu_device(self) -> str:
        return "virtio-vga"

    @property
    def default_cpu_model(self) -> str:
        return ""


class ARM64Config(BaseArchitectureConfig):
    """QEMU configuration for ARM64 (aarch64)."""

    @property
    def arch_type(self) -> Architecture:
        return Architecture.ARM64

    @property
    def machine_type(self) -> MachineType:
        return MachineType.VIRT

    @property
    def qemu_suffix(self) -> str:
        return "aarch64"

    @property
    def console_device(self) -> str:
        return "ttyAMA0"

    @property
    def virtio_net_device(self) -> str:
        return "virtio-net-device"

    @property
    def virtio_blk_device(self) -> str:
        return "virtio-blk-device"

    @property
    def virtio_gpu_device(self) -> str:
        return "virtio-gpu-pci"

    @property
    def default_cpu_model(self) -> str:
        return "max"


class ArchitectureConfig(BaseArchitectureConfig):
    """Auto-detecting QEMU architecture configuration.

    Instantiating returns the appropriate subclass for the current CPU.

    Example:
        config = ArchitectureConfig()  # Returns X86_64Config or ARM64Config
    """

    def __new__(cls) -> BaseArchitectureConfig:
        if cls is ArchitectureConfig:
            arch = _detect_architecture()
            if arch == Architecture.X86_64:
                return object.__new__(X86_64Config)
            elif arch == Architecture.ARM64:
                return object.__new__(ARM64Config)
            raise RuntimeError(f"Unsupported architecture: {arch}")
        return object.__new__(cls)

    @property
    def arch_type(self) -> Architecture:
        raise NotImplementedError

    @property
    def machine_type(self) -> MachineType:
        raise NotImplementedError

    @property
    def qemu_suffix(self) -> str:
        raise NotImplementedError

    @property
    def console_device(self) -> str:
        raise NotImplementedError

    @property
    def virtio_net_device(self) -> str:
        raise NotImplementedError

    @property
    def virtio_blk_device(self) -> str:
        raise NotImplementedError

    @property
    def virtio_gpu_device(self) -> str:
        raise NotImplementedError

    @property
    def default_cpu_model(self) -> str:
        raise NotImplementedError
