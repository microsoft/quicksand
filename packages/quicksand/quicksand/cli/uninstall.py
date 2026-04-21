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

from .install import ALIASES, _parse_extra, _resolve_packages


def uninstall(*extras: str) -> None:
    """Uninstall quicksand extras.

    Args:
        *extras: One or more extra/package names (e.g. ``"qemu"``, ``"ubuntu"``,
            ``"all"``).

    Raises:
        ValueError: If no extras are provided.
        RuntimeError: If pip uninstall fails.

    Examples::

        from quicksand.cli.uninstall import uninstall

        uninstall("qemu", "ubuntu")
        uninstall("all")
    """
    if not extras:
        raise ValueError("At least one extra name is required")

    packages: list[str] = []
    for raw in extras:
        name, _ver = _parse_extra(raw)
        for pkg in _resolve_packages(name):
            if pkg not in packages:
                packages.append(pkg)

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
        "extras",
        nargs="+",
        metavar="NAME",
        help=(f"Packages to uninstall. Aliases: {all_aliases}."),
    )


def cmd(args: argparse.Namespace) -> int:
    """Uninstall quicksand extras."""
    packages: list[str] = []
    for raw in args.extras:
        name, _ver = _parse_extra(raw)
        for pkg in _resolve_packages(name):
            if pkg not in packages:
                packages.append(pkg)

    return _uninstall_packages(packages)


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
