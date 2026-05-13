"""Tests for the per-user image cache (quicksand_core._image_cache)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from quicksand_core import _image_cache


@pytest.fixture
def fake_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the per-user cache root to a tmp dir for the test."""
    cache_root = tmp_path / "cache"
    cache_root.mkdir()

    class _StubConfig:
        cache_dir = cache_root

    monkeypatch.setattr(_image_cache, "get_platform_config", lambda: _StubConfig(), raising=False)
    # The function imports get_platform_config lazily; patch the qemu.platform symbol.
    import quicksand_core.qemu.platform as platform_mod

    monkeypatch.setattr(platform_mod, "get_platform_config", lambda: _StubConfig())
    return cache_root


def _make_image(dir_: Path, name: str, content: bytes = b"x") -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / name
    path.write_bytes(content)
    return path


class TestGetCacheDir:
    def test_returns_per_package_subdir(self, fake_cache_dir: Path):
        path = _image_cache.get_cache_dir("quicksand-ubuntu")
        assert path == fake_cache_dir / "images" / "quicksand-ubuntu"

    def test_does_not_create_dir(self, fake_cache_dir: Path):
        path = _image_cache.get_cache_dir("quicksand-ubuntu")
        assert not path.exists()


class TestResolve:
    def test_prefers_cache_over_legacy(self, fake_cache_dir: Path, tmp_path: Path):
        cache = _image_cache.get_cache_dir("pkg")
        cache_file = _make_image(cache, "img.qcow2", b"cache")
        legacy = tmp_path / "venv-images"
        _make_image(legacy, "img.qcow2", b"legacy")

        result = _image_cache.resolve("pkg", "img.qcow2", legacy)
        assert result == cache_file

    def test_falls_back_to_legacy_when_cache_missing(self, fake_cache_dir: Path, tmp_path: Path):
        legacy = tmp_path / "venv-images"
        legacy_file = _make_image(legacy, "img.qcow2")

        result = _image_cache.resolve("pkg", "img.qcow2", legacy)
        assert result == legacy_file

    def test_returns_none_when_neither_exists(self, fake_cache_dir: Path, tmp_path: Path):
        result = _image_cache.resolve("pkg", "img.qcow2", tmp_path / "nope")
        assert result is None


class TestResolveDir:
    def test_prefers_cache_when_artifacts_present(self, fake_cache_dir: Path, tmp_path: Path):
        cache = _image_cache.get_cache_dir("pkg")
        _make_image(cache, "img.qcow2")
        legacy = tmp_path / "venv-images"
        _make_image(legacy, "img.qcow2")

        assert _image_cache.resolve_dir("pkg", legacy) == cache

    def test_falls_back_to_legacy(self, fake_cache_dir: Path, tmp_path: Path):
        legacy = tmp_path / "venv-images"
        _make_image(legacy, "img.qcow2")

        assert _image_cache.resolve_dir("pkg", legacy) == legacy

    def test_returns_none_when_neither_has_artifacts(self, fake_cache_dir: Path, tmp_path: Path):
        # Empty dirs don't count as having artifacts.
        cache = _image_cache.get_cache_dir("pkg")
        cache.mkdir(parents=True)
        legacy = tmp_path / "venv-images"
        legacy.mkdir()

        assert _image_cache.resolve_dir("pkg", legacy) is None

    def test_save_format_nested_qcow2_counts(self, fake_cache_dir: Path, tmp_path: Path):
        # Save-format packages keep overlays in a subdir.
        legacy = tmp_path / "venv-images"
        (legacy / "overlays").mkdir(parents=True)
        (legacy / "overlays" / "0.qcow2").write_bytes(b"")

        assert _image_cache.resolve_dir("pkg", legacy) == legacy


class TestMirrorToCache:
    def test_mirrors_image_files(self, fake_cache_dir: Path, tmp_path: Path):
        legacy = tmp_path / "venv-images"
        _make_image(legacy, "img.qcow2", b"img")
        _make_image(legacy, "vmlinuz.kernel", b"k")
        _make_image(legacy, "initramfs.initrd", b"i")
        _make_image(legacy, "manifest.json", b"{}")

        count = _image_cache.mirror_to_cache("pkg", legacy)
        cache = _image_cache.get_cache_dir("pkg")

        assert count == 4
        assert (cache / "img.qcow2").read_bytes() == b"img"
        assert (cache / "vmlinuz.kernel").read_bytes() == b"k"
        assert (cache / "initramfs.initrd").read_bytes() == b"i"
        assert (cache / "manifest.json").read_bytes() == b"{}"

    def test_ignores_non_image_files(self, fake_cache_dir: Path, tmp_path: Path):
        legacy = tmp_path / "venv-images"
        _make_image(legacy, "img.qcow2")
        _make_image(legacy, "README.md")
        _make_image(legacy, "build.log")

        count = _image_cache.mirror_to_cache("pkg", legacy)
        cache = _image_cache.get_cache_dir("pkg")

        assert count == 1
        assert (cache / "img.qcow2").exists()
        assert not (cache / "README.md").exists()
        assert not (cache / "build.log").exists()

    def test_preserves_nested_structure(self, fake_cache_dir: Path, tmp_path: Path):
        legacy = tmp_path / "venv-images"
        _make_image(legacy, "manifest.json")
        _make_image(legacy / "overlays", "0.qcow2", b"a")
        _make_image(legacy / "overlays", "1.qcow2", b"b")

        count = _image_cache.mirror_to_cache("pkg", legacy)
        cache = _image_cache.get_cache_dir("pkg")

        assert count == 3
        assert (cache / "manifest.json").exists()
        assert (cache / "overlays" / "0.qcow2").read_bytes() == b"a"
        assert (cache / "overlays" / "1.qcow2").read_bytes() == b"b"

    def test_idempotent_skips_existing(self, fake_cache_dir: Path, tmp_path: Path):
        legacy = tmp_path / "venv-images"
        _make_image(legacy, "img.qcow2", b"img")

        first = _image_cache.mirror_to_cache("pkg", legacy)
        second = _image_cache.mirror_to_cache("pkg", legacy)

        assert first == 1
        assert second == 0

    def test_hardlinks_when_possible(self, fake_cache_dir: Path, tmp_path: Path):
        legacy = tmp_path / "venv-images"
        src = _make_image(legacy, "img.qcow2", b"img")

        _image_cache.mirror_to_cache("pkg", legacy)
        cache = _image_cache.get_cache_dir("pkg")
        dst = cache / "img.qcow2"

        # On the same filesystem (tmp_path), hardlinks share an inode.
        assert dst.stat().st_ino == src.stat().st_ino
        # And the file count should be 2 (legacy + cache).
        assert dst.stat().st_nlink >= 2

    def test_falls_back_to_copy_when_link_fails(self, fake_cache_dir: Path, tmp_path: Path):
        legacy = tmp_path / "venv-images"
        _make_image(legacy, "img.qcow2", b"img")

        with patch("os.link", side_effect=OSError("simulated cross-fs")):
            count = _image_cache.mirror_to_cache("pkg", legacy)

        assert count == 1
        cache = _image_cache.get_cache_dir("pkg")
        assert (cache / "img.qcow2").read_bytes() == b"img"

    def test_returns_zero_for_missing_src(self, fake_cache_dir: Path, tmp_path: Path):
        assert _image_cache.mirror_to_cache("pkg", tmp_path / "nope") == 0

    def test_cache_survives_legacy_deletion(self, fake_cache_dir: Path, tmp_path: Path):
        """After mirroring, removing the legacy file leaves the cache intact."""
        legacy = tmp_path / "venv-images"
        _make_image(legacy, "img.qcow2", b"important")

        _image_cache.mirror_to_cache("pkg", legacy)
        # Simulate venv being blown away.
        (legacy / "img.qcow2").unlink()
        legacy.rmdir()

        cache_path = _image_cache.get_cache_dir("pkg") / "img.qcow2"
        assert cache_path.exists()
        assert cache_path.read_bytes() == b"important"
