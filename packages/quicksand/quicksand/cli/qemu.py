"""QEMU status and install commands."""

from __future__ import annotations

import argparse
import subprocess


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the qemu subcommand."""
    qemu_parser = subparsers.add_parser(
        "qemu",
        help="Show QEMU installation info or install QEMU",
    )
    qemu_sub = qemu_parser.add_subparsers(dest="qemu_command")
    qemu_sub.add_parser("install", help="Install QEMU via system package manager")


def cmd(args: argparse.Namespace) -> int:
    """Show QEMU installation info, or install QEMU."""
    if getattr(args, "qemu_command", None) == "install":
        return _cmd_install()
    return _cmd_status()


def _cmd_status() -> int:
    """Show QEMU installation info."""
    from quicksand_core.qemu.platform import get_runtime

    try:
        runtime = get_runtime()
    except RuntimeError as e:
        print(f"QEMU not available: {e}")
        return 1

    # Determine source
    try:
        from quicksand_qemu import get_bin_dir

        bin_dir = get_bin_dir()
        if str(runtime.qemu_binary).startswith(str(bin_dir)):
            source = "bundled (quicksand-qemu)"
        else:
            source = "system"
    except (ImportError, FileNotFoundError):
        source = "system"

    print(f"Source: {source}")
    print(f"Path: {runtime.qemu_binary}")

    # Get version
    try:
        result = subprocess.run(
            [runtime.qemu_binary, "--version"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            version_line = result.stdout.strip().split("\n")[0]
            print(f"Version: {version_line}")
    except Exception:
        pass

    return 0


def _cmd_install() -> int:
    """Install QEMU via system package manager."""
    from quicksand_core.qemu.installer import install_qemu

    try:
        install_qemu()
    except RuntimeError as e:
        print(f"Failed to install QEMU: {e}")
        return 1

    print("QEMU installed successfully.")
    return _cmd_status()
