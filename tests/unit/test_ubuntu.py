"""Unit tests for quicksand_ubuntu package."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from quicksand_core.sandbox import Sandbox
from quicksand_ubuntu import UbuntuSandbox


class TestUbuntuImageProvider:
    """Tests for the _UbuntuImageProvider.resolve() method."""

    def test_resolve_raises_when_no_image(self):
        """Test that resolve raises FileNotFoundError when image is not built."""
        from quicksand_ubuntu import _UbuntuImageProvider

        provider = _UbuntuImageProvider()
        # Use an architecture that will never have an image built
        with pytest.raises(FileNotFoundError):
            provider.resolve(arch="i386")

    def test_resolve_returns_resolved_image(self):
        """Test that resolve returns a ResolvedImage with correct fields."""
        from quicksand_core._types import ResolvedImage
        from quicksand_ubuntu import _UbuntuImageProvider

        provider = _UbuntuImageProvider()
        # This will raise if no image is installed, so we test the type
        try:
            result = provider.resolve()
            assert isinstance(result, ResolvedImage)
            assert result.name == "ubuntu"
            assert result.chain is not None and len(result.chain) > 0
        except FileNotFoundError:
            pytest.skip("Ubuntu image not built/installed")


class TestUbuntuSandbox:
    """Tests for UbuntuSandbox class."""

    def test_is_sandbox_subclass(self):
        """Test that UbuntuSandbox is a Sandbox subclass."""
        assert issubclass(UbuntuSandbox, Sandbox)

    def test_creates_sandbox_with_default_config(self):
        """Test that UbuntuSandbox creates a Sandbox with default config."""
        sandbox = UbuntuSandbox()
        assert isinstance(sandbox, Sandbox)
        assert sandbox.config is not None
        assert sandbox.config.image == "ubuntu"

    def test_creates_sandbox_with_custom_kwargs(self):
        """Test that UbuntuSandbox accepts custom kwargs."""
        sandbox = UbuntuSandbox(memory="4G", cpus=2)
        assert sandbox.config.memory == "4G"
        assert sandbox.config.cpus == 2

    def test_is_running_property(self):
        """Test is_running property."""
        sandbox = UbuntuSandbox()
        assert sandbox.is_running is False

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test context manager interface."""
        sandbox = UbuntuSandbox()
        with (
            patch.object(sandbox, "start", new_callable=AsyncMock) as mock_start,
            patch.object(sandbox, "stop", new_callable=AsyncMock) as mock_stop,
        ):
            async with sandbox:
                mock_start.assert_called_once()
            mock_stop.assert_called_once()
