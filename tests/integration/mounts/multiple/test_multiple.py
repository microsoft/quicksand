"""Tests for multiple mounts."""

from __future__ import annotations

import pytest


@pytest.mark.integration
@pytest.mark.slow
class TestMultipleMounts:
    """Multiple mount tests using shared sandbox."""

    @pytest.mark.asyncio
    async def test_multiple_mounts_readable(self, shared_sandbox):
        """Test reading from multiple mounts."""
        result1 = await shared_sandbox.execute("cat /mnt/one/file1.txt")
        result2 = await shared_sandbox.execute("cat /mnt/two/file2.txt")

        assert result1.exit_code == 0
        assert "content1" in result1.stdout
        assert result2.exit_code == 0
        assert "content2" in result2.stdout

    @pytest.mark.asyncio
    async def test_multiple_mounts_isolated(self, shared_sandbox):
        """Test that mounts are isolated from each other."""
        # File from mount1 should not be visible in mount2
        result = await shared_sandbox.execute("ls /mnt/two/file1.txt")
        assert result.exit_code != 0
