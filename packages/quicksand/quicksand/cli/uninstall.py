"""Uninstall quicksand extras.

Programmatic API::

    from quicksand.cli.uninstall import uninstall

    uninstall("qemu", "ubuntu")
    uninstall("all")
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from .install import ALIASES


def _resolve(names: list[str] | tuple[str, ...]) -> list[str]:
    """Expand aliases and dedupe."""
    packages: list[str] = []
    for raw in names:
        for pkg in ALIASES.get(raw, [raw]):
            if pkg not in packages:
                packages.append(pkg)
    return packages


def uninstall(*names: str) -> None:
    """Uninstall quicksand extras.

    Args:
        *names: One or more extra/package names (e.g. ``"qemu"``, ``"ubuntu"``).

    Raises:
        ValueError: If no names are provided.
        RuntimeError: If pip uninstall fails.

    Examples::

        from quicksand.cli.uninstall import uninstall

        uninstall("qemu", "ubuntu")
    """
    if not names:
        raise ValueError("At least one name is required")

    packages = _resolve(names)
    rc = _uninstall_packages(packages)
    if rc != 0:
        raise RuntimeError(f"Failed to uninstall packages: {packages}")


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the uninstall subcommand."""
    all_aliases = ", ".join(ALIASES.keys())
    parser = subparsers.add_parser(
        "uninstall",
        help="Uninstall quicksand extras",
    )
    parser.add_argument(
        "names",
        nargs="+",
        metavar="NAME",
        help=(f"Packages to uninstall. Aliases: {all_aliases}."),
    )


def cmd(args: argparse.Namespace) -> int:
    """Uninstall quicksand extras."""
    return _uninstall_packages(_resolve(args.names))


def _uninstall_packages(packages: list[str]) -> int:
    """Run pip uninstall for the given packages."""
    print(f"Uninstalling: {', '.join(packages)}")

    pip_args = [
        sys.executable,
        "-m",
        "pip",
        "uninstall",
        "-y",
        *packages,
    ]

    result = subprocess.run(pip_args)
    if result.returncode != 0:
        print("\nError: pip uninstall failed.")
        return 1

    print(f"\nUninstalled {len(packages)} package(s)")
    return 0
