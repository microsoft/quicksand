"""Unit tests for the v7 save format and the save-kind state file claim."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from quicksand_core import _overlay_cache
from quicksand_core._types import SandboxConfig, SaveManifest
from quicksand_core.qemu.save import SaveWriter


@pytest.fixture
def fake_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()

    class _StubConfig:
        cache_dir = cache_root

    import quicksand_core.qemu.platform as platform_mod

    monkeypatch.setattr(platform_mod, "get_platform_config", lambda: _StubConfig())
    return cache_root


def _pool_overlay(name: str) -> Path:
    overlays_dir = _overlay_cache.get_overlays_dir()
    overlays_dir.mkdir(parents=True, exist_ok=True)
    p = overlays_dir / name
    p.write_bytes(b"")
    return p


# ---------------------------------------------------------------------------
# Save-kind state files (kind dispatch)
# ---------------------------------------------------------------------------


class TestSaveStateFile:
    def test_write_and_read(self, fake_cache_dir: Path, tmp_path: Path):
        save_dir = tmp_path / "save-x"
        save_dir.mkdir()
        overlay = _pool_overlay("a.qcow2")

        state = _overlay_cache.write_save_state(save_dir, [overlay])

        data = json.loads(state.read_text())
        assert data["kind"] == "save"
        assert data["save_dir"] == str(save_dir.resolve())
        assert data["overlays"] == [str(overlay)]

    def test_keyed_by_save_dir_hash(self, fake_cache_dir: Path, tmp_path: Path):
        a = tmp_path / "save-a"
        b = tmp_path / "save-b"
        a.mkdir()
        b.mkdir()
        path_a = _overlay_cache.write_save_state(a, [])
        path_b = _overlay_cache.write_save_state(b, [])
        assert path_a != path_b
        assert path_a.name.startswith("save-")
        assert path_b.name.startswith("save-")

    def test_clear_removes_file(self, fake_cache_dir: Path, tmp_path: Path):
        save_dir = tmp_path / "save"
        save_dir.mkdir()
        _overlay_cache.write_save_state(save_dir, [])
        _overlay_cache.clear_save_state(save_dir)
        assert not _overlay_cache.save_state_file_path(save_dir).exists()


class TestReapBothKinds:
    def test_dead_sandbox_reaped(self, fake_cache_dir: Path):
        overlay = _pool_overlay("a.qcow2")
        _overlay_cache.write_session_state("dead", 2**31 - 1, [overlay])
        _overlay_cache.reap_stale_sandboxes()
        assert not overlay.exists()

    def test_dead_save_reaped(self, fake_cache_dir: Path, tmp_path: Path):
        overlay = _pool_overlay("a.qcow2")
        deleted_save = tmp_path / "gone-save"
        deleted_save.mkdir()
        _overlay_cache.write_save_state(deleted_save, [overlay])
        # User rmtree'd the save dir but the state file lingered.
        deleted_save.rmdir()

        _overlay_cache.reap_stale_sandboxes()

        assert not overlay.exists()
        assert not _overlay_cache.save_state_file_path(deleted_save).exists()

    def test_live_save_preserves_overlay(self, fake_cache_dir: Path, tmp_path: Path):
        overlay = _pool_overlay("a.qcow2")
        save_dir = tmp_path / "live-save"
        save_dir.mkdir()
        _overlay_cache.write_save_state(save_dir, [overlay])

        _overlay_cache.reap_stale_sandboxes()

        assert overlay.exists()
        assert _overlay_cache.save_state_file_path(save_dir).exists()

    def test_shared_overlay_save_and_sandbox(self, fake_cache_dir: Path, tmp_path: Path):
        # Overlay is claimed by both a live save AND a dead sandbox. Sandbox
        # gets reaped; the save's claim keeps the overlay alive.
        overlay = _pool_overlay("shared.qcow2")
        save_dir = tmp_path / "live-save"
        save_dir.mkdir()
        _overlay_cache.write_save_state(save_dir, [overlay])
        _overlay_cache.write_session_state("dead-sb", 2**31 - 1, [overlay])

        _overlay_cache.reap_stale_sandboxes()

        assert overlay.exists()


# ---------------------------------------------------------------------------
# SaveWriter v7 path
# ---------------------------------------------------------------------------


class TestSaveWriterPool:
    """Cache-mode writes: manifest only, basenames in chain."""

    def _placeholder_manifest(self) -> SaveManifest:
        # The writer fills in chain itself based on the overlays it gets.
        return SaveManifest(config=SandboxConfig(image="ubuntu"), arch="amd64", chain=[])

    def test_writes_manifest_only_no_overlays_dir(self, fake_cache_dir: Path, tmp_path: Path):
        overlay = _pool_overlay("a.qcow2")
        writer = SaveWriter("my-save", workspace=tmp_path)

        writer.write(overlay_chain=[overlay], manifest=self._placeholder_manifest())

        save_dir = tmp_path / "my-save"
        assert (save_dir / "manifest.json").exists()
        # Cache-mode saves do NOT have an overlays/ subdir.
        assert not (save_dir / "overlays").exists()

    def test_writes_save_state_file(self, fake_cache_dir: Path, tmp_path: Path):
        overlay = _pool_overlay("a.qcow2")
        writer = SaveWriter("my-save", workspace=tmp_path)

        writer.write(overlay_chain=[overlay], manifest=self._placeholder_manifest())

        save_dir = tmp_path / "my-save"
        state_file = _overlay_cache.save_state_file_path(save_dir)
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["kind"] == "save"
        assert data["overlays"] == [str(overlay)]

    def test_manifest_chain_has_basenames(self, fake_cache_dir: Path, tmp_path: Path):
        overlay = _pool_overlay("a.qcow2")
        writer = SaveWriter("my-save", workspace=tmp_path)

        result = writer.write(overlay_chain=[overlay], manifest=self._placeholder_manifest())

        assert result.chain == [overlay.name]


class TestSaveWriterBundled:
    """Bundled writes: copy overlays into <save>/overlays/, basenames in chain."""

    def test_copies_overlays_into_save_dir(self, fake_cache_dir: Path, tmp_path: Path):
        overlay = tmp_path / "session.qcow2"
        overlay.write_bytes(b"data")
        manifest = SaveManifest(config=SandboxConfig(image="ubuntu"), arch="amd64", chain=[])
        writer = SaveWriter("bundled-save", workspace=tmp_path)

        result = writer.write(overlay_chain=[overlay], manifest=manifest, bundle=True)

        save_dir = tmp_path / "bundled-save"
        assert (save_dir / "manifest.json").exists()
        assert (save_dir / "overlays" / "0.qcow2").read_bytes() == b"data"
        assert result.chain == ["0.qcow2"]
