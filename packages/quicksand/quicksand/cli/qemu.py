"""QEMU status command."""

from __future__ import annotations

import argparse
import subprocess


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the qemu subcommand."""
    subparsers.add_parser(
        "qemu",
        help="Show QEMU installation info",
    )


def cmd(args: argparse.Namespace) -> int:
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
