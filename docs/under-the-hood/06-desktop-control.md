# Desktop Control: Under the Hood

Companion to [Desktop Control](../user-guide/06-desktop-control.md).

## Display devices

```python
Sandbox(image="ubuntu-desktop", enable_display=True)
```

```bash
# enable_display=True adds:
-device virtio-gpu-pci \
-display vnc=127.0.0.1:0 \
-device usb-ehci,id=ehci \
-device usb-tablet,bus=ehci.0 \
-device virtio-keyboard-pci
```

```bash
# enable_display=False (default) uses:
-nographic -vga none
```

| Flag | Purpose |
|---|---|
| `-device virtio-gpu-pci` | Virtual GPU — guest renders to a framebuffer QEMU can read. On x86_64 this is `-device virtio-vga` instead. |
| `-display vnc=127.0.0.1:0` | VNC server on localhost (display 0 = port 5900). For debugging; Quicksand uses QMP, not VNC. |
| `-device usb-ehci,id=ehci` | USB 2.0 host controller (bus for the tablet device) |
| `-device usb-tablet,bus=ehci.0` | Absolute-positioning input device (see [Mouse](#mouse) below) |
| `-device virtio-keyboard-pci` | Virtual keyboard for QMP key injection. Required on ARM64 `virt` which has no PS/2 controller. |

The VNC port is auto-allocated in the 5900-5999 range. QEMU's VNC syntax uses a display number: `-display vnc=127.0.0.1:{port - 5900}`.

## Screenshots

```python
await sb.screenshot("screen.png")
```

```json
{"execute": "screendump", "arguments": {"filename": "/tmp/screen.ppm"}}
```

QMP `screendump` reads the GPU framebuffer and writes it to a host file. The output format is PPM (a raw bitmap format). Quicksand converts PPM to PNG using `struct` + `zlib`. No Pillow is needed. If QEMU outputs PNG directly (some builds do), it's detected via magic bytes and used as-is.

## Keyboard

```python
await sb.type_text("hello")
```

Each character is mapped to a QKeyCode and sent via QMP `send-key`:

```json
{"execute": "send-key", "arguments": {"keys": [{"type": "qcode", "data": "h"}], "hold-time": 1}}
{"execute": "send-key", "arguments": {"keys": [{"type": "qcode", "data": "e"}], "hold-time": 1}}
{"execute": "send-key", "arguments": {"keys": [{"type": "qcode", "data": "l"}], "hold-time": 1}}
{"execute": "send-key", "arguments": {"keys": [{"type": "qcode", "data": "l"}], "hold-time": 1}}
{"execute": "send-key", "arguments": {"keys": [{"type": "qcode", "data": "o"}], "hold-time": 1}}
```

`hold-time=1` (1ms) is critical. The default is 100ms, which means `send-key` blocks for 100ms per character. Typing "hello" would take 500ms. At 1ms it takes ~5ms.

Uppercase and symbols use shift:

```python
await sb.type_text("Hello!")
```

```json
{"execute": "send-key", "arguments": {"keys": [{"type": "qcode", "data": "shift"}, {"type": "qcode", "data": "h"}], "hold-time": 1}}
...
{"execute": "send-key", "arguments": {"keys": [{"type": "qcode", "data": "shift"}, {"type": "qcode", "data": "1"}], "hold-time": 1}}
```

```python
await sb.press_key(Key.CTRL, Key.C)
```

```json
{"execute": "send-key", "arguments": {"keys": [{"type": "qcode", "data": "ctrl"}, {"type": "qcode", "data": "c"}], "hold-time": 100}}
```

`press_key()` uses the default 100ms hold time. Modifier combos need the keys held long enough for the guest to register them.

## Mouse

```python
await sb.mouse_move(500, 300)
```

```json
{"execute": "input-send-event", "arguments": {"events": [
  {"type": "abs", "data": {"axis": "x", "value": 500}},
  {"type": "abs", "data": {"axis": "y", "value": 300}}
]}}
```

The coordinates are absolute (0-32767 range on each axis). This is why the input device is a USB tablet (`-device usb-tablet`) rather than a relative mouse. A relative mouse would need to know the current position and calculate deltas, and rounding errors would cause drift over time.

```python
await sb.mouse_click("left")
```

Down and up are sent as separate QMP calls so window managers see a distinct button-press event:

```json
{"execute": "input-send-event", "arguments": {"events": [{"type": "btn", "data": {"button": "left", "down": true}}]}}
{"execute": "input-send-event", "arguments": {"events": [{"type": "btn", "data": {"button": "left", "down": false}}]}}
```

Double-clicks add a 50ms pause between the two click sequences so the window manager registers them as distinct clicks.
