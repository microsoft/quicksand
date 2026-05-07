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

Usage (invoked by VMProcessManager, not directly):
    python3 _reaper.py -- <child cmd> [args...]
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading

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


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] != "--":
        print("usage: _reaper.py -- <cmd> [args...]", file=sys.stderr)
        return 2

    cmd = sys.argv[2:]
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
    return child.returncode if child.returncode is not None else 0


if __name__ == "__main__":
    sys.exit(main())
