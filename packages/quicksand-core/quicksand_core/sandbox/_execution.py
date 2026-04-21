"""Execution mixin — command execution methods for the Sandbox class.

Analogous to an API router: groups the 'commands' concern.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from .._types import (
    AgentErrorBody,
    ExecuteParams,
    ExecuteResponseResult,
    ExecuteResult,
    GuestCommands,
    QuicksandGuestAgentMethod,
    Timeouts,
)
from ._protocol import _SandboxProtocol


class _ExecutionMixin(_SandboxProtocol):
    """Mixin providing command execution via the guest agent.

    Analogous to an API router for the 'commands' concern.
    Dependencies (is_running, _send_request) are declared by _SandboxProtocol.
    """

    async def execute(
        self,
        command: str,
        timeout: float = Timeouts.GUEST_AGENT_REQUEST,
        cwd: str | None = None,
        shell: str = GuestCommands.SHELL,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
        exclusive: bool = False,
    ) -> ExecuteResult:
        """
        Execute a shell command inside the sandbox.

        Args:
            command: The shell command to execute.
            timeout: Maximum execution time in seconds.
            cwd: Working directory for the command.
            shell: Shell to use for execution (default: /bin/sh).
                   Common options: /bin/sh, /bin/bash, /bin/zsh
            on_stdout: Optional callback invoked with each chunk of stdout
                       as it arrives. When provided, the command is executed
                       in streaming mode via SSE.
            on_stderr: Optional callback invoked with each chunk of stderr
                       as it arrives. When provided, the command is executed
                       in streaming mode via SSE.
            exclusive: If True, the guest agent will reject other requests
                       while this command is running. Used for system commands
                       like sync/fstrim that need exclusive access.

        Returns:
            ExecuteResult with stdout, stderr, and exit_code.
        """
        if not self.is_running:
            raise RuntimeError("Sandbox is not running")

        params = ExecuteParams(
            command=command, timeout=timeout, shell=shell, cwd=cwd, exclusive=exclusive
        )
        params_dict = {k: v for k, v in asdict(params).items() if v is not None}

        if on_stdout is not None or on_stderr is not None:
            client = self._agent_client
            if client is None:
                raise RuntimeError("Not connected to guest agent")
            response = await client.send_stream_request(
                params_dict,
                timeout=timeout + 5,
                on_stdout=on_stdout,
                on_stderr=on_stderr,
            )
        else:
            response = await self._send_request(
                QuicksandGuestAgentMethod.EXECUTE,
                params_dict,
                timeout=timeout + 5,
            )

        if "error" in response:
            error = AgentErrorBody(**response["error"])
            return ExecuteResult(stdout="", stderr=error.message, exit_code=-1)

        result = ExecuteResponseResult(**response["result"])
        return ExecuteResult(stdout=result.stdout, stderr=result.stderr, exit_code=result.exit_code)
