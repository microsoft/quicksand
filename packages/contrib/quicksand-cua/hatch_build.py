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

SAVE_NAME = "quicksand-cua"
BASE_IMAGE = "quicksand-agent"
SHELL = "/bin/bash"

logger = logging.getLogger("quicksand-cua")

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
    # ── Install Xvfb, x11vnc, and Playwright system deps ────────
    await shell("apt-get update", timeout=120)
    await shell(
        "apt-get install -y xvfb x11vnc xauth fonts-liberation fonts-noto-color-emoji",
        timeout=300,
    )
    await shell(
        "apt-get install -y novnc websockify socat rsync",
        timeout=300,
    )

    # ── Install Playwright and Chromium via Playwright ───────────
    await shell("/opt/python/bin/pip install playwright", timeout=300)
    await shell(
        "PLAYWRIGHT_BROWSERS_PATH=/opt/playwright"
        " /opt/python/bin/python3 -m playwright install --with-deps chromium",
        timeout=300,
    )

    # ── Environment for Playwright browser path ──────────────────
    await shell(
        """cat > /etc/profile.d/playwright.sh << 'PROF'
export PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
export DISPLAY=:0
PROF
""",
    )

    # ── Systemd service: Xvfb on display :0 ─────────────────────
    await shell(
        """cat > /etc/systemd/system/xvfb.service << 'EOF'
[Unit]
Description=Xvfb virtual framebuffer
After=network.target

[Service]
ExecStart=/usr/bin/Xvfb :0 -screen 0 1280x1024x24 -ac
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
""",
    )

    # ── Systemd service: x11vnc on port 5901 ────────────────────
    await shell(
        """cat > /etc/systemd/system/x11vnc.service << 'EOF'
[Unit]
Description=x11vnc VNC server for display :0
After=xvfb.service
Requires=xvfb.service

[Service]
Environment=DISPLAY=:0
ExecStart=/usr/bin/x11vnc -display :0 -rfbport 5901 -forever -shared -nopw
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
""",
    )

    # ── Find Playwright's Chromium binary ─────────────────────────
    await shell(
        "CHROMIUM=$(find /opt/playwright -name chrome -type f"
        " | head -1) && ln -sf $CHROMIUM /usr/local/bin/chromium",
    )

    # ── Systemd service: Chromium on display :0 ──────────────────
    await shell(
        """cat > /etc/systemd/system/chromium.service << 'EOF'
[Unit]
Description=Chromium browser on display :0
After=xvfb.service
Requires=xvfb.service

[Service]
Environment=DISPLAY=:0
ExecStart=/usr/local/bin/chromium --no-sandbox --disable-gpu --start-maximized --no-first-run
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
""",
    )

    # ── Systemd service: noVNC web client on port 6080 ───────────
    await shell(
        """cat > /etc/systemd/system/novnc.service << 'EOF'
[Unit]
Description=noVNC web client proxying to x11vnc
After=x11vnc.service
Requires=x11vnc.service

[Service]
ExecStart=/usr/bin/websockify --web /usr/share/novnc 6080 localhost:5901
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
""",
    )

    # ── Enable all services ──────────────────────────────────────
    await shell("systemctl enable xvfb.service")
    await shell("systemctl enable x11vnc.service")
    await shell("systemctl enable chromium.service")
    await shell("systemctl enable novnc.service")

    # ── Verify installs ──────────────────────────────────────────
    await shell("/opt/python/bin/python3 -c 'import playwright; print(\"playwright OK\")'")
    await shell("which Xvfb && which x11vnc && test -x /usr/local/bin/chromium")

    # ── Clean caches ─────────────────────────────────────────────
    await shell("apt-get clean")
    await shell("rm -rf /var/lib/apt/lists/* /root/.cache/pip /tmp/*")
    # Playwright caches a .zip of the browser it downloaded
    await shell("rm -rf /root/.cache/ms-playwright")
    # Headless shell is unused — we run full Chromium with a display
    await shell("rm -rf /opt/playwright/chromium_headless_shell-*")
    # Vulkan GPU drivers are useless in a QEMU VM (~97 MB)
    # Keep libLLVM + libgallium — Chromium needs Mesa llvmpipe for software rendering
    await shell("rm -f /usr/lib/aarch64-linux-gnu/libvulkan_*.so*")
    # Node.js pulled in by Playwright --with-deps but not needed at runtime
    await shell("rm -f /usr/lib/aarch64-linux-gnu/libnode.so*")


# =============================================================================


class OverlayImageBuildHook(BuildHookInterface):
    """Build hook that creates an overlay save from a running sandbox."""

    PLUGIN_NAME = "quicksand-cua-image"

    def initialize(self, version: str, build_data: dict) -> None:
        from quicksand_image_tools.build_utils import set_platform_wheel_tag

        if not set_platform_wheel_tag(build_data, target_name=self.target_name, version=version):
            return

        pkg_dir = Path(self.root) / "quicksand_cua"
        save_dir = pkg_dir / "images"
        overlays_dir = save_dir / "overlays"

        if not (save_dir / "manifest.json").exists() or not any(overlays_dir.glob("*.qcow2")):
            self.app.display_info("Save not found, building overlay...")
            asyncio.run(self._build_and_save(save_dir))

        self.app.display_info(f"Including save: {save_dir}")

    async def _build_and_save(self, save_dir: Path) -> None:
        logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

        from quicksand_core import Sandbox
        from quicksand_core._types import NetworkMode

        async with Sandbox(
            image=BASE_IMAGE,
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
