"""Async client for quicksand guest agent over virtio-serial (Unix/TCP socket).

Uses length-prefixed JSON framing over a QEMU chardev socket. A background
reader task demultiplexes incoming frames by ``id`` into per-request futures
(or per-stream queues), so caller timeouts and concurrent calls cannot corrupt
each other's reads.

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
    payload = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    return struct.pack(_HEADER_FMT, len(payload)) + payload


async def _read_frame(reader: asyncio.StreamReader) -> dict:
    header = await reader.readexactly(_HEADER_SIZE)
    (length,) = struct.unpack(_HEADER_FMT, header)
    if length > _MAX_FRAME_SIZE:
        raise RuntimeError(f"Frame too large: {length} bytes")
    payload = await reader.readexactly(length)
    return json.loads(payload)


class VirtioSerialAgentClient:
    """Async client for the quicksand guest agent over virtio-serial.

    Communicates via QEMU's chardev Unix domain socket (or TCP on Windows)
    using length-prefixed JSON framing. After authentication, a background
    reader task demultiplexes responses by ``id``: one-shot calls register
    a future, streaming calls register a queue. Late frames whose ``id`` has
    no waiter (e.g. response to a request whose caller already timed out)
    are dropped at the reader.
    """

    def __init__(self, socket_path: str | Path, token: str):
        self._socket_path = str(socket_path)
        self._token = token
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._writer_lock: asyncio.Lock | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._pending_streams: dict[int, asyncio.Queue[dict]] = {}
        self._request_id = 0

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and self._reader_task is not None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def connect(
        self,
        timeout: float = Timeouts.BOOT_DEFAULT,
        process_check: Callable[[], tuple[bool, str]] | None = None,
    ) -> None:
        """Connect to the agent via the chardev socket and authenticate.

        Auth is content-keyed (the agent's authenticated reply has no ``id``),
        so it runs synchronously on the bare reader. Once auth succeeds the
        demux reader task takes ownership of ``self._reader`` and all later
        request/response routing goes through it.

        Once connected to the QEMU chardev socket, the connection is kept
        open across auth retries.  Disconnecting causes QEMU to stop
        accepting new connections on the chardev, so reconnection is only
        attempted on hard connection errors (socket gone, refused, reset).
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        last_error: Exception | None = None
        attempt = 0
        # Auth frames written whose response we haven't consumed yet. Each
        # auth-read TimeoutError leaves one such frame queued in the chardev
        # buffer; when the agent eventually opens the device it will reply
        # to every queued auth in order. We drain those stale replies after
        # the successful one so the demux reader doesn't see them.
        auth_writes_pending = 0

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
                auth_writes_pending += 1

                remaining = max(deadline - loop.time(), 1.0)
                read_timeout = min(Timeouts.GUEST_AGENT_HTTP, remaining)
                response = await asyncio.wait_for(_read_frame(self._reader), timeout=read_timeout)
                auth_writes_pending -= 1
                result = response.get("result", {})
                if result.get("authenticated"):
                    logger.debug("Virtio-serial agent authentication successful")
                    # Drain replies to any earlier auth writes that timed out.
                    # By the time we got our successful reply, those replies
                    # are already in the host-side buffer, so a short timeout
                    # is sufficient.
                    drained = 0
                    while drained < auth_writes_pending:
                        try:
                            _ = await asyncio.wait_for(_read_frame(self._reader), timeout=0.5)
                            drained += 1
                        except TimeoutError:
                            break
                    if drained:
                        logger.debug("Drained %d stale auth responses", drained)
                    # Reset request ID so future requests start fresh
                    self._request_id = 0
                    # Spawn the demux reader. From here on, only this task
                    # touches ``self._reader``; callers wait on per-id futures
                    # or queues registered in ``_pending`` / ``_pending_streams``.
                    self._writer_lock = asyncio.Lock()
                    self._reader_task = asyncio.create_task(self._read_loop())
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
                # QEMU drops any chardev-buffered frames when the client
                # disconnects, so the next connection starts fresh.
                auth_writes_pending = 0
                await asyncio.sleep(0.1)

        raise TimeoutError(
            f"Could not connect to agent within {timeout}s via {self._socket_path}. "
            f"Last error: {last_error}"
        )

    async def _read_loop(self) -> None:
        """Background task: demultiplex incoming frames by ``id``.

        Stream requests register an ``asyncio.Queue`` (frames pushed in order,
        terminated by an ``exit`` or ``error`` frame). One-shot requests
        register an ``asyncio.Future`` (resolved by the first matching frame).
        Frames whose ``id`` has no waiter are dropped.
        """
        assert self._reader is not None
        try:
            while True:
                frame = await _read_frame(self._reader)
                frame_id = frame.get("id")
                if not isinstance(frame_id, int):
                    logger.debug("Dropping frame without integer id: %r", frame)
                    continue
                queue = self._pending_streams.get(frame_id)
                if queue is not None:
                    queue.put_nowait(frame)
                    continue
                fut = self._pending.pop(frame_id, None)
                if fut is not None and not fut.done():
                    fut.set_result(frame)
                else:
                    logger.debug("Dropping frame with no waiter id=%d", frame_id)
        except asyncio.IncompleteReadError:
            self._fail_pending(ConnectionResetError("agent connection closed"))
        except (ConnectionResetError, OSError) as e:
            self._fail_pending(e)
        except asyncio.CancelledError:
            self._fail_pending(ConnectionResetError("client closed"))
            raise
        except Exception as e:  # pragma: no cover — unexpected
            logger.exception("Unexpected error in agent reader loop")
            self._fail_pending(e)

    def _fail_pending(self, exc: BaseException) -> None:
        """Wake every outstanding caller with a connection error."""
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()
        for queue in self._pending_streams.values():
            queue.put_nowait({"error": {"message": f"Connection error: {exc}"}})
        self._pending_streams.clear()

    def _close_transport(self) -> None:
        if self._writer:
            with contextlib.suppress(Exception):
                self._writer.close()
        self._reader = None
        self._writer = None

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(BaseException):
                await self._reader_task
            self._reader_task = None
        self._close_transport()

    async def send_request(
        self,
        method: QuicksandGuestAgentMethod,
        params: dict[str, Any],
        timeout: float = Timeouts.GUEST_AGENT_REQUEST,
    ) -> dict[str, Any]:
        if self._writer is None or self._reader_task is None or self._writer_lock is None:
            raise RuntimeError("Not connected to agent")

        method_map = {
            QuicksandGuestAgentMethod.EXECUTE: "execute",
            QuicksandGuestAgentMethod.PING: "ping",
        }
        method_name = method_map.get(method)
        if method_name is None:
            return {"error": {"message": f"Unknown method: {method}"}}

        request_id = self._next_id()
        loop = asyncio.get_event_loop()
        future: asyncio.Future[dict] = loop.create_future()
        # Register BEFORE writing so a fast reply can't arrive before we have
        # a slot in ``_pending``.
        self._pending[request_id] = future

        msg = {"id": request_id, "method": method_name, "params": params}

        try:
            async with self._writer_lock:
                self._writer.write(_encode_frame(msg))
                await self._writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            self._pending.pop(request_id, None)
            return {"error": {"message": f"Connection error: {e}"}}

        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            # Caller gives up: pop our slot so a late reply is dropped at
            # the reader instead of poisoning the next call's read.
            self._pending.pop(request_id, None)
            return {"error": {"message": f"Request timed out after {timeout}s"}}
        except (ConnectionResetError, OSError) as e:
            return {"error": {"message": f"Connection error: {e}"}}

        if "error" in response:
            return {"error": response["error"]}
        return {"result": response.get("result", response)}

    async def send_stream_request(
        self,
        params: dict[str, Any],
        timeout: float,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if self._writer is None or self._reader_task is None or self._writer_lock is None:
            raise RuntimeError("Not connected to agent")

        request_id = self._next_id()
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._pending_streams[request_id] = queue

        msg = {"id": request_id, "method": "execute_stream", "params": params}

        try:
            async with self._writer_lock:
                self._writer.write(_encode_frame(msg))
                await self._writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            self._pending_streams.pop(request_id, None)
            return {"error": {"message": f"Stream connection error: {e}"}}

        try:
            return await asyncio.wait_for(
                self._consume_stream(queue, on_stdout, on_stderr),
                timeout=timeout,
            )
        except TimeoutError:
            return {"error": {"message": f"Stream timed out after {timeout}s"}}
        finally:
            # Drop any further frames for this id (the reader will log+skip
            # them once we're no longer registered).
            self._pending_streams.pop(request_id, None)

    async def _consume_stream(
        self,
        queue: asyncio.Queue[dict],
        on_stdout: Callable[[str], None] | None,
        on_stderr: Callable[[str], None] | None,
    ) -> dict[str, Any]:
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        while True:
            frame = await queue.get()
            if "error" in frame:
                return {"error": frame["error"]}
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
                return {
                    "result": {
                        "stdout": "".join(stdout_parts),
                        "stderr": "".join(stderr_parts),
                        "exit_code": frame.get("exit_code", -1),
                    }
                }
