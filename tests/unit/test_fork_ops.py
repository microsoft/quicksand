"""Tests for _ForkMixin.fork() and the shared-overlay claim refcount."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from quicksand_core import _overlay_cache
from quicksand_core._types import (
    ResolvedAccelerator,
    ResolvedImage,
    SandboxConfig,
)
from quicksand_core.sandbox._fork import _ForkMixin
from quicksand_core.sandbox._saves import _SaveMixin


@pytest.fixture
def fake_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()

    class _StubConfig:
        cache_dir = cache_root

    import quicksand_core.qemu.platform as platform_mod

    monkeypatch.setattr(platform_mod, "get_platform_config", lambda: _StubConfig())
    return cache_root


def _make_overlay(name: str) -> Path:
    path = _overlay_cache.get_overlays_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


# ---------------------------------------------------------------------------
# Cross-claim refcount on the bare _overlay_cache surface
# ---------------------------------------------------------------------------


class TestIsOverlayClaimedElsewhere:
    def test_returns_false_with_no_state(self, fake_cache_dir: Path):
        overlay = _make_overlay("a.qcow2")
        assert not _overlay_cache.is_overlay_claimed_elsewhere(overlay)

    def test_returns_true_when_other_state_claims(self, fake_cache_dir: Path):
        overlay = _make_overlay("a.qcow2")
        _overlay_cache.write_session_state("other", os.getpid(), [overlay])

        assert _overlay_cache.is_overlay_claimed_elsewhere(overlay)

    def test_excludes_own_sandbox_id(self, fake_cache_dir: Path):
        overlay = _make_overlay("a.qcow2")
        _overlay_cache.write_session_state("self", os.getpid(), [overlay])

        # Only our own state file claims it — should be considered unclaimed.
        assert not _overlay_cache.is_overlay_claimed_elsewhere(overlay, exclude_sandbox_id="self")

    def test_multiple_state_files(self, fake_cache_dir: Path):
        overlay = _make_overlay("shared.qcow2")
        _overlay_cache.write_session_state("a", os.getpid(), [overlay])
        _overlay_cache.write_session_state("b", os.getpid(), [overlay])

        # Excluding A still sees B's claim.
        assert _overlay_cache.is_overlay_claimed_elsewhere(overlay, exclude_sandbox_id="a")


class TestReapPreservesSharedOverlays:
    def test_dead_sandbox_overlay_kept_when_live_sibling_claims_it(self, fake_cache_dir: Path):
        shared = _make_overlay("shared.qcow2")
        dead_only = _make_overlay("dead-only.qcow2")
        live_pid = os.getpid()
        dead_pid = 2**31 - 1

        _overlay_cache.write_session_state("dead", dead_pid, [shared, dead_only])
        _overlay_cache.write_session_state("live", live_pid, [shared])

        _overlay_cache.reap_stale_sandboxes()

        # Shared overlay survives — live state still claims it.
        assert shared.exists()
        # Dead-only overlay gets reaped.
        assert not dead_only.exists()
        # Dead state file is gone, live state file remains.
        assert not _overlay_cache.state_file_path("dead").exists()
        assert _overlay_cache.state_file_path("live").exists()


class TestCleanupForStateFile:
    def test_skips_shared_overlay(self, fake_cache_dir: Path):
        shared = _make_overlay("shared.qcow2")
        state_a = _overlay_cache.write_session_state("a", 12345, [shared])
        _overlay_cache.write_session_state("b", 12346, [shared])

        _overlay_cache.cleanup_for_state_file(state_a)

        # B still claims the shared overlay — survives.
        assert shared.exists()
        # A's state file is gone.
        assert not state_a.exists()

    def test_deletes_overlay_when_no_remaining_claim(self, fake_cache_dir: Path):
        owned = _make_overlay("owned.qcow2")
        state_a = _overlay_cache.write_session_state("a", 12345, [owned])

        _overlay_cache.cleanup_for_state_file(state_a)

        assert not owned.exists()
        assert not state_a.exists()


# ---------------------------------------------------------------------------
# _ForkMixin.fork() — preconditions + pivot wiring
# ---------------------------------------------------------------------------


def _make_mock_fork_sandbox(tmp_path: Path, fake_cache_dir: Path):
    """Build a minimal Sandbox-shaped mock that mixes in _SaveMixin + _ForkMixin."""
    from quicksand_core.host import Accelerator
    from quicksand_core.qemu.overlay import OverlayManager

    # Pre-existing top overlay in the overlay cache.
    overlays_dir = _overlay_cache.get_overlays_dir()
    overlays_dir.mkdir(parents=True, exist_ok=True)
    overlay = overlays_dir / "top.qcow2"
    overlay.write_bytes(b"")
    image = tmp_path / "base.qcow2"
    image.write_bytes(b"")

    class _MockSandbox(_SaveMixin, _ForkMixin):
        config = SandboxConfig(image="ubuntu")
        _smb_server = None
        _progress_callback = None
        _save_name = None
        _workspace = None

        def __init__(self) -> None:
            self._sandbox_id = "parent-sandbox"
            self._dynamic_mounts = []
            self._is_running = True
            self._image = ResolvedImage(name="ubuntu", chain=[image])
            self._accel = ResolvedAccelerator(accel=Accelerator.HVF)
            self._overlay_path = overlay
            self._temp_dir = tmp_path
            self._session_overlays = [overlay]
            self._agent_client = None
            self._agent_port = None
            self._agent_token = None
            self._qmp_client = AsyncMock()
            self._qmp_port = 12345
            self._qmp_checkpoints: list[str] = []
            self._vnc_port = None
            self._overlay_manager = MagicMock(spec=OverlayManager)
            self._process_manager = MagicMock()
            self._runtime_info = None

        @property
        def is_running(self) -> bool:
            return self._is_running

        async def execute(self, *args, **kwargs):
            mock = AsyncMock()
            return await mock(*args, **kwargs)

    return _MockSandbox(), overlay, image


class TestForkPreconditions:
    @pytest.mark.asyncio
    async def test_raises_when_not_running(self, tmp_path: Path, fake_cache_dir: Path):
        sb, _, _ = _make_mock_fork_sandbox(tmp_path, fake_cache_dir)
        sb._is_running = False
        with pytest.raises(RuntimeError, match="non-running"):
            await sb.fork()

    @pytest.mark.asyncio
    async def test_raises_when_qmp_disconnected(self, tmp_path: Path, fake_cache_dir: Path):
        sb, _, _ = _make_mock_fork_sandbox(tmp_path, fake_cache_dir)
        sb._qmp_client = None
        with pytest.raises(RuntimeError, match="QMP is not connected"):
            await sb.fork()

    @pytest.mark.asyncio
    async def test_raises_on_active_checkpoints_without_flag(
        self, tmp_path: Path, fake_cache_dir: Path
    ):
        sb, _, _ = _make_mock_fork_sandbox(tmp_path, fake_cache_dir)
        sb._qmp_checkpoints = ["snap1"]
        with pytest.raises(RuntimeError, match="active checkpoint snapshots"):
            await sb.fork()

    @pytest.mark.asyncio
    async def test_delete_checkpoints_clears_them(self, tmp_path: Path, fake_cache_dir: Path):
        sb, overlay, _ = _make_mock_fork_sandbox(tmp_path, fake_cache_dir)
        sb._qmp_checkpoints = ["snap1", "snap2"]
        sb._overlay_manager.get_overlay_chain.return_value = [overlay]

        await sb.fork(delete_checkpoints=True)

        assert sb._qmp_checkpoints == []


class TestForkPivot:
    @pytest.mark.asyncio
    async def test_pivot_creates_new_active_overlay(self, tmp_path: Path, fake_cache_dir: Path):
        sb, frozen, _ = _make_mock_fork_sandbox(tmp_path, fake_cache_dir)
        sb._overlay_manager.get_overlay_chain.return_value = [frozen]

        await sb.fork()

        # New active overlay was appended to session_overlays.
        assert len(sb._session_overlays) == 2
        new_active = sb._session_overlays[-1]
        assert new_active != frozen
        assert sb._overlay_path == new_active

    @pytest.mark.asyncio
    async def test_qmp_pivot_call(self, tmp_path: Path, fake_cache_dir: Path):
        sb, frozen, _ = _make_mock_fork_sandbox(tmp_path, fake_cache_dir)
        sb._overlay_manager.get_overlay_chain.return_value = [frozen]

        await sb.fork()

        # blockdev-snapshot-sync should have been called.
        execute_calls = sb._qmp_client.execute.call_args_list
        snapshot_calls = [
            c for c in execute_calls if c.args and c.args[0] == "blockdev-snapshot-sync"
        ]
        assert len(snapshot_calls) == 1


class TestForkChildSetup:
    @pytest.mark.asyncio
    async def test_child_inherits_image_name_and_arch(self, tmp_path: Path, fake_cache_dir: Path):
        sb, frozen, base = _make_mock_fork_sandbox(tmp_path, fake_cache_dir)
        sb._overlay_manager.get_overlay_chain.return_value = [frozen]

        child = await sb.fork()

        assert child._image.name == "ubuntu"
        assert child._image.chain[0] == base
        # Final chain element is the frozen overlay.
        assert child._image.chain[-1] == frozen

    @pytest.mark.asyncio
    async def test_child_drops_mounts(self, tmp_path: Path, fake_cache_dir: Path):
        sb, frozen, _ = _make_mock_fork_sandbox(tmp_path, fake_cache_dir)
        sb._overlay_manager.get_overlay_chain.return_value = [frozen]

        child = await sb.fork()

        assert child.config.mounts == []

    @pytest.mark.asyncio
    async def test_child_has_unique_sandbox_id(self, tmp_path: Path, fake_cache_dir: Path):
        sb, frozen, _ = _make_mock_fork_sandbox(tmp_path, fake_cache_dir)
        sb._overlay_manager.get_overlay_chain.return_value = [frozen]

        child = await sb.fork()

        assert child._sandbox_id != sb._sandbox_id

    @pytest.mark.asyncio
    async def test_child_claims_frozen_overlay_in_state_file(
        self, tmp_path: Path, fake_cache_dir: Path
    ):
        sb, frozen, _ = _make_mock_fork_sandbox(tmp_path, fake_cache_dir)
        sb._overlay_manager.get_overlay_chain.return_value = [frozen]

        child = await sb.fork()

        # Child wrote a state file that claims the frozen overlay.
        child_state = _overlay_cache.state_file_path(child._sandbox_id)
        assert child_state.exists()
        data = json.loads(child_state.read_text())
        assert str(frozen) in data["overlays"]

    @pytest.mark.asyncio
    async def test_kwargs_override_inherited_config(self, tmp_path: Path, fake_cache_dir: Path):
        sb, frozen, _ = _make_mock_fork_sandbox(tmp_path, fake_cache_dir)
        sb._overlay_manager.get_overlay_chain.return_value = [frozen]
        # Parent has whatever defaults SandboxConfig assigns.
        assert sb.config.cpus != 4 or sb.config.memory != "4G"

        child = await sb.fork(memory="4G", cpus=4)

        assert child.config.memory == "4G"
        assert child.config.cpus == 4
        # Parent's config is unchanged.
        assert sb.config.cpus != 4 or sb.config.memory != "4G"

    @pytest.mark.asyncio
    async def test_kwargs_can_override_mount_default(self, tmp_path: Path, fake_cache_dir: Path):
        from quicksand_core._types import Mount

        sb, frozen, _ = _make_mock_fork_sandbox(tmp_path, fake_cache_dir)
        sb._overlay_manager.get_overlay_chain.return_value = [frozen]

        explicit_mounts = [Mount(host="/tmp", guest="/mnt/host", type="9p")]
        child = await sb.fork(mounts=explicit_mounts)

        assert child.config.mounts == explicit_mounts

    @pytest.mark.asyncio
    async def test_save_workspace_progress_forwarded(self, tmp_path: Path, fake_cache_dir: Path):
        sb, frozen, _ = _make_mock_fork_sandbox(tmp_path, fake_cache_dir)
        sb._overlay_manager.get_overlay_chain.return_value = [frozen]

        progress_calls: list = []
        cb = lambda stage, n, total: progress_calls.append((stage, n, total))  # noqa: E731

        child = await sb.fork(save="child-save", workspace=tmp_path / "ws", progress_callback=cb)

        assert child._save_name == "child-save"
        assert child._workspace == tmp_path / "ws"
        assert child._progress_callback is cb

    @pytest.mark.asyncio
    async def test_child_is_not_started(self, tmp_path: Path, fake_cache_dir: Path):
        sb, frozen, _ = _make_mock_fork_sandbox(tmp_path, fake_cache_dir)
        sb._overlay_manager.get_overlay_chain.return_value = [frozen]

        child = await sb.fork()

        assert child._is_running is False
        assert child._process_manager.command is None
