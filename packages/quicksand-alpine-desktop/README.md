# Quicksand Alpine Desktop

Minimal Alpine Linux 3.21 desktop VM (Xfce4) for the [quicksand](https://github.com/microsoft/quicksand) agent harness. Lightweight and fast-booting — ideal when you need GUI automation without the overhead of a full Ubuntu desktop.

## Installation

```bash
pip install "git+ssh://git@github.com/microsoft/quicksand.git#subdirectory=packages/quicksand"
quicksand install alpine-desktop
```

## Usage

```python
import asyncio
from quicksand import Key
from quicksand_alpine_desktop import AlpineDesktopSandbox

async def main():
    async with AlpineDesktopSandbox() as sb:
        await sb.screenshot("desktop.png")
        await sb.type_text("echo hello world")
        await sb.press_key(Key.RET)
        result = await sb.execute("cat /etc/os-release")
        print(result.stdout)

asyncio.run(main())
```

## What's Included

- Alpine 3.21 with Xfce4 desktop + LightDM (auto-login)
- Chromium browser
- Python 3, bash, curl
- The quicksand guest agent (pre-installed)

## Default Config

| Setting | Default |
|---------|---------|
| Memory | 1G |
| CPUs | 2 |
| Display | Enabled (always) |
| Boot timeout | 60s |
| Init system | OpenRC |
| Image size | ~300MB |

## License

MIT
