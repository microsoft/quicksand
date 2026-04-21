"""Pre-configured Sandbox using the bundled AIF agent overlay."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Unpack

from quicksand_core import Sandbox
from quicksand_core._types import SandboxConfigParams


class AIFAgentSandbox(Sandbox):
    """Pre-configured Sandbox that boots from the bundled AIF agent overlay.

    Requires quicksand-ubuntu to be installed (the overlay is built on top of it).

    Usage::

        async with AIFAgentSandbox() as sb:
            result = await sb.execute("python3 --version")
    """

    def __init__(
        self,
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
        save: str | None = None,
        workspace: str | Path | None = None,
        **kwargs: Unpack[SandboxConfigParams],
    ) -> None:
        kwargs.setdefault("image", "aif-agent-sandbox")
        super().__init__(
            progress_callback=progress_callback,
            save=save,
            workspace=workspace,
            **kwargs,
        )
