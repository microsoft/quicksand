"""Sandbox class — public API and VM lifecycle orchestration."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Unpack

from .._types import (
    BootTiming,
    ExecuteResult,
    QuicksandGuestAgentMethod,
    ResolvedAccelerator,
    ResolvedImage,
    SandboxConfig,
    SandboxConfigParams,
    Timeouts,
)
from ..qemu import OverlayManager, VMProcessManager
from ._checkpoints import _CheckpointMixin
from ._execution import _ExecutionMixin
from ._input import _InputMixin
from ._lifecycle import _LifecycleMixin
from ._mounts import _MountMixin
from ._saves import _SaveMixin

if TYPE_CHECKING:
    from ..host.quicksand_guest_agent_client import QuicksandGuestAgentClient
    from ..host.virtio_serial_agent_client import VirtioSerialAgentClient
    from ..qemu.qmp import QMPClient

    AgentClient = QuicksandGuestAgentClient | VirtioSerialAgentClient

logger = logging.getLogger("quicksand.sandbox")

# Re-exported for backwards compatibility — defined in _types.py
__all__ = ["ExecuteResult", "Sandbox", "SandboxConfig"]


class Sandbox(
    _ExecutionMixin, _CheckpointMixin, _SaveMixin, _InputMixin, _LifecycleMixin, _MountMixin
):
    """
    Manages a single VM sandbox.

    Start modes:

    - Ephemeral (default): temporary overlay, discarded on stop.
        Sandbox(image="ubuntu")

    - Named: saves to .quicksand/<name>/ on stop.
        Sandbox(image="ubuntu", save="my-env")

    - From save: set ``image`` to a save name or path.
        Sandbox(image="my-save")

    - Named + from save: auto-save on stop.
        Sandbox(image="my-save", save="next")

    Manual save (VM keeps running)::

        async with Sandbox(image="ubuntu") as sb:
            await sb.execute("pip install numpy")
            await sb.save("my-save")

        async with Sandbox(image="my-save") as sb:
            await sb.execute("python3 -c 'import numpy'")
    """

    def __init__(
        self,
        _config: SandboxConfig | None = None,
        /,
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
        save: str | None = None,
        workspace: str | Path | None = None,
        **kwargs: Unpack[SandboxConfigParams],
    ):
        if _config is not None:
            if kwargs:
                raise TypeError("Cannot pass both a SandboxConfig and keyword arguments")
            self.config = SandboxConfig.model_validate(_config)
        else:
            self.config = SandboxConfig.model_validate(kwargs)
        self._progress_callback = progress_callback
        self._save_name = save
        self._workspace = Path(workspace) if workspace else None
        self._is_running = False

        # Resolution outputs (set during start)
        self._image: ResolvedImage | None = None
        self._accel: ResolvedAccelerator | None = None

        # Runtime state
        self._overlay_path: Path | None = None
        self._temp_dir: Path | None = None
        self._process_manager = VMProcessManager()
        self._smb_server: Any = None
        self._dynamic_mounts: list = []
        self._agent_client: AgentClient | None = None
        self._agent_port: int | None = None
        self._agent_token: str | None = None
        self._agent_socket_path: Path | None = None
        self._qmp_client: QMPClient | None = None
        self._qmp_port: int | None = None
        self._qmp_checkpoints: list[str] = []
        self._vnc_port: int | None = None
        self._overlay_manager: OverlayManager | None = None
        self._runtime_info = None
        self._boot_timing: BootTiming | None = None

    @property
    def is_running(self) -> bool:
        return self._is_running and self._process_manager.is_running

    @property
    def accelerator(self):
        """The hardware accelerator in use. None before start()."""
        return self._accel.accel if self._accel else None

    @property
    def boot_timeout(self) -> float:
        """Effective boot timeout. May differ from config if TCG is used."""
        return self._accel.boot_timeout if self._accel else self.config.boot_timeout

    @property
    def qemu_command(self) -> list[str] | None:
        """The QEMU command line used to launch this sandbox. None before start()."""
        return self._process_manager.command

    @property
    def boot_timing(self) -> BootTiming | None:
        """Phase-level boot timing. None before start() completes."""
        return self._boot_timing

    async def start(self) -> None:
        """Start the sandbox VM."""
        if self.is_running:
            raise RuntimeError("Sandbox is already running")

        timing = BootTiming()

        t = time.perf_counter()
        self._resolve_image()
        timing.resolve_image_s = round(time.perf_counter() - t, 4)

        t = time.perf_counter()
        self._detect_accelerator()
        timing.detect_accelerator_s = round(time.perf_counter() - t, 4)

        t = time.perf_counter()
        self._load_runtime()
        timing.load_runtime_s = round(time.perf_counter() - t, 4)

        t = time.perf_counter()
        self._setup_disk()
        timing.setup_disk_s = round(time.perf_counter() - t, 4)

        t = time.perf_counter()
        self._launch_process()
        timing.launch_process_s = round(time.perf_counter() - t, 4)

        t = time.perf_counter()
        await self._connect_to_guest_agent()
        timing.connect_agent_s = round(time.perf_counter() - t, 4)

        # Parse console log to break down connect_agent into sub-phases.
        timing.console_log = self._get_console_output(max_bytes=16000)
        self._parse_boot_sub_phases(timing)

        t = time.perf_counter()
        await self._connect_to_qmp()
        timing.connect_qmp_s = round(time.perf_counter() - t, 4)

        self._is_running = True

        t = time.perf_counter()
        await self._post_boot_setup()
        timing.post_boot_s = round(time.perf_counter() - t, 4)

        self._boot_timing = timing

    async def stop(self) -> None:
        """Stop the sandbox VM.

        If ``save`` was provided at construction, saves to
        ``<workspace>/<save>/`` before stopping.
        """
        if not self._is_running:
            return
        if self._save_name is not None:
            try:
                await self.save(
                    self._save_name,
                    workspace=self._workspace,
                )
            except Exception as e:
                logger.warning(
                    "Auto-save for '%s' failed: %s",
                    self._save_name,
                    e,
                )
        await self._cleanup()

    async def _send_request(
        self,
        method: QuicksandGuestAgentMethod,
        params: dict,
        timeout: float = Timeouts.GUEST_AGENT_REQUEST,
    ) -> dict:
        if self._agent_client is None:
            raise RuntimeError("Not connected to guest agent")
        return await self._agent_client.send_request(method, params, timeout)

    async def __aenter__(self) -> Sandbox:
        await self.start()
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Boot timing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_boot_sub_phases(timing: BootTiming) -> None:
        """Parse console log to estimate kernel, init, and agent sub-phases.

        Uses kernel timestamps ``[  X.XXXXXX]`` when available, and known
        marker strings to split connect_agent_s into sub-phases.
        """
        import re

        console = timing.console_log or ""
        if not console:
            return

        # Extract kernel timestamps [  0.000000] if present (not suppressed by quiet)
        kernel_ts = [float(m) for m in re.findall(r"\[\s*(\d+\.\d+)\]", console)]

        # Detect key console markers
        lines = console.splitlines()
        has_init = any(
            marker in line
            for line in lines
            for marker in ("is starting up", "Welcome to", "systemd", "OpenRC")
        )
        has_agent_start = any("quicksand-guest-agent" in line for line in lines)

        if kernel_ts and has_init:
            # Kernel timestamps give us wall-clock relative to kernel start.
            # kernel_boot = time from kernel start to init (first init marker).
            # We approximate: the last kernel timestamp before the init marker
            # is when the kernel finished.
            max_kernel_ts = max(kernel_ts)

            # Estimate: kernel handed off to init at max_kernel_ts
            timing.kernel_boot_s = round(max_kernel_ts, 4)
            remaining = timing.connect_agent_s - max_kernel_ts

            if has_agent_start and remaining > 0:
                # Split remaining between init and agent roughly.
                # Agent startup is typically fast; init (DHCP etc.) is slow.
                timing.init_system_s = round(remaining * 0.85, 4)
                timing.agent_startup_s = round(remaining * 0.15, 4)
            else:
                timing.init_system_s = round(max(remaining, 0), 4)

        elif has_init:
            # No kernel timestamps (quiet mode). Use marker positions to
            # estimate ratios from line count.
            init_line = 0
            agent_line = len(lines)
            for i, line in enumerate(lines):
                if any(m in line for m in ("is starting up", "Welcome to", "systemd", "OpenRC")):
                    init_line = i
                    break
            for i, line in enumerate(lines):
                if "quicksand-guest-agent" in line and "starting" in line.lower():
                    agent_line = i
                    break

            total_lines = max(len(lines), 1)
            init_frac = init_line / total_lines
            agent_frac = agent_line / total_lines

            timing.kernel_boot_s = round(timing.connect_agent_s * init_frac, 4)
            timing.init_system_s = round(timing.connect_agent_s * (agent_frac - init_frac), 4)
            timing.agent_startup_s = round(timing.connect_agent_s * (1 - agent_frac), 4)
