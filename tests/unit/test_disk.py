"""Unit tests for OverlayManager."""

from __future__ import annotations

import json
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from quicksand_core._types import GuestCommands
from quicksand_core.qemu.overlay import OverlayManager
from quicksand_core.sandbox import ExecuteResult
from quicksand_core.sandbox._lifecycle import _LifecycleMixin


class _MockSandbox(_LifecycleMixin):
    """Minimal concrete sandbox for testing _LifecycleMixin filesystem expansion."""

    def __init__(self, execute_fn):
        self._execute_fn = execute_fn

    # Protocol requirements (flat fields)
    config = None
    _is_running = True
    _process_manager = None
    _smb_server = None
    _progress_callback = None
    _overlay_manager = None
    _runtime_info = None
    _image = None
    _accel = None
    _overlay_path = None
    _temp_dir = None
    _agent_client = None
    _agent_port = None
    _agent_token = None
    _qmp_client = None
    _qmp_port = None
    _qmp_checkpoints: ClassVar[list[str]] = []
    _vnc_port = None
    _save_name = None
    _workspace = None
    _dynamic_mounts: ClassVar[list] = []

    @property
    def is_running(self) -> bool:
        return True

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
        return self._execute_fn(command, timeout)

    async def _send_request(self, method, params, timeout=30):
        raise NotImplementedError

    async def _graceful_shutdown(self):
        raise NotImplementedError

    async def _mount_shares(self):
        raise NotImplementedError

    async def _cleanup_mounts(self):
        raise NotImplementedError

    async def save(self, name, *, workspace=None, compress=False, delete_checkpoints=False):
        raise NotImplementedError


class TestCreateOverlay:
    """Tests for OverlayManager.create_overlay."""

    def test_creates_overlay_from_base(self, tmp_dir):
        """Test creating a basic overlay backed by a base image."""
        qemu_img = tmp_dir / "qemu-img"
        qemu_img.touch()
        image_path = tmp_dir / "base.qcow2"
        image_path.write_bytes(b"fake-image")
        overlay_path = tmp_dir / "overlay.qcow2"

        dm = OverlayManager(qemu_img)
        with patch("subprocess.run") as mock_run:
            dm.create_overlay(image_path, overlay_path)

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert str(qemu_img) in args
        assert "create" in args
        assert str(overlay_path) in args

    def test_creates_overlay_with_disk_size(self, tmp_dir):
        """Test creating an overlay and resizing it."""
        qemu_img = tmp_dir / "qemu-img"
        qemu_img.touch()
        image_path = tmp_dir / "base.qcow2"
        image_path.write_bytes(b"fake-image")
        overlay_path = tmp_dir / "overlay.qcow2"

        dm = OverlayManager(qemu_img)
        with patch("subprocess.run") as mock_run:
            dm.create_overlay(image_path, overlay_path, disk_size="4G")

        # Should be called twice: create + resize
        assert mock_run.call_count == 2
        resize_args = mock_run.call_args_list[1][0][0]
        assert "resize" in resize_args
        assert "4G" in resize_args

    def test_creates_overlay_from_restore_chain(self, tmp_dir):
        """Test creating overlay from restore chain creates fresh overlay on top."""
        qemu_img = tmp_dir / "qemu-img"
        qemu_img.touch()
        image_path = tmp_dir / "base.qcow2"
        image_path.write_bytes(b"fake-image")
        overlay_path = tmp_dir / "overlay.qcow2"

        chain_overlay = tmp_dir / "restored.qcow2"
        chain_overlay.write_bytes(b"checkpoint-data")

        dm = OverlayManager(qemu_img)

        # Mock get_backing_file to return base image (chain already correct)
        def mock_run_side_effect(cmd, **kwargs):
            if "info" in cmd:
                mock_result = MagicMock()
                mock_result.stdout = json.dumps({"backing-filename": str(image_path.absolute())})
                return mock_result
            return MagicMock()

        with patch("subprocess.run", side_effect=mock_run_side_effect) as mock_run:
            dm.create_overlay(
                image_path,
                overlay_path,
                restore_chain=[chain_overlay],
            )

        # Should call qemu-img info (get_backing_file) then qemu-img create
        create_calls = [c for c in mock_run.call_args_list if "create" in c[0][0]]
        assert len(create_calls) == 1
        create_args = create_calls[0][0][0]
        assert "-b" in create_args
        assert str(chain_overlay.absolute()) in create_args


class TestGetBackingFile:
    """Tests for OverlayManager.get_backing_file."""

    def test_returns_backing_filename(self, tmp_dir):
        """Test parsing backing filename from qemu-img info output."""
        qemu_img = tmp_dir / "qemu-img"
        qemu_img.touch()
        overlay = tmp_dir / "overlay.qcow2"
        overlay.touch()

        dm = OverlayManager(qemu_img)
        mock_result = MagicMock()
        mock_result.stdout = json.dumps({"backing-filename": "/path/to/base.qcow2"})

        with patch("subprocess.run", return_value=mock_result):
            result = dm.get_backing_file(overlay)

        assert result == "/path/to/base.qcow2"

    def test_returns_none_when_no_backing(self, tmp_dir):
        """Test returns None for overlays without a backing file."""
        qemu_img = tmp_dir / "qemu-img"
        qemu_img.touch()
        overlay = tmp_dir / "overlay.qcow2"
        overlay.touch()

        dm = OverlayManager(qemu_img)
        mock_result = MagicMock()
        mock_result.stdout = json.dumps({"format": "qcow2"})

        with patch("subprocess.run", return_value=mock_result):
            result = dm.get_backing_file(overlay)

        assert result is None


class TestGetOverlayChain:
    """Tests for OverlayManager.get_overlay_chain."""

    def test_single_overlay_chain(self, tmp_dir):
        """Chain with one overlay directly backed by base."""
        qemu_img = tmp_dir / "qemu-img"
        qemu_img.touch()
        base = tmp_dir / "base.qcow2"
        base.touch()
        overlay = tmp_dir / "overlay.qcow2"
        overlay.touch()

        dm = OverlayManager(qemu_img)
        with patch.object(dm, "get_backing_file", return_value=str(base)):
            chain = dm.get_overlay_chain(overlay, base)

        assert chain == [overlay]

    def test_multi_overlay_chain(self, tmp_dir):
        """Chain with multiple overlays returns bottom-to-top order."""
        qemu_img = tmp_dir / "qemu-img"
        qemu_img.touch()
        base = tmp_dir / "base.qcow2"
        base.touch()
        overlay_0 = tmp_dir / "overlay-0.qcow2"
        overlay_0.touch()
        overlay_1 = tmp_dir / "overlay-1.qcow2"
        overlay_1.touch()

        dm = OverlayManager(qemu_img)

        def mock_backing(path):
            if path == overlay_1:
                return str(overlay_0)
            if path == overlay_0:
                return str(base)
            return None

        with patch.object(dm, "get_backing_file", side_effect=mock_backing):
            chain = dm.get_overlay_chain(overlay_1, base)

        assert chain == [overlay_0, overlay_1]


class TestPrepareRestoredChain:
    """Tests for OverlayManager._prepare_restored_chain."""

    def test_rebases_when_paths_differ(self, tmp_dir):
        """Calls qemu-img rebase -u when backing path doesn't match."""
        qemu_img = tmp_dir / "qemu-img"
        qemu_img.touch()
        base = tmp_dir / "base.qcow2"
        base.touch()
        overlay = tmp_dir / "overlay.qcow2"
        overlay.touch()

        dm = OverlayManager(qemu_img)

        def mock_run_side_effect(cmd, **kwargs):
            mock_result = MagicMock()
            if "info" in cmd:
                mock_result.stdout = json.dumps({"backing-filename": "/old/tmp/base.qcow2"})
            return mock_result

        with patch("subprocess.run", side_effect=mock_run_side_effect) as mock_run:
            dm._prepare_restored_chain([overlay], base)

        rebase_calls = [c for c in mock_run.call_args_list if "rebase" in c[0][0]]
        assert len(rebase_calls) == 1
        assert "-u" in rebase_calls[0][0][0]

    def test_skips_rebase_when_path_matches(self, tmp_dir):
        """Does not rebase when backing path already matches."""
        qemu_img = tmp_dir / "qemu-img"
        qemu_img.touch()
        base = tmp_dir / "base.qcow2"
        base.touch()
        overlay = tmp_dir / "overlay.qcow2"
        overlay.touch()

        dm = OverlayManager(qemu_img)

        def mock_run_side_effect(cmd, **kwargs):
            mock_result = MagicMock()
            if "info" in cmd:
                mock_result.stdout = json.dumps({"backing-filename": str(base.absolute())})
            return mock_result

        with patch("subprocess.run", side_effect=mock_run_side_effect) as mock_run:
            dm._prepare_restored_chain([overlay], base)

        rebase_calls = [c for c in mock_run.call_args_list if "rebase" in c[0][0]]
        assert len(rebase_calls) == 0


class TestResizeOverlay:
    """Tests for OverlayManager.resize_overlay."""

    def test_resize_overlay(self, tmp_dir):
        """Test resizing an overlay."""
        qemu_img = tmp_dir / "qemu-img"
        qemu_img.touch()
        overlay_path = tmp_dir / "overlay.qcow2"
        overlay_path.touch()

        dm = OverlayManager(qemu_img)
        with patch("subprocess.run") as mock_run:
            dm.resize_overlay(overlay_path, "8G")

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "resize" in args
        assert "8G" in args


class TestExpandGuestFilesystem:
    """Tests for _LifecycleMixin._expand_guest_filesystem."""

    @pytest.mark.asyncio
    async def test_expands_partitioned_disk(self):
        """Test expanding a partitioned disk layout."""
        calls = []

        def mock_execute(cmd: str, timeout: float) -> ExecuteResult:
            calls.append(cmd)
            if cmd == GuestCommands.DETECT_DISK_LAYOUT:
                return ExecuteResult(stdout="partitioned\n", stderr="", exit_code=0)
            return ExecuteResult(stdout="ok", stderr="", exit_code=0)

        sb = _MockSandbox(mock_execute)
        await sb._expand_guest_filesystem()

        assert GuestCommands.DETECT_DISK_LAYOUT in calls
        assert GuestCommands.GROWPART in calls
        assert GuestCommands.RESIZE_PARTITION in calls

    @pytest.mark.asyncio
    async def test_expands_whole_disk(self):
        """Test expanding a whole-disk filesystem."""
        calls = []

        def mock_execute(cmd: str, timeout: float) -> ExecuteResult:
            calls.append(cmd)
            if cmd == GuestCommands.DETECT_DISK_LAYOUT:
                return ExecuteResult(stdout="whole\n", stderr="", exit_code=0)
            return ExecuteResult(stdout="ok", stderr="", exit_code=0)

        sb = _MockSandbox(mock_execute)
        await sb._expand_guest_filesystem()

        assert GuestCommands.DETECT_DISK_LAYOUT in calls
        assert GuestCommands.RESIZE_WHOLE_DISK in calls
        assert GuestCommands.GROWPART not in calls
