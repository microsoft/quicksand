"""Tests for default network behavior (NetworkMode.MOUNTS_ONLY)."""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestNetworkDefault:
    """Default network tests using shared sandbox (NetworkMode.MOUNTS_ONLY)."""

    @pytest.mark.asyncio
    async def test_mounts_only_blocks_external(self, shared_sandbox):
        """Test that NetworkMode.MOUNTS_ONLY blocks internet access."""
        # Try to ping an external host (should fail/timeout)
        result = await shared_sandbox.execute("ping -c 1 -W 2 8.8.8.8", timeout=5.0)
        # Should fail - restricted network blocks external traffic
        assert result.exit_code != 0
