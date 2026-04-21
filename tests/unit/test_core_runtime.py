"""Unit tests for quicksand_core runtime and platform modules."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from quicksand_core.host import (
    Accelerator,
    AcceleratorStatus,
    Architecture,
    DarwinConfig,
    LinuxConfig,
    WindowsConfig,
)
from quicksand_core.qemu.arch import ARM64Config, X86_64Config
from quicksand_core.qemu.platform import (
    PlatformConfig,
    RuntimeInfo,
    get_machine_type,
    get_platform_config,
)


def _clear_platform_caches():
    """Clear all platform-related caches for testing."""
    get_platform_config.cache_clear()


class TestGetPlatformConfig:
    """Tests for get_platform_config function."""

    def test_linux_x86_64(self):
        """Test Linux x86_64 platform config."""
        with (
            patch("quicksand_core.host.os_._platform.system", return_value="Linux"),
            patch("quicksand_core.host.arch._platform.machine", return_value="x86_64"),
        ):
            _clear_platform_caches()
            config = get_platform_config()
            assert config.platform_key == "linux-x86_64"
            assert config.arch.arch_type == Architecture.X86_64

    def test_linux_aarch64(self):
        """Test Linux ARM64 platform config."""
        with (
            patch("quicksand_core.host.os_._platform.system", return_value="Linux"),
            patch("quicksand_core.host.arch._platform.machine", return_value="aarch64"),
        ):
            _clear_platform_caches()
            config = get_platform_config()
            # Architecture is normalized to arm64
            assert config.platform_key == "linux-arm64"
            assert config.arch.arch_type == Architecture.ARM64

    def test_darwin_x86_64(self):
        """Test macOS Intel platform config."""
        with (
            patch("quicksand_core.host.os_._platform.system", return_value="Darwin"),
            patch("quicksand_core.host.arch._platform.machine", return_value="x86_64"),
        ):
            _clear_platform_caches()
            config = get_platform_config()
            assert config.platform_key == "darwin-x86_64"

    def test_darwin_arm64(self):
        """Test macOS Apple Silicon platform config."""
        with (
            patch("quicksand_core.host.os_._platform.system", return_value="Darwin"),
            patch("quicksand_core.host.arch._platform.machine", return_value="arm64"),
        ):
            _clear_platform_caches()
            config = get_platform_config()
            assert config.platform_key == "darwin-arm64"

    def test_windows_x86_64(self):
        """Test Windows x86_64 platform config."""
        with (
            patch("quicksand_core.host.os_._platform.system", return_value="Windows"),
            patch("quicksand_core.host.arch._platform.machine", return_value="AMD64"),
            patch("quicksand_core.host.arch._detect_native_windows_arch", return_value=None),
        ):
            _clear_platform_caches()
            config = get_platform_config()
            assert config.platform_key == "windows-x86_64"

    def test_windows_arm64_native(self):
        """Test native ARM64 Windows platform config."""
        with (
            patch("quicksand_core.host.os_._platform.system", return_value="Windows"),
            patch("quicksand_core.host.arch._platform.machine", return_value="ARM64"),
            patch("quicksand_core.host.arch._detect_native_windows_arch", return_value="ARM64"),
        ):
            _clear_platform_caches()
            config = get_platform_config()
            assert config.platform_key == "windows-arm64"
            assert config.arch.arch_type == Architecture.ARM64

    def test_windows_arm64_emulated(self):
        """Test Windows ARM64 with x86_64 Python (transparent emulation).

        platform.machine() returns AMD64, but registry detects ARM64.
        """
        with (
            patch("quicksand_core.host.os_._platform.system", return_value="Windows"),
            patch("quicksand_core.host.arch._platform.machine", return_value="AMD64"),
            patch("quicksand_core.host.arch._detect_native_windows_arch", return_value="ARM64"),
        ):
            _clear_platform_caches()
            config = get_platform_config()
            assert config.platform_key == "windows-arm64"
            assert config.arch.arch_type == Architecture.ARM64


class TestPlatformConfigProperties:
    """Test PlatformConfig derived properties."""

    def test_arm64_darwin_properties(self):
        """Test ARM64 macOS derived properties."""
        config = PlatformConfig(arch=ARM64Config(), os=DarwinConfig())
        assert config.machine_type == "virt"
        assert config.qemu_system_binary() == "qemu-system-aarch64"
        assert config.console_device == "ttyAMA0"

    def test_x86_64_linux_properties(self):
        """Test x86_64 Linux derived properties."""
        config = PlatformConfig(arch=X86_64Config(), os=LinuxConfig())
        assert config.machine_type == "q35"
        assert config.qemu_system_binary() == "qemu-system-x86_64"
        assert config.console_device == "ttyS0"

    def test_windows_x86_64_properties(self):
        """Test Windows x86_64 derived properties."""
        config = PlatformConfig(arch=X86_64Config(), os=WindowsConfig())
        assert config.qemu_system_binary() == "qemu-system-x86_64.exe"
        assert config.qemu_img_binary() == "qemu-img.exe"


class TestAcceleratorStatus:
    """Tests for AcceleratorStatus."""

    def test_accelerator_property_returns_available(self):
        """Test accelerator returns available when set."""
        status = AcceleratorStatus(available=Accelerator.KVM, fallback=Accelerator.TCG)
        assert status.accelerator == Accelerator.KVM

    def test_accelerator_property_returns_fallback(self):
        """Test accelerator returns fallback when available is None."""
        status = AcceleratorStatus(available=None, fallback=Accelerator.TCG)
        assert status.accelerator == Accelerator.TCG


class TestDetectKvm:
    """Tests for Linux KVM detection."""

    def test_kvm_not_available(self):
        """Test KVM detection when /dev/kvm doesn't exist."""
        linux_config = LinuxConfig()

        with patch.object(
            type(linux_config), "kvm_device", new_callable=lambda: property(lambda self: None)
        ):
            result = linux_config.detect_accelerator()
            assert result.available is None
            assert result.fallback == Accelerator.TCG
            assert result.error is not None
            assert "/dev/kvm" in result.error

    def test_kvm_exists_but_no_permission(self):
        """Test KVM detection when /dev/kvm exists but no permission."""
        mock_path = MagicMock()
        mock_path.exists.return_value = True

        linux_config = LinuxConfig()

        with (
            patch.object(
                type(linux_config),
                "kvm_device",
                new_callable=lambda: property(lambda self: mock_path),
            ),
            patch("os.access", return_value=False),
        ):
            result = linux_config.detect_accelerator()
            assert result.available is None
            assert result.fallback == Accelerator.TCG
            assert result.error is not None
            assert "Permission denied" in result.error

    def test_kvm_available(self):
        """Test KVM detection when /dev/kvm is available."""
        mock_path = MagicMock()
        mock_path.exists.return_value = True

        linux_config = LinuxConfig()

        with (
            patch.object(
                type(linux_config),
                "kvm_device",
                new_callable=lambda: property(lambda self: mock_path),
            ),
            patch("os.access", return_value=True),
        ):
            result = linux_config.detect_accelerator()
            assert result.available == Accelerator.KVM
            assert result.error is None


class TestGetMachineType:
    """Tests for get_machine_type function."""

    def test_x86_64_returns_q35(self):
        """Test x86_64 returns q35 machine type."""
        with (
            patch("quicksand_core.host.os_._platform.system", return_value="Linux"),
            patch("quicksand_core.host.arch._platform.machine", return_value="x86_64"),
        ):
            _clear_platform_caches()
            assert get_machine_type() == "q35"

    def test_arm64_returns_virt(self):
        """Test ARM64 returns virt machine type."""
        with (
            patch("quicksand_core.host.os_._platform.system", return_value="Linux"),
            patch("quicksand_core.host.arch._platform.machine", return_value="arm64"),
        ):
            _clear_platform_caches()
            assert get_machine_type() == "virt"


class TestRuntimeInfo:
    """Tests for RuntimeInfo dataclass."""

    def test_create_runtime_info(self, tmp_dir):
        """Test creating RuntimeInfo."""
        qemu = tmp_dir / "qemu"
        qemu_img = tmp_dir / "qemu-img"
        runtime_dir = tmp_dir / "runtime"
        qemu.touch()
        qemu_img.touch()
        runtime_dir.mkdir()

        info = RuntimeInfo(qemu_binary=qemu, qemu_img=qemu_img, runtime_dir=runtime_dir)
        assert info.qemu_binary == qemu
        assert info.qemu_img == qemu_img
        assert info.runtime_dir == runtime_dir
