"""Tests for readonly mounts."""

from __future__ import annotations

import pytest


@pytest.mark.integration
@pytest.mark.slow
class TestReadonlyMount:
    """Readonly mount tests using shared sandbox."""

    @pytest.mark.asyncio
    async def test_readonly_can_read(self, shared_sandbox):
        """Test reading from readonly mount."""
        result = await shared_sandbox.execute("cat /mnt/host/test.txt")
        assert result.exit_code == 0
        assert "host content" in result.stdout

    @pytest.mark.asyncio
    async def test_readonly_cannot_write(self, shared_sandbox):
        """Test writing to readonly mount fails (enforced by 9p and SMB)."""
        result = await shared_sandbox.execute("touch /mnt/host/newfile.txt")
        assert result.exit_code != 0
