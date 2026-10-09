"""Unit tests for quicksand_core Sandbox class."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from quicksand_core import GuestForward, PortForward, Sandbox, SandboxConfig
from quicksand_core._types import NetworkMode
from quicksand_core.host import Accelerator, LinuxConfig, WindowsConfig
from quicksand_core.host.quicksand_guest_agent_client import _retry_on_transient_error
from quicksand_core.qemu.arch import X86_64Config
from quicksand_core.qemu.platform import PlatformConfig, RuntimeInfo


class TestSandboxInit:
    """Tests for Sandbox initialization."""

    def test_create_sandbox_with_config(self, fake_qcow2):
        """Test creating a Sandbox with a config."""
        sandbox = Sandbox(image="ubuntu")

        assert sandbox.config.image == "ubuntu"
        assert sandbox.is_running is False

    def test_sandbox_requires_image(self):
        """Test that Sandbox requires an image kwarg."""
        with pytest.raises(TypeError, match="image"):
            Sandbox()  # ty: ignore[missing-argument]

    def test_sandbox_with_progress_callback(self, fake_qcow2):
        """Test Sandbox with progress callback."""
        callback = MagicMock()
        sandbox = Sandbox(image="ubuntu", progress_callback=callback)

        assert sandbox._progress_callback == callback


class TestSandboxVMCommand:
    """Tests for VM command building."""

    def test_build_basic_command(self, fake_image_set, mock_runtime):
        """Test building basic VM command."""
        from quicksand_core._types import ResolvedAccelerator, ResolvedImage
        from quicksand_core.host import Accelerator

        sandbox = Sandbox(image="ubuntu")
        sandbox._runtime_info = mock_runtime
        sandbox._image = ResolvedImage(
            name="ubuntu",
            chain=[fake_image_set["qcow2"]],
        )
        sandbox._accel = ResolvedAccelerator(accel=Accelerator.HVF)
        sandbox._overlay_path = Path("/tmp/overlay.qcow2")
        sandbox._agent_port = 12345
        sandbox._agent_token = "testtoken123"

        cmd = sandbox._build_vm_command()

        assert str(mock_runtime.qemu_binary) in cmd
        assert "-m" in cmd
        assert "512M" in cmd
        assert "-smp" in cmd
        assert "1" in cmd

    def test_command_includes_kernel(self, fake_image_set, mock_runtime):
        """Test that command includes -kernel when kernel is available."""
        from quicksand_core._types import ResolvedAccelerator, ResolvedImage
        from quicksand_core.host import Accelerator

        sandbox = Sandbox(image="ubuntu")
        sandbox._runtime_info = mock_runtime
        sandbox._image = ResolvedImage(
            name="ubuntu",
            chain=[fake_image_set["qcow2"]],
            kernel=fake_image_set["kernel"],
            initrd=fake_image_set["initrd"],
        )
        sandbox._accel = ResolvedAccelerator(accel=Accelerator.HVF)
        sandbox._overlay_path = Path("/tmp/overlay.qcow2")
        sandbox._agent_port = 12345
        sandbox._agent_token = "testtoken123"

        cmd = sandbox._build_vm_command()

        assert "-kernel" in cmd
        assert str(fake_image_set["kernel"]) in cmd
        assert "-initrd" in cmd
        assert str(fake_image_set["initrd"]) in cmd
        assert "-append" in cmd


def _create_test_platform_config() -> PlatformConfig:
    """Helper to create a PlatformConfig for testing (x86_64 Linux)."""
    return PlatformConfig(arch=X86_64Config(), os=LinuxConfig())


class TestSandboxNetworkArgs:
    """Tests for network argument building."""

    def test_network_disabled(self, fake_qcow2):
        """Test network disabled config."""
        config = SandboxConfig(
            image="ubuntu",
            network_mode=NetworkMode.NONE,
        )
        platform_config = _create_test_platform_config()

        args = platform_config._build_network_args(config, 12345)
        assert args == ["-nic", "none"]

    def test_network_restricted(self, fake_qcow2):
        """Test restricted network (default)."""
        config = SandboxConfig(
            image="ubuntu",
            network_mode=NetworkMode.MOUNTS_ONLY,
        )
        platform_config = _create_test_platform_config()

        args = platform_config._build_network_args(config, 12345)
        assert "restrict=on" in args[1]

    def test_network_unrestricted(self, fake_qcow2):
        """Test unrestricted network."""
        config = SandboxConfig(
            image="ubuntu",
            network_mode=NetworkMode.FULL,
        )
        platform_config = _create_test_platform_config()

        args = platform_config._build_network_args(config, 12345)
        assert "restrict=off" in args[1]

    def test_network_mounts_only_without_smb(self, fake_qcow2):
        """Test MOUNTS_ONLY mode without SMB port (no guestfwd)."""
        config = SandboxConfig(
            image="ubuntu",
            network_mode=NetworkMode.MOUNTS_ONLY,
        )
        platform_config = _create_test_platform_config()

        args = platform_config._build_network_args(config, 12345)
        assert "restrict=on" in args[1]
        assert "guestfwd" not in args[1]

    def test_network_mounts_only_with_smb(self, fake_qcow2):
        """Test MOUNTS_ONLY mode with SMB port adds guestfwd tunnel."""
        config = SandboxConfig(
            image="ubuntu",
            network_mode=NetworkMode.MOUNTS_ONLY,
        )
        platform_config = _create_test_platform_config()

        args = platform_config._build_network_args(config, 12345, smb_port=4450)
        assert "restrict=on" in args[1]
        assert "guestfwd=tcp:10.0.2.100:445-cmd:" in args[1]
        assert "_tcp_relay.py" in args[1]
        assert "127.0.0.1 4450" in args[1]

    def test_mounts_only_relay_command_keeps_spaces_in_paths(self, fake_qcow2, monkeypatch):
        """QEMU splits a guestfwd cmd: like a shell, so a spaced Python path must be quoted."""
        python = "/opt/my tools/bin/python3"
        monkeypatch.setattr(sys, "executable", python)
        config = SandboxConfig(image="ubuntu", network_mode=NetworkMode.MOUNTS_ONLY)
        platform_config = _create_test_platform_config()

        args = platform_config._build_network_args(config, 12345, smb_port=4450)
        argv = shlex.split(args[1].split("-cmd:", 1)[1])
        assert argv[0] == python
        assert argv[1].endswith("_tcp_relay.py")
        assert argv[2:] == ["127.0.0.1", "4450"]

    def test_port_forwards(self, fake_qcow2):
        """Test port forwarding configuration."""
        config = SandboxConfig(
            image="ubuntu",
            port_forwards=[PortForward(host=8080, guest=80), PortForward(host=8443, guest=443)],
        )
        platform_config = _create_test_platform_config()

        args = platform_config._build_network_args(config, 12345)
        assert "hostfwd=tcp:127.0.0.1:8080-:80" in args[1]
        assert "hostfwd=tcp:127.0.0.1:8443-:443" in args[1]

    @pytest.mark.parametrize("network_mode", [NetworkMode.MOUNTS_ONLY, NetworkMode.FULL])
    def test_guest_forwards_relay_each_connection_to_the_host(self, fake_qcow2, network_mode):
        """Each GuestForward becomes a guestfwd rule that runs the TCP relay."""
        config = SandboxConfig(
            image="ubuntu",
            network_mode=network_mode,
            guest_forwards=[
                GuestForward(guest_address="10.0.2.101", guest_port=3128, host_port=8080),
                GuestForward(
                    guest_address="10.0.2.102",
                    guest_port=80,
                    host_port=9000,
                    host_address="192.168.1.20",
                ),
            ],
        )
        platform_config = _create_test_platform_config()

        args = platform_config._build_network_args(config, 12345)
        rules = [r for r in args[1].split(",") if r.startswith("guestfwd=")]
        assert len(rules) == 2
        targets = []
        for rule in rules:
            endpoint, cmd = rule.removeprefix("guestfwd=").split("-cmd:", 1)
            argv = shlex.split(cmd)
            assert argv[0] == sys.executable
            assert argv[1].endswith("_tcp_relay.py")
            targets.append((endpoint, argv[2:]))
        assert targets == [
            ("tcp:10.0.2.101:3128", ["127.0.0.1", "8080"]),
            ("tcp:10.0.2.102:80", ["192.168.1.20", "9000"]),
        ]

    def test_guest_forwards_are_refused_on_windows_hosts(self, fake_qcow2):
        """QEMU for Windows cannot spawn guestfwd commands, so fail loudly."""
        config = SandboxConfig(
            image="ubuntu",
            guest_forwards=[
                GuestForward(guest_address="10.0.2.101", guest_port=3128, host_port=8080)
            ],
        )
        platform_config = PlatformConfig(arch=X86_64Config(), os=WindowsConfig())

        with pytest.raises(RuntimeError, match="Windows"):
            platform_config._build_network_args(config, 12345)


class TestSandboxMountConfig:
    """Tests for mount configuration validation."""

    def test_no_mounts_no_qemu_args(self, fake_qcow2):
        """Test that mounts don't add QEMU args (CIFS is handled at runtime)."""
        config = SandboxConfig(image="ubuntu")
        platform_config = _create_test_platform_config()
        # CIFS mounts don't require any QEMU-level args
        cmd = platform_config.build_qemu_command(
            config=config,
            runtime_info=RuntimeInfo(
                qemu_binary=Path("/usr/bin/qemu-system-aarch64"),
                qemu_img=Path("/usr/bin/qemu-img"),
                runtime_dir=Path("/usr"),
            ),
            kernel_path=None,
            initrd_path=None,
            overlay_path=Path(str(fake_qcow2)),
            agent_port=12345,
            agent_token="test_token",
            accelerator=None,
        )
        # No -fsdev or virtio-9p args should be present
        assert "-fsdev" not in cmd
        assert "virtio-9p" not in str(cmd)


class TestWhpxAccelerator:
    """Regression: x86_64 WHPX must always get kernel-irqchip=off.

    WHPX's in-kernel irqchip + the guest's noapic boot param delivers device
    interrupts unreliably (CIFS mounts hang). kernel-irqchip=off fixes it on
    x86_64. This was previously gated on nested_virt (baseboard == Microsoft),
    but bare-metal Windows x86_64 hosts need it too. Arm WHPX accepts only
    kernel-irqchip=on, so other architectures keep a bare accel argument.
    """

    def _build(
        self,
        fake_qcow2,
        *,
        nested_virt: bool,
        arch=None,
        accelerator: Accelerator | None = Accelerator.WHPX,
        qemu_binary: str = "/usr/bin/qemu-system-x86_64",
    ):
        from quicksand_core.host import WindowsConfig
        from quicksand_core.qemu.arch import X86_64Config

        if arch is None:
            arch = X86_64Config()
        platform_config = PlatformConfig(arch=arch, os=WindowsConfig())
        return platform_config.build_qemu_command(
            config=SandboxConfig(image="ubuntu"),
            runtime_info=RuntimeInfo(
                qemu_binary=Path(qemu_binary),
                qemu_img=Path("/usr/bin/qemu-img"),
                runtime_dir=Path("/usr"),
            ),
            kernel_path=None,
            initrd_path=None,
            overlay_path=Path(str(fake_qcow2)),
            agent_port=12345,
            agent_token="test_token",
            accelerator=accelerator,
            nested_virt=nested_virt,
        )

    def test_whpx_kernel_irqchip_off_bare_metal(self, fake_qcow2):
        cmd = self._build(fake_qcow2, nested_virt=False)
        assert "-accel" in cmd
        accel_val = cmd[cmd.index("-accel") + 1]
        assert accel_val == "whpx,kernel-irqchip=off"

    def test_whpx_kernel_irqchip_off_nested(self, fake_qcow2):
        cmd = self._build(fake_qcow2, nested_virt=True)
        accel_val = cmd[cmd.index("-accel") + 1]
        assert accel_val == "whpx,kernel-irqchip=off"

    def test_arm64_whpx_omits_kernel_irqchip(self, fake_qcow2):
        """ARM64 WHPX (qemu-system-aarch64) rejects kernel-irqchip=off."""
        from quicksand_core.qemu.arch import ARM64Config

        for nested_virt in (False, True):
            cmd = self._build(
                fake_qcow2,
                nested_virt=nested_virt,
                arch=ARM64Config(),
                accelerator=Accelerator.WHPX,
                qemu_binary="/usr/bin/qemu-system-aarch64",
            )
            accel_val = cmd[cmd.index("-accel") + 1]
            assert accel_val == "whpx"
            assert "kernel-irqchip" not in " ".join(cmd)

    def test_other_accelerators_stay_bare(self, fake_qcow2):
        """KVM, HVF, and TCG stay bare; accelerator=None omits -accel."""
        from quicksand_core.qemu.arch import ARM64Config, X86_64Config

        cases = (
            (X86_64Config(), "/usr/bin/qemu-system-x86_64"),
            (ARM64Config(), "/usr/bin/qemu-system-aarch64"),
        )
        for arch, qemu_binary in cases:
            for accel in (Accelerator.KVM, Accelerator.HVF, Accelerator.TCG):
                cmd = self._build(
                    fake_qcow2,
                    nested_virt=False,
                    arch=arch,
                    accelerator=accel,
                    qemu_binary=qemu_binary,
                )
                accel_val = cmd[cmd.index("-accel") + 1]
                assert accel_val == accel.value
                assert "irqchip" not in " ".join(cmd)

            cmd = self._build(
                fake_qcow2,
                nested_virt=False,
                arch=arch,
                accelerator=None,
                qemu_binary=qemu_binary,
            )
            assert "-accel" not in cmd


class TestSandboxContextManager:
    """Tests for async context manager interface."""

    @pytest.mark.asyncio
    async def test_context_manager_calls_start_stop(self, fake_qcow2):
        """Test that context manager calls start and stop."""
        sandbox = Sandbox(image="ubuntu")

        with (
            patch.object(sandbox, "start", new_callable=AsyncMock) as mock_start,
            patch.object(sandbox, "stop", new_callable=AsyncMock) as mock_stop,
        ):
            async with sandbox:
                mock_start.assert_called_once()
            mock_stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_stops_on_exception(self, fake_qcow2):
        """Test that context manager stops even on exception."""
        sandbox = Sandbox(image="ubuntu")

        with (
            patch.object(sandbox, "start", new_callable=AsyncMock),
            patch.object(sandbox, "stop", new_callable=AsyncMock) as mock_stop,
        ):
            with pytest.raises(ValueError):
                async with sandbox:
                    raise ValueError("test error")
            mock_stop.assert_called_once()


class TestRetryLogic:
    """Tests for transient error retry logic."""

    @pytest.mark.asyncio
    async def test_retry_on_connection_reset(self):
        """Test that ConnectionResetError triggers retry."""
        call_count = 0

        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionResetError(54, "Connection reset by peer")
            return "success"

        result = await _retry_on_transient_error(flaky_func, max_retries=3, base_delay=0.01)
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_on_read_error(self):
        """Test that httpx.ReadError triggers retry."""
        call_count = 0

        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.ReadError("Connection reset")
            return "success"

        result = await _retry_on_transient_error(flaky_func, max_retries=3, base_delay=0.01)
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_connect_error(self):
        """Test that httpx.ConnectError triggers retry."""
        call_count = 0

        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.ConnectError("Connection refused")
            return "success"

        result = await _retry_on_transient_error(flaky_func, max_retries=3, base_delay=0.01)
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_http_status_error(self):
        """Test that HTTPStatusError does NOT trigger retry."""
        call_count = 0

        async def auth_error():
            nonlocal call_count
            call_count += 1
            request = httpx.Request("POST", "http://test")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("Unauthorized", request=request, response=response)

        with pytest.raises(httpx.HTTPStatusError):
            await _retry_on_transient_error(auth_error, max_retries=3, base_delay=0.01)

        assert call_count == 1  # No retries

    @pytest.mark.asyncio
    async def test_max_retries_exhausted(self):
        """Test that exception is raised after max retries."""
        call_count = 0

        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise httpx.ConnectError("Connection refused")

        with pytest.raises(httpx.ConnectError):
            await _retry_on_transient_error(always_fails, max_retries=2, base_delay=0.01)

        assert call_count == 3  # Initial + 2 retries

    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        """Test that successful first try returns immediately."""
        call_count = 0

        async def success_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await _retry_on_transient_error(success_func, max_retries=3, base_delay=0.01)
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_broken_pipe(self):
        """Test that BrokenPipeError triggers retry."""
        call_count = 0

        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise BrokenPipeError("Broken pipe")
            return "success"

        result = await _retry_on_transient_error(flaky_func, max_retries=3, base_delay=0.01)
        assert result == "success"
        assert call_count == 2
