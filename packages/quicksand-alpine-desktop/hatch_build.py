"""Hatch build hook for the Alpine Desktop overlay.

Boots Alpine base, installs Xfce4 desktop environment with X.org, LightDM,
Chromium, and supporting services, then saves the overlay.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

SAVE_NAME = "alpine-desktop"
SHELL = "/bin/sh"

logger = logging.getLogger("alpine-desktop")


async def _setup(shell: Callable[..., Coroutine[Any, Any, Any]]) -> None:
    """Install Xfce4 desktop environment on Alpine base."""

    # ── Install X.org + Xfce4 + desktop packages ─────────────────
    await shell(
        "apk add --no-cache xorg-server xf86-video-modesetting xf86-input-libinput",
        timeout=120,
    )
    await shell(
        "apk add --no-cache "
        "xfce4-session xfce4-panel xfce4-terminal xfce4-settings "
        "xfwm4 xfdesktop thunar xfce4-appfinder",
        timeout=300,
    )
    await shell(
        "apk add --no-cache "
        "lightdm lightdm-gtk-greeter dbus dbus-x11 "
        "mesa-dri-gallium chromium font-dejavu eudev bash",
        timeout=300,
    )

    # ── User config: add to video+input groups, change shell ─────
    await shell("addgroup quicksand video || true")
    await shell("addgroup quicksand input || true")
    await shell("sed -i 's|quicksand:/bin/sh|quicksand:/bin/bash|' /etc/passwd")

    # ── LightDM auto-login ───────────────────────────────────────
    await shell(
        "mkdir -p /etc/lightdm && "
        "printf '[Seat:*]\\nautologin-user=quicksand\\nautologin-session=xfce\\n"
        "user-session=xfce\\n' > /etc/lightdm/lightdm.conf"
    )

    # ── Mesa software rendering ──────────────────────────────────
    await shell("printf 'export LIBGL_ALWAYS_SOFTWARE=1\\n' > /etc/profile.d/quicksand-mesa.sh")

    # ── Kernel modules for GPU + input ───────────────────────────
    await shell(
        "echo virtio-gpu >> /etc/modules && "
        "echo drm >> /etc/modules && "
        "echo virtio_input >> /etc/modules && "
        "echo evdev >> /etc/modules && "
        "echo uinput >> /etc/modules"
    )

    # ── modprobe softdep: virtio_input → evdev ───────────────────
    await shell(
        "mkdir -p /etc/modprobe.d && "
        "printf 'softdep virtio_input post: evdev\\n' "
        "> /etc/modprobe.d/quicksand-input.conf"
    )

    # ── OpenRC service: load-input-modules ───────────────────────
    await shell(
        """cat > /etc/init.d/load-input-modules << 'INITEOF'
#!/sbin/openrc-run
description="Load input kernel modules and retrigger udev"

depend() {
    need udev modules
    before lightdm
}

start() {
    ebegin "Loading input modules"
    modprobe virtio_input 2>/dev/null
    modprobe evdev       2>/dev/null
    modprobe uinput      2>/dev/null
    modprobe hid         2>/dev/null
    udevadm trigger --subsystem-match=input --action=add
    udevadm settle --timeout=5
    eend 0
}
INITEOF
chmod +x /etc/init.d/load-input-modules
rc-update add load-input-modules boot"""
    )

    # ── Udev rules for input devices ─────────────────────────────
    kbd_rule = (
        'SUBSYSTEM=="input", KERNEL=="event*", '
        'ATTRS{name}=="QEMU Virtio Keyboard", '
        'ENV{ID_INPUT_KEYBOARD}="1", ENV{ID_INPUT_KEY}="1"'
    )
    await shell(
        "mkdir -p /etc/udev/rules.d\n"
        "cat > /etc/udev/rules.d/90-quicksand-input.rules << RULESEOF\n"
        'SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT}="1"\n'
        f"{kbd_rule}\n"
        'SUBSYSTEM=="input", KERNEL=="event*", '
        'ATTRS{name}=="QEMU*Tablet*", ENV{ID_INPUT_MOUSE}="1"\n'
        'SUBSYSTEM=="input", KERNEL=="event*", TAG+="seat"\n'
        "RULESEOF"
    )

    # ── Switch from mdev to eudev ────────────────────────────────
    await shell("rc-update del mdev sysinit || true")
    await shell("rc-update add udev sysinit")

    # ── Change rc_sys from "lxc" to "" ───────────────────────────
    await shell("sed -i 's/^rc_sys=.*/rc_sys=\"\"/' /etc/rc.conf")

    # ── Remove hwclock (unreliable on ARM64 virt) ────────────────
    await shell("rc-update del hwclock boot || true")

    # ── Enable desktop services ──────────────────────────────────
    await shell("rc-update add dbus default")
    await shell("rc-update add lightdm default")

    # ── Fix-devpts service ───────────────────────────────────────
    await shell(
        """cat > /etc/init.d/fix-devpts << 'FIXEOF'
#!/sbin/openrc-run
command="/bin/mount"
command_args="-o remount,ptmxmode=666 /dev/pts"
depend() { need devfs; }
FIXEOF
chmod +x /etc/init.d/fix-devpts
rc-update add fix-devpts sysinit"""
    )

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
        """mkdir -p /home/quicksand/.config/xfce4/xfconf/xfce-perchannel-xml

cat > /home/quicksand/.config/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml << 'X1'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfwm4" version="1.0">
  <property name="general" type="empty">
    <property name="use_compositing" type="bool" value="false"/>
    <property name="vblank_mode" type="string" value="off"/>
  </property>
</channel>
X1

cat > /home/quicksand/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-screensaver.xml << 'X2'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-screensaver" version="1.0">
  <property name="saver" type="empty">
    <property name="enabled" type="bool" value="false"/>
  </property>
  <property name="lock" type="empty">
    <property name="enabled" type="bool" value="false"/>
  </property>
</channel>
X2

cat > /home/quicksand/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-power-manager.xml << 'X3'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-power-manager" version="1.0">
  <property name="xfce4-power-manager" type="empty">
    <property name="dpms-enabled" type="bool" value="false"/>
    <property name="blank-on-ac" type="int" value="0"/>
  </property>
</channel>
X3

chown -R quicksand:quicksand /home/quicksand/.config"""
    )

    # ── Clean package caches ─────────────────────────────────────
    await shell("rm -rf /var/cache/apk/* /tmp/*")


class OverlayImageBuildHook(BuildHookInterface):
    """Build hook that creates an overlay save from a running Alpine sandbox."""

    PLUGIN_NAME = "alpine-desktop-image"

    def initialize(self, version: str, build_data: dict) -> None:
        from quicksand_image_tools.build_utils import set_platform_wheel_tag

        if not set_platform_wheel_tag(build_data, target_name=self.target_name, version=version):
            return

        pkg_dir = Path(self.root) / "quicksand_alpine_desktop"
        save_dir = pkg_dir / "images"
        overlays_dir = save_dir / "overlays"

        if not (save_dir / "manifest.json").exists() or not any(overlays_dir.glob("*.qcow2")):
            self.app.display_info("Save not found, building overlay...")
            asyncio.run(self._build_and_save(save_dir))

        self.app.display_info(f"Including save: {save_dir}")

    async def _build_and_save(self, save_dir: Path) -> None:
        logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

        from quicksand_alpine import AlpineSandbox
        from quicksand_core._types import NetworkMode

        async with AlpineSandbox(
            memory="2G",
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
