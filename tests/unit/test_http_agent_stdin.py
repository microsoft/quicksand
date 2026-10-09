"""HTTP stdin controls must run concurrently with the SSE response."""

from __future__ import annotations

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx
import pytest
from quicksand_core.host.quicksand_guest_agent_client import QuicksandGuestAgentClient


class EventStream(httpx.AsyncByteStream):
    def __init__(self):
        self.events: asyncio.Queue[dict | None] = asyncio.Queue()
        self.closed = False

    async def __aiter__(self):
        while (event := await self.events.get()) is not None:
            yield f"data: {json.dumps(event)}\n\n".encode()

    async def aclose(self):
        self.closed = True


class HttpAgent:
    def __init__(self, *, supports_stdin=True, fail_input=False, omit_exit=False):
        self.supports_stdin = supports_stdin
        self.fail_input = fail_input
        self.omit_exit = omit_exit
        self.requests = []
        self.received = bytearray()
        self.stream = EventStream()
        self.stdin_id = None

    async def handle(self, request: httpx.Request):
        self.requests.append(request)
        params = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer test-token"
        endpoint = request.url.path
        if endpoint == "/authenticate":
            data: dict[str, bool | list[str]] = {"authenticated": True}
            if self.supports_stdin:
                data["capabilities"] = ["stdin_streaming"]
            return httpx.Response(200, json=data)
        if endpoint == "/execute_stream":
            self.stdin_id = params.get("stdin_id")
            if self.stdin_id is None:
                self.stream.events.put_nowait({"stream": "stdout", "data": "legacy"})
                self.stream.events.put_nowait({"stream": "exit", "exit_code": 0})
            else:
                self.stream.events.put_nowait({"stream": "ready"})
                self.stream.events.put_nowait({"stream": "stdout", "data": "prompt>"})
            if self.omit_exit:
                self.stream.events.put_nowait(None)
            return httpx.Response(200, stream=self.stream)
        assert params["stdin_id"] == self.stdin_id
        if endpoint == "/cancel":
            return httpx.Response(200, json={"cancelled": True})
        assert endpoint == "/stdin"
        if self.fail_input:
            raise httpx.ReadError("lost acknowledgement")
        if params.get("eof"):
            self.stream.events.put_nowait({"stream": "exit", "exit_code": 0})
            self.stream.events.put_nowait(None)
            return httpx.Response(200, json={"closed": True})
        self.received.extend(base64.b64decode(params["data"], validate=True))
        self.stream.events.put_nowait({"stream": "stdout", "data": "accepted"})
        return httpx.Response(200, json={"closed": False})


@asynccontextmanager
async def connected_client(agent):
    original = httpx.AsyncClient

    def client_factory(**kwargs):
        return original(transport=httpx.MockTransport(agent.handle), **kwargs)

    client = QuicksandGuestAgentClient(port=1, token="test-token")
    with patch(
        "quicksand_core.host.quicksand_guest_agent_client.httpx.AsyncClient",
        side_effect=client_factory,
    ):
        await client.connect(timeout=2)
    try:
        yield client
    finally:
        await client.close()


async def test_http_stdin_progresses_while_sse_holds_execution_lock():
    agent = HttpAgent()
    prompt = asyncio.Event()
    echoed = asyncio.Event()
    output = []

    async def source():
        await prompt.wait()
        yield b"first"
        await echoed.wait()
        yield b"\x00\xfflast"

    def on_stdout(chunk):
        output.append(chunk)
        if "prompt>" in "".join(output):
            prompt.set()
        if "accepted" in "".join(output):
            echoed.set()

    async with connected_client(agent) as client:
        assert client.supports_stdin
        result = await client.send_stream_request(
            {"command": "cat"}, timeout=2, stdin=source(), on_stdout=on_stdout
        )
    assert result["result"]["stdout"] == "prompt>acceptedaccepted"
    assert agent.received == b"first\x00\xfflast"
    assert agent.stream.closed


async def test_http_legacy_agent_rejects_input_without_consuming_it():
    agent = HttpAgent(supports_stdin=False)
    produced = []

    async def source():
        produced.append(True)
        yield b"unused"

    async with connected_client(agent) as client:
        assert not client.supports_stdin
        result = await client.send_stream_request({"command": "cat"}, timeout=2, stdin=source())
    assert "does not support streaming stdin" in result["error"]["message"]
    assert not produced
    assert [request.url.path for request in agent.requests] == ["/authenticate"]


async def test_http_legacy_output_streaming_is_unchanged():
    agent = HttpAgent(supports_stdin=False)
    async with connected_client(agent) as client:
        result = await client.send_stream_request({"command": "echo legacy"}, timeout=2)
    assert result["result"]["stdout"] == "legacy"
    assert result["result"]["exit_code"] == 0


async def test_http_input_is_never_retried_after_lost_acknowledgement():
    agent = HttpAgent(fail_input=True)
    async with connected_client(agent) as client:
        with pytest.raises(RuntimeError, match="lost acknowledgement"):
            await client.send_stream_request({"command": "cat"}, timeout=2, stdin=b"once")
    endpoints = [request.url.path for request in agent.requests]
    assert endpoints.count("/stdin") == 1
    assert endpoints.count("/cancel") == 1
    assert agent.stream.closed


async def test_http_timeout_cancels_command_and_closes_producer():
    agent = HttpAgent()
    closed = asyncio.Event()

    async def source():
        try:
            yield b"first"
            await asyncio.Event().wait()
        finally:
            closed.set()

    async with connected_client(agent) as client:
        result = await client.send_stream_request({"command": "cat"}, timeout=0.1, stdin=source())
    assert "timed out" in result["error"]["message"]
    assert closed.is_set()
    assert any(request.url.path == "/cancel" for request in agent.requests)
    assert agent.stream.closed


async def test_http_missing_exit_status_is_an_error():
    agent = HttpAgent(omit_exit=True)
    async with connected_client(agent) as client:
        result = await client.send_stream_request({"command": "cat"}, timeout=2, stdin=b"unused")
    assert "without an exit status" in result["error"]["message"]
    assert any(request.url.path == "/cancel" for request in agent.requests)
