"""Tests for the QEMU launcher reaper (quicksand_core._reaper).

The reaper is normally invoked by VMProcessManager but it's a standalone
script, so we drive it directly via subprocess for end-to-end coverage of
the new --cleanup-state path that eager-cleans cached overlays on exit.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from quicksand_core import _reaper


def _spawn_reaper(args: list[str]) -> subprocess.Popen:
    """Invoke the reaper as a subprocess. Returns Popen."""
    return subprocess.Popen(
        [sys.executable, str(Path(_reaper.__file__)), *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class TestParseArgs:
    def test_no_cleanup_flag(self):
        cleanup, cmd = _reaper._parse_args(["--", "echo", "hi"])
        assert cleanup is None
        assert cmd == ["echo", "hi"]

    def test_with_cleanup_flag(self, tmp_path: Path):
        state = tmp_path / "s.json"
        cleanup, cmd = _reaper._parse_args(["--cleanup-state", str(state), "--", "echo", "hi"])
        assert cleanup == state
        assert cmd == ["echo", "hi"]

    def test_missing_separator_errors(self):
        with pytest.raises(SystemExit):
            _reaper._parse_args(["echo", "hi"])

    def test_missing_cleanup_path_errors(self):
        with pytest.raises(SystemExit):
            _reaper._parse_args(["--cleanup-state"])

    def test_unknown_flag_errors(self):
        with pytest.raises(SystemExit):
            _reaper._parse_args(["--bogus", "--", "echo"])


class TestCleanupOnChildExit:
    def test_cleanup_after_child_exits_normally(self, tmp_path: Path):
        overlay = tmp_path / "overlay.qcow2"
        overlay.write_bytes(b"x")
        state = tmp_path / "sandbox-x.json"
        state.write_text(json.dumps({"pid": 99999, "overlays": [str(overlay)]}))

        # Child is a no-op command that exits immediately. Keep stdin open
        # so the reaper's watcher thread doesn't race the child's natural
        # exit by killing it on EOF.
        proc = _spawn_reaper(["--cleanup-state", str(state), "--", "true"])
        rc = proc.wait(timeout=10)

        assert rc == 0
        assert not overlay.exists()
        assert not state.exists()

    def test_cleanup_when_parent_closes_stdin(self, tmp_path: Path):
        """If the parent goes away (stdin EOF), the reaper kills its child and
        still runs the cleanup before exiting."""
        overlay = tmp_path / "overlay.qcow2"
        overlay.write_bytes(b"x")
        state = tmp_path / "sandbox-x.json"
        state.write_text(json.dumps({"pid": 99999, "overlays": [str(overlay)]}))

        # Long-lived child (sleep 30) — we'll close the reaper's stdin to make
        # it terminate the child early, then verify cleanup ran.
        proc = _spawn_reaper(
            [
                "--cleanup-state",
                str(state),
                "--",
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
            ]
        )
        # Give the reaper a moment to spawn its child.
        time.sleep(0.5)
        assert proc.stdin is not None
        proc.stdin.close()
        # The reaper should now tear down its child and exit.
        proc.wait(timeout=10)

        assert not overlay.exists()
        assert not state.exists()

    def test_no_cleanup_flag_no_action(self, tmp_path: Path):
        """Without --cleanup-state, the reaper should not touch any state files."""
        overlay = tmp_path / "overlay.qcow2"
        overlay.write_bytes(b"x")

        proc = _spawn_reaper(["--", "true"])
        rc = proc.wait(timeout=10)

        assert rc == 0
        assert overlay.exists()  # untouched
