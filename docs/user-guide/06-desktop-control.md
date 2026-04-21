# Desktop Control

*See [Under the Hood: Desktop Control](../under-the-hood/06-desktop-control.md) for how display, keyboard, and mouse map to QEMU devices and QMP commands.*

Desktop sandboxes give agents a full graphical Linux environment. This includes a real desktop with a window manager, browser, and file manager. Agents interact with it through screenshots and programmatic keyboard/mouse input.

## Setup

```python
async with Sandbox(
    image="ubuntu-desktop",   # or "alpine-desktop"
    enable_display=True,       # Required for GUI interaction
) as sb:
    await sb.screenshot("screen.png")
```

Both `image` and `enable_display=True` are needed. Headless images (`"ubuntu"`, `"alpine"`) don't have a desktop environment installed.

## Screenshots

```python
await sb.screenshot("screen.png")
```

Captures the current screen as a PNG file on the host. Works regardless of what the guest is doing, even during boot or when the desktop is loading.

## Keyboard

**Type a string:**
```python
await sb.type_text("hello world")
```

Characters are typed one at a time through the virtual keyboard. Shift is handled automatically for uppercase letters and symbols.

**Press a key or combo:**
```python
from quicksand import Key

await sb.press_key(Key.RET)              # Enter
await sb.press_key(Key.CTRL, Key.C)      # Ctrl+C
await sb.press_key(Key.ALT, Key.F4)      # Alt+F4
await sb.press_key(Key.CTRL, Key.ALT, Key.T)  # Open terminal (Ubuntu)
```

## Mouse

**Move to a position:**
```python
await sb.mouse_move(500, 300)
```

Coordinates are absolute (0-32767 range on each axis). The cursor goes exactly where you point. There is no acceleration and no drift.

**Click:**
```python
await sb.mouse_click("left")
await sb.mouse_click("right")
await sb.mouse_click("left", double=True)  # Double-click
```

**Scroll:**
```python
await sb.mouse_click("wheel-up")
await sb.mouse_click("wheel-down")
```

## Display info

**Get the screen resolution:**
```python
width, height = await sb.query_display_size()
# e.g., (1024, 768)
```

**Get the current mouse position:**
```python
mouse = await sb.query_mouse_position()
```

**Connect a VNC viewer for debugging:**
```python
print(f"VNC port: {sb.vnc_port}")
# Connect with: vncviewer localhost:{sb.vnc_port}
```

## Desktop images

| Image | Desktop | Browser | Init | Size |
|-------|---------|---------|------|------|
| `alpine-desktop` | Xfce4 + LightDM | Chromium | OpenRC | 287 MB (ARM64) / 310 MB (x86_64) |
| `ubuntu-desktop` | Xfce4 + LightDM | Firefox ESR | systemd | 252 MB (ARM64) / 263 MB (x86_64) |

Both auto-login (no password prompt) and use software rendering.

## Common patterns

**Screenshot-action loop (CUA agent):**
```python
while not done:
    await sb.screenshot("screen.png")
    action = agent.decide(read_image("screen.png"))

    if action.type == "click":
        await sb.mouse_move(action.x, action.y)
        await sb.mouse_click("left")
    elif action.type == "type":
        await sb.type_text(action.text)
    elif action.type == "key":
        await sb.press_key(*action.keys)
```

**Open a URL in the browser:**
```python
# Ubuntu desktop
await sb.execute("firefox 'https://example.com' &")
import asyncio; await asyncio.sleep(3)  # Wait for browser to load
await sb.screenshot("page.png")

# Alpine desktop
await sb.execute("chromium-browser 'https://example.com' &")
```

**Take a screenshot after a command:**
```python
await sb.execute("xdg-open /mnt/data/document.pdf &")
import asyncio; await asyncio.sleep(2)
await sb.screenshot("document.png")
```
