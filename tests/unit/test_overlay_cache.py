"""Tests for the session overlay cache (quicksand_core._overlay_cache)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from quicksand_core import _overlay_cache


@pytest.fixture
def fake_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the per-user cache root to a tmp dir for the test."""
    cache_root = tmp_path / "cache"
    cache_root.mkdir()

    class _StubConfig:
        cache_dir = cache_root

    import quicksand_core.qemu.platform as platform_mod

    monkeypatch.setattr(platform_mod, "get_platform_config", lambda: _StubConfig())
    return cache_root


class TestGetOverlaysDir:
    def test_returns_cache_subdir(self, fake_cache_dir: Path):
        assert _overlay_cache.get_overlays_dir() == fake_cache_dir / "overlays"

    def test_does_not_create_dir(self, fake_cache_dir: Path):
        # Just calling get_overlays_dir shouldn't side-effect the fs.
        path = _overlay_cache.get_overlays_dir()
        assert not path.exists()


class TestAllocateOverlayPath:
    def test_creates_pool_dir_lazily(self, fake_cache_dir: Path):
        overlays_dir = _overlay_cache.get_overlays_dir()
        assert not overlays_dir.exists()

        _overlay_cache.allocate_overlay_path()

        assert overlays_dir.exists()
        assert overlays_dir.is_dir()

    def test_path_is_under_pool(self, fake_cache_dir: Path):
        path = _overlay_cache.allocate_overlay_path()
        assert path.parent == _overlay_cache.get_overlays_dir()

    def test_path_ends_with_qcow2(self, fake_cache_dir: Path):
        path = _overlay_cache.allocate_overlay_path()
        assert path.suffix == ".qcow2"

    def test_does_not_create_file(self, fake_cache_dir: Path):
        path = _overlay_cache.allocate_overlay_path()
        # Caller writes the qcow2 content via qemu-img.
        assert not path.exists()

    def test_returns_unique_paths(self, fake_cache_dir: Path):
        paths = {_overlay_cache.allocate_overlay_path() for _ in range(100)}
        assert len(paths) == 100

    def test_basename_is_hex(self, fake_cache_dir: Path):
        path = _overlay_cache.allocate_overlay_path()
        stem = path.stem  # filename without .qcow2
        # uuid4().hex is 32 lowercase hex chars
        assert len(stem) == 32
        assert all(c in "0123456789abcdef" for c in stem)


def _make_overlay(dir_: Path, name: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / name
    p.write_bytes(b"")
    return p


class TestSessionState:
    def test_write_creates_state_file_with_payload(self, fake_cache_dir: Path):
        overlays = [_make_overlay(_overlay_cache.get_overlays_dir(), "a.qcow2")]

        path = _overlay_cache.write_session_state("sb1", 42, overlays)

        assert path.exists()
        data = json.loads(path.read_text())
        assert data["sandbox_id"] == "sb1"
        assert data["pid"] == 42
        assert data["overlays"] == [str(overlays[0])]

    def test_write_is_atomic(self, fake_cache_dir: Path):
        # Writing twice should leave only the canonical file (no .tmp leftover).
        _overlay_cache.write_session_state("sb1", 1, [])
        _overlay_cache.write_session_state("sb1", 2, [])
        state_dir = _overlay_cache.get_state_dir()
        entries = sorted(p.name for p in state_dir.iterdir())
        assert entries == ["sandbox-sb1.json"]

    def test_clear_removes_state_file(self, fake_cache_dir: Path):
        _overlay_cache.write_session_state("sb1", 1, [])
        _overlay_cache.clear_session_state("sb1")
        assert not _overlay_cache.state_file_path("sb1").exists()

    def test_clear_is_idempotent(self, fake_cache_dir: Path):
        # No-op when state file doesn't exist.
        _overlay_cache.clear_session_state("never-existed")


class TestReapStaleSandboxes:
    def test_no_state_dir_returns_zero(self, fake_cache_dir: Path):
        assert _overlay_cache.reap_stale_sandboxes() == 0

    def test_live_pid_is_preserved(self, fake_cache_dir: Path):
        overlay = _make_overlay(_overlay_cache.get_overlays_dir(), "live.qcow2")
        _overlay_cache.write_session_state("sb-live", os.getpid(), [overlay])

        reaped = _overlay_cache.reap_stale_sandboxes()

        assert reaped == 0
        assert overlay.exists()
        assert _overlay_cache.state_file_path("sb-live").exists()

    def test_dead_pid_is_reaped(self, fake_cache_dir: Path):
        overlay = _make_overlay(_overlay_cache.get_overlays_dir(), "dead.qcow2")
        # PID 1 is init on POSIX but always alive. We need a guaranteed-dead PID.
        # Use a very high improbable PID; on POSIX a PID > max is invalid.
        dead_pid = 2**31 - 1
        _overlay_cache.write_session_state("sb-dead", dead_pid, [overlay])

        reaped = _overlay_cache.reap_stale_sandboxes()

        assert reaped == 1
        assert not overlay.exists()
        assert not _overlay_cache.state_file_path("sb-dead").exists()

    def test_corrupt_state_file_removed(self, fake_cache_dir: Path):
        state_dir = _overlay_cache.get_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        bad = state_dir / "sandbox-corrupt.json"
        bad.write_text("{not json")

        _overlay_cache.reap_stale_sandboxes()

        assert not bad.exists()

    def test_mixed_live_and_dead(self, fake_cache_dir: Path):
        overlays_dir = _overlay_cache.get_overlays_dir()
        live_overlay = _make_overlay(overlays_dir, "live.qcow2")
        dead_overlay = _make_overlay(overlays_dir, "dead.qcow2")
        _overlay_cache.write_session_state("sb-live", os.getpid(), [live_overlay])
        _overlay_cache.write_session_state("sb-dead", 2**31 - 1, [dead_overlay])

        reaped = _overlay_cache.reap_stale_sandboxes()

        assert reaped == 1
        assert live_overlay.exists()
        assert not dead_overlay.exists()
        assert _overlay_cache.state_file_path("sb-live").exists()
        assert not _overlay_cache.state_file_path("sb-dead").exists()


class TestCleanupForStateFile:
    def test_deletes_listed_overlays_and_state(self, fake_cache_dir: Path):
        overlay = _make_overlay(_overlay_cache.get_overlays_dir(), "x.qcow2")
        state = _overlay_cache.write_session_state("sb1", 999999, [overlay])

        _overlay_cache.cleanup_for_state_file(state)

        assert not overlay.exists()
        assert not state.exists()

    def test_handles_missing_overlay_files(self, fake_cache_dir: Path):
        # Listed overlay doesn't exist anymore — still clean up the state file.
        ghost = _overlay_cache.get_overlays_dir() / "ghost.qcow2"
        state = _overlay_cache.write_session_state("sb1", 999999, [ghost])

        _overlay_cache.cleanup_for_state_file(state)

        assert not state.exists()

    def test_handles_corrupt_state_file(self, fake_cache_dir: Path):
        state_dir = _overlay_cache.get_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        bad = state_dir / "sandbox-corrupt.json"
        bad.write_text("{not json")

        _overlay_cache.cleanup_for_state_file(bad)

        assert not bad.exists()
