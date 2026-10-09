"""Unit tests for quicksand_core configuration classes."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from quicksand_core import Forward, Guest, Host, Mount, SandboxConfig
from quicksand_core._types import NetworkConstants, NetworkMode


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
            port_forwards=[Forward(Host(8080), Guest(80))],
            network_mode=NetworkMode.FULL,
            extra_qemu_args=["-cpu", "host"],
            boot_timeout=120.0,
        )

        assert config.image == "ubuntu"
        assert config.memory == "2G"
        assert config.cpus == 4
        assert len(config.mounts) == 1
        assert config.port_forwards == [Forward(Host(8080), Guest(80))]
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


class TestForwardTypes:
    """Tests for Forward/Host/Guest and the deprecated PortForward alias."""

    def test_port_forward_is_deprecated_alias(self):
        """PortForward still builds a host-to-guest Forward but warns."""
        from quicksand_core import PortForward

        with pytest.warns(DeprecationWarning, match="Forward"):
            pf = PortForward(host=8080, guest=80)
        assert pf == Forward(Host(8080), Guest(80))

    def test_host_to_guest_defaults(self):
        f = Forward(Host(8080), Guest(80))
        assert f.host_to_guest
        assert f.host == Host(8080, "127.0.0.1")
        assert f.guest == Guest(80)
        assert f.guest.address is None  # left to QEMU: the guest's own IP

    def test_guest_to_host_defaults(self):
        f = Forward(Guest(3128), Host(8080))
        assert not f.host_to_guest
        assert f.guest == Guest(3128, NetworkConstants.GUESTFWD_DEFAULT_IP)
        assert f.host == Host(8080, "127.0.0.1")

    def test_explicit_addresses_are_kept(self):
        f = Forward(Guest(80, "10.0.2.102"), Host(5001, "db.internal"))
        assert f.guest.address == "10.0.2.102"
        assert f.host.address == "db.internal"
        g = Forward(Host(8080, "0.0.0.0"), Guest(80, "10.0.2.15"))
        assert g.host.address == "0.0.0.0"
        assert g.guest.address == "10.0.2.15"

    @pytest.mark.parametrize(
        "make",
        [
            lambda: Host(8080, "0.0.0.0"),
            lambda: Host("0.0.0.0", 8080),
            lambda: Host("0.0.0.0:8080"),
            lambda: Host(port=8080, address="0.0.0.0"),
            lambda: Host("0.0.0.0", port=8080),
        ],
    )
    def test_host_constructor_forms_are_equivalent(self, make):
        assert make() == Host(8080, "0.0.0.0")
        assert (make().port, make().address) == (8080, "0.0.0.0")

    @pytest.mark.parametrize(
        "make",
        [
            lambda: Guest(80, "10.0.2.102"),
            lambda: Guest("10.0.2.102", 80),
            lambda: Guest("10.0.2.102:80"),
            lambda: Guest(port=80, address="10.0.2.102"),
        ],
    )
    def test_guest_constructor_forms_are_equivalent(self, make):
        assert make() == Guest(80, "10.0.2.102")

    def test_port_only_forms(self):
        assert Host(8080).address == "127.0.0.1"
        assert Host(port=8080).address == "127.0.0.1"
        assert Guest(80).address is None
        assert Host("db.internal", 5432) == Host("db.internal:5432")

    @pytest.mark.parametrize(
        ("make", "exc", "match"),
        [
            (lambda: Host(), TypeError, "missing a port"),  # ty: ignore[no-matching-overload]
            (lambda: Host("0.0.0.0"), TypeError, "missing a port"),
            (lambda: Host("a:b"), ValueError, "expected 'address:port'"),
            (lambda: Host(":80"), ValueError, "expected 'address:port'"),
            (lambda: Host(1, 2), TypeError, "multiple values for port"),  # ty: ignore[no-matching-overload]
            (lambda: Host("a", "b"), TypeError, "multiple values for address"),  # ty: ignore[no-matching-overload]
            (lambda: Host("a:1", 2), TypeError, "multiple values for port"),
            (lambda: Host(1, "a", "b"), TypeError, "at most 2 positional"),  # ty: ignore[no-matching-overload]
            (lambda: Host(True), TypeError, "int port or a str address"),
            (lambda: Host(1.5), TypeError, "int port or a str address"),  # ty: ignore[no-matching-overload]
            (lambda: Host("x:0"), ValueError, "Host.port"),
        ],
    )
    def test_endpoint_constructor_rejects(self, make, exc, match):
        with pytest.raises(exc, match=match):
            make()

    def test_kind_is_not_part_of_repr_or_equality(self):
        assert "kind" not in repr(Host(1))
        assert Host(1) == Host(1, "127.0.0.1")

    @pytest.mark.parametrize(
        ("endpoint", "match"),
        [
            (lambda: Host(0), "Host.port"),
            (lambda: Guest(70000), "Guest.port"),
        ],
    )
    def test_endpoint_rejects_bad_port(self, endpoint, match):
        with pytest.raises(ValueError, match=match):
            endpoint()

    @pytest.mark.parametrize(
        ("src", "dst", "match"),
        [
            (Host(1), Host(2), "Host endpoint to a Guest endpoint"),
            (Guest(1), Guest(2), "Host endpoint to a Guest endpoint"),
            (Host(1, "localhost"), Guest(2), "Host.address must be an IPv4"),
            (Host(1), Guest(2, "guest.local"), "Guest.address must be an IPv4"),
            (Guest(1, "not-an-ip"), Host(2), "Guest.address must be an IPv4"),
            (Guest(1, "192.168.1.1"), Host(2), "slirp network"),
            (Guest(1, "10.0.2.2"), Host(2), "gateway"),
            (Guest(1, "10.0.2.3"), Host(2), "DNS"),
            (Guest(1, "10.0.2.15"), Host(2), "guest itself"),
        ],
    )
    def test_forward_rejects_bad_combinations(self, src, dst, match):
        with pytest.raises(ValueError, match=match):
            Forward(src, dst)

    def test_host_destination_may_be_hostname(self):
        f = Forward(Guest(5432), Host(5432, "db.internal"))
        assert f.host.address == "db.internal"

    def test_config_splits_forward_directions(self):
        h2g = Forward(Host(8080), Guest(80))
        g2h = Forward(Guest(3128), Host(8080))
        config = SandboxConfig(image="ubuntu", port_forwards=[h2g, g2h])
        assert config.port_forwards == [h2g, g2h]
        assert config.host_forwards == [h2g]
        assert config.guest_forwards == [g2h]

    def test_config_accepts_forward_dicts(self):
        """Dict input is discriminated by the endpoint ``kind`` tag."""
        config = SandboxConfig.model_validate(
            {
                "image": "ubuntu",
                "port_forwards": [
                    {"src": {"kind": "host", "port": 8080}, "dst": {"kind": "guest", "port": 80}},
                    {"src": {"kind": "guest", "port": 3128}, "dst": {"kind": "host", "port": 8080}},
                ],
            }
        )
        assert config.host_forwards == [Forward(Host(8080), Guest(80))]
        assert config.guest_forwards == [Forward(Guest(3128), Host(8080))]

    def test_guest_to_host_requires_nic(self):
        with pytest.raises(ValidationError, match=r"NetworkMode\.NONE"):
            SandboxConfig(
                image="ubuntu",
                network_mode=NetworkMode.NONE,
                port_forwards=[Forward(Guest(3128), Host(8080))],
            )

    def test_host_to_guest_allowed_with_network_none(self):
        """Only guest-to-host forwards need a NIC at config time."""
        config = SandboxConfig(
            image="ubuntu",
            network_mode=NetworkMode.NONE,
            port_forwards=[Forward(Host(8080), Guest(80))],
        )
        assert config.host_forwards == [Forward(Host(8080), Guest(80))]

    def test_guest_to_host_rejects_smb_tunnel_collision(self):
        with pytest.raises(ValidationError, match="CIFS mount tunnel"):
            SandboxConfig(
                image="ubuntu",
                port_forwards=[
                    Forward(
                        Guest(NetworkConstants.GUESTFWD_SMB_PORT, NetworkConstants.GUESTFWD_SMB_IP),
                        Host(8080),
                    )
                ],
            )

    def test_guest_to_host_rejects_duplicate_source(self):
        with pytest.raises(ValidationError, match="Duplicate guest-to-host"):
            SandboxConfig(
                image="ubuntu",
                port_forwards=[Forward(Guest(3128), Host(8080)), Forward(Guest(3128), Host(9090))],
            )

    def test_host_to_guest_rejects_duplicate_bind(self):
        with pytest.raises(ValidationError, match="Duplicate host-to-guest"):
            SandboxConfig(
                image="ubuntu",
                port_forwards=[Forward(Host(8080), Guest(80)), Forward(Host(8080), Guest(81))],
            )

    def test_same_port_different_address_ok(self):
        config = SandboxConfig(
            image="ubuntu",
            port_forwards=[
                Forward(Guest(3128), Host(8080)),
                Forward(Guest(3128, "10.0.2.102"), Host(9090)),
                Forward(Host(8080), Guest(80)),
                Forward(Host(8080, "0.0.0.0"), Guest(81)),
            ],
        )
        assert len(config.guest_forwards) == 2
        assert len(config.host_forwards) == 2
