"""Unit tests for _MountMixin (CIFS-based mounts)."""

from __future__ import annotations

import contextlib
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from quicksand_core._types import Mount, MountHandle, MountOptions, NetworkMode
from quicksand_core.sandbox import ExecuteResult, SandboxConfig
from quicksand_core.sandbox._mounts import _MountMixin


class _MockSandbox(_MountMixin):
    """Minimal concrete sandbox for testing _MountMixin in isolation."""

    def __init__(self, execute_fn, mounts, *, network_mode=NetworkMode.FULL):
        self.config = SandboxConfig(
            image="ubuntu",
            mounts=mounts,
            network_mode=network_mode,
        )
        self._smb_server = None
        self._dynamic_mounts: list[MountHandle] = []
        self._execute_fn = execute_fn

    # Protocol requirements (flat fields)
    _is_running = True
    _process_manager = None
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

    async def save(self, name, *, workspace=None, compress=False, delete_checkpoints=False):
        raise NotImplementedError

    @property
    def vnc_port(self):
        return None

    async def type_text(self, text):
        raise NotImplementedError

    async def press_key(self, *keys):
        raise NotImplementedError

    async def mouse_move(self, x, y):
        raise NotImplementedError

    async def mouse_click(self, button="left", *, double=False):
        raise NotImplementedError

    async def screenshot(self, path):
        raise NotImplementedError

    async def query_display_size(self):
        raise NotImplementedError

    async def query_mouse_position(self):
        raise NotImplementedError


class TestMountShares:
    """Tests for _MountMixin._mount_shares (boot-time CIFS mounts)."""

    @pytest.mark.asyncio
    @patch("quicksand_core.sandbox._mounts.create_smb_server")
    @patch("quicksand_core.sandbox._mounts.asyncio.sleep", new_callable=AsyncMock)
    async def test_mounts_single_share(self, mock_sleep, mock_create_server):
        """Test mounting a single share via CIFS."""
        commands = []

        def mock_execute(cmd: str, timeout: float) -> ExecuteResult:
            commands.append(cmd)
            return ExecuteResult(stdout="", stderr="", exit_code=0)

        mock_server = MagicMock()
        mock_server.add_share.return_value = "QUICKSAND0"
        mock_server.credentials = ("guest", "")
        mock_create_server.return_value = mock_server

        mounts = [Mount(host="/host/data", guest="/mnt/data")]
        sb = _MockSandbox(mock_execute, mounts)
        await sb._mount_shares()

        # Should have called add_share on the SMB server
        mock_server.add_share.assert_called_once_with("/host/data", False)
        # Should have mkdir + CIFS mount commands
        assert any("mkdir -p /mnt/data" in c for c in commands)
        assert any("mount -t cifs" in c and "QUICKSAND0" in c for c in commands)

    @pytest.mark.asyncio
    @patch("quicksand_core.sandbox._mounts.create_smb_server")
    @patch("quicksand_core.sandbox._mounts.asyncio.sleep", new_callable=AsyncMock)
    async def test_mounts_readonly_share(self, mock_sleep, mock_create_server):
        """Test mounting a readonly share via CIFS."""
        commands = []

        def mock_execute(cmd: str, timeout: float) -> ExecuteResult:
            commands.append(cmd)
            return ExecuteResult(stdout="", stderr="", exit_code=0)

        mock_server = MagicMock()
        mock_server.add_share.return_value = "QUICKSAND0"
        mock_server.credentials = ("guest", "")
        mock_create_server.return_value = mock_server

        mounts = [Mount(host="/host/data", guest="/mnt/data", readonly=True)]
        sb = _MockSandbox(mock_execute, mounts)
        await sb._mount_shares()

        mock_server.add_share.assert_called_once_with("/host/data", True)
        mount_cmd = next(c for c in commands if "mount -t cifs" in c)
        assert ",ro" in mount_cmd

    @pytest.mark.asyncio
    @patch("quicksand_core.sandbox._mounts.create_smb_server")
    @patch("quicksand_core.sandbox._mounts.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_failure(self, mock_sleep, mock_create_server):
        """Test that mount retries on transient failures."""
        attempt = [0]

        def mock_execute(cmd: str, timeout: float) -> ExecuteResult:
            if "mount -t cifs" in cmd:
                attempt[0] += 1
                if attempt[0] < 3:
                    return ExecuteResult(stdout="", stderr="mount error", exit_code=1)
            return ExecuteResult(stdout="", stderr="", exit_code=0)

        mock_server = MagicMock()
        mock_server.add_share.return_value = "QUICKSAND0"
        mock_server.credentials = ("guest", "")
        mock_create_server.return_value = mock_server

        mounts = [Mount(host="/host/data", guest="/mnt/data")]
        sb = _MockSandbox(mock_execute, mounts)
        await sb._mount_shares()

        assert attempt[0] == 3  # Succeeded on third attempt

    @pytest.mark.asyncio
    @patch("quicksand_core.sandbox._mounts.create_smb_server")
    @patch("quicksand_core.sandbox._mounts.asyncio.sleep", new_callable=AsyncMock)
    async def test_raises_after_max_retries(self, mock_sleep, mock_create_server):
        """Test that mount raises after exhausting retries."""

        def mock_execute(cmd: str, timeout: float) -> ExecuteResult:
            if "mount -t cifs" in cmd:
                return ExecuteResult(stdout="", stderr="mount error", exit_code=1)
            return ExecuteResult(stdout="", stderr="", exit_code=0)

        mock_server = MagicMock()
        mock_server.add_share.return_value = "QUICKSAND0"
        mock_server.credentials = ("guest", "")
        mock_create_server.return_value = mock_server

        mounts = [Mount(host="/host/data", guest="/mnt/data")]
        sb = _MockSandbox(mock_execute, mounts)

        with pytest.raises(RuntimeError, match="Failed to mount CIFS share"):
            await sb._mount_shares()

    @pytest.mark.asyncio
    @patch("quicksand_core.sandbox._mounts.create_smb_server")
    @patch("quicksand_core.sandbox._mounts.asyncio.sleep", new_callable=AsyncMock)
    async def test_unmounts_on_failure(self, mock_sleep, mock_create_server):
        """Test that successful mounts are rolled back on failure."""
        commands = []
        share_counter = [0]

        def mock_execute(cmd: str, timeout: float) -> ExecuteResult:
            commands.append(cmd)
            # Second CIFS mount always fails
            if "mount -t cifs" in cmd and "QUICKSAND1" in cmd:
                return ExecuteResult(stdout="", stderr="error", exit_code=1)
            return ExecuteResult(stdout="", stderr="", exit_code=0)

        mock_server = MagicMock()
        mock_server.credentials = ("guest", "")

        def add_share(host_path, readonly):
            name = f"QUICKSAND{share_counter[0]}"
            share_counter[0] += 1
            return name

        mock_server.add_share.side_effect = add_share
        mock_create_server.return_value = mock_server

        mounts = [
            Mount(host="/host/a", guest="/mnt/a"),
            Mount(host="/host/b", guest="/mnt/b"),
        ]
        sb = _MockSandbox(mock_execute, mounts)

        with contextlib.suppress(RuntimeError):
            await sb._mount_shares()

        # First mount should have been unmounted during rollback
        assert any("umount /mnt/a" in c for c in commands)
        # SMB share should also be removed
        mock_server.remove_share.assert_any_call("QUICKSAND0")

    @pytest.mark.asyncio
    async def test_raises_without_full_network(self):
        """Test that mounts raise without network_mode=FULL or MOUNTS_ONLY."""

        def mock_execute(cmd: str, timeout: float) -> ExecuteResult:
            return ExecuteResult(stdout="", stderr="", exit_code=0)

        mounts = [Mount(host="/host/data", guest="/mnt/data")]
        sb = _MockSandbox(mock_execute, mounts, network_mode=NetworkMode.NONE)

        with pytest.raises(ValueError, match="network_mode=FULL or MOUNTS_ONLY"):
            await sb._mount_shares()


class TestDynamicMount:
    """Tests for _MountMixin.mount() and unmount()."""

    @pytest.mark.asyncio
    @patch("quicksand_core.sandbox._mounts.create_smb_server")
    async def test_mount_returns_handle(self, mock_create_server):
        """Test that mount() returns a MountHandle."""
        commands = []

        def mock_execute(cmd: str, timeout: float) -> ExecuteResult:
            commands.append(cmd)
            return ExecuteResult(stdout="", stderr="", exit_code=0)

        mock_server = MagicMock()
        mock_server.add_share.return_value = "QUICKSAND0"
        mock_server.credentials = ("guest", "")
        mock_create_server.return_value = mock_server

        sb = _MockSandbox(mock_execute, [])
        handle = await sb.mount("/host/data", "/mnt/data")

        assert isinstance(handle, MountHandle)
        assert handle.host == "/host/data"
        assert handle.guest == "/mnt/data"
        assert handle.readonly is False
        assert handle in sb.active_mounts

    @pytest.mark.asyncio
    @patch("quicksand_core.sandbox._mounts.create_smb_server")
    async def test_unmount_removes_handle(self, mock_create_server):
        """Test that unmount() removes the handle from active_mounts."""

        def mock_execute(cmd: str, timeout: float) -> ExecuteResult:
            return ExecuteResult(stdout="", stderr="", exit_code=0)

        mock_server = MagicMock()
        mock_server.add_share.return_value = "QUICKSAND0"
        mock_server.credentials = ("guest", "")
        mock_create_server.return_value = mock_server

        sb = _MockSandbox(mock_execute, [])
        handle = await sb.mount("/host/data", "/mnt/data")
        await sb.unmount(handle)

        assert handle not in sb.active_mounts
        mock_server.remove_share.assert_called_once_with("QUICKSAND0")

    @pytest.mark.asyncio
    async def test_mount_raises_with_network_none(self):
        """Test that mount() raises with network_mode=NONE."""

        def mock_execute(cmd: str, timeout: float) -> ExecuteResult:
            return ExecuteResult(stdout="", stderr="", exit_code=0)

        sb = _MockSandbox(mock_execute, [], network_mode=NetworkMode.NONE)

        with pytest.raises(RuntimeError, match="network_mode=FULL or MOUNTS_ONLY"):
            await sb.mount("/host/data", "/mnt/data")


class TestCleanupMounts:
    """Tests for _MountMixin._cleanup_mounts."""

    @pytest.mark.asyncio
    async def test_cleanup_no_smb(self):
        """Test cleanup when no SMB server is running."""

        def mock_execute(cmd: str, timeout: float) -> ExecuteResult:
            return ExecuteResult(stdout="", stderr="", exit_code=0)

        sb = _MockSandbox(mock_execute, [])
        errors = await _MountMixin._cleanup_mounts(sb)
        assert errors == []

    @pytest.mark.asyncio
    async def test_cleanup_stops_smb_server(self):
        """Test cleanup stops the SMB server."""

        def mock_execute(cmd: str, timeout: float) -> ExecuteResult:
            return ExecuteResult(stdout="", stderr="", exit_code=0)

        sb = _MockSandbox(mock_execute, [])
        mock_smb = MagicMock()
        sb._smb_server = mock_smb

        errors = await _MountMixin._cleanup_mounts(sb)
        assert errors == []
        mock_smb.stop.assert_called_once()
        assert sb._smb_server is None

    @pytest.mark.asyncio
    async def test_cleanup_returns_errors(self):
        """Test cleanup returns errors from SMB server stop."""

        def mock_execute(cmd: str, timeout: float) -> ExecuteResult:
            return ExecuteResult(stdout="", stderr="", exit_code=0)

        sb = _MockSandbox(mock_execute, [])
        mock_smb = MagicMock()
        sb._smb_server = mock_smb
        mock_smb.stop.side_effect = RuntimeError("stop failed")

        errors = await _MountMixin._cleanup_mounts(sb)
        assert len(errors) == 1
        assert errors[0][0] == "SMB server"


class TestCifsOpts:
    """Tests for MountOptions.cifs_opts static method."""

    def test_cifs_opts_authenticated(self):
        """Test that cifs_opts produces NTLMSSP options when password is set."""
        result = MountOptions.cifs_opts("myuser", "mypass")
        assert result == "username=myuser,password=mypass,sec=ntlmssp,vers=3.0"

    def test_cifs_opts_anonymous(self):
        """Test that cifs_opts produces sec=none for empty password."""
        result = MountOptions.cifs_opts("guest", "")
        assert result == "username=guest,password=,sec=none,vers=3.0"
