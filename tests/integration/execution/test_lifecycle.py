"""Integration tests for sandbox lifecycle (no real VM needed)."""

from __future__ import annotations

import pytest
from quicksand import Sandbox

from tests.conftest import skip_no_qemu


@pytest.mark.integration
class TestSandboxLifecycle:
    """Tests for sandbox lifecycle that don't require a real VM."""

    @skip_no_qemu
    @pytest.mark.asyncio
    async def test_sandbox_start_requires_image(self, tmp_dir):
        """Test that starting sandbox without image fails gracefully."""
        sandbox = Sandbox(image="nonexistent-image-that-does-not-exist")

        with pytest.raises(RuntimeError, match="Image not found"):
            await sandbox.start()

    @skip_no_qemu
    def test_sandbox_not_running_initially(self, fake_qcow2):
        """Test sandbox is not running after creation."""
        sandbox = Sandbox(image="ubuntu")

        assert sandbox.is_running is False

    @skip_no_qemu
    @pytest.mark.asyncio
    async def test_sandbox_stop_when_not_running(self, fake_qcow2):
        """Test that stopping a non-running sandbox is safe."""
        sandbox = Sandbox(image="ubuntu")

        # Should not raise
        await sandbox.stop()
        assert sandbox.is_running is False
