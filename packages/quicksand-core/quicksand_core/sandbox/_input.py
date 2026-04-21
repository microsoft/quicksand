"""Input injection mixin — keyboard, mouse, and screenshot operations."""

from __future__ import annotations

import contextlib
import struct
import zlib
from pathlib import Path

from ._protocol import _SandboxProtocol


def _convert_to_png(src: str, dst: str) -> None:
    """Convert *src* (PPM or PNG) to a proper PNG at *dst*.

    QEMU's screendump writes PPM binary (P6) even when the filename ends in
    .png, because many QEMU builds lack PNG support.  This function reads the
    raw RGB data from the PPM and writes a spec-compliant PNG using only the
    Python standard library (struct + zlib).  If *src* already starts with the
    PNG magic bytes it is simply copied as-is.
    """
    raw = Path(src).read_bytes()

    # Already a real PNG — just copy.
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        Path(dst).write_bytes(raw)
        return

    # Parse PPM P6 header: "P6\n<width> <height>\n<maxval>\n<data>"
    if not raw.startswith(b"P6"):
        raise ValueError(f"Unsupported screendump format in {src!r} (expected P6 PPM or PNG)")

    lines = raw.split(b"\n", 3)
    # Skip comment lines (lines starting with '#')
    header_parts: list[bytes] = []
    pos = 0
    for line in lines[:3]:
        if line.startswith(b"#"):
            pos += len(line) + 1
            continue
        header_parts.append(line)
        pos += len(line) + 1
        if len(header_parts) == 3:
            break

    width, height = map(int, header_parts[1].split())
    pixels = raw[pos:]  # 3 bytes per pixel (R, G, B)

    def _png_chunk(tag: bytes, data: bytes) -> bytes:
        length = struct.pack(">I", len(data))
        body = tag + data
        crc = struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        return length + body + crc

    # IHDR: width, height, bit_depth=8, color_type=2 (RGB), compress=0, filter=0, interlace=0
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    # Build filtered scanlines (filter type 0 = None for each row)
    row_size = width * 3
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)  # filter byte
        scanlines.extend(pixels[y * row_size : (y + 1) * row_size])

    idat_data = zlib.compress(bytes(scanlines), level=1)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr_data)
        + _png_chunk(b"IDAT", idat_data)
        + _png_chunk(b"IEND", b"")
    )
    Path(dst).write_bytes(png)


class _InputMixin(_SandboxProtocol):
    """Mixin providing input injection and screenshot operations via QMP.

    Requires ``enable_display=True`` in the sandbox config.  All methods
    raise ``RuntimeError`` if called when the sandbox is not running or
    display is not enabled.
    """

    def _require_display(self) -> None:
        if not self.is_running:
            raise RuntimeError("Sandbox is not running")
        if not self.config.enable_display:
            raise RuntimeError("Display is not enabled. Set enable_display=True in SandboxConfig.")
        if self._qmp_client is None:
            raise RuntimeError("QMP client is not connected")

    async def type_text(self, text: str) -> None:
        """Type a string into the guest via keyboard events.

        Characters that have no QKeyCode mapping are silently skipped.
        Requires ``enable_display=True``.
        """
        self._require_display()
        assert self._qmp_client is not None
        await self._qmp_client.type_text(text)

    async def press_key(self, *keys: str) -> None:
        """Press a key or key combination in the guest.

        Args:
            *keys: Key enum values or QKeyCode strings, e.g.
                   ``press_key(Key.CTRL, Key.C)`` or ``press_key(Key.RET)``.
        Requires ``enable_display=True``.
        """
        self._require_display()
        assert self._qmp_client is not None
        await self._qmp_client.send_key(list(keys))

    async def mouse_move(self, x: int, y: int) -> None:
        """Move the mouse to absolute coordinates.

        Args:
            x: Absolute X position in the range 0-32767.
            y: Absolute Y position in the range 0-32767.
        Requires ``enable_display=True``.
        """
        self._require_display()
        assert self._qmp_client is not None
        await self._qmp_client.mouse_move(x, y)

    async def mouse_click(self, button: str = "left", *, double: bool = False) -> None:
        """Click a mouse button.

        Args:
            button: ``"left"``, ``"middle"``, ``"right"``,
                    ``"wheel-up"``, or ``"wheel-down"``.
            double: If ``True``, send a double-click.
        Requires ``enable_display=True``.
        """
        self._require_display()
        assert self._qmp_client is not None
        await self._qmp_client.mouse_click(button, double=double)

    async def screenshot(self, path: str | Path) -> None:
        """Save the current guest display as a PNG file on the host.

        QEMU writes the raw framebuffer; this method converts it to a proper
        PNG regardless of whether QEMU was compiled with PNG support (it often
        outputs PPM binary data even when given a .png filename).

        Args:
            path: Destination path on the host for the PNG file.
        Requires ``enable_display=True``.
        """
        import tempfile

        self._require_display()
        assert self._qmp_client is not None

        dest = Path(path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Capture to a temp file so we can inspect and convert if needed.
        with tempfile.NamedTemporaryFile(suffix=".ppm", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            await self._qmp_client.screendump(tmp_path)
            _convert_to_png(tmp_path, str(dest))
        finally:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()

    async def query_display_size(self) -> tuple[int, int]:
        """Return the guest display resolution as ``(width, height)`` in pixels.

        Takes a screendump and reads the PPM header to get the actual
        framebuffer dimensions.  There is no QMP command that directly
        returns the display resolution.

        Requires ``enable_display=True``.
        """
        import tempfile

        self._require_display()
        assert self._qmp_client is not None

        with tempfile.NamedTemporaryFile(suffix=".ppm", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            await self._qmp_client.screendump(tmp_path)
            header = Path(tmp_path).read_bytes()[:128]
            # PPM P6 header: "P6\n<width> <height>\n<maxval>\n"
            # Skip comment lines starting with '#'
            lines = header.split(b"\n")
            data_lines = [line for line in lines if not line.startswith(b"#")]
            if len(data_lines) >= 2 and data_lines[0] == b"P6":
                parts = data_lines[1].split()
                width, height = int(parts[0]), int(parts[1])
                return (width, height)
            return (0, 0)
        finally:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()

    async def query_mouse_position(self) -> dict | None:
        """Return information about the current mouse device and position.

        Returns the dict for the currently-active mouse device from QMP
        ``query-mice``, or ``None`` if no mouse device is found.  The dict
        includes at least ``name``, ``absolute``, and ``current``.

        Requires ``enable_display=True``.
        """
        self._require_display()
        assert self._qmp_client is not None
        mice = await self._qmp_client.query_mice()
        for mouse in mice:
            if mouse.get("current"):
                return mouse
        return mice[0] if mice else None

    @property
    def vnc_port(self) -> int | None:
        """The host-side VNC port, or ``None`` if display is not enabled."""
        return self._vnc_port
