"""Tests for basic command execution."""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestBasicCommands:
    """Basic command execution tests using shared sandbox."""

    @pytest.mark.asyncio
    async def test_execute_echo(self, shared_sandbox):
        """Test simple echo command."""
        result = await shared_sandbox.execute("echo 'hello world'")
        assert result.exit_code == 0
        assert "hello world" in result.stdout

    @pytest.mark.asyncio
    async def test_execute_exit_code(self, shared_sandbox):
        """Test command exit codes are captured."""
        result = await shared_sandbox.execute("exit 42")
        assert result.exit_code == 42

    @pytest.mark.asyncio
    async def test_execute_stderr(self, shared_sandbox):
        """Test stderr is captured."""
        result = await shared_sandbox.execute("echo error >&2")
        assert "error" in result.stderr

    @pytest.mark.asyncio
    async def test_execute_with_cwd(self, shared_sandbox):
        """Test command execution with working directory."""
        result = await shared_sandbox.execute("pwd", cwd="/tmp")
        assert "/tmp" in result.stdout

    @pytest.mark.asyncio
    async def test_multiline_script(self, shared_sandbox):
        """Test executing a multiline script."""
        script = """
        x=5
        y=10
        echo $((x + y))
        """
        result = await shared_sandbox.execute(script)
        assert result.exit_code == 0
        assert "15" in result.stdout

    @pytest.mark.asyncio
    async def test_environment_persistence(self, shared_sandbox):
        """Test that environment changes don't persist between commands."""
        await shared_sandbox.execute("export TESTVAR=value1")
        result = await shared_sandbox.execute("echo $TESTVAR")
        # Environment should not persist (each execute is a new shell)
        assert "value1" not in result.stdout or result.stdout.strip() == ""
