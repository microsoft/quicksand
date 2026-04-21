"""Async client for quicksand guest agent over virtio-serial (Unix/TCP socket).

Uses length-prefixed JSON framing over a QEMU chardev socket, bypassing
the guest networking stack entirely.

Frame format:
    ┌──────────┬─────────────────────┐
    │ 4 bytes  │ N bytes             │
    │ length N │ JSON payload (UTF-8)│
    │ (big-end)│                     │
    └──────────┴─────────────────────┘
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .._types import QuicksandGuestAgentMethod, Timeouts

logger = logging.getLogger("quicksand.virtio_agent")

_HEADER_FMT = "!I"  # 4-byte big-endian unsigned int
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_MAX_FRAME_SIZE = 64 * 1024 * 1024  # 64 MB sanity limit


def _encode_frame(msg: dict) -> bytes:
    """Encode a message as a length-prefixed JSON frame."""
    payload = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    return struct.pack(_HEADER_FMT, len(payload)) + payload


async def _read_frame(reader: asyncio.StreamReader) -> dict:
    """Read one length-prefixed JSON frame from the stream."""
    header = await reader.readexactly(_HEADER_SIZE)
    (length,) = struct.unpack(_HEADER_FMT, header)
    if length > _MAX_FRAME_SIZE:
        raise RuntimeError(f"Frame too large: {length} bytes")
    payload = await reader.readexactly(length)
    return json.loads(payload)


class VirtioSerialAgentClient:
    """Async client for the quicksand guest agent over virtio-serial.

    Communicates via QEMU's chardev Unix domain socket (or TCP on Windows)
    using length-prefixed JSON framing. Provides the same interface as
    QuicksandGuestAgentClient.
    """

    def __init__(self, socket_path: str | Path, token: str):
        self._socket_path = str(socket_path)
        self._token = token
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock: asyncio.Lock | None = None
        self._request_id = 0

    @property
    def is_connected(self) -> bool:
        return self._writer is not None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def connect(
        self,
        timeout: float = Timeouts.BOOT_DEFAULT,
        process_check: Callable[[], tuple[bool, str]] | None = None,
    ) -> None:
        """Connect to the agent via the chardev socket and authenticate.

        Retries until the socket is ready (QEMU creates it at launch) and
        the guest agent sends a valid authentication response.

        Once connected to the QEMU chardev socket, the connection is kept
        open across auth retries.  Disconnecting causes QEMU to stop
        accepting new connections on the chardev, so reconnection is only
        attempted on hard connection errors (socket gone, refused, reset).
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        last_error: Exception | None = None
        attempt = 0

        logger.debug("Connecting to agent via virtio-serial: %s", self._socket_path)

        while loop.time() < deadline:
            attempt += 1

            if process_check:
                is_running, error_info = process_check()
                if not is_running:
                    raise RuntimeError(error_info)

            # Re-use an existing connection if we already have one
            # (previous auth timed out waiting for the guest agent to open
            # the virtio-serial device).
            if self._writer is None:
                try:
                    reader, writer = await asyncio.open_unix_connection(self._socket_path)
                    self._reader = reader
                    self._writer = writer
                except (OSError, ConnectionRefusedError) as e:
                    logger.debug("Connection attempt %d failed: %s", attempt, e)
                    last_error = e
                    await asyncio.sleep(0.1)
                    continue

            assert self._writer is not None and self._reader is not None
            try:
                # Authenticate
                auth_msg = {
                    "id": self._next_id(),
                    "method": "authenticate",
                    "params": {"token": self._token},
                }
                self._writer.write(_encode_frame(auth_msg))
                await self._writer.drain()

                remaining = max(deadline - loop.time(), 1.0)
                read_timeout = min(Timeouts.GUEST_AGENT_HTTP, remaining)
                response = await asyncio.wait_for(_read_frame(self._reader), timeout=read_timeout)
                result = response.get("result", {})
                if result.get("authenticated"):
                    logger.debug("Virtio-serial agent authentication successful")
                    # Drain stale auth responses from previous retries
                    drained = 0
                    stale_expected = attempt - 1
                    while drained < stale_expected:
                        try:
                            _ = await asyncio.wait_for(_read_frame(self._reader), timeout=2.0)
                            drained += 1
                        except TimeoutError:
                            break
                    if drained:
                        logger.debug("Drained %d stale auth responses", drained)
                    # Reset request ID so future requests start fresh
                    self._request_id = 0
                    return

                raise RuntimeError("Authentication rejected by agent")

            except TimeoutError:
                # Guest agent hasn't opened the device yet — keep the
                # connection open and retry.
                logger.debug("Connection attempt %d: auth read timed out, retrying", attempt)
                last_error = TimeoutError("auth read timed out")
                continue

            except (
                OSError,
                ConnectionRefusedError,
                ConnectionResetError,
                asyncio.IncompleteReadError,
                RuntimeError,
            ) as e:
                logger.debug("Connection attempt %d failed: %s", attempt, e)
                last_error = e
                self._close_transport()
                await asyncio.sleep(0.1)

        raise TimeoutError(
            f"Could not connect to agent within {timeout}s via {self._socket_path}. "
            f"Last error: {last_error}"
        )

    def _close_transport(self) -> None:
        if self._writer:
            with contextlib.suppress(Exception):
                self._writer.close()
        self._reader = None
        self._writer = None

    async def close(self) -> None:
        self._close_transport()

    async def send_request(
        self,
        method: QuicksandGuestAgentMethod,
        params: dict[str, Any],
        timeout: float = Timeouts.GUEST_AGENT_REQUEST,
    ) -> dict[str, Any]:
        if self._writer is None or self._reader is None:
            raise RuntimeError("Not connected to agent")

        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            method_map = {
                QuicksandGuestAgentMethod.EXECUTE: "execute",
                QuicksandGuestAgentMethod.PING: "ping",
            }
            method_name = method_map.get(method)
            if method_name is None:
                return {"error": {"message": f"Unknown method: {method}"}}

            msg = {"id": self._next_id(), "method": method_name, "params": params}

            try:
                self._writer.write(_encode_frame(msg))
                await self._writer.drain()

                response = await asyncio.wait_for(_read_frame(self._reader), timeout=timeout)

                resp_id = response.get("id")
                if resp_id != msg["id"]:
                    return {
                        "error": {
                            "message": (
                                f"Response ID mismatch: expected {msg['id']}, got {resp_id}"
                            )
                        }
                    }

                if "error" in response:
                    return {"error": response["error"]}
                return {"result": response.get("result", response)}

            except TimeoutError:
                return {"error": {"message": f"Request timed out after {timeout}s"}}
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                return {"error": {"message": f"Connection error: {e}"}}

    async def send_stream_request(
        self,
        params: dict[str, Any],
        timeout: float,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if self._writer is None or self._reader is None:
            raise RuntimeError("Not connected to agent")

        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            msg = {"id": self._next_id(), "method": "execute_stream", "params": params}
            stdout_parts: list[str] = []
            stderr_parts: list[str] = []
            exit_code = -1

            try:
                self._writer.write(_encode_frame(msg))
                await self._writer.drain()

                deadline = asyncio.get_event_loop().time() + timeout
                while True:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        return {"error": {"message": f"Stream timed out after {timeout}s"}}

                    frame = await asyncio.wait_for(_read_frame(self._reader), timeout=remaining)

                    frame_id = frame.get("id")
                    if frame_id != msg["id"]:
                        return {
                            "error": {
                                "message": (
                                    f"Stream response ID mismatch: expected {msg['id']}, "
                                    f"got {frame_id}"
                                )
                            }
                        }

                    stream = frame.get("stream")
                    if stream == "stdout":
                        data = frame.get("data", "")
                        stdout_parts.append(data)
                        if on_stdout:
                            on_stdout(data)
                    elif stream == "stderr":
                        data = frame.get("data", "")
                        stderr_parts.append(data)
                        if on_stderr:
                            on_stderr(data)
                    elif stream == "exit":
                        exit_code = frame.get("exit_code", -1)
                        break
                    elif "error" in frame:
                        return {"error": frame["error"]}

                return {
                    "result": {
                        "stdout": "".join(stdout_parts),
                        "stderr": "".join(stderr_parts),
                        "exit_code": exit_code,
                    }
                }

            except TimeoutError:
                return {"error": {"message": f"Stream timed out after {timeout}s"}}
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                return {"error": {"message": f"Stream connection error: {e}"}}
