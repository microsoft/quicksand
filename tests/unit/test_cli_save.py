"""Tests for the ``quicksand save`` CLI subcommands."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest
from quicksand.cli import save as save_cli


@pytest.fixture
def fake_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the per-user cache to a tmp dir for the test."""
    cache_root = tmp_path / "cache"
    cache_root.mkdir()

    class _StubConfig:
        cache_dir = cache_root

    import quicksand_core.qemu.platform as platform_mod

    monkeypatch.setattr(platform_mod, "get_platform_config", lambda: _StubConfig())
    return cache_root


def _make_save(workspace_root: Path, name: str) -> Path:
    save_dir = workspace_root / ".quicksand" / "sandboxes" / name
    save_dir.mkdir(parents=True)
    (save_dir / "manifest.json").write_text("{}")
    (save_dir / "overlays").mkdir()
    (save_dir / "overlays" / "0.qcow2").write_bytes(b"")
    return save_dir


# ---------------------------------------------------------------------------
# save delete
# ---------------------------------------------------------------------------


class TestSaveDelete:
    def test_deletes_local_save(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_cache_dir: Path,
        capsys: pytest.CaptureFixture,
    ):
        monkeypatch.chdir(tmp_path)
        save_dir = _make_save(tmp_path, "my-save")

        args = argparse.Namespace(save_command="delete", name="my-save", global_save=False)
        rc = save_cli.cmd(args)

        assert rc == 0
        assert not save_dir.exists()
        out = capsys.readouterr().out
        assert "Removed save:" in out

    def test_returns_error_when_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_cache_dir: Path,
        capsys: pytest.CaptureFixture,
    ):
        monkeypatch.chdir(tmp_path)
        args = argparse.Namespace(save_command="delete", name="nope", global_save=False)
        rc = save_cli.cmd(args)

        assert rc == 1
        err = capsys.readouterr().err
        assert "No save found" in err

    def test_local_precedes_global(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_cache_dir: Path,
        capsys: pytest.CaptureFixture,
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        # Patch Path.home() since it caches at import time.
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

        local = _make_save(tmp_path, "name")
        global_save = _make_save(tmp_path / "home", "name")

        args = argparse.Namespace(save_command="delete", name="name", global_save=False)
        rc = save_cli.cmd(args)

        assert rc == 0
        # Local was deleted, global untouched.
        assert not local.exists()
        assert global_save.exists()

    def test_global_flag_targets_global(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_cache_dir: Path
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

        local = _make_save(tmp_path, "name")
        global_save = _make_save(tmp_path / "home", "name")

        args = argparse.Namespace(save_command="delete", name="name", global_save=True)
        rc = save_cli.cmd(args)

        assert rc == 0
        assert local.exists()
        assert not global_save.exists()

    def test_explicit_path(self, tmp_path: Path, fake_cache_dir: Path):
        save_dir = tmp_path / "somewhere-else" / "saves" / "thing"
        save_dir.mkdir(parents=True)
        (save_dir / "manifest.json").write_text("{}")

        args = argparse.Namespace(save_command="delete", name=str(save_dir), global_save=False)
        rc = save_cli.cmd(args)

        assert rc == 0
        assert not save_dir.exists()


# ---------------------------------------------------------------------------
# save gc
# ---------------------------------------------------------------------------


class TestSaveGc:
    def test_reaps_dead_sandbox(self, fake_cache_dir: Path, capsys: pytest.CaptureFixture):
        from quicksand_core._overlay_cache import (
            get_overlays_dir,
            write_session_state,
        )

        overlays = get_overlays_dir()
        overlays.mkdir(parents=True, exist_ok=True)
        orphan = overlays / "orphan.qcow2"
        orphan.write_bytes(b"")
        write_session_state("dead", 2**31 - 1, [orphan])

        rc = save_cli.cmd(argparse.Namespace(save_command="gc"))

        assert rc == 0
        out = capsys.readouterr().out
        assert "Reaped 1" in out
        assert not orphan.exists()

    def test_keeps_live_sandbox(self, fake_cache_dir: Path, capsys: pytest.CaptureFixture):
        from quicksand_core._overlay_cache import (
            get_overlays_dir,
            state_file_path,
            write_session_state,
        )

        overlays = get_overlays_dir()
        overlays.mkdir(parents=True, exist_ok=True)
        live = overlays / "live.qcow2"
        live.write_bytes(b"")
        write_session_state("alive", os.getpid(), [live])

        rc = save_cli.cmd(argparse.Namespace(save_command="gc"))

        assert rc == 0
        out = capsys.readouterr().out
        assert "Reaped 0" in out
        assert live.exists()
        assert state_file_path("alive").exists()

    def test_empty_pool(self, fake_cache_dir: Path, capsys: pytest.CaptureFixture):
        rc = save_cli.cmd(argparse.Namespace(save_command="gc"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Reaped 0" in out
