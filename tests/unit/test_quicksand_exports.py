"""Unit tests for quicksand package exports."""

from __future__ import annotations


class TestQuicksandExports:
    """Tests for quicksand package exports."""

    def test_core_exports(self):
        """Test core classes are exported."""
        from quicksand import (
            ExecuteResult,
            Mount,
            Sandbox,
            SandboxConfig,
        )

        assert Sandbox is not None
        assert SandboxConfig is not None
        assert Mount is not None
        assert ExecuteResult is not None

    def test_runtime_exports(self):
        """Test runtime functions are exported."""
        from quicksand import (
            RuntimeInfo,
            get_accelerator,
            get_machine_type,
            get_runtime,
            is_runtime_available,
        )

        assert get_runtime is not None
        assert RuntimeInfo is not None
        assert is_runtime_available is not None
        assert get_accelerator is not None
        assert get_machine_type is not None

    def test_platform_exports(self):
        """Test platform functions are exported."""
        from quicksand import (
            PlatformConfig,
            get_platform_config,
        )

        assert get_platform_config is not None
        assert PlatformConfig is not None

    def test_accelerator_exports(self):
        """Test accelerator functions are exported."""
        from quicksand import AcceleratorStatus, detect_accelerator

        assert detect_accelerator is not None
        assert AcceleratorStatus is not None

    def test_ubuntu_exports_when_installed(self):
        """Test Ubuntu exports are available when installed."""
        from quicksand import UbuntuSandbox

        # These should not be None since quicksand-ubuntu is installed
        assert UbuntuSandbox is not None

    def test_version(self):
        """Test version is available."""
        import quicksand

        assert hasattr(quicksand, "__version__")
        assert isinstance(quicksand.__version__, str)
        assert len(quicksand.__version__) > 0
