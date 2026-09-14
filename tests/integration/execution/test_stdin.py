"""End-to-end stdin behavior with a stdin-capable guest image."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os

import pytest
import pytest_asyncio
from quicksand import Sandbox
from quicksand_core.host.quicksand_guest_agent_client import QuicksandGuestAgentClient
from quicksand_core.host.virtio_serial_agent_client import VirtioSerialAgentClient

from tests.conftest import has_qemu
from tests.integration.conftest import get_vm_type


class _HttpSandbox(Sandbox):
    def _build_vm_command(self) -> list[str]:
        self._agent_socket_path = None
        self._agent_socket_port = None
        return super()._build_vm_command()


@pytest_asyncio.fixture(scope="module", params=["virtio", "http"])
async def stdin_sandbox(request):
    if not has_qemu():
        pytest.skip("QEMU not installed")
    image = os.environ.get("QUICKSAND_STDIN_TEST_IMAGE", get_vm_type())
    sandbox_class = _HttpSandbox if request.param == "http" else Sandbox
    sandbox = sandbox_class(image=image)
    try:
        await sandbox.start()
        assert sandbox._agent_client is not None
        if not sandbox._agent_client.supports_stdin:
            pytest.skip("Guest image must be rebuilt with stdin_streaming support")
        expected_client = (
            QuicksandGuestAgentClient if request.param == "http" else VirtioSerialAgentClient
        )
        assert isinstance(sandbox._agent_client, expected_client)
        yield sandbox
    finally:
        await sandbox.stop()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_binary_stdin_is_streamed_without_corruption(stdin_sandbox):
    payload = bytes(range(256)) * 4096

    async def chunks():
        yield b""
        yield payload[:1]
        yield payload[1:100001]
        yield payload[100001:]

    result = await stdin_sandbox.execute("sha256sum", stdin=chunks())
    assert result.exit_code == 0, result.stderr
    assert result.stdout.split()[0] == hashlib.sha256(payload).hexdigest()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stdin_can_wait_for_output_without_a_newline(stdin_sandbox):
    prompt = asyncio.Event()
    reply = asyncio.Event()
    output = []

    async def chunks():
        await prompt.wait()
        yield b"hello\n"
        await reply.wait()
        yield b"world\n"

    def on_stdout(chunk):
        output.append(chunk)
        text = "".join(output)
        if "prompt>" in text:
            prompt.set()
        if "received:hello" in text:
            reply.set()

    result = await stdin_sandbox.execute(
        "printf 'prompt>'; IFS= read -r line; printf 'received:%s' \"$line\"; cat",
        stdin=chunks(),
        on_stdout=on_stdout,
        timeout=10,
        exclusive=True,
    )
    assert result.exit_code == 0, result.stderr
    assert result.stdout == "prompt>received:helloworld\n"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_empty_stdin_sends_eof(stdin_sandbox):
    result = await stdin_sandbox.execute("wc -c", stdin=b"")
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "0"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_early_exit_stops_stdin_producer(stdin_sandbox):
    closed = asyncio.Event()

    async def chunks():
        try:
            yield b"first"
            await asyncio.Event().wait()
        finally:
            closed.set()

    result = await stdin_sandbox.execute("head -c 1", stdin=chunks())
    assert result.exit_code == 0, result.stderr
    assert result.stdout == "f"
    assert closed.is_set()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stdin_cancellation_kills_process_group(stdin_sandbox):
    started = asyncio.Event()
    output = []

    def on_stdout(chunk):
        output.append(chunk)
        if "\n" in "".join(output):
            started.set()

    async def chunks():
        await asyncio.Event().wait()
        yield b"unused"

    task = asyncio.create_task(
        stdin_sandbox.execute(
            'sleep 30 & printf \'%s %s\\n\' "$$" "$!"; wait',
            stdin=chunks(),
            on_stdout=on_stdout,
            exclusive=True,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        pids = [int(part) for part in "".join(output).strip().split()]
        assert len(pids) == 2
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    for _ in range(50):
        result = await stdin_sandbox.execute(" && ".join(f"test ! -d /proc/{pid}" for pid in pids))
        if result.exit_code == 0:
            break
        await asyncio.sleep(0.05)
    assert result.exit_code == 0, "Cancelled command or descendant is still running"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stdin_timeout_releases_exclusive_command(stdin_sandbox):
    async def chunks():
        await asyncio.Event().wait()
        yield b"unused"

    result = await stdin_sandbox.execute("sleep 30", stdin=chunks(), timeout=0.2, exclusive=True)
    assert result.exit_code == -1
    assert "timed out" in result.stderr
    result = await stdin_sandbox.execute("printf available")
    assert result.exit_code == 0, result.stderr
    assert result.stdout == "available"
