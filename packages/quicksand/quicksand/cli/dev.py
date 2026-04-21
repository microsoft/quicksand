"""Development tools for building images and packages.

Routes scaffold commands to their respective packages and delegates
everything else to quicksand-image-tools.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the dev subcommand."""
    parser = subparsers.add_parser(
        "dev",
        help="Development tools for building images and packages (requires quicksand[dev])",
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

    # Catch-all for image-tools commands (init, build-base, build-image, etc.)
    dev_sub.add_parser(
        "image-tools",
        help="Run quicksand-image-tools commands",
        add_help=False,
        prefix_chars="\x00",  # disable prefix parsing so all args pass through
    ).add_argument("image_tools_args", nargs=argparse.REMAINDER)


def _check_dev_installed() -> bool:
    """Check if quicksand[dev] is installed, print help if not."""
    try:
        import quicksand_image_tools  # noqa: F401

        return True
    except ImportError:
        print(
            "Error: quicksand[dev] is not installed.\n\n"
            "Install with:\n"
            "  pip install 'quicksand[dev]'\n"
            "  # or: uv pip install 'quicksand[dev]'",
            file=sys.stderr,
        )
        return False


def cmd(args: argparse.Namespace) -> int:
    """Route dev subcommands."""
    if args.dev_command == "scaffold":
        return _cmd_scaffold(args)
    if args.dev_command == "image-tools":
        return _cmd_image_tools(args)
    # No subcommand — show help
    if not _check_dev_installed():
        return 1
    from quicksand_image_tools.cli import main as dev_main

    sys.argv = ["quicksand dev", "--help"]
    return dev_main()


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


def _cmd_image_tools(args: argparse.Namespace) -> int:
    """Delegate to quicksand-image-tools."""
    if not _check_dev_installed():
        return 1
    from quicksand_image_tools.cli import main as dev_main

    sys.argv = ["quicksand-image-tools", *args.image_tools_args]
    return dev_main()
