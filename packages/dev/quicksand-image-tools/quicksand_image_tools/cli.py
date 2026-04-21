"""Command-line interface for quicksand-image-tools."""

from __future__ import annotations

import argparse
import logging
import sys
from importlib.metadata import entry_points
from pathlib import Path

from quicksand_core import BaseImageInfo

from .build import build_image


def discover_bases() -> dict[str, BaseImageInfo]:
    """Discover all installed base image packages via entry points.

    Base image packages (quicksand-ubuntu, quicksand-alpine, etc.) register
    entry points in the 'quicksand.images' group with ``type = "base"``.
    This function loads them all and returns a dict mapping base name to
    BaseImageInfo.
    """
    import importlib

    bases: dict[str, BaseImageInfo] = {}
    eps = entry_points(group="quicksand.images")
    for ep in eps:
        try:
            provider = ep.load()
            if getattr(provider, "type", None) != "base":
                continue
            mod_name = ep.value.split(":")[0]
            mod = importlib.import_module(mod_name)
            docker_dir = getattr(mod, "_DOCKER_DIR", None) or getattr(mod, "DOCKER_DIR", None)
            version = getattr(mod, "DISTRO_VERSION", getattr(mod, "__version__", "unknown"))
            if docker_dir is None:
                continue
            bases[provider.name] = BaseImageInfo(
                name=provider.name, docker_dir=docker_dir, version=version
            )
        except Exception as e:
            logging.debug(f"Failed to load base entry point {ep.name}: {e}")
    return bases


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging for the CLI."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=sys.stderr,
    )


def main() -> int:
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        prog="quicksand-image-tools",
        description="Build custom VM images for quicksand",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init command
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize a directory for building custom images",
    )
    init_parser.add_argument(
        "directory",
        type=Path,
        nargs="?",
        default=Path("."),
        help="Directory to initialize (default: current directory)",
    )
    init_parser.add_argument(
        "base",
        choices=["ubuntu", "alpine"],
        nargs="?",
        default=None,
        help="Base image type (required if directory has no Dockerfile)",
    )

    # build-base command
    base_parser = subparsers.add_parser(
        "build-base",
        help="Build base Docker images locally (so you can FROM them)",
    )
    base_parser.add_argument(
        "base",
        choices=["ubuntu", "alpine", "all"],
        nargs="?",
        default="all",
        help="Which base image to build (default: all)",
    )

    # build-image command
    build_parser = subparsers.add_parser(
        "build-image",
        help="Build a VM image from a Dockerfile",
    )
    build_parser.add_argument(
        "dockerfile",
        type=Path,
        help="Path to Dockerfile",
    )
    build_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output path for the qcow2 image (default: auto-generated in cache)",
    )
    build_parser.add_argument(
        "--cache-dir",
        type=Path,
        help="Directory for cached images",
    )

    # package subcommand
    package_parser = subparsers.add_parser(
        "package",
        help="Manage quicksand image packages",
    )
    package_subparsers = package_parser.add_subparsers(dest="package_command", required=True)

    pkg_init_parser = package_subparsers.add_parser(
        "init",
        help="Bootstrap a new quicksand image package by copying an existing base",
    )
    pkg_init_parser.add_argument(
        "name",
        help="Package name, e.g. 'mylinux' → package quicksand-mylinux",
    )
    pkg_init_parser.add_argument(
        "base",
        help="Base image package to copy and extend (e.g. ubuntu, alpine)",
    )
    pkg_init_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Root directory for the new package (default: packages/quicksand-<name>)",
    )

    args = parser.parse_args()
    _setup_logging(args.verbose)

    if args.command == "init":
        return cmd_init(args)
    if args.command == "build-base":
        return cmd_build_base(args)
    if args.command == "build-image":
        return cmd_build_image(args)
    if args.command == "package" and args.package_command == "init":
        return cmd_package_init(args)

    return 1


def cmd_init(args: argparse.Namespace) -> int:
    """Handle the init command."""
    import subprocess

    dest_dir = args.directory
    if not dest_dir.exists():
        dest_dir.mkdir(parents=True)

    dockerfile_path = dest_dir / "Dockerfile"

    # If no Dockerfile exists, we need the base argument
    if not dockerfile_path.exists():
        if args.base is None:
            print(
                "Error: No Dockerfile found in directory.\n"
                "Specify a base image type: quicksand-image-tools init <directory> ubuntu|alpine",
                file=sys.stderr,
            )
            return 1

        # Get version for the base image via entry point discovery
        bases = discover_bases()
        info = bases.get(args.base)
        if info:
            version = info.version
        else:
            print(f"Warning: {args.base} version not found, using 'latest'", file=sys.stderr)
            version = "latest"

        # Check if base image exists (try versioned tag first, then latest)
        base_name = f"quicksand/{args.base}-base"
        versioned_tag = f"{base_name}:{version}"

        result = subprocess.run(
            ["docker", "image", "inspect", versioned_tag],
            capture_output=True,
        )

        if result.returncode != 0:
            # Base image doesn't exist, build it
            print(f"Base image {versioned_tag} not found, building...")
            build_args = argparse.Namespace(base=args.base)
            ret = cmd_build_base(build_args)
            if ret != 0:
                return ret

        # Write the Dockerfile with versioned tag
        dockerfile_content = f"""FROM {versioned_tag}

# Add your customizations here
"""
        dockerfile_path.write_text(dockerfile_content)
        print(f"Created {dockerfile_path}")

    return 0


def cmd_build_base(args: argparse.Namespace) -> int:
    """Handle the build-base command.

    Builds base Docker images that can be used as FROM targets.
    The Rust agent is compiled during the Docker build via multi-stage build.
    """
    import shutil
    import subprocess

    from .build import get_agent_source_dir

    # Discover installed base image packages via entry points
    base_info = discover_bases()

    if args.base == "all":
        bases_to_build = list(base_info.keys())
        if not bases_to_build:
            print(
                "No base image packages found. Install quicksand[ubuntu] or quicksand[alpine].",
                file=sys.stderr,
            )
            return 1
    else:
        bases_to_build = [args.base]

    built = []

    for base in bases_to_build:
        info = base_info.get(base)
        if info is None:
            print(f"Warning: {base} base not found (install quicksand[{base}])", file=sys.stderr)
            continue

        docker_dir = info.docker_dir
        version = info.version

        # Build Docker image with version tag
        # The Dockerfile uses multi-stage build to compile the Rust agent
        base_name = f"quicksand/{base}-base"
        versioned_tag = f"{base_name}:{version}"
        latest_tag = f"{base_name}:latest"

        # Copy agent source to docker directory for the build
        agent_dest = docker_dir / "agent"
        if agent_dest.exists():
            shutil.rmtree(agent_dest)

        agent_source = get_agent_source_dir()
        shutil.copytree(
            agent_source,
            agent_dest,
            ignore=shutil.ignore_patterns("target", ".git"),
        )

        try:
            print(f"Building {versioned_tag}...")
            result = subprocess.run(
                ["docker", "build", "-t", versioned_tag, "-t", latest_tag, "."],
                cwd=docker_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(f"Error building {versioned_tag}:\n{result.stderr}", file=sys.stderr)
                return 1

            built.append(versioned_tag)
            print(f"Built: {versioned_tag} (also tagged as {latest_tag})")
        finally:
            # Clean up copied agent source
            shutil.rmtree(agent_dest, ignore_errors=True)

    if built:
        print("\nYou can now use these in your Dockerfile:")
        for tag in built:
            print(f"  FROM {tag}")
    return 0


def cmd_build_image(args: argparse.Namespace) -> int:
    """Handle the build-image command.

    The Dockerfile should use a multi-stage build to compile the Rust agent.
    See the quicksand-ubuntu or quicksand-alpine packages for examples.
    """
    if not args.dockerfile.exists():
        print(f"Error: Dockerfile not found: {args.dockerfile}", file=sys.stderr)
        return 1

    try:
        output_path = build_image(
            args.dockerfile,
            output_path=args.output,
            cache_dir=args.cache_dir,
        )
        print(f"Built image: {output_path}")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_package_init(args: argparse.Namespace) -> int:
    """Bootstrap a new quicksand image package by copying an existing base package."""
    from . import scaffold

    name = args.name.lower()
    name_under = name.replace("-", "_")
    base = args.base
    old_pkg = f"quicksand-{base}"

    repo_root = scaffold.find_repo_root()
    if repo_root is None:
        print("Error: not in a git repository", file=sys.stderr)
        return 1

    output_dir = args.output_dir or Path(name)

    if output_dir.exists() and any(output_dir.iterdir()):
        print(f"Error: {output_dir} already exists and is non-empty", file=sys.stderr)
        return 1

    base_pkg_dir = repo_root / "packages" / old_pkg
    if not base_pkg_dir.exists():
        print(f"Error: base package not found: {base_pkg_dir}", file=sys.stderr)
        return 1

    print(f"Copying {old_pkg} → {name}...")
    scaffold.copy_base_package(base_pkg_dir, output_dir)
    scaffold.rename_module(output_dir, old_pkg, name)
    scaffold.replace_in_tree(output_dir, old_pkg, name, base)
    scaffold.reset_versions(output_dir, name)
    scaffold.reset_readme(output_dir, name)
    scaffold.reset_docker_dir(output_dir, name, base)

    registered = scaffold.register_package(name, repo_root)

    module_dir = output_dir / name_under
    docker_dir = module_dir / "docker"

    print(f"\nPackage scaffolded: {output_dir}")
    if registered:
        print(f"  Registered quicksand[{name}] in packages/quicksand/pyproject.toml")
    print("\nNext steps:")
    print(f"  1. Set DISTRO_VERSION in {module_dir}/__init__.py")
    print(f"  2. Edit {docker_dir}/Dockerfile to customize the image")
    print("  3. Sync the workspace:")
    print("       uv sync")
    print("  4. Build and test:")
    print(f"       uv build --package {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
