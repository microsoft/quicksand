"""Shared stdin flow control and public execute() routing."""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock

import pytest
from quicksand_core import Sandbox
from quicksand_core._types import QuicksandGuestAgentMethod
from quicksand_core.host._stdin import STDIN_CHUNK_SIZE, run_with_stdin
from quicksand_core.host.virtio_serial_agent_client import VirtioSerialAgentClient

_RESULT = {"result": {"stdout": "done", "stderr": "", "exit_code": 0}}


async def test_input_waits_for_acknowledgement_and_bounds_chunks():
    ready = asyncio.Event()
    writing = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    produced = []
    chunks = []
    payload = bytes(range(256)) * 1024

    async def source():
        produced.append(1)
        yield payload
        produced.append(2)
        yield b"last"

    async def response():
        ready.set()
        await finished.wait()
        return _RESULT

    async def send(method, params, timeout):
        assert method == QuicksandGuestAgentMethod.STDIN
        if params.get("eof"):
            finished.set()
            return {"result": {"closed": True}}
        chunk = base64.b64decode(params["data"], validate=True)
        chunks.append(chunk)
        if len(chunks) == 1:
            writing.set()
            await release.wait()
        return {"result": {"closed": False}}

    task = asyncio.create_task(
        run_with_stdin(
            response(), source(), stdin_id="test", ready=ready, send_request=send, timeout=5
        )
    )
    try:
        await asyncio.wait_for(writing.wait(), timeout=2)
        assert produced == [1]
        assert len(chunks) == 1
    finally:
        release.set()
    assert await task == _RESULT
    assert b"".join(chunks) == payload + b"last"
    assert all(len(chunk) <= STDIN_CHUNK_SIZE for chunk in chunks)


@pytest.mark.parametrize(
    ("source", "expected"),
    [(b"", b""), (b"\x00\xff\n", b"\x00\xff\n"), ("hello \u2603", b"hello \xe2\x98\x83")],
)
async def test_static_input_preserves_bytes_and_closes_stdin(source, expected):
    ready = asyncio.Event()
    finished = asyncio.Event()
    requests = []

    async def response():
        ready.set()
        await finished.wait()
        return _RESULT

    async def send(method, params, timeout):
        requests.append(params)
        if params.get("eof"):
            finished.set()
        return {"result": {"closed": params.get("eof", False)}}

    assert (
        await run_with_stdin(
            response(), source, stdin_id="test", ready=ready, send_request=send, timeout=2
        )
        == _RESULT
    )
    assert requests[-1] == {"stdin_id": "test", "eof": True}
    assert b"".join(base64.b64decode(r["data"]) for r in requests if "data" in r) == expected


async def test_exit_before_input_does_not_consume_producer():
    ready = asyncio.Event()
    produced = []
    send = AsyncMock()

    async def source():
        produced.append(True)
        yield b"unused"

    async def response():
        ready.set()
        return _RESULT

    assert (
        await run_with_stdin(
            response(), source(), stdin_id="test", ready=ready, send_request=send, timeout=2
        )
        == _RESULT
    )
    assert not produced
    send.assert_not_awaited()


async def test_early_exit_closes_a_blocked_producer():
    ready = asyncio.Event()
    producing = asyncio.Event()
    closed = asyncio.Event()

    async def source():
        try:
            yield b"first"
            producing.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    async def response():
        ready.set()
        await producing.wait()
        return _RESULT

    send = AsyncMock(return_value={"result": {"closed": False}})
    assert (
        await run_with_stdin(
            response(), source(), stdin_id="test", ready=ready, send_request=send, timeout=2
        )
        == _RESULT
    )
    assert closed.is_set()
    assert send.await_count == 1


@pytest.mark.parametrize("error_type", [ValueError, TimeoutError])
async def test_producer_errors_cancel_command_and_propagate(error_type):
    ready = asyncio.Event()
    send = AsyncMock(return_value={"result": {"closed": False}})

    async def source():
        yield b"first"
        raise error_type("producer failed")

    async def response():
        ready.set()
        await asyncio.Event().wait()
        return _RESULT

    with pytest.raises(error_type, match="producer failed"):
        await run_with_stdin(
            response(), source(), stdin_id="test", ready=ready, send_request=send, timeout=2
        )
    assert send.await_args_list[-1].args[:2] == (
        QuicksandGuestAgentMethod.CANCEL,
        {"stdin_id": "test"},
    )


async def test_invalid_producer_chunk_cancels_command():
    ready = asyncio.Event()
    send = AsyncMock(return_value={"result": {"cancelled": True}})

    async def source():
        yield "not bytes"

    async def response():
        ready.set()
        await asyncio.Event().wait()
        return _RESULT

    with pytest.raises(TypeError, match="stdin chunks must be bytes"):
        await run_with_stdin(
            response(), source(), stdin_id="test", ready=ready, send_request=send, timeout=2
        )
    assert send.await_args is not None
    assert send.await_args.args[0] == QuicksandGuestAgentMethod.CANCEL


async def test_cancellation_before_ready_still_cancels_remote_command():
    started = asyncio.Event()
    ready = asyncio.Event()
    send = AsyncMock(return_value={"result": {"cancelled": True}})

    async def response():
        started.set()
        await asyncio.Event().wait()
        return _RESULT

    task = asyncio.create_task(
        run_with_stdin(
            response(), b"unused", stdin_id="test", ready=ready, send_request=send, timeout=2
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert send.await_args is not None
    assert send.await_args.args[0] == QuicksandGuestAgentMethod.CANCEL


async def test_timeout_cancels_command_and_stops_producer():
    ready = asyncio.Event()
    closed = asyncio.Event()
    send = AsyncMock(return_value={"result": {"closed": False}})

    async def source():
        try:
            yield b"first"
            await asyncio.Event().wait()
        finally:
            closed.set()

    async def response():
        ready.set()
        await asyncio.Event().wait()
        return _RESULT

    result = await run_with_stdin(
        response(), source(), stdin_id="test", ready=ready, send_request=send, timeout=0.1
    )
    assert "timed out" in result["error"]["message"]
    assert closed.is_set()
    assert send.await_args is not None
    assert send.await_args.args[0] == QuicksandGuestAgentMethod.CANCEL


@pytest.mark.parametrize("ack", [{}, {"closed": "false"}, {"closed": False}])
async def test_invalid_eof_acknowledgement_is_not_ignored(ack):
    ready = asyncio.Event()

    async def response():
        ready.set()
        await asyncio.Event().wait()
        return _RESULT

    send = AsyncMock(return_value={"result": ack})
    with pytest.raises(RuntimeError, match="acknowledge"):
        await run_with_stdin(
            response(), b"", stdin_id="test", ready=ready, send_request=send, timeout=2
        )


@pytest.mark.parametrize("stdin", [b"", b"hello", "hello"])
async def test_execute_routes_input_to_streaming_client(stdin):
    sandbox = Sandbox(image="ubuntu")
    sandbox._is_running = True
    sandbox._process_manager = MagicMock(is_running=True)
    client = AsyncMock(spec=VirtioSerialAgentClient)
    client.send_stream_request.return_value = _RESULT
    sandbox._agent_client = client

    result = await sandbox.execute("cat", stdin=stdin)

    assert result.stdout == "done"
    client.send_request.assert_not_awaited()
    assert client.send_stream_request.await_args.kwargs["stdin"] is stdin
    assert "stdin" not in client.send_stream_request.await_args.args[0]


async def test_execute_without_input_retains_request_path():
    sandbox = Sandbox(image="ubuntu")
    sandbox._is_running = True
    sandbox._process_manager = MagicMock(is_running=True)
    client = AsyncMock(spec=VirtioSerialAgentClient)
    client.send_request.return_value = _RESULT
    sandbox._agent_client = client

    result = await sandbox.execute("true")

    assert result.exit_code == 0
    client.send_request.assert_awaited_once()
    client.send_stream_request.assert_not_awaited()
