"""Unit tests for checkpoint_ops module."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from quicksand_core._types import (
    FilePatterns,
    ResolvedAccelerator,
    ResolvedImage,
    SandboxConfig,
    SaveManifest,
)
from quicksand_core.qemu.save import SaveWriter


class TestCreateSave:
    """Tests for SaveWriter."""

    def test_creates_save_directory(self, tmp_dir):
        """Test creating a save directory."""
        # Set up fake files
        overlay = tmp_dir / "overlay.qcow2"
        overlay.write_bytes(b"overlay-data")

        manifest = SaveManifest(
            version=5,
            config=SandboxConfig(image="test"),
        )

        writer = SaveWriter("my-save", workspace=tmp_dir)
        writer.write(overlay_chain=[overlay], manifest=manifest)

        save_path = tmp_dir / "my-save"
        assert save_path.is_dir()
        assert save_path.exists()

        # Verify directory contents
        manifest_path = save_path / FilePatterns.MANIFEST
        assert manifest_path.exists()
        overlay_path = save_path / "overlays" / "0.qcow2"
        assert overlay_path.exists()

        # Check manifest
        manifest_data = json.loads(manifest_path.read_text())
        assert manifest_data["config"]["image"] == "test"

    def test_save_path_is_directory(self, tmp_dir):
        """Test that save creates a directory at the given path."""
        overlay = tmp_dir / "overlay.qcow2"
        overlay.write_bytes(b"data")

        manifest = SaveManifest(
            version=5,
            config=SandboxConfig(image="test"),
        )

        writer = SaveWriter("my-save", workspace=tmp_dir)
        writer.write(overlay_chain=[overlay], manifest=manifest)

        save_path = tmp_dir / "my-save"
        assert save_path == tmp_dir / "my-save"
        assert save_path.is_dir()

    def test_overwrites_existing_save(self, tmp_dir):
        """Test that SaveWriter atomically replaces an existing save."""
        overlay = tmp_dir / "overlay.qcow2"
        overlay.write_bytes(b"data")

        existing = tmp_dir / "my-save"
        existing.mkdir()
        (existing / "old-file.txt").write_text("should be replaced")

        manifest = SaveManifest(
            version=5,
            config=SandboxConfig(image="test"),
        )

        writer = SaveWriter("my-save", workspace=tmp_dir)
        writer.write(overlay_chain=[overlay], manifest=manifest)

        assert existing.is_dir()
        assert not (existing / "old-file.txt").exists()

    def test_does_not_call_graceful_shutdown(self, tmp_dir):
        """Test that create does not perform graceful shutdown (caller's responsibility)."""
        overlay = tmp_dir / "overlay.qcow2"
        overlay.write_bytes(b"data")

        manifest = SaveManifest(
            version=5,
            config=SandboxConfig(image="test"),
        )

        # Should succeed without any shutdown machinery
        writer = SaveWriter("cp", workspace=tmp_dir)
        writer.write(overlay_chain=[overlay], manifest=manifest)
        save_path = tmp_dir / "cp"
        assert save_path.exists()


class TestLegacyTarSupport:
    """Tests that uncompressed .tar saves (pre-gzip) can still be read."""

    def test_load_manifest_from_legacy_uncompressed_tar(self, tmp_dir):
        """load_manifest can read an uncompressed .tar file (legacy format)."""
        # Legacy tar support is gone -- saves are now directories only.
        # This test verifies that the ImageResolver rejects tar files
        # with a clear error message.
        from quicksand_core.qemu.image_resolver import ImageResolver

        overlay = tmp_dir / "overlay.qcow2"
        overlay.write_bytes(b"overlay-data")

        # Create an uncompressed .tar manually (simulating legacy format)
        import io

        legacy_tar = tmp_dir / "legacy.tar"
        manifest = {
            "version": 5,
            "config": {"image": "ubuntu"},
        }
        manifest_json = json.dumps(manifest).encode()

        with tarfile.open(legacy_tar, "w") as tar:
            info = tarfile.TarInfo(name="manifest.json")
            info.size = len(manifest_json)
            tar.addfile(info, io.BytesIO(manifest_json))
            tar.add(overlay, arcname="overlays/0.qcow2")

        # Tar files are no longer supported -- should fail to resolve
        with pytest.raises(RuntimeError, match="Tar save files are no longer supported"):
            ImageResolver().resolve(str(legacy_tar))


class TestHotSave:
    """Tests for _SaveMixin._hot_save() -- QMP pivot, no rebase."""

    def _make_mock_sandbox(self, tmp_dir: Path):
        """Create a minimal _SaveMixin sandbox for hot-save testing."""
        from quicksand_core.sandbox._saves import _SaveMixin

        overlay = tmp_dir / "overlay.qcow2"
        overlay.write_bytes(b"overlay-data")
        image = tmp_dir / "image.qcow2"
        image.write_bytes(b"base-data")

        class _MockSandbox(_SaveMixin):
            config = SandboxConfig(image="ubuntu")
            _smb_server = None
            _progress_callback = None
            _save_name = None
            _workspace = None
            _dynamic_mounts: ClassVar[list] = []

            def __init__(self) -> None:
                from quicksand_core.host import Accelerator

                self._is_running = True
                self._image = ResolvedImage(
                    name="ubuntu",
                    chain=[image],
                )
                self._accel = ResolvedAccelerator(
                    accel=Accelerator.HVF,
                )
                self._overlay_path = overlay
                self._temp_dir = tmp_dir
                self._agent_client = None
                self._agent_port = None
                self._agent_token = None
                self._qmp_client = AsyncMock()
                self._qmp_port = 12345
                self._qmp_checkpoints: list[str] = []
                self._vnc_port = None
                self._overlay_manager = MagicMock()
                self._process_manager = MagicMock()
                self._runtime_info = None

            @property
            def is_running(self) -> bool:
                return self._is_running

            async def _graceful_shutdown(self) -> None:
                self._is_running = False

            async def _send_request(self, method, params, timeout=30):
                raise NotImplementedError

            async def _mount_shares(self):
                raise NotImplementedError

            async def _cleanup_mounts(self):
                raise NotImplementedError

            async def execute(
                self,
                command,
                timeout=30,
                cwd=None,
                shell="",
                on_stdout=None,
                on_stderr=None,
                exclusive=False,
            ):
                return MagicMock(stdout="", stderr="", exit_code=0)

        return _MockSandbox(), overlay, image

    @pytest.mark.asyncio
    async def test_qmp_snapshot_called(self, tmp_dir):
        """save() calls blockdev-snapshot-sync via QMP."""
        sb, overlay, _image = self._make_mock_sandbox(tmp_dir)
        sb._overlay_manager.get_overlay_chain.return_value = [overlay]

        with patch("quicksand_core.qemu.save.SaveWriter") as MockWriter:
            MockWriter.return_value.write.return_value = MagicMock()
            await sb.save("cp", workspace=tmp_dir)

        calls = sb._qmp_client.execute.call_args_list
        snapshot_calls = [c for c in calls if c[0][0] == "blockdev-snapshot-sync"]
        assert len(snapshot_calls) == 1

    @pytest.mark.asyncio
    async def test_flush_called_before_snapshot(self, tmp_dir):
        """save() flushes QEMU block layer before blockdev-snapshot-sync."""
        sb, overlay, _image = self._make_mock_sandbox(tmp_dir)
        sb._overlay_manager.get_overlay_chain.return_value = [overlay]

        with patch("quicksand_core.qemu.save.SaveWriter") as MockWriter:
            MockWriter.return_value.write.return_value = MagicMock()
            await sb.save("cp", workspace=tmp_dir)

        calls = sb._qmp_client.execute.call_args_list
        call_strs = [str(c) for c in calls]
        flush_idx = next(i for i, c in enumerate(call_strs) if "flush" in c)
        snapshot_idx = next(i for i, c in enumerate(call_strs) if "blockdev-snapshot-sync" in c)
        assert flush_idx < snapshot_idx

    @pytest.mark.asyncio
    async def test_snapshot_overlay_chain_contains_original(self, tmp_dir):
        """SaveWriter.write receives overlay_chain containing the frozen overlay."""
        sb, overlay, _image = self._make_mock_sandbox(tmp_dir)
        sb._overlay_manager.get_overlay_chain.return_value = [overlay]

        with patch("quicksand_core.qemu.save.SaveWriter") as MockWriter:
            MockWriter.return_value.write.return_value = MagicMock()
            await sb.save("cp", workspace=tmp_dir)
            passed_chain = MockWriter.return_value.write.call_args.kwargs["overlay_chain"]

        assert passed_chain == [overlay]

    @pytest.mark.asyncio
    async def test_disk_paths_updated_after_save(self, tmp_dir):
        """After save, VM overlay_path points to the new overlay file."""
        sb, overlay, _image = self._make_mock_sandbox(tmp_dir)
        sb._overlay_manager.get_overlay_chain.return_value = [overlay]

        with patch("quicksand_core.qemu.save.SaveWriter") as MockWriter:
            MockWriter.return_value.write.return_value = MagicMock()
            await sb.save("cp", workspace=tmp_dir)

        assert sb._overlay_path != overlay  # pivoted to new overlay

    @pytest.mark.asyncio
    async def test_get_overlay_chain_called(self, tmp_dir):
        """save() walks the overlay chain via get_overlay_chain."""
        sb, overlay, image = self._make_mock_sandbox(tmp_dir)
        sb._overlay_manager.get_overlay_chain.return_value = [overlay]

        with patch("quicksand_core.qemu.save.SaveWriter") as MockWriter:
            MockWriter.return_value.write.return_value = MagicMock()
            await sb.save("cp", workspace=tmp_dir)

        sb._overlay_manager.get_overlay_chain.assert_called_once_with(overlay, image)

    @pytest.mark.asyncio
    async def test_save_raises_with_checkpoints_by_default(self, tmp_dir):
        """save() raises RuntimeError if checkpoint tags exist and delete_checkpoints=False."""
        sb, _overlay, _image = self._make_mock_sandbox(tmp_dir)
        sb._qmp_checkpoints = ["v1", "v2"]

        with pytest.raises(RuntimeError, match="active checkpoint snapshots"):
            await sb.save("cp", workspace=tmp_dir)

    @pytest.mark.asyncio
    async def test_save_deletes_checkpoints_when_opted_in(self, tmp_dir):
        """save(delete_checkpoints=True) calls delvm for each tag before freezing."""
        sb, overlay, _image = self._make_mock_sandbox(tmp_dir)
        sb._qmp_checkpoints = ["v1", "v2"]
        sb._overlay_manager.get_overlay_chain.return_value = [overlay]

        with patch("quicksand_core.qemu.save.SaveWriter") as MockWriter:
            MockWriter.return_value.write.return_value = MagicMock()
            await sb.save("cp", workspace=tmp_dir, delete_checkpoints=True)

        calls = [str(c) for c in sb._qmp_client.execute.call_args_list]
        assert any("delvm v1" in c for c in calls)
        assert any("delvm v2" in c for c in calls)
        assert sb._qmp_checkpoints == []


class TestCheckpointRevert:
    """Tests for checkpoint() and revert()."""

    def _make_mock_sandbox(self, tmp_dir: Path):
        from quicksand_core.sandbox._checkpoints import _CheckpointMixin

        overlay = tmp_dir / "overlay.qcow2"
        overlay.write_bytes(b"overlay-data")
        image = tmp_dir / "image.qcow2"
        image.write_bytes(b"base-data")

        class _MockSandbox(_CheckpointMixin):
            config = SandboxConfig(image="ubuntu")
            _smb_server = None
            _progress_callback = None
            _save_name = None
            _workspace = None
            _dynamic_mounts: ClassVar[list] = []

            def __init__(self) -> None:
                from quicksand_core.host import Accelerator

                self._is_running = True
                self._image = ResolvedImage(
                    name="ubuntu",
                    chain=[image],
                )
                self._accel = ResolvedAccelerator(
                    accel=Accelerator.HVF,
                )
                self._overlay_path = overlay
                self._temp_dir = tmp_dir
                self._agent_client = None
                self._agent_port = None
                self._agent_token = None
                self._qmp_client = AsyncMock()
                self._qmp_port = 12345
                self._qmp_checkpoints: list[str] = []
                self._vnc_port = None
                self._overlay_manager = MagicMock()
                self._process_manager = MagicMock()
                self._runtime_info = None

            @property
            def is_running(self) -> bool:
                return self._is_running

            async def _graceful_shutdown(self) -> None:
                self._is_running = False

            async def _send_request(self, method, params, timeout=30):
                raise NotImplementedError

            async def _mount_shares(self):
                raise NotImplementedError

            async def _cleanup_mounts(self):
                raise NotImplementedError

            async def execute(
                self,
                command,
                timeout=30,
                cwd=None,
                shell="",
                on_stdout=None,
                on_stderr=None,
                exclusive=False,
            ):
                return MagicMock(stdout="", stderr="", exit_code=0)

        return _MockSandbox()

    @pytest.mark.asyncio
    async def test_checkpoint_tracks_tag(self, tmp_dir):
        """checkpoint() appends the tag to checkpoints."""
        sb = self._make_mock_sandbox(tmp_dir)
        await sb.checkpoint("before-install")
        assert "before-install" in sb._qmp_checkpoints

    @pytest.mark.asyncio
    async def test_checkpoint_no_duplicates(self, tmp_dir):
        """checkpoint() with the same tag doesn't duplicate it."""
        sb = self._make_mock_sandbox(tmp_dir)
        await sb.checkpoint("v1")
        await sb.checkpoint("v1")
        assert sb._qmp_checkpoints.count("v1") == 1

    @pytest.mark.asyncio
    async def test_checkpoint_calls_qmp(self, tmp_dir):
        """checkpoint() sends human-monitor-command savevm <tag> via QMP."""
        sb = self._make_mock_sandbox(tmp_dir)
        await sb.checkpoint("v1")
        call_kwargs = sb._qmp_client.execute.call_args
        assert call_kwargs[0][0] == "human-monitor-command"
        assert call_kwargs[1]["command-line"] == "savevm v1"

    @pytest.mark.asyncio
    async def test_revert_calls_qmp(self, tmp_dir):
        """revert() sends human-monitor-command loadvm <tag> via QMP."""
        sb = self._make_mock_sandbox(tmp_dir)
        await sb.checkpoint("v1")
        sb._qmp_client.reset_mock()
        await sb.revert("v1")
        call_kwargs = sb._qmp_client.execute.call_args
        assert call_kwargs[0][0] == "human-monitor-command"
        assert call_kwargs[1]["command-line"] == "loadvm v1"

    @pytest.mark.asyncio
    async def test_revert_unknown_tag_raises(self, tmp_dir):
        """revert() raises ValueError for an untracked tag."""
        sb = self._make_mock_sandbox(tmp_dir)
        with pytest.raises(ValueError, match="No checkpoint 'v1'"):
            await sb.revert("v1")

    @pytest.mark.asyncio
    async def test_revert_not_running_raises(self, tmp_dir):
        """revert() raises RuntimeError if sandbox is not running."""
        sb = self._make_mock_sandbox(tmp_dir)
        sb._is_running = False
        with pytest.raises(RuntimeError, match="non-running"):
            await sb.revert("v1")
