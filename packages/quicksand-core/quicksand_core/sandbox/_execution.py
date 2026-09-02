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
        user: str | None = None,
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
            user: Optional OS user to run the command as. The command runs
                  with that user's uid/gid/groups and HOME, defaulting cwd to
                  the user's home directory. The user must already exist (see
                  ``create_user``). Prefer obtaining a ``SandboxUser`` via
                  ``create_user`` and calling ``.execute()`` on it.

        Returns:
            ExecuteResult with stdout, stderr, and exit_code.
        """
        if not self.is_running:
            raise RuntimeError("Sandbox is not running")

        params = ExecuteParams(
            command=command, timeout=timeout, shell=shell, cwd=cwd, exclusive=exclusive, user=user
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

    async def create_user(self, name: str) -> SandboxUser:
        """Create an OS user in the sandbox and return a handle scoped to it.

        Distro-agnostic: the guest agent creates the account by writing the
        standard POSIX files (``/etc/passwd``, ``/etc/group``, ``/etc/shadow``),
        so it behaves identically on Alpine, Ubuntu, and any other guest with
        no dependency on distro-specific ``adduser``/``useradd`` tools.

        Multiple users share one VM, giving logical per-user isolation
        (separate ``$HOME``, ownership, credentials) without the memory cost of
        a separate VM per user.

        Args:
            name: Username. Must match ``[a-z_][a-z0-9_-]*`` (max 32 chars).

        Returns:
            A ``SandboxUser`` whose ``execute`` runs commands as this user.

        Raises:
            RuntimeError: If the sandbox is not running, or creation fails
                          (e.g. invalid name or the user already exists).
        """
        if not self.is_running:
            raise RuntimeError("Sandbox is not running")
        response = await self._send_request(QuicksandGuestAgentMethod.CREATE_USER, {"name": name})
        if "error" in response:
            message = response["error"].get("message", "unknown error")
            raise RuntimeError(f"Failed to create user {name!r}: {message}")
        result = response["result"]
        return SandboxUser(self, name, uid=result["uid"], gid=result["gid"], home=result["home"])

    async def delete_user(self, name: str, *, remove_home: bool = True) -> None:
        """Delete an OS user from the sandbox.

        Kills the user's running processes, removes the account entries, and
        (by default) deletes the home directory.

        Args:
            name: Username to delete.
            remove_home: If True (default), also remove ``/home/<name>``.

        Raises:
            RuntimeError: If the sandbox is not running, or deletion fails.
        """
        if not self.is_running:
            raise RuntimeError("Sandbox is not running")
        response = await self._send_request(
            QuicksandGuestAgentMethod.DELETE_USER,
            {"name": name, "remove_home": remove_home},
        )
        if "error" in response:
            message = response["error"].get("message", "unknown error")
            raise RuntimeError(f"Failed to delete user {name!r}: {message}")


class SandboxUser:
    """A handle to an OS user inside a running sandbox.

    Returned by :meth:`Sandbox.create_user`. Its :meth:`execute` runs commands
    as this user (uid/gid/supplementary groups and ``HOME``), defaulting the
    working directory to the user's home. It is a thin view over the sandbox's
    single control channel — every user multiplexes over the same agent
    connection, so there is no per-user VM overhead.
    """

    def __init__(
        self,
        sandbox: _ExecutionMixin,
        name: str,
        *,
        uid: int,
        gid: int,
        home: str,
    ):
        self._sandbox = sandbox
        self.name = name
        self.uid = uid
        self.gid = gid
        self.home = home

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
        """Execute a command as this user. See :meth:`Sandbox.execute`."""
        return await self._sandbox.execute(
            command,
            timeout=timeout,
            cwd=cwd,
            shell=shell,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
            exclusive=exclusive,
            user=self.name,
        )

    def __repr__(self) -> str:
        return f"SandboxUser(name={self.name!r}, uid={self.uid}, home={self.home!r})"
