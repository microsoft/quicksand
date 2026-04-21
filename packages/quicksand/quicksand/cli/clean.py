"""Clean quicksand data directories.

Programmatic API::

    from quicksand.cli.clean import clean

    clean()                # remove local .quicksand/
    clean(global_=True)    # also remove ~/.quicksand/
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

LOCAL_DIR = Path.cwd() / ".quicksand"
GLOBAL_DIR = Path.home() / ".quicksand"


def _dir_size(path: Path) -> int:
    """Return total size in bytes of all files under *path*."""
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _fmt_size(size: int) -> str:
    n = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _remove(path: Path) -> None:
    if not path.exists():
        return
    size = _dir_size(path)
    shutil.rmtree(path)
    print(f"  Removed {path} ({_fmt_size(size)})")


def clean(*, global_: bool = False) -> None:
    """Remove quicksand data directories.

    Args:
        global_: If True, also remove ``~/.quicksand/``.

    Examples::

        from quicksand.cli.clean import clean

        clean()
        clean(global_=True)
    """
    _remove(LOCAL_DIR)
    if global_:
        _remove(GLOBAL_DIR)


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the clean subcommand."""
    parser = subparsers.add_parser(
        "clean",
        help="Remove quicksand data directories",
    )
    parser.add_argument(
        "--global",
        dest="global_",
        action="store_true",
        help="Also remove ~/.quicksand/",
    )


def cmd(args: argparse.Namespace) -> int:
    """Clean quicksand data directories."""
    clean(global_=args.global_)
    return 0
