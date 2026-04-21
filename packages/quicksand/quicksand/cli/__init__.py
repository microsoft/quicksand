"""Quicksand CLI - run sandboxes from the command line."""

from __future__ import annotations

import argparse
import sys

from . import clean, dev, install, qemu, release_save, run, uninstall


def main() -> int:
    """Main entry point for the quicksand CLI."""
    parser = argparse.ArgumentParser(
        prog="quicksand",
        description="A VM harness for AI agents.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # Register subcommands
    run.register(subparsers)
    install.register(subparsers)
    uninstall.register(subparsers)
    clean.register(subparsers)
    release_save.register(subparsers)
    qemu.register(subparsers)
    dev.register(subparsers)

    from .. import benchmark as benchmark_mod

    benchmark_mod.register(subparsers)

    args = parser.parse_args()

    if args.command == "run":
        return run.cmd(args)
    elif args.command == "install":
        return install.cmd(args)
    elif args.command == "uninstall":
        return uninstall.cmd(args)
    elif args.command == "release":
        return release_save.cmd(args)
    elif args.command == "qemu":
        return qemu.cmd(args)
    elif args.command == "clean":
        return clean.cmd(args)
    elif args.command == "dev":
        return dev.cmd(args)
    elif args.command == "benchmark":
        from .. import benchmark as benchmark_mod

        return benchmark_mod.cmd(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
