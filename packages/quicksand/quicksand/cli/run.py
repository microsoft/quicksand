"""Run an interactive shell in a sandbox."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path

GLOBAL_SAVES_DIR = Path.home() / ".quicksand" / "sandboxes"


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the run subcommand."""
    parser = subparsers.add_parser(
        "run",
        help="Run an interactive shell in a sandbox",
    )
    _add_arguments(parser)


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add run command arguments to the parser."""
    parser.add_argument(
        "image",
        metavar="IMAGE",
        help="Image to boot: base name (ubuntu, alpine), overlay package, save name, or path",
    )
    parser.add_argument(
        "--arch",
        default=None,
        help="Target architecture (e.g. amd64, arm64). "
        "Auto-detected if omitted. Forces TCG emulation when cross-arch.",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="NAME_OR_PATH",
        help="Save sandbox state on exit (name or path)",
    )
    parser.add_argument(
        "--memory",
        default="512M",
        help="Memory allocation (default: 512M)",
    )
    parser.add_argument(
        "--cpus",
        type=int,
        default=1,
        help="Number of CPUs (default: 1)",
    )
    parser.add_argument(
        "-v",
        "--mount",
        action="append",
        metavar="HOST:GUEST",
        help="Mount host directory into sandbox (can be repeated)",
    )
    parser.add_argument(
        "--network",
        choices=["none", "mounts-only", "full"],
        default=None,
        help="Network mode (default: mounts-only)",
    )
    parser.add_argument(
        "-p",
        "--port",
        action="append",
        metavar="HOST:GUEST",
        help="Forward host port to guest port (can be repeated)",
    )
    parser.add_argument(
        "--boot-timeout",
        type=float,
        default=None,
        help="Boot timeout in seconds",
    )
    parser.add_argument(
        "--accel",
        choices=["auto", "kvm", "hvf", "whpx", "tcg", "none"],
        default=None,
        help="Hardware acceleration (default: auto)",
    )
    parser.add_argument(
        "--disk-size",
        default=None,
        help="Resize disk (e.g., '2G', '4G')",
    )
    parser.add_argument(
        "--enable-display",
        action="store_true",
        help="Enable virtual display (VNC) for input injection / screenshots",
    )
    parser.add_argument(
        "--extra-args",
        action="append",
        metavar="ARG",
        help="Extra QEMU argument (can be repeated)",
    )
    parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error"],
        default="warning",
        help="Log level (default: warning)",
    )


_SAVE_EXTS = (".tar.gz", ".tar")


def _resolve_save_name(name: str) -> Path | None:
    """Resolve a save name to an existing save path.

    Precedence:
    1. Literal path (if contains / or has a save extension)
    2. Directory, .tar.gz, .tar in project-local sandboxes
    3. Directory, .tar.gz, .tar in user-global sandboxes

    Returns None if not found.
    """
    if "/" in name or "\\" in name or any(name.endswith(ext) for ext in _SAVE_EXTS):
        p = Path(name)
        return p if p.exists() else None

    # Directory first, then .tar.gz, then legacy .tar
    suffixes = ["", *_SAVE_EXTS]
    for base_dir in [Path.cwd() / ".quicksand" / "sandboxes", GLOBAL_SAVES_DIR]:
        for suffix in suffixes:
            candidate = base_dir / f"{name}{suffix}"
            if candidate.exists():
                return candidate

    return None


def _save_output_path(name: str) -> Path:
    """Resolve an output save name to a path (for writing).

    Literal paths are used as-is. Plain names go to ~/.quicksand/sandboxes/<name>/.
    """
    if "/" in name or "\\" in name or any(name.endswith(ext) for ext in _SAVE_EXTS):
        return Path(name)
    return GLOBAL_SAVES_DIR / name


def cmd(args: argparse.Namespace) -> int:
    """Run an interactive shell in a sandbox."""
    return asyncio.run(_cmd_async(args))


async def _cmd_async(args: argparse.Namespace) -> int:
    """Async implementation of the run command."""
    import logging

    from quicksand_core import Mount, Sandbox
    from quicksand_core._types import NetworkMode
    from quicksand_core.host import Accelerator

    # Configure logging
    level = getattr(logging, args.log_level.upper())
    logging.basicConfig(level=level, format="%(name)s: %(message)s")

    # Parse mounts
    mounts: list[Mount] = []
    if args.mount:
        for mount_str in args.mount:
            if ":" not in mount_str:
                print(f"Invalid mount format: {mount_str} (expected HOST:GUEST)", file=sys.stderr)
                return 1
            host_path, guest_path = mount_str.split(":", 1)
            mounts.append(Mount(host=host_path, guest=guest_path))

    # Parse port forwards
    from quicksand_core._types import PortForward

    port_forwards: list[PortForward] = []
    if args.port:
        for pf_str in args.port:
            if ":" not in pf_str:
                print(
                    f"Invalid port-forward format: {pf_str} (expected HOST:GUEST)",
                    file=sys.stderr,
                )
                return 1
            host_port, guest_port = pf_str.split(":", 1)
            try:
                port_forwards.append(PortForward(host=int(host_port), guest=int(guest_port)))
            except ValueError:
                print(f"Invalid port numbers: {pf_str}", file=sys.stderr)
                return 1

    # Resolve network mode
    _network_mode_map = {
        "none": NetworkMode.NONE,
        "mounts-only": NetworkMode.MOUNTS_ONLY,
        "full": NetworkMode.FULL,
    }
    network_mode = _network_mode_map[args.network] if args.network else NetworkMode.MOUNTS_ONLY

    # Resolve accelerator
    _accel_map = {
        "kvm": Accelerator.KVM,
        "hvf": Accelerator.HVF,
        "whpx": Accelerator.WHPX,
        "tcg": Accelerator.TCG,
    }
    accel: Accelerator | str | None = "auto"
    if args.accel is not None:
        if args.accel == "auto":
            accel = "auto"
        elif args.accel == "none":
            accel = None
        else:
            accel = _accel_map[args.accel]

    # Common config kwargs
    config_kwargs: dict = {
        "memory": args.memory,
        "cpus": args.cpus,
        "mounts": mounts,
        "network_mode": network_mode,
        "port_forwards": port_forwards,
        "extra_qemu_args": args.extra_args or [],
        "accel": accel,
        "enable_display": args.enable_display,
    }
    if args.boot_timeout is not None:
        config_kwargs["boot_timeout"] = args.boot_timeout
    if args.disk_size is not None:
        config_kwargs["disk_size"] = args.disk_size
    if args.arch is not None:
        config_kwargs["arch"] = args.arch

    # Unified image resolution: base name, save name, overlay package, or path
    image = args.image
    sb = Sandbox(image=image, **config_kwargs)

    # Start sandbox and run interactive shell
    label = image
    print(f"Starting {label} sandbox...")
    await sb.start()

    try:
        print("Welcome to quicksand! Type 'exit' to quit.\n")
        await _run_interactive_shell(sb)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        if args.output:
            print(f"\nSaving as: {args.output}...")
            await sb.save(args.output)
            print(f"Saved: {args.output}")
        print("\nStopping sandbox...")
        await sb.stop()

    return 0


async def _run_interactive_shell(sb) -> None:
    """Run an interactive shell loop."""
    with contextlib.suppress(ImportError):
        import readline  # noqa: F401 - enables line editing in input()

    while True:
        try:
            command = input("$ ")
        except EOFError:
            # Ctrl+D
            break

        if not command.strip():
            continue

        if command.strip() in ("exit", "quit"):
            break

        try:
            result = await sb.execute(
                command,
                on_stdout=lambda s: print(s, end="", flush=True),
                on_stderr=lambda s: print(s, end="", file=sys.stderr, flush=True),
            )
            if result.exit_code != 0 and result.stderr:
                print(result.stderr, end="", file=sys.stderr)
        except KeyboardInterrupt:
            print("^C")
