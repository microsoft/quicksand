"""Unit tests for quicksand_core configuration classes."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from quicksand_core import Mount, PortForward, SandboxConfig
from quicksand_core._types import NetworkMode


class TestMount:
    """Tests for Mount dataclass."""

    def test_create_mount(self):
        """Test creating a basic mount."""
        mount = Mount(host="/host/path", guest="/guest/path")
        assert mount.host == "/host/path"
        assert mount.guest == "/guest/path"
        assert mount.readonly is False

    def test_create_readonly_mount(self):
        """Test creating a readonly mount."""
        mount = Mount(host="/host", guest="/guest", readonly=True)
        assert mount.readonly is True

    def test_mount_equality(self):
        """Test Mount equality."""
        mount1 = Mount("/a", "/b", False)
        mount2 = Mount("/a", "/b", False)
        mount3 = Mount("/a", "/b", True)

        assert mount1 == mount2
        assert mount1 != mount3


class TestSandboxConfig:
    """Tests for SandboxConfig dataclass."""

    def test_create_minimal_config(self):
        """Test creating config with only required fields."""
        config = SandboxConfig(image="ubuntu")
        assert config.image == "ubuntu"
        assert config.memory == "512M"
        assert config.cpus == 1
        assert config.mounts == []
        assert config.port_forwards == []
        assert config.network_mode is NetworkMode.MOUNTS_ONLY
        assert config.extra_qemu_args == []
        assert config.boot_timeout == 60.0

    def test_create_full_config(self):
        """Test creating config with all fields (image is str only)."""
        mounts = [Mount("/host", "/guest")]
        config = SandboxConfig(
            image="ubuntu",
            memory="2G",
            cpus=4,
            mounts=mounts,
            port_forwards=[PortForward(host=8080, guest=80)],
            network_mode=NetworkMode.FULL,
            extra_qemu_args=["-cpu", "host"],
            boot_timeout=120.0,
        )

        assert config.image == "ubuntu"
        assert config.memory == "2G"
        assert config.cpus == 4
        assert len(config.mounts) == 1
        assert config.port_forwards == [PortForward(host=8080, guest=80)]
        assert config.network_mode is NetworkMode.FULL
        assert config.extra_qemu_args == ["-cpu", "host"]
        assert config.boot_timeout == 120.0

    def test_config_requires_image(self):
        """Test that image is required."""
        with pytest.raises((TypeError, ValidationError)):
            SandboxConfig()  # type: ignore

    def test_config_with_string_image(self):
        """Test config with string image name."""
        config = SandboxConfig(image="ubuntu")
        assert config.image == "ubuntu"

    def test_config_is_frozen(self):
        """Test that SandboxConfig is frozen (immutable)."""
        config = SandboxConfig(image="ubuntu")
        with pytest.raises(ValidationError):
            config.image = "alpine"


class TestExecuteResult:
    """Tests for ExecuteResult dataclass."""

    def test_create_execute_result(self):
        """Test creating an ExecuteResult."""
        from quicksand_core import ExecuteResult

        result = ExecuteResult(stdout="hello", stderr="", exit_code=0)
        assert result.stdout == "hello"
        assert result.stderr == ""
        assert result.exit_code == 0

    def test_execute_result_with_error(self):
        """Test ExecuteResult with non-zero exit code."""
        from quicksand_core import ExecuteResult

        result = ExecuteResult(stdout="", stderr="error message", exit_code=1)
        assert result.exit_code == 1
        assert result.stderr == "error message"
