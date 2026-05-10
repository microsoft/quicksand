"""Development tools for building images and packages."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the dev subcommand."""
    parser = subparsers.add_parser(
        "dev",
        help="Development tools for building images and packages",
    )
    dev_sub = parser.add_subparsers(dest="dev_command")

    # quicksand dev scaffold overlay <name> --base <base> [--output-dir DIR]
    scaffold_parser = dev_sub.add_parser("scaffold", help="Scaffold a new image package")
    scaffold_sub = scaffold_parser.add_subparsers(dest="scaffold_type")

    overlay_parser = scaffold_sub.add_parser("overlay", help="Scaffold an overlay image package")
    overlay_parser.add_argument("name", help="Package name (e.g. my-agent-sandbox)")
    overlay_parser.add_argument("--base", required=True, help="Base image (e.g. ubuntu, alpine)")
    overlay_parser.add_argument("--output-dir", type=Path, default=None, help="Output directory")

    base_parser = scaffold_sub.add_parser("base", help="Scaffold a base image package")
    base_parser.add_argument("name", help="Package name (e.g. quicksand-mylinux)")
    base_parser.add_argument("--output-dir", type=Path, default=None, help="Output directory")

    parser.set_defaults(_dev_parser=parser)


def cmd(args: argparse.Namespace) -> int:
    """Route dev subcommands."""
    if args.dev_command == "scaffold":
        return _cmd_scaffold(args)
    args._dev_parser.print_help()
    return 0


def _cmd_scaffold(args: argparse.Namespace) -> int:
    """Handle scaffold subcommands."""
    if args.scaffold_type == "overlay":
        try:
            from quicksand_overlay_scaffold.scaffold import scaffold
        except ImportError:
            print(
                "Error: quicksand-overlay-scaffold is not installed.\n\n"
                "Install with: pip install 'quicksand[dev]'",
                file=sys.stderr,
            )
            return 1
        try:
            scaffold(name=args.name, base=args.base, output_dir=args.output_dir)
        except (ValueError, FileExistsError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        return 0

    if args.scaffold_type == "base":
        try:
            from quicksand_base_scaffold.scaffold import scaffold
        except ImportError:
            print(
                "Error: quicksand-base-scaffold is not installed.\n\n"
                "Install with: pip install 'quicksand[dev]'",
                file=sys.stderr,
            )
            return 1
        try:
            scaffold(name=args.name, output_dir=args.output_dir)
        except (ValueError, FileExistsError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        return 0

    # No scaffold type — show help
    print("Usage: quicksand dev scaffold {overlay,base} ...", file=sys.stderr)
    return 1
