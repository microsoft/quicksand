"""Runtime QEMU installation fallback.

Provides ``install_qemu()`` to install QEMU via the system package manager
and ``ensure_runtime()`` to resolve QEMU with automatic installation as a
last resort.  The bundled ``quicksand-qemu`` wheel remains the primary path;
this module is a fallback for environments where the bundled wheel is
unavailable or doesn't match the host platform.

NOTE: The platform-specific install logic is shared with
``quicksand-qemu/hatch_build.py`` (build-time hook).  Keep in sync.
"""

from __future__ import annotations

import logging
import os
import platform as _platform
import shutil
import subprocess
from pathlib import Path

from ..host.arch import Architecture, _detect_architecture

logger = logging.getLogger("quicksand.qemu.installer")

# Stefan Weil Windows installer — update URL when upgrading QEMU.
WINDOWS_QEMU_INSTALLER = "qemu-w64-setup-20260324.exe"
WINDOWS_QEMU_URL = f"https://qemu.weilnetz.de/w64/2026/{WINDOWS_QEMU_INSTALLER}"

WINDOWS_ARM64_QEMU_INSTALLER = "qemu-arm-setup-20260401.exe"
WINDOWS_ARM64_QEMU_URL = f"https://qemu.weilnetz.de/aarch64/{WINDOWS_ARM64_QEMU_INSTALLER}"


def install_qemu() -> None:
    """Install QEMU via the system package manager.

    Supports macOS (Homebrew), Linux (apt-get/dnf), and Windows (Stefan Weil
    installer).  On Linux, expects passwordless ``sudo``.

    Raises:
        RuntimeError: If the platform is unsupported or installation fails.
    """
    arch = _detect_architecture()
    system = _platform.system().lower()

    qemu_name = "qemu-system-x86_64" if arch == Architecture.X86_64 else "qemu-system-aarch64"

    if shutil.which(qemu_name):
        logger.info("Found %s on PATH", qemu_name)
        return

    logger.info("QEMU not found — installing for %s...", system)

    if system == "darwin":
        _install_qemu_macos()
    elif system == "linux":
        _install_qemu_linux(arch)
    elif system == "windows":
        _install_qemu_windows(arch)
    else:
        raise RuntimeError(f"Unsupported platform for automatic QEMU install: {system}")


def ensure_runtime():
    """Resolve QEMU runtime, auto-installing if needed.

    Tries the standard resolution chain (bundled wheel → system PATH).
    If neither is found, installs QEMU via the system package manager
    and retries.

    Returns:
        RuntimeInfo: Resolved QEMU runtime paths.

    Raises:
        RuntimeError: If QEMU cannot be found or installed.
    """
    from .platform import _WrongArchError, get_runtime

    try:
        return get_runtime()
    except _WrongArchError:
        raise  # Don't install over a misconfigured bundled package
    except RuntimeError:
        logger.info("QEMU not found, attempting automatic installation...")
        install_qemu()
        return get_runtime()  # Retry; let any error propagate


def _install_qemu_macos() -> None:
    """Install QEMU via Homebrew."""
    if not shutil.which("brew"):
        raise RuntimeError(
            "Homebrew is required to install QEMU on macOS.\nInstall it from https://brew.sh"
        )
    subprocess.run(["brew", "install", "qemu"], check=True)
    logger.info("Installed QEMU via Homebrew")


def _install_qemu_linux(arch: Architecture) -> None:
    """Install QEMU via apt-get or dnf."""
    pkg = "qemu-system-x86" if arch == Architecture.X86_64 else "qemu-system-arm"

    if shutil.which("apt-get"):
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        subprocess.run(
            ["sudo", "apt-get", "update", "-qq"],
            check=True,
            env=env,
        )
        subprocess.run(
            ["sudo", "apt-get", "install", "-y", "-qq", pkg, "qemu-utils"],
            check=True,
            env=env,
        )
        logger.info("Installed %s and qemu-utils via apt", pkg)
    elif shutil.which("dnf"):
        subprocess.run(
            ["sudo", "dnf", "install", "-y", "qemu-system-x86-core", "qemu-img"],
            check=True,
        )
        logger.info("Installed QEMU via dnf")
    else:
        raise RuntimeError(
            "No supported package manager found (apt-get or dnf).\nPlease install QEMU manually."
        )


def _install_qemu_windows(arch: Architecture) -> None:
    """Install QEMU via the Stefan Weil Windows installer."""
    import urllib.request

    if arch == Architecture.ARM64:
        installer_name = WINDOWS_ARM64_QEMU_INSTALLER
        installer_url = WINDOWS_ARM64_QEMU_URL
    else:
        installer_name = WINDOWS_QEMU_INSTALLER
        installer_url = WINDOWS_QEMU_URL

    installer = Path(installer_name)
    if not installer.exists():
        logger.info("Downloading %s...", installer_url)
        urllib.request.urlretrieve(installer_url, installer)

    logger.info("Running QEMU installer (silent)...")
    subprocess.run([str(installer), "/S"], check=True)

    # Add to PATH for this process
    qemu_dir = Path(r"C:\Program Files\qemu")
    if qemu_dir.exists():
        os.environ["PATH"] = str(qemu_dir) + os.pathsep + os.environ.get("PATH", "")
        logger.info("Installed QEMU to %s", qemu_dir)
    else:
        raise RuntimeError(
            f"QEMU installer completed but {qemu_dir} not found.\n"
            "The installer may have failed silently."
        )
