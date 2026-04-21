"""Lifecycle mixin — VM start, stop, and cleanup methods for the Sandbox class."""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
import shutil
import tempfile
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .._types import (
    EnvironmentVariables,
    FilePatterns,
    NetworkMode,
    ResolvedAccelerator,
    Timeouts,
)
from ..host.quicksand_guest_agent_client import QuicksandGuestAgentClient
from ..qemu import OverlayManager
from ..qemu.platform import get_platform_config, get_platform_config_for_arch, get_runtime
from ._protocol import _SandboxProtocol

if TYPE_CHECKING:
    from ..qemu.platform import PlatformConfig

logger = logging.getLogger("quicksand.sandbox")


class _LifecycleMixin(_SandboxProtocol):
    """Mixin providing VM lifecycle operations: start phases, stop, and cleanup."""

    # ------------------------------------------------------------------
    # start() sub-phases
    # ------------------------------------------------------------------

    def _get_platform_config(self) -> PlatformConfig:
        """Get platform config, using guest arch when loading a cross-arch save."""
        if self._image and self._image.guest_arch:
            return get_platform_config_for_arch(self._image.guest_arch)
        return get_platform_config()

    def _resolve_image(self) -> None:
        """Resolve config.image to concrete paths using ImageResolver."""
        from ..qemu.image_resolver import ImageResolver

        self._image = ImageResolver().resolve(self.config.image, arch=self.config.arch)

    def _detect_accelerator(self) -> None:
        """Resolve config.accel to a concrete Accelerator and adjust boot timeout."""
        from ..host import Accelerator

        # Cross-arch saves always require TCG (software emulation)
        if self._image and self._image.guest_arch:
            self._accel = ResolvedAccelerator(
                accel=Accelerator.TCG,
                nested_virt=False,
                boot_timeout=Timeouts.BOOT_TCG,
            )
            logger.warning(
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                "  WARNING: QEMU is using TCG (pure software emulation).\n"
                "  Hardware acceleration (KVM/HVF) is not available.\n"
                "  Performance will be 10-20x slower than normal.\n"
                "  Boot timeout increased to %ss.\n"
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!",
                Timeouts.BOOT_TCG,
            )
            return

        nested = False
        if self.config.accel == "auto":
            status = self._get_platform_config().detect_accelerator()
            accel = status.accelerator
            nested = status.nested
        elif self.config.accel is None:
            accel = Accelerator.TCG
        else:
            accel = self.config.accel

        boot_timeout = self.config.boot_timeout

        if accel == Accelerator.TCG and boot_timeout == Timeouts.BOOT_DEFAULT:
            boot_timeout = Timeouts.BOOT_TCG
            logger.warning(
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                "  WARNING: QEMU is using TCG (pure software emulation).\n"
                "  Hardware acceleration (KVM/HVF) is not available.\n"
                "  Performance will be 10-20x slower than normal.\n"
                "  Boot timeout increased to %ss.\n"
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!",
                boot_timeout,
            )

        if accel == Accelerator.WHPX and boot_timeout == Timeouts.BOOT_DEFAULT:
            boot_timeout = Timeouts.BOOT_WHPX
            logger.info("Using WHPX - increasing boot timeout to %ss", boot_timeout)

        self._accel = ResolvedAccelerator(
            accel=accel,
            nested_virt=nested,
            boot_timeout=boot_timeout,
        )

    def _load_runtime(self) -> None:
        """Fetch the QEMU runtime.

        For cross-arch saves, resolves the QEMU binary for the guest architecture.
        """
        self._runtime_info = get_runtime(self._get_platform_config())
        self._overlay_manager = OverlayManager(self._runtime_info.qemu_img)

    def _setup_disk(self) -> None:
        """Create the working directory and qcow2 overlay (fresh or restored from save).

        If ``_image.overlays`` is set (save or overlay package), the
        overlay chain is restored. Otherwise a fresh overlay is created.
        """
        self._temp_dir = Path(tempfile.mkdtemp(prefix="quicksand-"))
        self._overlay_path = self._temp_dir / FilePatterns.OVERLAY
        self._create_overlay()

    def _launch_process(self) -> None:
        """Generate agent credentials, build the VM command, and start QEMU."""
        from ..utils import find_free_port, find_free_vnc_port

        self._agent_token = secrets.token_hex(16)
        self._agent_port = find_free_port()
        self._qmp_port = find_free_port()

        if self.config.enable_display:
            self._vnc_port = find_free_vnc_port()

        # Create virtio-serial socket path for agent communication.
        assert self._temp_dir is not None
        self._agent_socket_path = self._temp_dir / "agent.sock"

        # Start the SMB server early so the guestfwd command can be embedded
        # in the QEMU command line. QuicksandSMBServer uses guestfwd in all
        # modes (no TCP port), so we always start it when mounts are possible.
        if self.config.network_mode in (NetworkMode.MOUNTS_ONLY, NetworkMode.FULL):
            self._ensure_smb_server()

        cmd = self._build_vm_command()
        logger.debug(
            "Starting VM: port=%s, image=%s",
            self._agent_port,
            self._image.chain[0] if self._image else None,
        )
        logger.debug("QEMU command: %s", " ".join(str(c) for c in cmd))

        env = os.environ.copy()
        if (
            self._runtime_info is not None
            and self._runtime_info.module_dir is not None
            and self._runtime_info.module_dir.exists()
        ):
            env[EnvironmentVariables.QEMU_MODULE_DIR] = str(self._runtime_info.module_dir)

        assert self._temp_dir is not None
        console_log_path = self._temp_dir / FilePatterns.CONSOLE_LOG
        self._process_manager.start(cmd, env, console_log_path)

    async def _connect_to_guest_agent(self) -> None:
        """Wait for the guest agent; clean up and re-raise on failure."""
        try:
            await self._wait_for_guest_agent()
        except Exception as e:
            console_output = self._get_console_output()
            await self._cleanup()
            error_msg = f"Failed to connect to guest agent: {e}"
            if console_output:
                error_msg += f"\n\nConsole output (last 2KB):\n{console_output}"
            raise RuntimeError(error_msg) from e

    async def _connect_to_qmp(self) -> None:
        """Connect the QMP client. Required for checkpointing."""
        from ..qemu.qmp import QMPClient

        assert self._qmp_port is not None
        assert self._accel is not None
        client = QMPClient("127.0.0.1", self._qmp_port)
        await client.connect(timeout=self._accel.boot_timeout)
        self._qmp_client = client
        logger.debug("QMP client connected on port %d", self._qmp_port)

    async def _post_boot_setup(self) -> None:
        """Expand the filesystem (non-fatal) and mount shares (fatal on failure)."""
        if self.config.disk_size:
            try:
                await self._expand_guest_filesystem()
            except Exception as e:
                logger.warning(f"Failed to expand filesystem: {e}")

        if self.config.mounts:
            try:
                await self._mount_shares()
            except Exception as e:
                await self._cleanup()
                raise RuntimeError(f"Failed to mount shares: {e}") from e

    # ------------------------------------------------------------------
    # start() helpers
    # ------------------------------------------------------------------

    def _create_overlay(self) -> None:
        assert self._overlay_manager is not None
        assert self._image is not None
        assert self._overlay_path is not None

        self._overlay_manager.create_overlay(
            self._image.chain[0],
            self._overlay_path,
            restore_chain=self._image.chain[1:] or None,
            disk_size=self.config.disk_size,
        )

    async def _expand_guest_filesystem(self) -> None:
        from .._types import GuestCommands

        logger.debug("Expanding guest filesystem")
        result = await self.execute(GuestCommands.DETECT_DISK_LAYOUT, timeout=30.0)
        disk_layout = result.stdout.strip()
        logger.debug(f"Disk layout: {disk_layout}")

        if disk_layout == "partitioned":
            result = await self.execute(GuestCommands.GROWPART, timeout=30.0)
            logger.debug(f"growpart output: {result.stdout}")
            await self.execute(GuestCommands.RESIZE_PARTITION, timeout=30.0)
        else:
            result = await self.execute(GuestCommands.RESIZE_WHOLE_DISK, timeout=30.0)

        logger.debug(f"Filesystem resize output: {result.stdout}")

    def _build_vm_command(self) -> list[str]:
        assert self._runtime_info is not None
        assert self._overlay_path is not None
        assert self._agent_port is not None
        assert self._agent_token is not None
        assert self._accel is not None

        platform = self._get_platform_config()

        smb_port = self._smb_server.port if self._smb_server is not None else None

        return platform.build_qemu_command(
            config=self.config,
            runtime_info=self._runtime_info,
            kernel_path=self._image.kernel if self._image else None,
            initrd_path=self._image.initrd if self._image else None,
            overlay_path=self._overlay_path,
            agent_port=self._agent_port,
            agent_token=self._agent_token,
            accelerator=self._accel.accel,
            nested_virt=self._accel.nested_virt,
            qmp_port=self._qmp_port,
            vnc_port=self._vnc_port,
            smb_port=smb_port,
            smb_server=self._smb_server,
            agent_socket_path=self._agent_socket_path,
        )

    async def _wait_for_guest_agent(self) -> None:
        assert self._agent_port is not None
        assert self._agent_token is not None
        assert self._accel is not None
        assert self._process_manager.is_running

        def check_process() -> tuple[bool, str]:
            exited, error_info = self._process_manager.check_exited()
            if exited:
                return (False, error_info)
            return (True, "")

        # Try virtio-serial first (faster: no guest networking dependency).
        # Fall back to HTTP if the socket path doesn't exist or connection fails.
        if self._agent_socket_path is not None:
            try:
                from ..host.virtio_serial_agent_client import VirtioSerialAgentClient

                client = VirtioSerialAgentClient(self._agent_socket_path, self._agent_token)
                await client.connect(
                    timeout=self._accel.boot_timeout,
                    process_check=check_process,
                )
                self._agent_client = client
                logger.debug("Connected to agent via virtio-serial")
                return
            except (TimeoutError, RuntimeError, OSError) as e:
                logger.debug("Virtio-serial connection failed, falling back to HTTP: %s", e)

        # Fallback: HTTP via hostfwd
        self._agent_client = QuicksandGuestAgentClient(self._agent_port, self._agent_token)

        try:
            await self._agent_client.connect(
                timeout=self._accel.boot_timeout,
                process_check=check_process,
            )
        except TimeoutError as e:
            console_output = self._get_console_output(max_bytes=4000)
            qemu_running = self._process_manager.is_running
            stderr = self._process_manager.get_stderr() if not qemu_running else ""
            raise TimeoutError(
                f"Could not connect to agent within "
                f"{self._accel.boot_timeout}s.\n"
                f"Agent port: {self._agent_port}\n"
                f"QEMU process running: {qemu_running}\n"
                f"Original error: {e}\n"
                f"QEMU stderr: {stderr or '(empty)'}\n"
                f"Console output (last 4KB):\n{console_output or '(empty)'}"
            ) from e

    # ------------------------------------------------------------------
    # stop() helpers
    # ------------------------------------------------------------------

    async def _cleanup(self) -> None:
        """Clean up resources. Logs errors but continues cleanup."""
        self._is_running = False
        cleanup_errors: list[tuple[str, Exception]] = []

        cleanup_errors.extend(await self._cleanup_mounts())

        if self._agent_client:
            try:
                await self._agent_client.close()
            except Exception as e:
                cleanup_errors.append(("agent client", e))
                logger.warning("Failed to close agent client: %s", e)
            finally:
                self._agent_client = None

        if self._qmp_client:
            with contextlib.suppress(Exception):
                await self._qmp_client.close()
            self._qmp_client = None

        process_errors = self._process_manager.terminate()
        cleanup_errors.extend(process_errors)

        if self._temp_dir and self._temp_dir.exists():
            try:
                shutil.rmtree(self._temp_dir)
            except Exception as e:
                cleanup_errors.append(("temp directory", e))
                logger.error(
                    "Failed to remove temp directory %s: %s. "
                    "You may need to manually clean up /tmp/quicksand-* directories.",
                    self._temp_dir,
                    e,
                )
            finally:
                self._temp_dir = None

        self._overlay_path = None
        self._agent_port = None
        self._agent_token = None
        self._agent_socket_path = None

        if cleanup_errors:
            error_summary = "; ".join(f"{resource}: {err}" for resource, err in cleanup_errors)
            warnings.warn(
                f"Sandbox cleanup completed with errors: {error_summary}",
                ResourceWarning,
                stacklevel=2,
            )

    async def _graceful_shutdown(self) -> None:
        if not self._is_running:
            return

        from .._types import GuestCommands

        with contextlib.suppress(Exception):
            await self.execute(GuestCommands.SYNC, timeout=Timeouts.MOUNT_OPERATION)

        if self._agent_client is not None:
            with contextlib.suppress(Exception):
                await self._agent_client.close()

        if self._qmp_client is not None:
            with contextlib.suppress(Exception):
                await self._qmp_client.close()
            self._qmp_client = None

        self._process_manager.terminate(graceful_timeout=Timeouts.GRACEFUL_SHUTDOWN)
        self._is_running = False
        self._agent_client = None

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _make_progress_cb(self, stage: str) -> Callable[[int, int], None]:
        def cb(downloaded: int, total: int) -> None:
            if self._progress_callback:
                self._progress_callback(stage, downloaded, total)

        return cb

    def _get_console_output(self, max_bytes: int = 2000) -> str:
        return self._process_manager.get_console_output(max_bytes)
