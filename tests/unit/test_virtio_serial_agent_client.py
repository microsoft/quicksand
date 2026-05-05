"""Unit tests for VirtioSerialAgentClient framing/demux behavior."""

from __future__ import annotations

import asyncio
import contextlib
import json
import struct
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from quicksand_core._types import QuicksandGuestAgentMethod
from quicksand_core.host.virtio_serial_agent_client import VirtioSerialAgentClient

_HEADER_FMT = "!I"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_TEST_TOKEN = "test-token"


def _encode_frame(msg: dict) -> bytes:
    payload = json.dumps(msg).encode()
    return struct.pack(_HEADER_FMT, len(payload)) + payload


async def _read_frame(reader: asyncio.StreamReader) -> dict:
    header = await reader.readexactly(_HEADER_SIZE)
    (length,) = struct.unpack(_HEADER_FMT, header)
    payload = await reader.readexactly(length)
    return json.loads(payload)


def _ok(request_id: int, stdout: str = "") -> dict:
    return {
        "id": request_id,
        "result": {"stdout": stdout, "stderr": "", "exit_code": 0},
    }


# Handler signature: receives a non-auth request frame, returns reply frames.
Handler = Callable[[dict], Awaitable[list[dict]]]


class FakeAgent:
    """Server speaking the agent wire protocol on a Unix socket.

    Auto-replies to ``authenticate`` with an id-less authenticated frame.
    Dispatches every other request frame to ``handler`` and writes the
    returned frames back to the same connection.
    """

    def __init__(self, sock_path: Path, handler: Handler):
        self._sock_path = sock_path
        self._handler = handler
        self._server: asyncio.AbstractServer | None = None
        self._writers: list[asyncio.StreamWriter] = []
        self._handler_tasks: list[asyncio.Task] = []
        self.received_requests: list[dict] = []

    async def __aenter__(self) -> FakeAgent:
        self._server = await asyncio.start_unix_server(self._on_connect, path=str(self._sock_path))
        return self

    async def __aexit__(self, *_exc_info) -> None:
        for task in self._handler_tasks:
            task.cancel()
        for task in self._handler_tasks:
            with contextlib.suppress(BaseException):
                await task
        for writer in self._writers:
            with contextlib.suppress(Exception):
                writer.close()
        for writer in self._writers:
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def disconnect_all(self) -> None:
        """Close every active server-side connection."""
        for writer in list(self._writers):
            with contextlib.suppress(Exception):
                writer.close()

    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.append(writer)
        try:
            while True:
                msg = await _read_frame(reader)
                self.received_requests.append(msg)
                if msg.get("method") == "authenticate":
                    writer.write(_encode_frame({"result": {"authenticated": True}}))
                    await writer.drain()
                    continue
                self._handler_tasks.append(asyncio.create_task(self._handle(msg, writer)))
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            pass

    async def _handle(self, msg: dict, writer: asyncio.StreamWriter) -> None:
        try:
            replies = await self._handler(msg)
        except asyncio.CancelledError:
            return
        for reply in replies:
            try:
                writer.write(_encode_frame(reply))
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                return


@pytest.fixture
def sock_path(tmp_path: Path) -> Path:
    return tmp_path / "agent.sock"


async def _connected_client(sock_path: Path) -> VirtioSerialAgentClient:
    client = VirtioSerialAgentClient(sock_path, token=_TEST_TOKEN)
    await client.connect(timeout=5.0)
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_wrong_id_frame_does_not_raise_mismatch(tmp_path: Path) -> None:
    """A frame whose id matches no outstanding request is dropped at the
    reader; the caller times out without surfacing 'Response ID mismatch'."""

    async def fake_agent(_reader, writer):
        for msg in [
            {"result": {"authenticated": True}},
            {"id": 99, "result": {"stdout": "", "stderr": "", "exit_code": 0}},
        ]:
            b = json.dumps(msg).encode()
            writer.write(struct.pack("!I", len(b)) + b)
        await writer.drain()

    sock = tmp_path / "agent.sock"
    server = await asyncio.start_unix_server(fake_agent, path=str(sock))
    client = VirtioSerialAgentClient(sock, token=_TEST_TOKEN)
    try:
        await client.connect(timeout=5.0)
        result = await client.send_request(
            QuicksandGuestAgentMethod.EXECUTE,
            {"command": "x"},
            timeout=0.5,
        )
        assert "error" in result
        assert "Response ID mismatch" not in result["error"]["message"], result
    finally:
        await client.close()
        server.close()


async def test_concurrent_calls_each_get_their_own_response(sock_path: Path) -> None:
    """Five concurrent calls; the agent replies in reverse-id order so wire
    order is the opposite of call order. Each caller receives its own
    response, routed by id."""

    num_requests = 5

    async def handler(msg: dict) -> list[dict]:
        # Higher ids reply first — wire order is reversed from call order.
        delay = (num_requests + 1 - msg["id"]) * 0.02
        await asyncio.sleep(delay)
        return [_ok(msg["id"], stdout=f"id={msg['id']}")]

    async with FakeAgent(sock_path, handler):
        client = await _connected_client(sock_path)
        try:
            results = await asyncio.gather(
                *[
                    client.send_request(
                        QuicksandGuestAgentMethod.EXECUTE,
                        {"command": f"req-{i}"},
                        timeout=2.0,
                    )
                    for i in range(num_requests)
                ]
            )
        finally:
            await client.close()

    for i, r in enumerate(results, start=1):
        assert "result" in r, r
        assert r["result"]["stdout"] == f"id={i}"


async def test_timeout_does_not_poison_subsequent_calls(sock_path: Path) -> None:
    """Request A times out on the host; its late reply is dropped at the
    reader. Request B's reply is delivered correctly."""

    async def handler(msg: dict) -> list[dict]:
        if msg["id"] == 1:
            await asyncio.sleep(0.5)
        return [_ok(msg["id"], stdout=f"id={msg['id']}")]

    async with FakeAgent(sock_path, handler):
        client = await _connected_client(sock_path)
        try:
            r1 = await client.send_request(
                QuicksandGuestAgentMethod.EXECUTE,
                {"command": "first"},
                timeout=0.1,
            )
            r2 = await client.send_request(
                QuicksandGuestAgentMethod.EXECUTE,
                {"command": "second"},
                timeout=2.0,
            )
        finally:
            await client.close()

    assert "error" in r1
    assert "timed out" in r1["error"]["message"].lower()
    assert "result" in r2, r2
    assert r2["result"]["stdout"] == "id=2"


async def test_connection_drop_fails_pending_callers_fast(sock_path: Path) -> None:
    """When the agent disconnects mid-request, the pending caller wakes up
    with a connection error well before the request timeout would fire."""

    async def handler(msg: dict) -> list[dict]:
        await asyncio.sleep(10)
        return [_ok(msg["id"])]

    async with FakeAgent(sock_path, handler) as agent:
        client = await _connected_client(sock_path)
        try:

            async def disconnect_after_delay():
                await asyncio.sleep(0.1)
                await agent.disconnect_all()

            disconnect_task = asyncio.create_task(disconnect_after_delay())
            try:
                start = asyncio.get_event_loop().time()
                result = await client.send_request(
                    QuicksandGuestAgentMethod.EXECUTE,
                    {"command": "x"},
                    timeout=10.0,
                )
                elapsed = asyncio.get_event_loop().time() - start
            finally:
                await disconnect_task
        finally:
            await client.close()

    assert "error" in result
    assert "Connection error" in result["error"]["message"]
    assert elapsed < 1.0, f"Caller waited {elapsed:.2f}s; should fail fast"


async def test_stream_request_accumulates_multi_frame_output(sock_path: Path) -> None:
    """A streaming request emits multiple frames sharing one id; the client
    accumulates stdout/stderr across frames, invokes per-chunk callbacks,
    and terminates on the exit frame."""

    chunks_seen: list[tuple[str, str]] = []

    async def handler(msg: dict) -> list[dict]:
        rid = msg["id"]
        return [
            {"id": rid, "stream": "stdout", "data": "hello "},
            {"id": rid, "stream": "stdout", "data": "world\n"},
            {"id": rid, "stream": "stderr", "data": "warn\n"},
            {"id": rid, "stream": "exit", "exit_code": 0},
        ]

    async with FakeAgent(sock_path, handler):
        client = await _connected_client(sock_path)
        try:
            result = await client.send_stream_request(
                {"command": "x"},
                timeout=2.0,
                on_stdout=lambda d: chunks_seen.append(("out", d)),
                on_stderr=lambda d: chunks_seen.append(("err", d)),
            )
        finally:
            await client.close()

    assert "result" in result, result
    assert result["result"]["stdout"] == "hello world\n"
    assert result["result"]["stderr"] == "warn\n"
    assert result["result"]["exit_code"] == 0
    assert chunks_seen == [
        ("out", "hello "),
        ("out", "world\n"),
        ("err", "warn\n"),
    ]
