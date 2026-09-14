"""Unit tests for multi-user support (SandboxUser, create/delete, exec routing).

These exercise the host-side wiring with a mocked agent client, so no VM boots.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from quicksand_core import Sandbox, SandboxUser
from quicksand_core._types import QuicksandGuestAgentMethod


def _running_sandbox(send_request: AsyncMock) -> Sandbox:
    """A Sandbox marked running with a mocked agent transport."""
    sb = Sandbox(image="alpine")
    sb._is_running = True
    sb._process_manager = MagicMock(is_running=True)
    client = AsyncMock()
    client.send_request = send_request
    sb._agent_client = client
    return sb


@pytest.mark.asyncio
async def test_execute_threads_user_into_params():
    send = AsyncMock(return_value={"result": {"stdout": "alice", "stderr": "", "exit_code": 0}})
    sb = _running_sandbox(send)

    result = await sb.execute("whoami", user="alice")

    assert result.stdout == "alice"
    method, params, *_ = send.call_args.args
    assert method == QuicksandGuestAgentMethod.EXECUTE
    assert params["user"] == "alice"


@pytest.mark.asyncio
async def test_execute_omits_user_when_none():
    send = AsyncMock(return_value={"result": {"stdout": "", "stderr": "", "exit_code": 0}})
    sb = _running_sandbox(send)

    await sb.execute("true")

    _, params, *_ = send.call_args.args
    assert "user" not in params  # None fields are dropped before sending


@pytest.mark.asyncio
async def test_create_user_returns_handle_and_routes():
    send = AsyncMock(return_value={"result": {"uid": 1000, "gid": 1000, "home": "/home/alice"}})
    sb = _running_sandbox(send)

    user = await sb.create_user("alice")

    assert isinstance(user, SandboxUser)
    assert (user.name, user.uid, user.gid, user.home) == ("alice", 1000, 1000, "/home/alice")
    method, params, *_ = send.call_args.args
    assert method == QuicksandGuestAgentMethod.CREATE_USER
    assert params == {"name": "alice"}


@pytest.mark.asyncio
async def test_create_user_raises_on_agent_error():
    send = AsyncMock(return_value={"error": {"message": "User already exists: alice"}})
    sb = _running_sandbox(send)

    with pytest.raises(RuntimeError, match="already exists"):
        await sb.create_user("alice")


@pytest.mark.asyncio
async def test_sandbox_user_execute_injects_user():
    send = AsyncMock()
    # First call: create_user; subsequent: execute.
    send.side_effect = [
        {"result": {"uid": 1000, "gid": 1000, "home": "/home/alice"}},
        {"result": {"stdout": "alice", "stderr": "", "exit_code": 0}},
    ]
    sb = _running_sandbox(send)

    user = await sb.create_user("alice")
    await user.execute("whoami")

    method, params, *_ = send.call_args.args
    assert method == QuicksandGuestAgentMethod.EXECUTE
    assert params["user"] == "alice"


@pytest.mark.asyncio
async def test_delete_user_routes_with_remove_home():
    send = AsyncMock(return_value={"result": {"removed": True}})
    sb = _running_sandbox(send)

    await sb.delete_user("alice", remove_home=False)

    method, params, *_ = send.call_args.args
    assert method == QuicksandGuestAgentMethod.DELETE_USER
    assert params == {"name": "alice", "remove_home": False}
