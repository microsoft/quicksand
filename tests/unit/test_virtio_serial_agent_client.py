"""Unit tests for VirtioSerialAgentClient framing/demux behavior."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import socket
import struct
import sys
from collections.abc import Awaitable, Callable
from unittest.mock import patch

import pytest
from quicksand_core._types import QuicksandGuestAgentMethod
from quicksand_core.host.virtio_serial_agent_client import VirtioSerialAgentClient

_HEADER_FMT = "!I"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_TEST_TOKEN = "test-token"

# On Windows the client connects with asyncio.open_connection(host, port) (there is
# no open_unix_connection); on Linux/macOS it uses asyncio.open_unix_connection(path).
_IS_WINDOWS = sys.platform == "win32"

# A dummy endpoint passed to the client constructor; the actual connection is
# always supplied by _patch_connect, so the value is never dialed.
_DUMMY_SOCKET_PATH = "/unused"
_DUMMY_SOCKET_PORT = 1


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


def _socketpair() -> tuple[socket.socket, socket.socket]:
    """Return a connected non-blocking socket pair (no filesystem path)."""
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)
    return a, b


def _make_client() -> VirtioSerialAgentClient:
    """Construct a client the way the current platform's lifecycle code does.

    Windows uses a loopback TCP port; Linux/macOS use a Unix socket path. The
    endpoint is a dummy because _patch_connect injects the real connection.
    """
    if _IS_WINDOWS:
        return VirtioSerialAgentClient(None, token=_TEST_TOKEN, socket_port=_DUMMY_SOCKET_PORT)
    return VirtioSerialAgentClient(_DUMMY_SOCKET_PATH, token=_TEST_TOKEN)


def _patch_connect(client_sock: socket.socket):
    """Patch the platform's connect call so the client uses *client_sock*.

    On Windows the client calls ``asyncio.open_connection(host, port)``
    On other platforms it calls ``asyncio.open_unix_connection(path)``.
    """
    if _IS_WINDOWS:
        real_open_connection = asyncio.open_connection

        async def _fake(*args, **kwargs):
            # FakeAgent's own server-side setup passes sock=...; let it through.
            if "sock" in kwargs:
                return await real_open_connection(*args, **kwargs)
            return await real_open_connection(sock=client_sock)

        return patch("asyncio.open_connection", side_effect=_fake)

    async def _fake(*_a, **_kw):
        return await asyncio.open_connection(sock=client_sock)

    return patch("asyncio.open_unix_connection", side_effect=_fake)


class FakeAgent:
    """Speaks the agent wire protocol over an in-memory socketpair.

    Auto-replies to ``authenticate`` with an id-less authenticated frame.
    Dispatches every other request frame to ``handler`` and writes the
    returned frames back to the same connection.
    """

    def __init__(self, handler: Handler, *, capabilities: list[str] | None = None):
        self._handler = handler
        self._capabilities = capabilities
        self._client_sock: socket.socket | None = None
        self._srv_writer: asyncio.StreamWriter | None = None
        self._serve_task: asyncio.Task | None = None
        self._handler_tasks: list[asyncio.Task] = []
        self.received_requests: list[dict] = []

    async def __aenter__(self) -> FakeAgent:
        c, s = _socketpair()
        self._client_sock = c
        reader, writer = await asyncio.open_connection(sock=s)
        self._srv_writer = writer
        self._serve_task = asyncio.create_task(self._serve(reader, writer))
        return self

    async def __aexit__(self, *_exc_info) -> None:
        if self._serve_task:
            self._serve_task.cancel()
            with contextlib.suppress(BaseException):
                await self._serve_task
        for t in self._handler_tasks:
            t.cancel()
        for t in self._handler_tasks:
            with contextlib.suppress(BaseException):
                await t
        if self._srv_writer:
            with contextlib.suppress(Exception):
                self._srv_writer.close()
        if self._client_sock:
            with contextlib.suppress(OSError):
                self._client_sock.close()

    @property
    def client_sock(self) -> socket.socket:
        assert self._client_sock is not None
        return self._client_sock

    async def disconnect_all(self) -> None:
        """Close the server-side connection."""
        if self._srv_writer:
            self._srv_writer.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                msg = await _read_frame(reader)
                self.received_requests.append(msg)
                if msg.get("method") == "authenticate":
                    result: dict = {"authenticated": True}
                    if self._capabilities is not None:
                        result["capabilities"] = self._capabilities
                    writer.write(_encode_frame({"result": result}))
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


async def _connected_client(agent: FakeAgent) -> VirtioSerialAgentClient:
    client = _make_client()
    with _patch_connect(agent.client_sock):
        await client.connect(timeout=5.0)
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_wrong_id_frame_does_not_raise_mismatch() -> None:
    """A frame whose id matches no outstanding request is dropped at the
    reader; the caller times out without surfacing 'Response ID mismatch'."""

    c, s = _socketpair()
    _, srv_writer = await asyncio.open_connection(sock=s)

    # Write auth reply + a mismatched-id response immediately.
    for msg in [
        {"result": {"authenticated": True}},
        {"id": 99, "result": {"stdout": "", "stderr": "", "exit_code": 0}},
    ]:
        srv_writer.write(_encode_frame(msg))
    await srv_writer.drain()

    client = _make_client()
    try:
        with _patch_connect(c):
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
        srv_writer.close()


async def test_concurrent_calls_each_get_their_own_response() -> None:
    """Five concurrent calls; the agent replies in reverse-id order so wire
    order is the opposite of call order. Each caller receives its own
    response, routed by id."""

    num_requests = 5

    async def handler(msg: dict) -> list[dict]:
        # Higher ids reply first — wire order is reversed from call order.
        delay = (num_requests + 1 - msg["id"]) * 0.02
        await asyncio.sleep(delay)
        return [_ok(msg["id"], stdout=f"id={msg['id']}")]

    async with FakeAgent(handler) as agent:
        client = await _connected_client(agent)
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


async def test_timeout_does_not_poison_subsequent_calls() -> None:
    """Request A times out on the host; its late reply is dropped at the
    reader. Request B's reply is delivered correctly."""

    async def handler(msg: dict) -> list[dict]:
        if msg["id"] == 1:
            await asyncio.sleep(0.5)
        return [_ok(msg["id"], stdout=f"id={msg['id']}")]

    async with FakeAgent(handler) as agent:
        client = await _connected_client(agent)
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


async def test_connection_drop_fails_pending_callers_fast() -> None:
    """When the agent disconnects mid-request, the pending caller wakes up
    with a connection error well before the request timeout would fire."""

    async def handler(msg: dict) -> list[dict]:
        await asyncio.sleep(10)
        return [_ok(msg["id"])]

    async with FakeAgent(handler) as agent:
        client = await _connected_client(agent)
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


async def test_stream_request_accumulates_multi_frame_output() -> None:
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

    async with FakeAgent(handler) as agent:
        client = await _connected_client(agent)
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


async def test_legacy_agent_rejects_stdin_without_consuming_it():
    produced = []

    async def source():
        produced.append(True)
        yield b"unused"

    async def handler(msg):
        raise AssertionError(f"Unexpected execution: {msg}")

    async with FakeAgent(handler) as agent:
        client = await _connected_client(agent)
        try:
            assert not client.supports_stdin
            result = await client.send_stream_request({"command": "cat"}, timeout=2, stdin=source())
        finally:
            await client.close()
    assert "does not support streaming stdin" in result["error"]["message"]
    assert not produced
    assert [r["method"] for r in agent.received_requests] == ["authenticate"]


async def test_stdin_and_output_progress_in_both_directions():
    prompt = asyncio.Event()
    echoed = asyncio.Event()
    output = []
    received = bytearray()
    execution_id = None

    async def source():
        await prompt.wait()
        yield b"first\n"
        await echoed.wait()
        yield b"\x00\xfflast"

    async def handler(msg):
        nonlocal execution_id
        request_id = msg["id"]
        if msg["method"] == "execute_stream":
            execution_id = request_id
            return [
                {"id": request_id, "stream": "ready"},
                {"id": request_id, "stream": "stdout", "data": "prompt>"},
            ]
        assert msg["method"] == "stdin"
        if msg["params"].get("eof"):
            return [
                {"id": request_id, "result": {"closed": True}},
                {"id": execution_id, "stream": "exit", "exit_code": 0},
            ]
        received.extend(base64.b64decode(msg["params"]["data"], validate=True))
        return [
            {"id": request_id, "result": {"closed": False}},
            {"id": execution_id, "stream": "stdout", "data": "accepted"},
        ]

    def on_stdout(chunk):
        output.append(chunk)
        if "prompt>" in "".join(output):
            prompt.set()
        if "accepted" in "".join(output):
            echoed.set()

    async with FakeAgent(handler, capabilities=["stdin_streaming"]) as agent:
        client = await _connected_client(agent)
        try:
            assert client.supports_stdin
            result = await client.send_stream_request(
                {"command": "cat"}, timeout=2, stdin=source(), on_stdout=on_stdout
            )
        finally:
            await client.close()
    assert result["result"]["stdout"] == "prompt>acceptedaccepted"
    assert received == b"first\n\x00\xfflast"
    assert not client.supports_stdin


async def test_concurrent_stdin_streams_do_not_mix_input():
    executions = {}

    async def handler(msg):
        request_id = msg["id"]
        stdin_id = msg["params"]["stdin_id"]
        if msg["method"] == "execute_stream":
            executions[stdin_id] = (request_id, bytearray())
            return [{"id": request_id, "stream": "ready"}]
        execution_id, data = executions[stdin_id]
        if msg["params"].get("eof"):
            return [
                {"id": request_id, "result": {"closed": True}},
                {"id": execution_id, "stream": "stdout", "data": data.decode()},
                {"id": execution_id, "stream": "exit", "exit_code": 0},
            ]
        data.extend(base64.b64decode(msg["params"]["data"]))
        return [{"id": request_id, "result": {"closed": False}}]

    async with FakeAgent(handler, capabilities=["stdin_streaming"]) as agent:
        client = await _connected_client(agent)
        try:
            results = await asyncio.gather(
                client.send_stream_request({"command": "cat"}, timeout=2, stdin=b"one"),
                client.send_stream_request({"command": "cat"}, timeout=2, stdin=b"two"),
            )
            assert not client._pending
            assert not client._pending_streams
        finally:
            await client.close()
    assert [result["result"]["stdout"] for result in results] == ["one", "two"]
    assert len(executions) == 2


async def test_cancel_during_stdin_ack_cleans_up_pending_requests():
    writing = asyncio.Event()
    cancelled = asyncio.Event()
    closed = asyncio.Event()

    async def source():
        try:
            yield b"first"
        finally:
            closed.set()

    async def handler(msg):
        request_id = msg["id"]
        if msg["method"] == "execute_stream":
            return [{"id": request_id, "stream": "ready"}]
        if msg["method"] == "stdin":
            writing.set()
            await asyncio.Event().wait()
        if msg["method"] == "cancel":
            cancelled.set()
            return [{"id": request_id, "result": {"cancelled": True}}]
        return [_ok(request_id, "still connected")]

    async with FakeAgent(handler, capabilities=["stdin_streaming"]) as agent:
        client = await _connected_client(agent)
        try:
            task = asyncio.create_task(
                client.send_stream_request({"command": "cat"}, timeout=5, stdin=source())
            )
            await asyncio.wait_for(writing.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cancelled.is_set()
            assert closed.is_set()
            assert not client._pending
            assert not client._pending_streams
            result = await client.send_request(QuicksandGuestAgentMethod.PING, {}, timeout=2)
            assert result["result"]["stdout"] == "still connected"
        finally:
            await client.close()


async def test_cancel_before_write_cancels_registered_future():
    async def handler(msg):
        return [_ok(msg["id"])]

    async with FakeAgent(handler) as agent:
        client = await _connected_client(agent)
        assert client._writer_lock is not None
        await client._writer_lock.acquire()
        try:
            task = asyncio.create_task(
                client.send_request(QuicksandGuestAgentMethod.PING, {}, timeout=2)
            )
            await asyncio.sleep(0)
            future = next(iter(client._pending.values()))
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert future.cancelled()
            assert not client._pending
        finally:
            client._writer_lock.release()
            await client.close()
