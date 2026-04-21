"""Base protocol declaring the Sandbox API surface accessible within mixins."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .._types import (
    ExecuteResult,
    MountHandle,
    QuicksandGuestAgentMethod,
    ResolvedAccelerator,
    ResolvedImage,
    SandboxConfig,
    SaveManifest,
    Timeouts,
)
from ..host.smb import SMBServer
from ..qemu import OverlayManager, VMProcessManager
from ..qemu.platform import RuntimeInfo

if TYPE_CHECKING:
    from ..host.quicksand_guest_agent_client import QuicksandGuestAgentClient
    from ..host.virtio_serial_agent_client import VirtioSerialAgentClient
    from ..qemu.qmp import QMPClient

    AgentClient = QuicksandGuestAgentClient | VirtioSerialAgentClient


class _SandboxProtocol(Protocol):
    """Protocol for the full Sandbox interface, as seen by mixin classes."""

    # ------------------------------------------------------------------
    # State attributes (flat fields)
    # ------------------------------------------------------------------

    config: SandboxConfig
    _is_running: bool
    _image: ResolvedImage | None
    _accel: ResolvedAccelerator | None
    _overlay_path: Path | None
    _temp_dir: Path | None
    _process_manager: VMProcessManager
    _smb_server: SMBServer | None
    _dynamic_mounts: list[MountHandle]
    _progress_callback: Callable[[str, int, int], None] | None
    _overlay_manager: OverlayManager | None
    _runtime_info: RuntimeInfo | None
    _save_name: str | None
    _workspace: Path | None
    _agent_client: AgentClient | None
    _agent_port: int | None
    _agent_token: str | None
    _qmp_client: QMPClient | None
    _qmp_port: int | None
    _qmp_checkpoints: list[str]
    _vnc_port: int | None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool: ...

    async def execute(
        self,
        command: str,
        timeout: float = Timeouts.GUEST_AGENT_REQUEST,
        cwd: str | None = None,
        shell: str = "",
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
        exclusive: bool = False,
    ) -> ExecuteResult: ...

    async def save(
        self,
        name: str,
        *,
        workspace: str | Path | None = None,
        compress: bool = False,
        delete_checkpoints: bool = False,
    ) -> SaveManifest: ...

    # Input / display API (implemented by _InputMixin)

    @property
    def vnc_port(self) -> int | None: ...

    async def type_text(self, text: str) -> None: ...

    async def press_key(self, *keys: str) -> None: ...

    async def mouse_move(self, x: int, y: int) -> None: ...

    async def mouse_click(self, button: str = "left", *, double: bool = False) -> None: ...

    async def screenshot(self, path: str | Path) -> None: ...

    async def query_display_size(self) -> tuple[int, int]: ...

    async def query_mouse_position(self) -> dict | None: ...

    # ------------------------------------------------------------------
    # Internal API used across mixins
    # ------------------------------------------------------------------

    async def _send_request(
        self,
        method: QuicksandGuestAgentMethod,
        params: dict[str, Any],
        timeout: float = Timeouts.GUEST_AGENT_REQUEST,
    ) -> dict[str, Any]: ...

    async def _graceful_shutdown(self) -> None: ...

    def _ensure_smb_server(self) -> SMBServer: ...

    async def _mount_shares(self) -> None: ...

    async def _cleanup_mounts(self) -> list[tuple[str, Exception]]: ...
