# Quicksand Ubuntu Desktop

Pre-built Ubuntu 24.04 Desktop VM image with Xfce4 for the [quicksand](https://github.com/microsoft/quicksand) agent harness. Gives your agent a full graphical Linux desktop with keyboard, mouse, and screenshot control.

## Installation

```bash
pip install quick-sandbox
quicksand install ubuntu-desktop
```

## Usage

```python
import asyncio
from quicksand import Key
from quicksand_ubuntu_desktop import UbuntuDesktopSandbox

async def main():
    async with UbuntuDesktopSandbox() as sb:
        # Take a screenshot
        await sb.screenshot("desktop.png")

        # Type text and press keys
        await sb.type_text("echo hello world")
        await sb.press_key(Key.RET)

        # Mouse control (absolute coordinates 0-32767)
        await sb.mouse_move(16383, 16383)
        await sb.mouse_click()

        # execute() still works alongside GUI input
        result = await sb.execute("uname -a")
        print(result.stdout)

asyncio.run(main())
```

### With custom config

```python
from quicksand_ubuntu_desktop import UbuntuDesktopSandbox

async with UbuntuDesktopSandbox(memory="4G", cpus=4) as sb:
    await sb.screenshot("desktop.png")
```

## What's Included

- Ubuntu 24.04 with Xfce4 desktop + LightDM (auto-login, no password prompt)
- Firefox ESR browser
- Full apt/deb ecosystem
- Python 3, bash, curl, ca-certificates
- The quicksand guest agent (pre-installed)
- Software rendering via Mesa (`LIBGL_ALWAYS_SOFTWARE=1`)

## Default Config

| Setting | Default |
|---------|---------|
| Memory | 1G |
| CPUs | 2 |
| Display | Enabled (always) |
| Boot timeout | 60s |
| Init system | systemd |

## Desktop API

All methods are async:

- `await screenshot(path)` saves the guest display as a PNG
- `await type_text(text)` types a string via keyboard events
- `await press_key(*keys)` presses a key combo, e.g. `press_key(Key.CTRL, Key.C)`
- `await mouse_move(x, y)` moves the mouse to an absolute position (0-32767 range)
- `await mouse_click(button, double=False)` clicks `"left"`, `"right"`, `"middle"`, `"wheel-up"`, or `"wheel-down"`
- `await query_display_size()` returns the display resolution as `(width, height)`
- `await execute(cmd)` runs a shell command (same as headless sandboxes)
- `vnc_port` is the host-side VNC port for connecting a viewer

All standard sandbox features (mounts, save/load, network config) work with desktop sandboxes too.

## License

MIT
