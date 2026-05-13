"""Verify the state-file-before-overlay-creation ordering.

A concurrent ``reap_stale_sandboxes`` orphan-sweep could nuke a
fresh-but-unclaimed qcow2 if we created the file before writing the
state file. The fix is to claim first, create second, and roll the
claim back if the create call fails.

These tests poke the ordering directly because the race window is
microseconds and not reliably reproducible via timing.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from quicksand_core import _overlay_cache
from quicksand_core._types import ResolvedAccelerator, ResolvedImage, SandboxConfig
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


# ---------------------------------------------------------------------------
# _setup_disk: claim before _create_overlay
# ---------------------------------------------------------------------------


class TestSetupDiskOrdering:
    def _make_sandbox(self, tmp_path: Path):
        """Build a Sandbox with just enough state to exercise _setup_disk."""
        from quicksand_core import Sandbox

        sb = Sandbox.__new__(Sandbox)
        # __init__ would call _reap_stale_once (touches platform config); we
        # skip it and only populate what _setup_disk reads.
        sb._sandbox_id = "ordering-test"
        sb._session_overlays = []
        sb._image = ResolvedImage(name="ubuntu", chain=[tmp_path / "base.qcow2"])
        sb._overlay_manager = MagicMock()
        sb._overlay_manager.create_overlay = MagicMock()
        return sb

    def test_state_file_written_before_create_overlay(self, tmp_path: Path, fake_cache_dir: Path):
        from quicksand_core.sandbox import _lifecycle

        sb = self._make_sandbox(tmp_path)
        seen_overlays_at_create: list[list[Path]] = []
        seen_state_files: list[bool] = []

        def fake_create():
            # State file should already exist by now.
            seen_state_files.append(_overlay_cache.state_file_path(sb._sandbox_id).exists())
            seen_overlays_at_create.append(list(sb._session_overlays))

        sb._overlay_manager.create_overlay = lambda *a, **kw: None  # quiet default
        sb._create_overlay = fake_create  # type: ignore[attr-defined]

        _lifecycle._LifecycleMixin._setup_disk(sb)  # type: ignore[arg-type]

        assert seen_state_files == [True], "state file should exist before _create_overlay runs"
        # And the claim listed the new overlay before the file ever existed.
        assert len(seen_overlays_at_create[0]) == 1

    def test_create_failure_rolls_claim_back(self, tmp_path: Path, fake_cache_dir: Path):
        from quicksand_core.sandbox import _lifecycle

        sb = self._make_sandbox(tmp_path)

        def boom():
            raise RuntimeError("simulated qemu-img failure")

        sb._create_overlay = boom  # type: ignore[attr-defined]

        with pytest.raises(RuntimeError, match="simulated"):
            _lifecycle._LifecycleMixin._setup_disk(sb)  # type: ignore[arg-type]

        # Claim was withdrawn from both the in-memory list AND the state file.
        assert sb._session_overlays == []
        state_path = _overlay_cache.state_file_path(sb._sandbox_id)
        assert state_path.exists()
        data = json.loads(state_path.read_text())
        assert data["overlays"] == []


# ---------------------------------------------------------------------------
# _pivot_overlay: claim before QMP blockdev-snapshot-sync
# ---------------------------------------------------------------------------


def _make_pivot_sandbox(tmp_path: Path):
    from quicksand_core.host import Accelerator

    overlays_dir = _overlay_cache.get_overlays_dir()
    overlays_dir.mkdir(parents=True, exist_ok=True)
    initial = overlays_dir / "initial.qcow2"
    initial.write_bytes(b"")

    class _MockSandbox(_SaveMixin):
        config = SandboxConfig(image="ubuntu")
        _smb_server = None
        _progress_callback = None
        _save_name = None
        _workspace = None

        def __init__(self) -> None:
            self._sandbox_id = "pivot-ordering"
            self._dynamic_mounts = []
            self._is_running = True
            self._image = ResolvedImage(name="ubuntu", chain=[tmp_path / "base.qcow2"])
            self._accel = ResolvedAccelerator(accel=Accelerator.HVF)
            self._overlay_path = initial
            self._temp_dir = tmp_path
            self._session_overlays = [initial]
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

        async def execute(self, *args, **kwargs):
            return None

    return _MockSandbox(), initial


class TestPivotOrdering:
    @pytest.mark.asyncio
    async def test_state_file_written_before_qmp_pivot(self, tmp_path: Path, fake_cache_dir: Path):
        sb, initial = _make_pivot_sandbox(tmp_path)

        observed: dict = {}

        async def observe_qmp(command, *args, **kwargs):
            # Only check at the pivot call — the pre-pivot block flush
            # happens before the claim and that's by design (no file
            # gets created until blockdev-snapshot-sync runs).
            if command == "blockdev-snapshot-sync":
                state_path = _overlay_cache.state_file_path(sb._sandbox_id)
                data = json.loads(state_path.read_text())
                observed["overlays_at_qmp"] = list(data["overlays"])
            return {}

        sb._qmp_client.execute = observe_qmp

        await sb._pivot_overlay(delete_checkpoints=False)

        assert "overlays_at_qmp" in observed
        # State should show TWO overlays at the moment QMP fires: the
        # previous active + the freshly-allocated new active.
        assert len(observed["overlays_at_qmp"]) == 2
        assert str(initial) in observed["overlays_at_qmp"]

    @pytest.mark.asyncio
    async def test_qmp_failure_rolls_claim_back(self, tmp_path: Path, fake_cache_dir: Path):
        sb, initial = _make_pivot_sandbox(tmp_path)

        async def boom(command, *args, **kwargs):
            if command == "blockdev-snapshot-sync":
                raise RuntimeError("simulated QMP failure")
            return {}

        sb._qmp_client.execute = boom

        with pytest.raises(RuntimeError, match="simulated QMP failure"):
            await sb._pivot_overlay(delete_checkpoints=False)

        # _session_overlays back to just the original; state file too.
        assert sb._session_overlays == [initial]
        data = json.loads(_overlay_cache.state_file_path(sb._sandbox_id).read_text())
        assert data["overlays"] == [str(initial)]
