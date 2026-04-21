"""Tests for command timeout handling."""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestTimeouts:
    """Timeout tests using shared sandbox."""

    @pytest.mark.asyncio
    async def test_command_timeout(self, shared_sandbox):
        """Test command timeout handling."""
        result = await shared_sandbox.execute("sleep 10", timeout=1.0)
        assert result.exit_code != 0 or "timeout" in result.stderr.lower()
