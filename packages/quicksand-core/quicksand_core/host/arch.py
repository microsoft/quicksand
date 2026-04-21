"""Host CPU architecture detection."""

from __future__ import annotations

import platform as _platform
from enum import StrEnum

# All known aliases, mapped to canonical Architecture values.
_X86_NAMES = {"x86_64", "amd64", "x64"}
_ARM_NAMES = {"arm64", "aarch64"}


class Architecture(StrEnum):
    """Normalized CPU architectures.

    Canonical values are ``"x86_64"`` and ``"arm64"``.
    Use :meth:`from_str` to normalize any alias (amd64, x64, aarch64, etc.).
    Use :attr:`image_arch` to get the name used in image file conventions (amd64/arm64).
    """

    X86_64 = "x86_64"
    ARM64 = "arm64"

    @classmethod
    def from_str(cls, value: str) -> Architecture:
        """Normalize any architecture alias to a canonical Architecture.

        Accepts: x86_64, amd64, x64, arm64, aarch64 (case-insensitive).

        Raises:
            ValueError: If the value is not a recognized architecture.
        """
        v = value.lower()
        if v in _X86_NAMES:
            return cls.X86_64
        if v in _ARM_NAMES:
            return cls.ARM64
        raise ValueError(
            f"Unknown architecture: {value!r}. "
            f"Expected one of: {', '.join(sorted(_X86_NAMES | _ARM_NAMES))}"
        )

    @property
    def image_arch(self) -> str:
        """Architecture string used in image file naming (``amd64`` or ``arm64``)."""
        return "amd64" if self == Architecture.X86_64 else "arm64"


def _detect_native_windows_arch() -> str | None:
    """Read the native CPU architecture from the Windows registry.

    On Windows ARM64, Python may run as x86_64 through transparent emulation,
    causing ``platform.machine()`` to return ``"AMD64"``.  The registry key
    ``HKLM\\...\\Session Manager\\Environment\\PROCESSOR_ARCHITECTURE``
    always reflects the true hardware architecture regardless of emulation.

    Returns the registry value (e.g. ``"ARM64"``, ``"AMD64"``), or ``None``
    if not on Windows or the key cannot be read.
    """
    if _platform.system() != "Windows":
        return None
    try:
        import winreg  # type: ignore[import-not-found]  # Windows-only module

        key = winreg.OpenKey(  # ty:ignore[unresolved-attribute]
            winreg.HKEY_LOCAL_MACHINE,  # ty:ignore[unresolved-attribute]
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        )
        value, _ = winreg.QueryValueEx(key, "PROCESSOR_ARCHITECTURE")  # ty:ignore[unresolved-attribute]
        winreg.CloseKey(key)  # ty:ignore[unresolved-attribute]
        return value
    except Exception:
        return None


def _detect_architecture() -> Architecture:
    """Detect and normalize the host CPU architecture.

    On Windows, reads the native architecture from the registry to see
    through x86_64-on-ARM64 transparent emulation.
    """
    native = _detect_native_windows_arch()
    machine = native if native else _platform.machine()
    machine = machine.lower()
    try:
        return Architecture.from_str(machine)
    except ValueError:
        raise RuntimeError(
            f"Unsupported CPU architecture: {machine}\n"
            f"Quicksand supports: x86_64/amd64, arm64/aarch64"
        ) from None


def _is_emulated() -> bool:
    """Return True when Python runs under architecture emulation.

    On Windows ARM64, Python typically runs as x86_64 via transparent
    emulation.  The native hardware is ARM64 but ``sysconfig`` reports
    ``win-amd64``.  This detects that mismatch.
    """
    import sysconfig

    native = _detect_architecture()
    plat = sysconfig.get_platform()  # e.g. "win-amd64", "linux-x86_64"
    try:
        interp = Architecture.from_str(plat.rsplit("-", 1)[-1])
    except (ValueError, IndexError):
        return False
    return native != interp
