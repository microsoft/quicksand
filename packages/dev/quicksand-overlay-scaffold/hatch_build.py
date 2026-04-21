"""Hatch build hook for overlay image packages.

Edit _setup() to customize what gets installed in the sandbox.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

SAVE_NAME = "quicksand-overlay-scaffold"
SHELL = "/bin/bash"

logger = logging.getLogger("quicksand-overlay-scaffold")

# Type alias for the shell callable passed to _setup
Shell = Callable[..., Coroutine[Any, Any, Any]]


# =============================================================================
#  EDIT HERE — Install steps for your overlay image
#
#  `shell` runs a command inside the sandbox, streams stdout/stderr,
#  and raises RuntimeError on non-zero exit. Pass timeout= for slow commands.
#
#  Examples:
#      await shell("apt-get update", timeout=120)
#      await shell("pip install numpy", timeout=300)
# =============================================================================


async def _setup(shell: Shell) -> None:
    await shell("echo 'Add your install steps here'")


# =============================================================================


class OverlayImageBuildHook(BuildHookInterface):
    """Build hook that creates an overlay save from a running sandbox."""

    PLUGIN_NAME = "quicksand-overlay-scaffold-image"

    def initialize(self, version: str, build_data: dict) -> None:
        if SAVE_NAME == "quicksand-overlay-scaffold":
            # Scaffold template — not yet customized, skip image build
            return

        from quicksand_image_tools.build_utils import set_platform_wheel_tag

        if not set_platform_wheel_tag(build_data, target_name=self.target_name, version=version):
            return

        pkg_dir = Path(self.root) / "quicksand_overlay_scaffold"
        save_dir = pkg_dir / "images"
        overlays_dir = save_dir / "overlays"

        if not (save_dir / "manifest.json").exists() or not any(overlays_dir.glob("*.qcow2")):
            self.app.display_info("Save not found, building overlay...")
            asyncio.run(self._build_and_save(save_dir))

        self.app.display_info(f"Including save: {save_dir}")

    async def _build_and_save(self, save_dir: Path) -> None:
        logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

        from quicksand_core._types import NetworkMode
        from quicksand_ubuntu import UbuntuSandbox

        async with UbuntuSandbox(
            memory="4G",
            cpus=4,
            network_mode=NetworkMode.FULL,
            disk_size="10G",
        ) as sb:
            await _setup(_make_shell(sb))
            await sb.save(SAVE_NAME, workspace=save_dir.parent, compress=True)

        built = save_dir.parent / SAVE_NAME
        if save_dir.exists():
            shutil.rmtree(save_dir)
        built.rename(save_dir)


def _make_shell(sb) -> Shell:
    """Create a shell callable that streams output and raises on failure."""
    stdout_buf: list[str] = []
    stderr_buf: list[str] = []

    def _flush_lines(buf: list[str], log_fn) -> None:
        text = "".join(buf)
        buf.clear()
        for line in text.splitlines():
            if line:
                log_fn("%s", line)

    def _on_stdout(chunk: str) -> None:
        stdout_buf.append(chunk)
        if "\n" in chunk:
            _flush_lines(stdout_buf, logger.info)

    def _on_stderr(chunk: str) -> None:
        stderr_buf.append(chunk)
        if "\n" in chunk:
            _flush_lines(stderr_buf, logger.warning)

    async def shell(cmd: str, **kwargs) -> None:
        logger.info(">>> %s", cmd)
        stdout_buf.clear()
        stderr_buf.clear()
        result = await sb.execute(
            cmd,
            shell=SHELL,
            on_stdout=_on_stdout,
            on_stderr=_on_stderr,
            **kwargs,
        )
        _flush_lines(stdout_buf, logger.info)
        _flush_lines(stderr_buf, logger.warning)
        if result.exit_code != 0:
            raise RuntimeError(f"Command failed (exit {result.exit_code}): {cmd}\n{result.stderr}")

    return shell
