"""Tests for save creation."""

from __future__ import annotations

import pytest
from quicksand import Sandbox

from tests.conftest import skip_no_qemu


@pytest.mark.integration
@pytest.mark.slow
class TestSaveCreate:
    """Save creation tests."""

    @skip_no_qemu
    @pytest.mark.asyncio
    async def test_save_creates_files(self, real_image, real_kernel, tmp_dir):
        """Test that save() creates a directory with the expected contents."""
        async with Sandbox(image="ubuntu") as sb:
            await sb.execute("echo 'test' > /tmp/marker.txt")
            manifest = await sb.save("my-save", workspace=tmp_dir)

        # Save is a directory
        save_path = tmp_dir / "my-save"
        assert save_path.is_dir()
        assert (save_path / "manifest.json").exists()
        assert (save_path / "overlays" / "0.qcow2").exists()

        assert manifest.version == 6
        assert manifest.config.image == "ubuntu"

    @skip_no_qemu
    @pytest.mark.asyncio
    async def test_save_overwrites_existing(self, real_image, real_kernel, tmp_dir):
        """Test that save atomically replaces an existing save."""
        existing = tmp_dir / "my-save"
        existing.mkdir()
        (existing / "old-file.txt").write_text("should be replaced")

        async with Sandbox(image="ubuntu") as sb:
            manifest = await sb.save("my-save", workspace=tmp_dir)

        assert existing.is_dir()
        assert not (existing / "old-file.txt").exists()
        assert (existing / "manifest.json").exists()
        assert manifest.version == 6

    @skip_no_qemu
    @pytest.mark.asyncio
    async def test_validate_save(self, real_image, real_kernel, tmp_dir):
        """Test validate_save returns correct manifest."""
        async with Sandbox(image="ubuntu") as sb:
            await sb.save("my-save", workspace=tmp_dir)

        save_path = tmp_dir / "my-save"
        manifest = Sandbox.validate_save(save_path)
        assert manifest.version == 6
        assert manifest.config.image == "ubuntu"
