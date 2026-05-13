"""Manage persisted Sandbox saves and the overlay cache.

Subcommands:
    quicksand save delete <name>   Delete a save by name (local or global).
    quicksand save gc              Sweep orphan overlays whose owning
                                   Sandbox process has died.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``save`` subcommand group."""
    parser = subparsers.add_parser(
        "save",
        help="Manage saved sandbox states and the overlay cache",
    )
    save_sub = parser.add_subparsers(dest="save_command")

    delete_parser = save_sub.add_parser("delete", help="Delete a saved sandbox state")
    delete_parser.add_argument(
        "name",
        help="Save name (e.g. my-env) or path. Local saves take precedence over global.",
    )
    delete_parser.add_argument(
        "--global",
        dest="global_save",
        action="store_true",
        help="Force lookup in ~/.quicksand/sandboxes/ instead of $CWD/.quicksand/sandboxes/.",
    )

    save_sub.add_parser(
        "gc",
        help=(
            "Reclaim overlay files left behind by crashed Sandbox processes. "
            "Walks per-sandbox state files in the cache, prunes ones whose owning "
            "PID is dead, and unlinks overlays they exclusively claimed."
        ),
    )

    parser.set_defaults(_save_parser=parser)


def cmd(args: argparse.Namespace) -> int:
    """Dispatch ``save`` subcommands."""
    if args.save_command == "delete":
        return _cmd_delete(args)
    if args.save_command == "gc":
        return _cmd_gc(args)
    args._save_parser.print_help()
    return 0


# ---------------------------------------------------------------------------
# save delete
# ---------------------------------------------------------------------------


def _cmd_delete(args: argparse.Namespace) -> int:
    save_dir = _resolve_save_dir(args.name, prefer_global=args.global_save)
    if save_dir is None:
        print(f"No save found matching '{args.name}'.", file=sys.stderr)
        print(
            "Checked: $CWD/.quicksand/sandboxes/, ~/.quicksand/sandboxes/, and an explicit path.",
            file=sys.stderr,
        )
        return 1

    # Release this save's claim on any cached overlays it references (v7).
    # Done BEFORE rmtree so the GC sweep can clean up overlays in the same
    # call. v6 saves have no state file; clear_save_state is a no-op for them.
    try:
        from quicksand_core._overlay_cache import clear_save_state

        clear_save_state(save_dir)
    except Exception:
        pass

    try:
        shutil.rmtree(save_dir)
    except OSError as e:
        print(f"Failed to remove {save_dir}: {e}", file=sys.stderr)
        return 1

    print(f"Removed save: {save_dir}")

    # Sweep overlays that lost their last claim.
    _run_gc_quietly()
    return 0


def _resolve_save_dir(name: str, *, prefer_global: bool) -> Path | None:
    """Locate a save directory the same way ImageResolver does."""
    candidates: list[Path] = []

    # An explicit relative or absolute path.
    explicit = Path(name)
    if explicit.is_absolute() or "/" in name or "\\" in name:
        candidates.append(explicit)
    else:
        local = Path.cwd() / ".quicksand" / "sandboxes" / name
        global_ = Path.home() / ".quicksand" / "sandboxes" / name
        if prefer_global:
            candidates.extend([global_, local])
        else:
            candidates.extend([local, global_])

    for c in candidates:
        if c.is_dir():
            return c
    return None


# ---------------------------------------------------------------------------
# save gc
# ---------------------------------------------------------------------------


def _cmd_gc(args: argparse.Namespace) -> int:
    try:
        from quicksand_core._overlay_cache import (
            get_overlays_dir,
            get_state_dir,
            reap_stale_sandboxes,
        )
    except ImportError as e:
        print(f"Cannot reach quicksand-core: {e}", file=sys.stderr)
        return 1

    overlays_dir = get_overlays_dir()
    state = get_state_dir()

    before_overlays = _file_count(overlays_dir)
    before_state = _file_count(state)

    reaped = reap_stale_sandboxes()

    after_overlays = _file_count(overlays_dir)
    after_state = _file_count(state)

    print(f"Reaped {reaped} stale sandbox state file(s).")
    print(
        f"Overlays in cache: {before_overlays} → {after_overlays} "
        f"(freed {max(before_overlays - after_overlays, 0)})"
    )
    print(
        f"State files:      {before_state} → {after_state} "
        f"(freed {max(before_state - after_state, 0)})"
    )
    return 0


def _run_gc_quietly() -> None:
    try:
        from quicksand_core._overlay_cache import reap_stale_sandboxes

        reap_stale_sandboxes()
    except Exception:
        pass


def _file_count(directory: Path) -> int:
    if not directory.exists():
        return 0
    return sum(1 for entry in directory.iterdir() if entry.is_file())
