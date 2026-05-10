"""List installed quicksand extras."""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version

from .install import ALIASES


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the list subcommand."""
    parser = subparsers.add_parser(
        "list",
        help="List installed quicksand extras",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Also show extras that are not installed",
    )


def cmd(args: argparse.Namespace) -> int:
    """Print installed quicksand extras."""
    try:
        qs_ver = version("quick-sandbox")
    except PackageNotFoundError:
        qs_ver = "(not installed)"
    print(f"quicksand {qs_ver}")
    print()

    width = max(len(alias) for alias in ALIASES)
    any_installed = False

    for alias, pkgs in ALIASES.items():
        pkg_versions = [(pkg, _pkg_version(pkg)) for pkg in pkgs]
        installed = [(p, v) for p, v in pkg_versions if v is not None]

        if not installed:
            if args.all:
                print(f"  {alias:<{width}}  (not installed)")
            continue

        any_installed = True
        if len(pkg_versions) == 1:
            print(f"  {alias:<{width}}  {pkg_versions[0][0]} {pkg_versions[0][1]}")
        else:
            print(f"  {alias:<{width}}  {pkg_versions[0][0]} {_fmt_ver(pkg_versions[0][1])}")
            for pkg, ver in pkg_versions[1:]:
                print(f"  {' ' * width}  {pkg} {_fmt_ver(ver)}")

    if not any_installed and not args.all:
        print("  (no extras installed — use `quicksand install` to add some)")

    return 0


def _pkg_version(pkg: str) -> str | None:
    try:
        return version(pkg)
    except PackageNotFoundError:
        return None


def _fmt_ver(ver: str | None) -> str:
    return ver if ver is not None else "(not installed)"
