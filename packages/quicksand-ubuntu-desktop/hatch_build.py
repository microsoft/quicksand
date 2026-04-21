"""Hatch build hook for the Ubuntu Desktop overlay.

Boots Ubuntu base, installs Xfce4 desktop environment with X.org, LightDM,
Firefox ESR, and supporting services, then saves the overlay.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

SAVE_NAME = "ubuntu-desktop"
SHELL = "/bin/bash"

logger = logging.getLogger("ubuntu-desktop")


async def _setup(shell: Callable[..., Coroutine[Any, Any, Any]]) -> None:
    """Install Xfce4 desktop environment on Ubuntu base."""

    # ── Install Xfce4 desktop packages ───────────────────────────
    await shell("DEBIAN_FRONTEND=noninteractive apt-get update", timeout=120)
    await shell(
        "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
        "xserver-xorg-core xserver-xorg-video-modesetting xserver-xorg-input-libinput",
        timeout=300,
    )
    await shell(
        "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
        "xfce4-session xfce4-panel xfce4-terminal xfce4-settings "
        "xfwm4 xfdesktop4 thunar",
        timeout=300,
    )
    await shell(
        "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
        "lightdm lightdm-gtk-greeter dbus-x11 libgl1-mesa-dri fonts-dejavu-core",
        timeout=300,
    )

    # ── Install Firefox ESR from Mozilla PPA ─────────────────────
    await shell(
        "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
        "software-properties-common",
        timeout=120,
    )
    await shell(
        "add-apt-repository -y ppa:mozillateam/ppa && "
        "printf 'Package: firefox*\\nPin: release o=LP-PPA-mozillateam\\n"
        "Pin-Priority: 1001\\n' > /etc/apt/preferences.d/mozilla-firefox",
        timeout=120,
    )
    await shell("apt-get update", timeout=120)
    await shell(
        "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends firefox-esr",
        timeout=300,
    )
    await shell(
        "apt-get remove --purge -y software-properties-common && apt-get autoremove --purge -y",
        timeout=120,
    )

    # ── LightDM auto-login ───────────────────────────────────────
    await shell(
        "mkdir -p /etc/lightdm && "
        "printf '[Seat:*]\\nautologin-user=quicksand\\nautologin-session=xfce\\n"
        "user-session=xfce\\n' > /etc/lightdm/lightdm.conf"
    )

    # ── Mesa software rendering ──────────────────────────────────
    await shell("echo 'LIBGL_ALWAYS_SOFTWARE=1' >> /etc/environment")

    # ── Kernel module: virtio-gpu ────────────────────────────────
    await shell("echo virtio-gpu >> /etc/modules")

    # ── Switch default target to graphical ───────────────────────
    await shell("systemctl set-default graphical.target")

    # ── Xorg config ──────────────────────────────────────────────
    await shell(
        """mkdir -p /etc/X11
cat > /etc/X11/xorg.conf << 'XORGEOF'
# Quicksand Xorg configuration for QEMU virtio-gpu virtual display.
#
# We do NOT set AutoAddDevices=False here.  The USB tablet (event1) and VirtIO
# keyboard (event2) are enumerated by udev a few milliseconds after X starts;
# letting Xorg auto-add them via the config/udev backend is the only reliable
# way to get input working, because static InputDevice entries run at PreInit
# time (before udev has finished, so libinput fails with ENODEV).

Section "Device"
    Identifier  "VirtualGPU"
    Driver      "modesetting"
    Option      "kmsdev" "/dev/dri/card0"
    # Render cursor into the framebuffer so QMP screendump captures it.
    Option      "SWcursor" "true"
EndSection

Section "Screen"
    Identifier  "DefaultScreen"
    Device      "VirtualGPU"
EndSection
XORGEOF"""
    )

    # ── Xfce4 pre-configuration ──────────────────────────────────
    await shell(
        "mkdir -p /home/quicksand/.config/xfce4/xfconf/xfce-perchannel-xml && "
        "chown -R quicksand:quicksand /home/quicksand/.config"
    )

    # ── Clean package caches ─────────────────────────────────────
    await shell("apt-get clean")
    await shell("rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/*")


class OverlayImageBuildHook(BuildHookInterface):
    """Build hook that creates an overlay save from a running Ubuntu sandbox."""

    PLUGIN_NAME = "ubuntu-desktop-image"

    def initialize(self, version: str, build_data: dict) -> None:
        from quicksand_image_tools.build_utils import set_platform_wheel_tag

        if not set_platform_wheel_tag(build_data, target_name=self.target_name, version=version):
            return

        pkg_dir = Path(self.root) / "quicksand_ubuntu_desktop"
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
            disk_size="5G",
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
