"""GUI input and screenshot example for quicksand.

Demonstrates QEMU's built-in VNC display with QMP input injection using
the Ubuntu Desktop image (Xfce4 + LightDM auto-login).

  - UbuntuDesktopSandbox boots directly into an Xfce4 desktop
  - sb.screenshot() captures the guest framebuffer via QMP screendump
  - sb.type_text() and sb.press_key() inject keyboard events
  - sb.mouse_move() and sb.mouse_click() control the pointer
  - sb.execute() runs commands via the guest agent

To watch live, connect any VNC viewer:
    vncviewer 127.0.0.1:<sb.vnc_port>

Run with: uv run python examples/gui_input.py
Requires: pip install quicksand[ubuntu-desktop]
"""

import asyncio
import time
from pathlib import Path

from quicksand import Key
from quicksand_ubuntu_desktop import UbuntuDesktopSandbox

SCREENSHOTS_DIR = Path(".quicksand/gui-screenshots")


async def _shot(sb, name: str) -> None:
    path = SCREENSHOTS_DIR / name
    await sb.screenshot(path)
    print(f"  screenshot -> {path}")


async def main() -> None:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    print("Starting desktop sandbox...")
    async with UbuntuDesktopSandbox(memory="1G", cpus=2) as sb:
        print(f"VNC: 127.0.0.1:{sb.vnc_port}  (connect any VNC viewer to watch live)")

        # Verify guest agent works
        result = await sb.execute("cat /etc/os-release | head -3")
        print(f"  OS: {result.stdout.strip()}")

        # Boot screenshot
        await _shot(sb, "01_boot.png")

        # Wait for Xfce4 to fully start
        time.sleep(10)
        await _shot(sb, "02_desktop.png")

        # Open xfce4-terminal and type a command
        print("Launching terminal...")
        await sb.execute("DISPLAY=:0 xfce4-terminal --geometry 80x24+50+50 &", timeout=5.0)
        time.sleep(2)
        await _shot(sb, "03_terminal.png")

        # Type in the terminal
        print("Typing in terminal...")
        await sb.type_text("echo 'hello from quicksand desktop'")
        await sb.press_key(Key.RET)
        time.sleep(1.0)
        await _shot(sb, "04_echo.png")

        print(f"\nDone. Screenshots in {SCREENSHOTS_DIR.absolute()}")


if __name__ == "__main__":
    asyncio.run(main())
