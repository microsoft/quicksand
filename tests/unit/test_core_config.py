"""Unit tests for quicksand_core configuration classes."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from quicksand_core import GuestForward, Mount, PortForward, SandboxConfig
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


class TestGuestForward:
    """Tests for GuestForward and its validation in SandboxConfig."""

    def test_host_address_defaults_to_loopback(self):
        forward = GuestForward(guest_address="10.0.2.101", guest_port=3128, host_port=8080)
        assert forward.host_address == "127.0.0.1"

    def test_config_defaults_to_no_guest_forwards(self):
        assert SandboxConfig(image="ubuntu").guest_forwards == []

    def test_config_accepts_guest_forwards(self):
        forward = GuestForward(guest_address="10.0.2.101", guest_port=3128, host_port=8080)
        config = SandboxConfig(image="ubuntu", guest_forwards=[forward])
        assert config.guest_forwards == [forward]

    @pytest.mark.parametrize(
        "guest_address",
        [
            "proxy.local",  # not an IPv4 address
            "10.0.3.101",  # outside the 10.0.2.0/24 user-mode network
            "10.0.2.0",  # network address
            "10.0.2.255",  # broadcast address
            "10.0.2.2",  # gateway
            "10.0.2.3",  # DNS
            "10.0.2.15",  # the guest itself
        ],
    )
    def test_rejects_unusable_guest_address(self, guest_address):
        forward = GuestForward(guest_address=guest_address, guest_port=3128, host_port=8080)
        with pytest.raises(ValidationError, match="guest_address"):
            SandboxConfig(image="ubuntu", guest_forwards=[forward])

    def test_rejects_duplicate_guest_endpoint(self):
        forwards = [
            GuestForward(guest_address="10.0.2.101", guest_port=3128, host_port=8080),
            GuestForward(guest_address="10.0.2.101", guest_port=3128, host_port=9090),
        ]
        with pytest.raises(ValidationError, match=r"10\.0\.2\.101:3128"):
            SandboxConfig(image="ubuntu", guest_forwards=forwards)

    def test_rejects_the_smb_tunnel_endpoint(self):
        forward = GuestForward(guest_address="10.0.2.100", guest_port=445, host_port=8080)
        with pytest.raises(ValidationError, match=r"10\.0\.2\.100:445"):
            SandboxConfig(image="ubuntu", guest_forwards=[forward])

    def test_rejects_guest_forwards_without_a_network(self):
        forward = GuestForward(guest_address="10.0.2.101", guest_port=3128, host_port=8080)
        with pytest.raises(ValidationError, match="NONE"):
            SandboxConfig(image="ubuntu", network_mode=NetworkMode.NONE, guest_forwards=[forward])


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
