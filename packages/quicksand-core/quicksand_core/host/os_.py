"""OS-specific configuration using subclass pattern.

This module provides OS-specific values (accelerator, paths, kernel params, etc.)
through a class hierarchy. Each OS has its own subclass.

OS-specific features:
- Linux: KVM acceleration, io_uring disk AIO, microvm support
- macOS: HVF acceleration
- Windows: WHPX acceleration, noapic kernel param
"""

from __future__ import annotations

import os
import platform as _platform
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class OS(StrEnum):
    """Supported host operating systems."""

    DARWIN = "darwin"
    LINUX = "linux"
    WINDOWS = "windows"


class Accelerator(StrEnum):
    """Hardware virtualization accelerators."""

    KVM = "kvm"
    HVF = "hvf"
    WHPX = "whpx"
    TCG = "tcg"


@dataclass
class AcceleratorStatus:
    """Result of accelerator detection with detailed status."""

    available: Accelerator | None
    fallback: Accelerator
    error: str | None = None
    hint: str | None = None
    nested: bool = False  # True if running inside a hypervisor (WHPX needs kernel-irqchip=off)

    @property
    def accelerator(self) -> Accelerator:
        """Get the accelerator to use (available or fallback)."""
        return self.available if self.available else self.fallback


class BaseOSConfig(ABC):
    """Abstract base class for OS-specific configuration.

    Subclasses provide concrete values for each supported OS.
    Use OSConfig() to auto-detect the current OS.
    """

    @property
    @abstractmethod
    def os_type(self) -> OS:
        """The OS enum value."""
        ...

    @property
    @abstractmethod
    def binary_extension(self) -> str:
        """Extension for executable binaries (e.g., '.exe' or '')."""
        ...

    @property
    @abstractmethod
    def extra_kernel_params(self) -> list[str]:
        """Extra kernel parameters needed for this OS."""
        ...

    @property
    @abstractmethod
    def default_accelerator(self) -> Accelerator:
        """The default hardware accelerator for this OS."""
        ...

    @property
    @abstractmethod
    def kvm_device(self) -> Path | None:
        """Path to KVM device, or None if not applicable."""
        ...

    @property
    @abstractmethod
    def cache_dir(self) -> Path:
        """Default cache directory for this OS."""
        ...

    @property
    @abstractmethod
    def disk_aio(self) -> str | None:
        """Disk AIO backend for this OS.

        Returns:
            'io_uring' for Linux (best performance), None for others.
            When None, QEMU uses its default (threads-based AIO).
        """
        ...

    @property
    @abstractmethod
    def supports_microvm(self) -> bool:
        """Whether this OS supports the microvm machine type.

        microvm provides ~4x faster boot but requires KVM (Linux only).
        """
        ...

    @abstractmethod
    def detect_accelerator(self) -> AcceleratorStatus:
        """Detect if hardware acceleration is available."""
        ...

    @property
    def images_dir(self) -> Path:
        """Directory for cached VM images."""
        return self.cache_dir / "images"


class LinuxConfig(BaseOSConfig):
    """Configuration for Linux."""

    @property
    def os_type(self) -> OS:
        return OS.LINUX

    @property
    def binary_extension(self) -> str:
        return ""

    @property
    def extra_kernel_params(self) -> list[str]:
        return []

    @property
    def default_accelerator(self) -> Accelerator:
        return Accelerator.KVM

    @property
    def kvm_device(self) -> Path | None:
        return Path("/dev/kvm")

    @property
    def cache_dir(self) -> Path:
        xdg = os.environ.get("XDG_CACHE_HOME")
        if xdg:
            return Path(xdg) / "quicksand"
        return Path.home() / ".cache" / "quicksand"

    @property
    def disk_aio(self) -> str | None:
        # io_uring provides ~50% lower disk latency on Linux (kernel 5.8+)
        return "io_uring"

    @property
    def supports_microvm(self) -> bool:
        # microvm is a Linux-only machine type that provides ~4x faster boot
        return True

    def detect_accelerator(self) -> AcceleratorStatus:
        """Detect KVM on Linux."""
        kvm_path = self.kvm_device

        if not kvm_path or not kvm_path.exists():
            return AcceleratorStatus(
                available=None,
                fallback=Accelerator.TCG,
                error="/dev/kvm does not exist",
                hint=(
                    "KVM may not be available if:\n"
                    "  - Running in a VM without nested virtualization\n"
                    "  - KVM kernel module not loaded: sudo modprobe kvm\n"
                    "  - CPU doesn't support virtualization (check BIOS settings)"
                ),
            )

        if not os.access(kvm_path, os.R_OK | os.W_OK):
            return AcceleratorStatus(
                available=None,
                fallback=Accelerator.TCG,
                error=f"Permission denied: {kvm_path}",
                hint="Add your user to the kvm group: sudo usermod -aG kvm $USER",
            )

        return AcceleratorStatus(available=Accelerator.KVM, fallback=Accelerator.KVM)


class DarwinConfig(BaseOSConfig):
    """Configuration for macOS (Darwin)."""

    @property
    def os_type(self) -> OS:
        return OS.DARWIN

    @property
    def binary_extension(self) -> str:
        return ""

    @property
    def extra_kernel_params(self) -> list[str]:
        return []

    @property
    def default_accelerator(self) -> Accelerator:
        return Accelerator.HVF

    @property
    def kvm_device(self) -> Path | None:
        return None

    @property
    def cache_dir(self) -> Path:
        return Path.home() / ".cache" / "quicksand"

    @property
    def disk_aio(self) -> str | None:
        # io_uring is Linux-only; macOS uses QEMU's default (threads)
        return None

    @property
    def supports_microvm(self) -> bool:
        # microvm requires KVM (Linux only)
        return False

    def detect_accelerator(self) -> AcceleratorStatus:
        """Detect Hypervisor.framework on macOS."""
        try:
            result = subprocess.run(
                ["sysctl", "-n", "kern.hv_support"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip() == "1":
                return AcceleratorStatus(available=Accelerator.HVF, fallback=Accelerator.HVF)

            return AcceleratorStatus(
                available=None,
                fallback=Accelerator.TCG,
                error="Hypervisor.framework not supported",
                hint="HVF requires macOS 10.10+ on Intel or Apple Silicon",
            )

        except subprocess.TimeoutExpired:
            return AcceleratorStatus(
                available=None,
                fallback=Accelerator.TCG,
                error="sysctl timed out checking for HVF support",
            )
        except FileNotFoundError:
            return AcceleratorStatus(
                available=None,
                fallback=Accelerator.TCG,
                error="sysctl command not found",
            )


class WindowsConfig(BaseOSConfig):
    """Configuration for Windows."""

    @property
    def os_type(self) -> OS:
        return OS.WINDOWS

    @property
    def binary_extension(self) -> str:
        return ".exe"

    @property
    def extra_kernel_params(self) -> list[str]:
        # WHPX IO-APIC workaround
        return ["noapic"]

    @property
    def default_accelerator(self) -> Accelerator:
        return Accelerator.WHPX

    @property
    def kvm_device(self) -> Path | None:
        return None

    @property
    def cache_dir(self) -> Path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "quicksand"
        return Path.home() / "AppData" / "Local" / "quicksand"

    @property
    def disk_aio(self) -> str | None:
        # io_uring is Linux-only; Windows uses QEMU's default
        return None

    @property
    def supports_microvm(self) -> bool:
        # microvm requires KVM (Linux only)
        return False

    def detect_accelerator(self) -> AcceleratorStatus:
        """Detect Windows Hypervisor Platform."""
        try:
            # Check both WHPX availability and whether we're inside a hypervisor
            result = subprocess.run(
                [
                    "powershell",
                    "-Command",
                    "(Get-WmiObject Win32_ComputerSystem).HypervisorPresent;"
                    "(Get-WmiObject Win32_BaseBoard).Manufacturer",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and "True" in result.stdout:
                # Nested if baseboard is "Microsoft Corporation" (Azure/Hyper-V VM)
                nested = "Microsoft Corporation" in result.stdout
                return AcceleratorStatus(
                    available=Accelerator.WHPX,
                    fallback=Accelerator.WHPX,
                    nested=nested,
                )

            return AcceleratorStatus(
                available=None,
                fallback=Accelerator.TCG,
                error="Windows Hypervisor Platform not enabled",
                hint=(
                    "Enable WHPX in Windows Features:\n"
                    "  1. Open 'Turn Windows features on or off'\n"
                    "  2. Enable 'Windows Hypervisor Platform'\n"
                    "  3. Restart your computer"
                ),
            )

        except subprocess.TimeoutExpired:
            return AcceleratorStatus(
                available=None,
                fallback=Accelerator.TCG,
                error="PowerShell timed out checking for WHPX",
            )
        except FileNotFoundError:
            return AcceleratorStatus(
                available=None,
                fallback=Accelerator.TCG,
                error="PowerShell not found",
            )


def _detect_os() -> OS:
    """Detect the current operating system."""
    system = _platform.system().lower()
    mapping = {
        "darwin": OS.DARWIN,
        "linux": OS.LINUX,
        "windows": OS.WINDOWS,
    }
    if system not in mapping:
        raise RuntimeError(
            f"Unsupported operating system: {system}\n"
            f"Quicksand supports: {', '.join(p.value for p in OS)}"
        )
    return mapping[system]


class OSConfig(BaseOSConfig):
    """Auto-detecting OS configuration.

    Instantiating this class returns the appropriate subclass
    for the current operating system.

    Example:
        config = OSConfig()  # Returns LinuxConfig, DarwinConfig, or WindowsConfig
        print(config.os_type)  # OS.LINUX on Linux
    """

    def __new__(cls) -> BaseOSConfig:
        if cls is OSConfig:
            os_type = _detect_os()
            if os_type == OS.LINUX:
                return object.__new__(LinuxConfig)
            elif os_type == OS.DARWIN:
                return object.__new__(DarwinConfig)
            elif os_type == OS.WINDOWS:
                return object.__new__(WindowsConfig)
            raise RuntimeError(f"Unsupported OS: {os_type}")
        return object.__new__(cls)

    # These are required to satisfy the ABC, but will never be called
    # because __new__ returns a different class instance
    @property
    def os_type(self) -> OS:
        raise NotImplementedError

    @property
    def binary_extension(self) -> str:
        raise NotImplementedError

    @property
    def extra_kernel_params(self) -> list[str]:
        raise NotImplementedError

    @property
    def default_accelerator(self) -> Accelerator:
        raise NotImplementedError

    @property
    def kvm_device(self) -> Path | None:
        raise NotImplementedError

    @property
    def cache_dir(self) -> Path:
        raise NotImplementedError

    @property
    def disk_aio(self) -> str | None:
        raise NotImplementedError

    @property
    def supports_microvm(self) -> bool:
        raise NotImplementedError

    def detect_accelerator(self) -> AcceleratorStatus:
        raise NotImplementedError
