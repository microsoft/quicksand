"""Image management commands."""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import entry_points
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the images subcommand."""
    parser = subparsers.add_parser(
        "images",
        help="Image management commands",
    )
    images_sub = parser.add_subparsers(dest="images_command", required=True)
    images_sub.add_parser("list", help="List installed base images")

    install_parser = images_sub.add_parser(
        "install",
        help="Install a base image",
    )
    install_parser.add_argument(
        "name",
        help="Image name to install (e.g. ubuntu-desktop)",
    )
    install_parser.add_argument(
        "--arch",
        choices=["arm64", "amd64"],
        default=None,
        help="Target architecture (auto-detected from host if not specified)",
    )
    install_parser.add_argument(
        "--output",
        metavar="PATH",
        type=Path,
        default=None,
        help="Override the output path for the installed image",
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-install even if the image already exists",
    )


def cmd(args: argparse.Namespace) -> int:
    """Run an image management command."""
    if args.images_command == "list":
        return cmd_list()
    if args.images_command == "install":
        return cmd_install(args)
    return 1


def cmd_list() -> int:
    """List installed base images."""
    images: list[dict] = []

    # Discover images via quicksand.images entry points
    eps = entry_points(group="quicksand.images")
    for ep in eps:
        try:
            provider = ep.load()
        except Exception:
            continue

        # Resolve path using the package's get_image_path() if available
        try:
            pkg = ep.value.split(":")[0]
            import importlib

            mod = importlib.import_module(pkg)
            image_path = Path(mod.get_image_path())
            version = getattr(mod, "DISTRO_VERSION", getattr(mod, "__version__", ""))
            if image_path.exists():
                images.append(
                    {
                        "name": provider.name,
                        "version": version,
                        "path": image_path,
                        "size": _format_size(image_path.stat().st_size),
                    }
                )
        except (FileNotFoundError, AttributeError, ImportError):
            pass

    if not images:
        print("No images installed.", file=sys.stderr)
        print(
            "Install with: pip install 'quick-sandbox[alpine-desktop]'"
            " or 'quick-sandbox[ubuntu-desktop]'",
            file=sys.stderr,
        )
        return 1

    for img in images:
        print(f"{img['name']:20} {img['version']:6} {img['path']}  ({img['size']})")

    return 0


def cmd_install(args: argparse.Namespace) -> int:
    """Install a base image by name."""
    name: str = args.name
    arch: str | None = args.arch
    output: Path | None = args.output
    force: bool = args.force

    # Find the package that provides this image name via entry points
    eps = entry_points(group="quicksand.images")
    for ep in eps:
        try:
            provider = ep.load()
        except Exception:
            continue

        if provider.name != name:
            continue

        # Found — now call install_image() from the package
        pkg_name = ep.value.split(":")[0]
        try:
            import importlib

            mod = importlib.import_module(pkg_name)
        except ImportError:
            print(
                f"Error: package {pkg_name!r} is installed but could not be imported.",
                file=sys.stderr,
            )
            return 1

        if not hasattr(mod, "install_image"):
            print(
                f"Error: {name!r} does not support installation via `quicksand images install`.\n"
                "Use `quicksand install {name}` to install the pre-built wheel instead.",
                file=sys.stderr,
            )
            return 1

        # Check if already installed
        if not force:
            try:
                existing = mod.get_image_path(arch)
                print(f"Image already installed: {existing}")
                print("Use --force to reinstall.")
                return 0
            except FileNotFoundError:
                pass

        print(f"Installing {name} image...")
        if arch:
            print(f"  Architecture: {arch}")

        def progress(message: str, current: int, total: int) -> None:
            if total > 0:
                pct = int(current / total * 100)
                print(f"  [{pct:3d}%] {message}")
            else:
                print(f"  {message}")

        try:
            result_path = mod.install_image(
                output_path=output,
                arch=arch,
                progress_callback=progress,
            )
            print(f"\nInstalled: {result_path}")
            return 0
        except Exception as e:
            print(f"\nError: {e}", file=sys.stderr)
            return 1

    print(
        f"Error: no image named {name!r} found.\n"
        "Available images (from installed quicksand.images entry points):\n"
        + "\n".join(f"  {ep.name}" for ep in entry_points(group="quicksand.images")),
        file=sys.stderr,
    )
    return 1


def _format_size(size_bytes: int) -> str:
    """Format size in human-readable format."""
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.0f} TB"
