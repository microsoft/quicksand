"""Tests for file read/write operations via execute()."""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestFileOperations:
    """File operation tests using shared sandbox."""

    @pytest.mark.asyncio
    async def test_write_and_read_file(self, shared_sandbox):
        """Test writing and reading a text file via shell commands."""
        await shared_sandbox.execute("echo -n 'test content' > /tmp/test.txt")
        result = await shared_sandbox.execute("cat /tmp/test.txt")
        assert result.stdout.strip() == "test content"

    @pytest.mark.asyncio
    async def test_write_and_read_binary(self, shared_sandbox):
        """Test writing and reading binary data via shell commands."""
        # Write binary bytes using printf with POSIX octal escapes
        await shared_sandbox.execute(r"printf '\000\001\002\377' > /tmp/binary.bin")
        result = await shared_sandbox.execute("od -A n -t x1 /tmp/binary.bin | tr -d ' \\n'")
        assert result.stdout.strip() == "000102ff"
