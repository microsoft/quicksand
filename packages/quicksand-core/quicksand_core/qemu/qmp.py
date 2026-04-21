"""QEMU Machine Protocol (QMP) client.

QMP is a JSON-RPC protocol exposed by QEMU for host-side VM control
(block device snapshots, pause/resume, status queries, etc.).
It is NOT for executing commands inside the guest — that is the guest agent's job.

We use TCP transport (127.0.0.1:port) so the same code works on all platforms.
Unix sockets are not supported on Windows.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

logger = logging.getLogger("quicksand.qmp")

# ---------------------------------------------------------------------------
# Character → QKeyCode mapping used by QMPClient.type_text()
# ---------------------------------------------------------------------------
# Each entry: char → (key_codes, needs_shift)
# key_codes is the list of keys to pass to send-key (without the shift modifier).
# ---------------------------------------------------------------------------

_CHAR_TO_KEYS: dict[str, tuple[list[str], bool]] = {
    # Lowercase letters
    **{c: ([c], False) for c in "abcdefghijklmnopqrstuvwxyz"},
    # Uppercase letters
    **{c.upper(): ([c], True) for c in "abcdefghijklmnopqrstuvwxyz"},
    # Digits
    **{str(n): ([str(n)], False) for n in range(10)},
    # Whitespace / control
    " ": (["spc"], False),
    "\n": (["ret"], False),
    "\t": (["tab"], False),
    # Unshifted symbols
    "-": (["minus"], False),
    "=": (["equal"], False),
    "[": (["bracket_left"], False),
    "]": (["bracket_right"], False),
    "\\": (["backslash"], False),
    ";": (["semicolon"], False),
    "'": (["apostrophe"], False),
    "`": (["grave_accent"], False),
    ",": (["comma"], False),
    ".": (["dot"], False),
    "/": (["slash"], False),
    # Shifted symbols
    "!": (["1"], True),
    "@": (["2"], True),
    "#": (["3"], True),
    "$": (["4"], True),
    "%": (["5"], True),
    "^": (["6"], True),
    "&": (["7"], True),
    "*": (["8"], True),
    "(": (["9"], True),
    ")": (["0"], True),
    "_": (["minus"], True),
    "+": (["equal"], True),
    "{": (["bracket_left"], True),
    "}": (["bracket_right"], True),
    "|": (["backslash"], True),
    ":": (["semicolon"], True),
    '"': (["apostrophe"], True),
    "~": (["grave_accent"], True),
    "<": (["comma"], True),
    ">": (["dot"], True),
    "?": (["slash"], True),
}


class QMPClient:
    """Async QMP client over TCP.

    Usage:
        client = QMPClient("127.0.0.1", port)
        await client.connect(timeout=30)
        await client.execute("blockdev-snapshot-sync",
                       device="drive0",
                       **{"snapshot-file": "/path/new.qcow2"},
                       format="qcow2")
        await client.close()
    """

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._buf = ""

    async def connect(self, timeout: float = 30.0) -> None:
        """Connect and complete the QMP capability negotiation.

        QEMU sends a greeting immediately on connect; we respond with
        qmp_capabilities to enter command mode.

        Raises:
            TimeoutError: If QEMU does not accept the connection within timeout.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        last_exc: Exception = ConnectionRefusedError("QMP port not yet open")

        while asyncio.get_event_loop().time() < deadline:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port),
                    timeout=2.0,
                )
                self._reader = reader
                self._writer = writer

                # QEMU sends greeting immediately
                greeting = await self._recv_json()
                if "QMP" not in greeting:
                    raise RuntimeError(f"Unexpected QMP greeting: {greeting}")

                # Enter command mode
                await self._send_json({"execute": "qmp_capabilities"})
                resp = await self._recv_json()
                if "error" in resp:
                    raise RuntimeError(f"qmp_capabilities failed: {resp['error']}")

                logger.debug("QMP connected on port %d", self._port)
                return

            except (ConnectionRefusedError, OSError) as e:
                last_exc = e
                if self._writer:
                    self._writer.close()
                    with contextlib.suppress(Exception):
                        await self._writer.wait_closed()
                    self._writer = None
                    self._reader = None
                await asyncio.sleep(0.05)

        raise TimeoutError(
            f"Could not connect to QMP on 127.0.0.1:{self._port} within {timeout}s: {last_exc}"
        )

    async def execute(self, command: str, **arguments: object) -> dict:
        """Send a QMP command and return the response.

        Async events (prefixed with "event") that arrive before the response
        are silently discarded — we only care about command responses here.

        Args:
            command: QMP command name (e.g. "blockdev-snapshot-sync").
            **arguments: Command arguments. Use ** unpacking for hyphenated keys:
                client.execute("cmd", **{"hyphen-key": value})

        Returns:
            The full QMP response dict (contains "return" or "error" key).

        Raises:
            RuntimeError: If not connected or QEMU returns an error.
        """
        if self._writer is None:
            raise RuntimeError("QMPClient is not connected")

        msg: dict = {"execute": command}
        if arguments:
            msg["arguments"] = arguments

        await self._send_json(msg)

        # Skip async events until we get the command response
        while True:
            resp = await self._recv_json()
            if "return" in resp or "error" in resp:
                if "error" in resp:
                    raise RuntimeError(f"QMP command '{command}' failed: {resp['error']}")
                return resp
            # Event — log and discard
            logger.debug("QMP event: %s", resp.get("event", resp))

    async def close(self) -> None:
        """Close the QMP connection."""
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except OSError:
                pass
            finally:
                self._writer = None
                self._reader = None

    # ------------------------------------------------------------------
    # Input injection
    # ------------------------------------------------------------------

    async def send_key(self, keys: list[str], hold_time: int = 100) -> None:
        """Send a key combination to the guest.

        Args:
            keys: List of Key enum values or QKeyCode strings to press
                  simultaneously, e.g. [Key.CTRL, Key.C] or [Key.A].
            hold_time: How long to hold the keys in milliseconds.
        """
        key_values = [{"type": "qcode", "data": k} for k in keys]
        await self.execute("send-key", keys=key_values, **{"hold-time": hold_time})

    async def input_send_event(self, events: list[dict]) -> None:
        """Send raw input events via QMP input-send-event.

        Each event dict must have "type" and "data" matching the QAPI InputEvent schema.
        See: https://www.qemu.org/docs/master/interop/qemu-qmp-ref.html
        """
        await self.execute("input-send-event", events=events)

    async def type_text(self, text: str) -> None:
        """Type a string into the guest by sending key events per character.

        Handles uppercase letters and common symbols via shift key combinations.
        Unsupported characters are silently skipped.
        """
        for char in text:
            mapping = _CHAR_TO_KEYS.get(char)
            if mapping is None:
                logger.debug("type_text: skipping unsupported character %r", char)
                continue
            key_codes, needs_shift = mapping
            keys = (["shift", *key_codes]) if needs_shift else key_codes
            # hold_time=1ms keeps the key press short so QMP returns fast.
            # The default 100ms causes send-key to block for 100ms per character,
            # making type_text take 3+ seconds for a typical command.
            await self.send_key(keys, hold_time=1)

    async def mouse_move(self, x: int, y: int) -> None:
        """Move the mouse to absolute coordinates (0-32767 range).

        Args:
            x: Absolute X coordinate (0 = left, 32767 = right).
            y: Absolute Y coordinate (0 = top, 32767 = bottom).
        """
        await self.input_send_event(
            [
                {"type": "abs", "data": {"axis": "x", "value": x}},
                {"type": "abs", "data": {"axis": "y", "value": y}},
            ]
        )

    async def mouse_click(self, button: str = "left", *, double: bool = False) -> None:
        """Click a mouse button.

        Args:
            button: One of "left", "middle", "right", "wheel-up", "wheel-down".
            double: If True, send two click sequences for a double-click.
        """
        down = {"type": "btn", "data": {"button": button, "down": True}}
        up = {"type": "btn", "data": {"button": button, "down": False}}
        # Send down and up as separate calls so window managers that trigger on
        # "Press" (button-down only) — such as Openbox's root-window menu —
        # see a distinct down event before the up arrives.
        await self.input_send_event([down])
        await self.input_send_event([up])
        if double:
            # Brief pause so the window manager registers a distinct second
            # click rather than merging the events into one.
            await asyncio.sleep(0.05)
            await self.input_send_event([down])
            await self.input_send_event([up])

    async def screendump(self, path: str) -> None:
        """Save the current guest display as an image file on the host.

        QEMU writes the file directly; no guest involvement required.
        Requires the VM to have a display device (enable_display=True in config).

        Args:
            path: Absolute host path for the output file (PNG or PPM).
        """
        await self.execute("screendump", filename=path)

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    async def query_mice(self) -> list[dict]:
        """Query the connected mouse devices and current pointer position.

        Returns a list of mouse device dicts.  Each dict includes at least
        ``name``, ``absolute`` (whether the device uses absolute coords),
        and ``current`` (True for the active device).

        Returns:
            The raw QMP ``query-mice`` response list.
        """
        return (await self.execute("query-mice"))["return"]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send_json(self, obj: dict) -> None:
        assert self._writer is not None
        data = (json.dumps(obj) + "\r\n").encode()
        self._writer.write(data)
        await self._writer.drain()

    async def _recv_json(self) -> dict:
        """Read one complete JSON line from the stream."""
        assert self._reader is not None
        while "\n" not in self._buf:
            chunk = await self._reader.read(4096)
            if not chunk:
                raise ConnectionError("QMP connection closed by QEMU")
            self._buf += chunk.decode()
        line, self._buf = self._buf.split("\n", 1)
        return json.loads(line.strip())
