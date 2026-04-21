"""Unit tests for save module."""

from __future__ import annotations

import json

import pytest
from quicksand_core._types import SandboxConfig, SaveManifest
from quicksand_core.qemu.image_resolver import SAVE_VERSION, ImageResolver
from quicksand_core.utils import compute_file_sha256


class TestSaveManifest:
    """Tests for SaveManifest model."""

    def test_create_save_manifest(self, tmp_dir):
        """Test creating SaveManifest."""
        config = SandboxConfig(image="test")
        manifest = SaveManifest(
            version=6,
            config=config,
            arch="x86_64",
        )
        assert manifest.version == 6
        assert manifest.config.image == "test"
        assert manifest.arch == "x86_64"

    def test_save_manifest_default_arch(self):
        """Test that SaveManifest arch defaults to None."""
        config = SandboxConfig(image="test")
        manifest = SaveManifest(version=6, config=config)
        assert manifest.arch is None


class TestComputeFileSha256:
    """Tests for compute_file_sha256 function."""

    def test_compute_hash(self, tmp_dir):
        """Test computing file hash."""
        test_file = tmp_dir / "test.txt"
        test_file.write_text("hello world")

        hash_result = compute_file_sha256(test_file)

        # Known SHA-256 of "hello world"
        assert hash_result == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"

    def test_compute_hash_binary(self, tmp_dir):
        """Test computing hash of binary file."""
        test_file = tmp_dir / "test.bin"
        test_file.write_bytes(b"\x00\x01\x02\x03")

        hash_result = compute_file_sha256(test_file)

        # Should return valid hex string
        assert len(hash_result) == 64
        assert all(c in "0123456789abcdef" for c in hash_result)


class TestLoadManifest:
    """Tests for loading manifest from save directories."""

    def _create_save_dir(self, tmp_dir, manifest_data, overlay_content=b"fake overlay"):
        """Helper to create a save directory structure."""
        save_dir = tmp_dir / "test-save"
        save_dir.mkdir(exist_ok=True)
        (save_dir / "manifest.json").write_text(json.dumps(manifest_data))
        overlays_dir = save_dir / "overlays"
        overlays_dir.mkdir(exist_ok=True)
        (overlays_dir / "0.qcow2").write_bytes(overlay_content)
        return save_dir

    def test_load_valid_manifest(self, tmp_dir):
        """Test loading a valid manifest from save directory."""
        manifest_data = {
            "version": 6,
            "config": {"image": "ubuntu"},
        }
        save_dir = self._create_save_dir(tmp_dir, manifest_data)

        result = SaveManifest.model_validate_json((save_dir / "manifest.json").read_text())

        assert result.version == 6
        assert result.config.image == "ubuntu"

    def test_missing_manifest(self, tmp_dir):
        """Test error when manifest is missing from save directory."""
        save_dir = tmp_dir / "empty-save"
        save_dir.mkdir()
        overlays_dir = save_dir / "overlays"
        overlays_dir.mkdir()
        (overlays_dir / "0.qcow2").write_bytes(b"fake")

        with pytest.raises(ValueError, match=r"Missing manifest\.json"):
            ImageResolver().validate_save(save_dir)

    def test_invalid_json(self, tmp_dir):
        """Test error when manifest is invalid JSON."""
        save_dir = tmp_dir / "bad-json-save"
        save_dir.mkdir()
        (save_dir / "manifest.json").write_text("not valid json")
        overlays_dir = save_dir / "overlays"
        overlays_dir.mkdir()
        (overlays_dir / "0.qcow2").write_bytes(b"fake")

        with pytest.raises(ValueError):
            SaveManifest.model_validate_json((save_dir / "manifest.json").read_text())


class TestValidateSave:
    """Tests for ImageResolver.validate_save."""

    @pytest.fixture
    def valid_save(self, tmp_dir):
        """Create a valid save directory that can be validated."""
        save_dir = tmp_dir / "my-save"
        save_dir.mkdir()
        manifest = {
            "version": SAVE_VERSION,
            "config": {"image": "test-image"},
        }
        (save_dir / "manifest.json").write_text(json.dumps(manifest))
        overlays_dir = save_dir / "overlays"
        overlays_dir.mkdir()
        (overlays_dir / "0.qcow2").write_bytes(b"fake overlay content")
        return save_dir

    def test_valid_save_passes_validation(self, valid_save):
        """Test that a valid save directory passes validation."""
        result = ImageResolver().validate_save(valid_save)
        assert result.version == SAVE_VERSION
        assert result.config.image == "test-image"

    def test_not_a_directory(self, tmp_dir):
        """Test error when path is not a directory."""
        file_path = tmp_dir / "not-a-dir.txt"
        file_path.write_text("not a directory")

        with pytest.raises(ValueError, match="not a directory"):
            ImageResolver().validate_save(file_path)

    def test_missing_overlays_dir(self, tmp_dir):
        """Test error when overlays directory is missing."""
        save_dir = tmp_dir / "no-overlays"
        save_dir.mkdir()
        manifest = {
            "version": SAVE_VERSION,
            "config": {"image": "test"},
        }
        (save_dir / "manifest.json").write_text(json.dumps(manifest))

        with pytest.raises(ValueError, match="Missing overlays"):
            ImageResolver().validate_save(save_dir)

    def test_version_too_new(self, tmp_dir):
        """Test error when save version is too new."""
        save_dir = tmp_dir / "new-version"
        save_dir.mkdir()
        manifest = {
            "version": SAVE_VERSION + 1,
            "config": {"image": "test"},
        }
        (save_dir / "manifest.json").write_text(json.dumps(manifest))
        overlays_dir = save_dir / "overlays"
        overlays_dir.mkdir()
        (overlays_dir / "0.qcow2").write_bytes(b"fake")

        with pytest.raises(ValueError, match="newer than supported"):
            ImageResolver().validate_save(save_dir)

    def test_empty_overlays_dir(self, tmp_dir):
        """Test error when overlays directory is empty."""
        save_dir = tmp_dir / "empty-overlays"
        save_dir.mkdir()
        manifest = {
            "version": SAVE_VERSION,
            "config": {"image": "test"},
        }
        (save_dir / "manifest.json").write_text(json.dumps(manifest))
        overlays_dir = save_dir / "overlays"
        overlays_dir.mkdir()

        with pytest.raises(ValueError, match="No overlay files found"):
            ImageResolver().validate_save(save_dir)


class TestSandboxSaveMethod:
    """Tests for Sandbox.save() method."""

    @pytest.mark.asyncio
    async def test_save_requires_running(self, tmp_dir):
        """Test that save() raises when sandbox is not running."""
        from quicksand_core import Sandbox

        sandbox = Sandbox(image="ubuntu")

        with pytest.raises(RuntimeError, match="non-running"):
            await sandbox.save("my-save", workspace=tmp_dir)


class TestSandboxValidateSaveMethod:
    """Tests for Sandbox.validate_save() method."""

    def test_validate_missing_save(self, tmp_dir):
        """Test that validate_save() raises when save doesn't exist."""
        from quicksand_core import Sandbox

        with pytest.raises(FileNotFoundError, match="Save not found"):
            Sandbox.validate_save(tmp_dir / "nonexistent")
