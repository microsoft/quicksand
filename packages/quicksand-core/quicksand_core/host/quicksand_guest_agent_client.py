"""Async HTTP client for quicksand guest agent communication."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Callable
from typing import Any, TypeVar
from uuid import uuid4

import httpx

from .._types import NetworkConstants, QuicksandGuestAgentMethod, StdinSource, Timeouts
from ._stdin import STDIN_CAPABILITY, STDIN_UNSUPPORTED, run_with_stdin, validate_stdin

logger = logging.getLogger("quicksand.quicksand_guest_agent")

T = TypeVar("T")


async def _retry_on_transient_error(
    func: Callable[[], Any],
    *,
    max_retries: int = 3,
    base_delay: float = 0.1,
    max_delay: float = 2.0,
) -> Any:
    """Retry an async function on transient transport errors with exponential backoff.

    Retries on: ConnectionResetError, BrokenPipeError, httpx.TransportError
    Does NOT retry on: httpx.HTTPStatusError (4xx/5xx responses)
    """
    last_exception: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await func()
        except (ConnectionResetError, BrokenPipeError) as e:
            last_exception = e
        except httpx.TransportError as e:
            last_exception = e
        except httpx.HTTPStatusError:
            raise

        if attempt < max_retries:
            delay = min(base_delay * (2**attempt) * (0.5 + random.random()), max_delay)
            logger.debug(
                f"Transient error on attempt {attempt + 1}/{max_retries + 1}, "
                f"retrying in {delay:.2f}s: {last_exception}"
            )
            await asyncio.sleep(delay)

    assert last_exception is not None
    raise last_exception


class QuicksandGuestAgentClient:
    """Async HTTP client for communicating with the quicksand guest agent.

    This class handles all HTTP communication with the guest agent,
    including connection, authentication, and request/response handling.
    """

    def __init__(self, port: int, token: str):
        """Initialize the quicksand guest agent client.

        Args:
            port: The port number where the agent is listening.
            token: Authentication token for the agent.
        """
        self._port = port
        self._token = token
        self._client: httpx.AsyncClient | None = None
        self._supports_stdin = False
        # Lazy-init: asyncio.Lock() binds to the current event loop at creation
        # time, so we must not create it in __init__ (which may run outside any loop).
        self._lock: asyncio.Lock | None = None

    @property
    def is_connected(self) -> bool:
        """Check if the client is connected to the agent."""
        return self._client is not None

    @property
    def supports_stdin(self) -> bool:
        return self._supports_stdin

    async def connect(
        self,
        timeout: float = Timeouts.BOOT_DEFAULT,
        process_check: Callable[[], tuple[bool, str]] | None = None,
    ) -> None:
        """Connect to the agent and authenticate.

        Args:
            timeout: Maximum time to wait for connection.
            process_check: Optional callback that returns (is_running, error_info).
                          Used to detect if the VM process has exited.

        Raises:
            TimeoutError: If connection times out.
            RuntimeError: If authentication fails or VM exits.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        base_url = f"http://{NetworkConstants.LOCALHOST}:{self._port}"

        logger.debug(f"Connecting to quicksand guest agent: port={self._port}, timeout={timeout}s")

        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(Timeouts.GUEST_AGENT_HTTP, connect=Timeouts.GUEST_AGENT_CONNECT),
            headers={"Authorization": f"Bearer {self._token}"},
        )

        last_error: Exception | None = None
        attempt = 0

        while loop.time() < deadline:
            attempt += 1

            # Check if VM process has exited
            if process_check:
                is_running, error_info = process_check()
                if not is_running:
                    await self._client.aclose()
                    self._client = None
                    raise RuntimeError(error_info)

            try:
                logger.debug(f"Connection attempt {attempt} to {base_url}")
                response = await self._client.post(
                    "/authenticate",
                    json={"token": self._token},
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("authenticated"):
                        capabilities = data.get("capabilities", [])
                        self._supports_stdin = (
                            isinstance(capabilities, list) and STDIN_CAPABILITY in capabilities
                        )
                        logger.debug("Quicksand guest agent authentication successful")
                        return  # Success!
                    raise RuntimeError("Authentication rejected by quicksand guest agent")
                raise RuntimeError(f"Authentication failed: HTTP {response.status_code}")

            except (httpx.TransportError, RuntimeError) as e:
                logger.debug(f"Connection attempt {attempt} failed: {e}")
                last_error = e
                await asyncio.sleep(0.5)

        # Timeout
        if self._client:
            await self._client.aclose()
            self._client = None
        raise TimeoutError(
            f"Could not connect to quicksand guest agent within {timeout}s. "
            f"Port: {self._port}, Last error: {last_error}"
        )

    async def close(self) -> None:
        """Close the HTTP client connection."""
        self._supports_stdin = False
        if self._client:
            try:
                await self._client.aclose()
            except Exception as e:
                logger.warning(f"Failed to close HTTP client: {e}")
            finally:
                self._client = None

    async def send_request(
        self,
        method: QuicksandGuestAgentMethod,
        params: dict[str, Any],
        timeout: float = Timeouts.GUEST_AGENT_REQUEST,
    ) -> dict[str, Any]:
        """Send a request to the quicksand guest agent.

        Args:
            method: The guest agent method to call.
            params: Parameters for the method.
            timeout: Request timeout in seconds.

        Returns:
            Dict with either "result" or "error" key.
        """
        if self._client is None:
            raise RuntimeError("Not connected to quicksand guest agent")

        if method in (QuicksandGuestAgentMethod.STDIN, QuicksandGuestAgentMethod.CANCEL):
            return await self._send_control_request(method, params, timeout)

        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            endpoint_map = {
                QuicksandGuestAgentMethod.EXECUTE: "/execute",
                QuicksandGuestAgentMethod.PING: "/ping",
                QuicksandGuestAgentMethod.CREATE_USER: "/create_user",
                QuicksandGuestAgentMethod.DELETE_USER: "/delete_user",
            }

            endpoint = endpoint_map.get(method)
            if endpoint is None:
                return {"error": {"message": f"Unknown method: {method}"}}

            client = self._client

            async def do_request() -> httpx.Response:
                if method == QuicksandGuestAgentMethod.PING:
                    return await client.get(endpoint, timeout=timeout)
                else:
                    return await client.post(endpoint, json=params, timeout=timeout)

            try:
                response = await _retry_on_transient_error(do_request, max_retries=3)

                if response.status_code == 401:
                    return {"error": {"message": "Authentication failed"}}

                if response.status_code != 200:
                    return {"error": {"message": f"HTTP {response.status_code}"}}

                return {"result": response.json()}

            except httpx.TransportError as e:
                return {"error": {"message": f"Connection error after retries: {e}"}}
            except httpx.HTTPStatusError as e:
                return {"error": {"message": str(e)}}

    async def _send_control_request(
        self,
        method: QuicksandGuestAgentMethod,
        params: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        assert self._client is not None
        # A streaming response holds _lock. Input/cancel requests must bypass it
        # and must never be retried, since replaying a chunk would duplicate bytes.
        try:
            response = await self._client.post(f"/{method.value}", json=params, timeout=timeout)
            if response.status_code != 200:
                return {"error": {"message": f"HTTP {response.status_code}: {response.text}"}}
            return {"result": response.json()}
        except (httpx.TransportError, json.JSONDecodeError) as error:
            return {"error": {"message": f"Input connection error: {error}"}}

    async def send_stream_request(
        self,
        params: dict[str, Any],
        timeout: float,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
        *,
        stdin: StdinSource | None = None,
    ) -> dict[str, Any]:
        """POST to /execute_stream and parse SSE events, invoking callbacks.

        Accumulates full stdout/stderr and returns them along with exit_code
        in the same shape as send_request(): {"result": {...}} or {"error": {...}}.
        """
        if self._client is None:
            raise RuntimeError("Not connected to quicksand guest agent")

        stdin_id = None
        ready = None
        if stdin is not None:
            validate_stdin(stdin)
            if not self.supports_stdin:
                return {"error": {"message": STDIN_UNSUPPORTED}}
            stdin_id = uuid4().hex
            ready = asyncio.Event()
            params = {**params, "stdin_id": stdin_id}

        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            response = self._receive_stream(params, timeout, on_stdout, on_stderr, ready)
            if stdin is not None:
                assert stdin_id is not None and ready is not None
                return await run_with_stdin(
                    response,
                    stdin,
                    stdin_id=stdin_id,
                    ready=ready,
                    send_request=self.send_request,
                    timeout=timeout,
                )
            return await response

    async def _receive_stream(
        self,
        params: dict[str, Any],
        timeout: float,
        on_stdout: Callable[[str], None] | None,
        on_stderr: Callable[[str], None] | None,
        ready: asyncio.Event | None,
    ) -> dict[str, Any]:
        assert self._client is not None
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        exit_code = -1
        exited = False
        try:
            async with self._client.stream(
                "POST",
                "/execute_stream",
                json=params,
                timeout=httpx.Timeout(timeout, connect=Timeouts.GUEST_AGENT_CONNECT),
            ) as response:
                if response.status_code == 401:
                    return {"error": {"message": "Authentication failed"}}
                if response.status_code != 200:
                    return {"error": {"message": f"HTTP {response.status_code}"}}

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        event = json.loads(line[6:])
                    except json.JSONDecodeError:
                        if ready is not None:
                            return {"error": {"message": "Invalid guest stream event"}}
                        continue
                    if not isinstance(event, dict):
                        return {"error": {"message": "Invalid guest stream event"}}
                    if "error" in event:
                        return {"error": event["error"]}
                    stream = event.get("stream")
                    if stream == "ready":
                        if ready is not None:
                            ready.set()
                    elif stream == "stdout":
                        data = event.get("data", "")
                        stdout_parts.append(data)
                        if on_stdout:
                            on_stdout(data)
                    elif stream == "stderr":
                        data = event.get("data", "")
                        stderr_parts.append(data)
                        if on_stderr:
                            on_stderr(data)
                    elif stream == "exit":
                        exit_code = event.get("exit_code", -1)
                        exited = True
                        break

            if ready is not None and not exited:
                return {"error": {"message": "Guest stream ended without an exit status"}}
            return {
                "result": {
                    "stdout": "".join(stdout_parts),
                    "stderr": "".join(stderr_parts),
                    "exit_code": exit_code,
                }
            }
        except httpx.TransportError as error:
            return {"error": {"message": f"Stream connection error: {error}"}}
        except httpx.HTTPStatusError as error:
            return {"error": {"message": str(error)}}


# Alias for backwards compatibility
GuestAgentClient = QuicksandGuestAgentClient
