# Quicksand CUA Sandbox

An overlay image package for [quicksand](https://github.com/microsoft/quicksand) that adds browser automation via Playwright and Chromium with a VNC-accessible virtual display. Built on top of `quicksand-agent`.

## Installation

```bash
quicksand install quicksand-cua
```

## Usage

### Simple (recommended)

```python
import asyncio
from quicksand_cua import CuaSandbox

async def main():
    async with CuaSandbox() as sb:
        # Xvfb, x11vnc, and Chromium start automatically via systemd
        result = await sb.execute("python3 -c 'from playwright.sync_api import sync_playwright; print(\"OK\")'")
        print(result.stdout)

asyncio.run(main())
```

### With custom config

```python
from quicksand_core import Sandbox

async with Sandbox(image="quicksand-cua", memory="4G", cpus=4) as sb:
    result = await sb.execute("chromium --version")
```

## What's Included

Everything from [quicksand-agent](../quicksand-agent) (Python 3.12, uv, requests, pyyaml, ddgs, markitdown), plus:

- **Xvfb** virtual framebuffer (display `:0`, 1280x1024x24)
- **x11vnc** VNC server on port 5901
- **Playwright** browser automation library
- **Chromium** browser (installed via Playwright)
- **noVNC** web client on port 6080
- **Fonts** (Liberation, Noto Color Emoji)

All services (Xvfb, x11vnc, Chromium, noVNC) are enabled as systemd units and start automatically on boot.

## License

MIT
