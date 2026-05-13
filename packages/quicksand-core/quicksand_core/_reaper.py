#!/usr/bin/env python3
"""Tiny launcher that wraps a child process and kills it when the Python
parent dies.

VMProcessManager spawns this between itself and QEMU. The parent opens a
pipe to our stdin (Popen(stdin=PIPE)); we read bytes from that pipe and
forward them to the child's stdin. When the parent dies for any reason
(Ctrl+C, crash, kill -9, OOM kill), the kernel closes the write end of
the pipe and our read returns EOF — at which point we tear down the child.

Stdout and stderr are NOT redirected: the child inherits them from us, so
the parent's view of the child's streams is unchanged.

When the parent passes ``--cleanup-state <path>`` we also clean up that
Sandbox's cached overlays after the child exits — the state file is a JSON
blob written by ``quicksand_core._overlay_cache.write_session_state`` and
lists the absolute overlay paths owned by the dead Sandbox.

Usage (invoked by VMProcessManager, not directly):
    python3 _reaper.py [--cleanup-state <path>] -- <child cmd> [args...]
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

GRACEFUL_TIMEOUT = 15.0


def _kill(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    with contextlib.suppress(Exception):
        child.terminate()
        try:
            child.wait(timeout=GRACEFUL_TIMEOUT)
            return
        except subprocess.TimeoutExpired:
            pass
        child.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            child.wait(timeout=GRACEFUL_TIMEOUT)


def _watch_stdin(child: subprocess.Popen) -> None:
    """Forward our stdin to the child; kill the child on EOF."""
    try:
        while True:
            data = os.read(0, 4096)
            if not data:
                break
            if child.stdin is None:
                continue
            try:
                child.stdin.write(data)
                child.stdin.flush()
            except (BrokenPipeError, ValueError, OSError):
                break
    except OSError:
        pass
    finally:
        _kill(child)


def _parse_args(argv: list[str]) -> tuple[Path | None, list[str]]:
    """Pull our flags off the front; return ``(cleanup_state, child_cmd)``.

    Recognised flags (before the literal ``--`` separator):
        --cleanup-state <path>     Path to a Sandbox state file. After the
                                   child exits we read this file (JSON listing
                                   cached overlays) and unlink everything listed
                                   plus the state file itself.
    """
    cleanup_state: Path | None = None
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--":
            return cleanup_state, argv[i + 1 :]
        if token == "--cleanup-state":
            if i + 1 >= len(argv):
                raise SystemExit("--cleanup-state requires a path argument")
            cleanup_state = Path(argv[i + 1])
            i += 2
            continue
        raise SystemExit(f"unknown reaper flag: {token!r}")
    raise SystemExit("missing '--' separator before child command")


def _cleanup_state_file(path: Path) -> None:
    """Delete the overlays listed in ``path`` and then ``path`` itself.

    Skips overlays that another state file still claims — a sibling
    Sandbox (typically a fork or its parent) may still need them. Best-
    effort: any IO error is swallowed so the reaper can exit cleanly.
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        with contextlib.suppress(OSError):
            path.unlink()
        return
    overlays = data.get("overlays") or []
    # Drop our state file FIRST so the claim-elsewhere check excludes us.
    with contextlib.suppress(OSError):
        path.unlink()
    state_dir = path.parent
    for raw in overlays:
        overlay = Path(raw)
        if _claimed_by_another_state(state_dir, raw):
            continue
        with contextlib.suppress(FileNotFoundError), contextlib.suppress(OSError):
            overlay.unlink()


def _claimed_by_another_state(state_dir: Path, overlay_str: str) -> bool:
    """Inline cousin of ``_overlay_cache._is_claimed_elsewhere``.

    Walks both sandbox-* and save-* state files. Kept dependency-free so
    the reaper can run as a standalone script without importing the rest
    of quicksand_core.
    """
    if not state_dir.exists():
        return False
    try:
        entries = [
            p
            for p in state_dir.glob("*.json")
            if p.name.startswith("sandbox-") or p.name.startswith("save-")
        ]
    except OSError:
        return False
    for state in entries:
        try:
            data = json.loads(state.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if overlay_str in (data.get("overlays") or []):
            return True
    return False


def main() -> int:
    try:
        cleanup_state, cmd = _parse_args(sys.argv[1:])
    except SystemExit as e:
        print(
            f"usage: _reaper.py [--cleanup-state <path>] -- <cmd> [args...]: {e}", file=sys.stderr
        )
        return 2

    if not cmd:
        print("usage: _reaper.py [--cleanup-state <path>] -- <cmd> [args...]", file=sys.stderr)
        return 2

    child = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def _forward(signum, _frame):
        if child.poll() is None:
            with contextlib.suppress(Exception):
                child.send_signal(signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, _forward)

    threading.Thread(target=_watch_stdin, args=(child,), daemon=True).start()

    try:
        child.wait()
    except KeyboardInterrupt:
        _kill(child)
    finally:
        if cleanup_state is not None:
            _cleanup_state_file(cleanup_state)
    return child.returncode if child.returncode is not None else 0


if __name__ == "__main__":
    sys.exit(main())
