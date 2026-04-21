"""Hatch build hook for the AIF agent sandbox overlay.

Boots Ubuntu, installs uv, Python 3.12, build-essential, and Python packages
(requests, pyyaml, ddgs, markitdown), then saves the overlay.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

SAVE_NAME = "aif-agent-sandbox"
SHELL = "/bin/bash"

logger = logging.getLogger("aif-agent-sandbox")


async def _setup(shell: Callable[..., Coroutine[Any, Any, Any]]) -> None:
    """Install steps for the AIF agent sandbox."""

    # ── Install uv ──────────────────────────────────────────────
    await shell("curl -LsSf https://astral.sh/uv/install.sh | sh", timeout=60)

    # ── Create Python 3.12 venv with pip at /opt/python ────────
    await shell("/root/.local/bin/uv venv /opt/python --python 3.12 --seed", timeout=120)

    # ── Make venv discoverable on PATH ────────────────────────
    # Symlink venv binaries into /usr/local/bin (guest agent's PATH)
    await shell(
        "find /opt/python/bin -maxdepth 1"
        " \\( -type f -o -type l \\)"
        " -executable -exec ln -sf {} /usr/local/bin/ \\;",
    )

    # The venv's python is a symlink to /usr/bin/python3.12. Linux
    # resolves /proc/self/exe to the real binary, so Python never
    # finds pyvenv.cfg and never adds the venv's site-packages.
    # Fix: drop a .pth file into system dist-packages.
    await shell(
        "echo /opt/python/lib/python3.12/site-packages"
        " > /usr/lib/python3/dist-packages/quicksand-venv.pth",
    )

    # profile.d for interactive shells (sets VIRTUAL_ENV + PATH)
    await shell(
        """cat > /etc/profile.d/python-venv.sh << 'PROF'
export VIRTUAL_ENV=/opt/python
export PATH=/opt/python/bin:$PATH
PROF
""",
    )

    # Verify
    await shell("/opt/python/bin/python3 --version")
    await shell("/opt/python/bin/pip --version")

    # ── Install build-essential ─────────────────────────────────
    await shell("apt-get update", timeout=120)
    await shell("apt-get install -y build-essential", timeout=600)

    # ── Install Python packages ─────────────────────────────────
    await shell(
        "/opt/python/bin/pip install requests 'pyyaml>=6.0'"
        " 'ddgs>=9.11.2' 'markitdown[all]>=0.1.5'",
        timeout=600,
    )

    await shell(
        "/opt/python/bin/python3 -c "
        "'import requests; import yaml; import ddgs; import markitdown;"
        ' print("OK")\'',
    )

    # ── Clean caches ────────────────────────────────────────────
    await shell("apt-get clean")
    await shell("rm -rf /var/lib/apt/lists/* /root/.cache/pip /root/.cache/uv /tmp/*")


class OverlayImageBuildHook(BuildHookInterface):
    """Build hook that creates an overlay save from a running sandbox."""

    PLUGIN_NAME = "aif-agent-sandbox-image"

    def initialize(self, version: str, build_data: dict) -> None:
        from quicksand_image_tools.build_utils import set_platform_wheel_tag

        if not set_platform_wheel_tag(build_data, target_name=self.target_name, version=version):
            return

        pkg_dir = Path(self.root) / "aif_agent_sandbox"
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


def _make_shell(sb) -> Callable[..., Coroutine[Any, Any, Any]]:
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
