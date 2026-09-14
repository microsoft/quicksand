"""Shared, backpressured stdin handling for both guest-agent transports."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from collections.abc import AsyncGenerator, AsyncIterable, Awaitable, Callable, Coroutine
from typing import Any

from .._types import QuicksandGuestAgentMethod, StdinSource

logger = logging.getLogger("quicksand.stdin")

STDIN_CHUNK_SIZE = 64 * 1024
STDIN_CAPABILITY = "stdin_streaming"
STDIN_UNSUPPORTED = (
    "This guest image does not support streaming stdin. "
    "Update or rebuild the image with a guest agent that supports stdin_streaming."
)
_CANCEL_TIMEOUT = 2.0

SendRequest = Callable[
    [QuicksandGuestAgentMethod, dict[str, Any], float],
    Awaitable[dict[str, Any]],
]


def validate_stdin(source: StdinSource) -> None:
    if not isinstance(source, (str, bytes, AsyncIterable)):
        raise TypeError("stdin must be str, bytes, or an async iterable of bytes")


async def _stdin_chunks(source: StdinSource) -> AsyncGenerator[bytes, None]:
    if isinstance(source, str):
        source = source.encode("utf-8")
    if isinstance(source, bytes):
        for offset in range(0, len(source), STDIN_CHUNK_SIZE):
            yield source[offset : offset + STDIN_CHUNK_SIZE]
        return

    iterator = aiter(source)
    try:
        async for chunk in iterator:
            if not isinstance(chunk, bytes):
                raise TypeError("stdin chunks must be bytes")
            for offset in range(0, len(chunk), STDIN_CHUNK_SIZE):
                yield chunk[offset : offset + STDIN_CHUNK_SIZE]
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()


async def _cancel_execution(stdin_id: str, send_request: SendRequest) -> None:
    try:
        response = await asyncio.wait_for(
            send_request(
                QuicksandGuestAgentMethod.CANCEL,
                {"stdin_id": stdin_id},
                _CANCEL_TIMEOUT,
            ),
            timeout=_CANCEL_TIMEOUT,
        )
        if "error" in response:
            logger.warning("Failed to cancel stdin execution: %s", response["error"])
        else:
            result = response.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("cancelled"), bool):
                logger.warning("Invalid cancellation acknowledgement from guest agent")
    except (OSError, RuntimeError, TimeoutError) as error:
        logger.warning("Failed to cancel stdin execution: %s", error)


async def run_with_stdin(
    response: Coroutine[Any, Any, dict[str, Any]],
    source: StdinSource,
    *,
    stdin_id: str,
    ready: asyncio.Event,
    send_request: SendRequest,
    timeout: float,
) -> dict[str, Any]:
    async def send_input(data: bytes | None) -> bool:
        params: dict[str, Any] = {"stdin_id": stdin_id}
        if data is None:
            params["eof"] = True
        else:
            params["data"] = base64.b64encode(data).decode("ascii")
        reply = await send_request(QuicksandGuestAgentMethod.STDIN, params, timeout)
        if "error" in reply:
            raise RuntimeError(f"Failed to send stdin: {reply['error']['message']}")
        result = reply.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("closed"), bool):
            raise RuntimeError("Invalid stdin acknowledgement from guest agent")
        if data is None and not result["closed"]:
            raise RuntimeError("Guest agent did not acknowledge stdin EOF")
        return result["closed"]

    async def feed() -> None:
        await ready.wait()
        async with contextlib.aclosing(_stdin_chunks(source)) as chunks:
            while not output_task.done():
                chunk = await anext(chunks, None)
                if output_task.done():
                    return
                if await send_input(chunk):
                    return

    output_task = asyncio.create_task(response)
    input_task = asyncio.create_task(feed())
    deadline = asyncio.timeout(timeout)
    result: dict[str, Any] | None = None
    try:
        async with deadline:
            done, _ = await asyncio.wait(
                (output_task, input_task), return_when=asyncio.FIRST_COMPLETED
            )
            if input_task in done:
                await input_task
            result = await output_task
        return result
    except TimeoutError:
        if not deadline.expired():
            raise
        result = {"error": {"message": f"Stream timed out after {timeout}s"}}
        return result
    finally:
        input_task.cancel()
        output_task.cancel()
        if result is None or "error" in result:
            await _cancel_execution(stdin_id, send_request)
        cleanup = await asyncio.gather(input_task, output_task, return_exceptions=True)
        if result is not None:
            for error in cleanup:
                if isinstance(error, Exception):
                    raise error
