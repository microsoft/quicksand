"""QEMU platform configuration and command building.

Provides PlatformConfig which composes architecture + OS configuration to produce
QEMU command lines. Also provides runtime discovery (get_runtime, get_accelerator).

Performance optimizations applied automatically:
- io_uring disk AIO: ~50% lower disk latency (Linux only)
- IOThreads: Better concurrent disk I/O (all platforms)
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from .._types import KernelParams, NetworkConstants, NetworkMode
from ..host.os_ import (
    Accelerator,
    AcceleratorStatus,
    BaseOSConfig,
    OSConfig,
)
from ..host.smb import SMBServer
from .arch import (
    ArchitectureConfig,
    BaseArchitectureConfig,
    MachineType,
)

if TYPE_CHECKING:
    from .._types import SandboxConfig

__all__ = [
    "PlatformConfig",
    "RuntimeInfo",
    "detect_accelerator",
    "get_accelerator",
    "get_machine_type",
    "get_platform_config",
    "get_platform_config_for_arch",
    "get_runtime",
    "is_runtime_available",
]


@dataclass
class RuntimeInfo:
    """Resolved paths to QEMU binaries."""

    qemu_binary: Path
    qemu_img: Path
    runtime_dir: Path
    data_dir: Path | None = None  # share/qemu/ for firmware/ROMs
    module_dir: Path | None = None  # lib/qemu/ for accelerator modules (Linux)


class PlatformConfig:
    """Composed platform configuration combining architecture + OS.

    Use get_platform_config() to get the cached singleton for the current platform.
    """

    def __init__(
        self,
        arch: BaseArchitectureConfig | None = None,
        os: BaseOSConfig | None = None,
    ):
        self.arch = arch if arch is not None else ArchitectureConfig()
        self.os = os if os is not None else OSConfig()

    @property
    def platform_key(self) -> str:
        return f"{self.os.os_type}-{self.arch.arch_type}"

    def qemu_system_binary(self) -> str:
        return f"qemu-system-{self.arch.qemu_suffix}{self.os.binary_extension}"

    def qemu_img_binary(self) -> str:
        return f"qemu-img{self.os.binary_extension}"

    def detect_accelerator(self) -> AcceleratorStatus:
        return self.os.detect_accelerator()

    def get_accelerator(self) -> Accelerator:
        return self.detect_accelerator().accelerator

    @property
    def machine_type(self) -> MachineType:
        return self.arch.machine_type

    @property
    def console_device(self) -> str:
        return self.arch.console_device

    @property
    def virtio_net_device(self) -> str:
        return self.arch.virtio_net_device

    @property
    def virtio_blk_device(self) -> str:
        return self.arch.virtio_blk_device

    @property
    def cache_dir(self) -> Path:
        return self.os.cache_dir

    @property
    def images_dir(self) -> Path:
        return self.os.images_dir

    @property
    def disk_aio(self) -> str | None:
        return self.os.disk_aio

    def _get_effective_machine_type(self) -> MachineType:
        return self.arch.machine_type

    def _get_virtio_blk_device(self) -> str:
        return self.arch.virtio_blk_device

    def _get_virtio_net_device(self) -> str:
        return self.arch.virtio_net_device

    # =========================================================================
    # QEMU Command Building
    # =========================================================================

    def build_qemu_command(
        self,
        config: SandboxConfig,
        runtime_info: RuntimeInfo,
        kernel_path: Path | None,
        initrd_path: Path | None,
        overlay_path: Path,
        agent_port: int,
        agent_token: str,
        accelerator: Accelerator | None,
        nested_virt: bool = False,
        qmp_port: int | None = None,
        vnc_port: int | None = None,
        smb_port: int | None = None,
        smb_server: SMBServer | None = None,
        agent_socket_path: Path | None = None,
    ) -> list[str]:
        """Build the complete QEMU command line."""
        data_dir = runtime_info.data_dir
        effective_machine = self._get_effective_machine_type()
        virtio_blk = self._get_virtio_blk_device()

        drive_opts = (
            f"file={overlay_path},format=qcow2,if=none,id=drive0,"
            f"cache=writethrough,discard=unmap,detect-zeroes=unmap"
        )
        if self.disk_aio:
            drive_opts += f",aio={self.disk_aio}"

        cmd = [
            str(runtime_info.qemu_binary),
            "-nodefaults",
            "-machine",
            effective_machine,
            "-m",
            config.memory,
            "-smp",
            str(config.cpus),
        ]

        # -L must come before -display so QEMU can locate keymap files.
        if data_dir and data_dir.exists():
            cmd.extend(["-L", str(data_dir)])

        cmd.extend(
            [
                "-object",
                "iothread,id=iothread0",
                "-drive",
                drive_opts,
                "-device",
                f"{virtio_blk},drive=drive0,iothread=iothread0",
                "-serial",
                "stdio",
            ]
        )

        if vnc_port is not None:
            # Enable virtual display for input injection and screenshots.
            # VNC is bound to localhost only.
            # QEMU VNC syntax: -display vnc=host:display where port = 5900 + display.
            # find_free_vnc_port() allocates in 5900-5999, so display = port - 5900.
            # The GPU device is arch-specific: virtio-vga (x86) or virtio-gpu-pci (ARM64).
            vnc_display = vnc_port - 5900
            gpu_device = self.arch.virtio_gpu_device
            cmd.extend(
                [
                    "-device",
                    gpu_device,
                    "-display",
                    f"vnc=127.0.0.1:{vnc_display}",
                    # USB tablet provides absolute mouse coordinates for input-send-event.
                    # Without it, QEMU has no absolute input handler and rejects abs events.
                    "-device",
                    "usb-ehci,id=ehci",
                    "-device",
                    "usb-tablet,bus=ehci.0",
                    # VirtIO keyboard: QMP send-key injects through this device.
                    # Without it, send-key has no keyboard backend on ARM64 virt
                    # (no PS/2 i8042 controller exists on this machine type).
                    "-device",
                    "virtio-keyboard-pci",
                ]
            )
        else:
            cmd.extend(["-nographic", "-vga", "none"])

        if effective_machine != MachineType.VIRT:
            cmd.extend(["-global", "virtio-net-pci.romfile="])

        has_hw_accel = accelerator in (Accelerator.KVM, Accelerator.HVF)
        cmd.extend(self.arch.build_cpu_args(has_hw_accel))

        if accelerator:
            accel_arg = accelerator.value
            if accelerator == Accelerator.WHPX and nested_virt:
                accel_arg = "whpx,kernel-irqchip=off"
            cmd.extend(["-accel", accel_arg])

        if qmp_port is not None:
            cmd.extend(["-qmp", f"tcp:127.0.0.1:{qmp_port},server,nowait"])

        if agent_socket_path is not None:
            cmd.extend(self._build_virtio_serial_args(agent_socket_path))

        cmd.extend(
            self._build_kernel_args(config, kernel_path, initrd_path, agent_port, agent_token)
        )
        cmd.extend(self._build_network_args(config, agent_port, smb_port, smb_server))
        cmd.extend(self._build_9p_args(config))
        cmd.extend(config.extra_qemu_args)

        return cmd

    def _build_kernel_args(
        self,
        config: SandboxConfig,
        kernel_path: Path | None,
        initrd_path: Path | None,
        agent_port: int,
        agent_token: str,
    ) -> list[str]:
        if not kernel_path:
            return []

        cmd = ["-kernel", str(kernel_path)]

        kernel_args = (
            f"root=/dev/vda rw rootflags=rw console={self.console_device} rootfstype=ext4 "
            f"quiet loglevel=0 raid=noautodetect "
            f"{KernelParams.TOKEN}={agent_token} "
            f"{KernelParams.PORT}={agent_port}"
        )

        for param in self.os.extra_kernel_params:
            kernel_args += f" {param}"

        cmd.extend(["-append", kernel_args])

        if initrd_path:
            cmd.extend(["-initrd", str(initrd_path)])

        return cmd

    def _build_virtio_serial_args(
        self,
        socket_path: Path,
    ) -> list[str]:
        """Build QEMU args for virtio-serial agent channel."""
        use_mmio = self.arch.machine_type == MachineType.VIRT
        serial_device = "virtio-serial-device" if use_mmio else "virtio-serial-pci"

        return [
            "-device",
            serial_device,
            "-chardev",
            f"socket,path={socket_path},server=on,wait=off,id=vserial0",
            "-device",
            "virtserialport,chardev=vserial0,name=quicksand.agent.0",
        ]

    def _build_9p_args(
        self,
        config: SandboxConfig,
    ) -> list[str]:
        """Build QEMU args for virtio-9p (Plan 9) mounts.

        Each 9p mount in config.mounts gets a ``-fsdev``/``-device`` pair.
        The mount tag is ``pb9p{i}`` where i is the 0-based index among 9p
        mounts only. The same tag is used in the guest ``mount -t 9p`` command.
        """
        plan9_mounts = [m for m in config.mounts if m.type == "9p"]
        if not plan9_mounts:
            return []

        # MMIO machines (virt/ARM64) use virtio-*-device; PCI machines (q35) use virtio-*-pci
        use_mmio = self.arch.machine_type == MachineType.VIRT
        device_type = "virtio-9p-device" if use_mmio else "virtio-9p-pci"

        cmd: list[str] = []
        for i, mount in enumerate(plan9_mounts):
            tag = f"pb9p{i}"
            ro = ",readonly=on" if mount.readonly else ""
            cmd.extend(
                [
                    "-fsdev",
                    f"local,id=pb_fs_{i},path={mount.host},security_model=none{ro}",
                    "-device",
                    f"{device_type},id=pb_9p_{i},fsdev=pb_fs_{i},mount_tag={tag}",
                ]
            )
        return cmd

    def _build_network_args(
        self,
        config: SandboxConfig,
        agent_port: int,
        smb_port: int | None = None,
        smb_server: SMBServer | None = None,
    ) -> list[str]:
        if config.network_mode is NetworkMode.NONE:
            return ["-nic", "none"]

        hostfwd = f"hostfwd=tcp:127.0.0.1:{agent_port}-:{agent_port}"

        for pf in config.port_forwards:
            hostfwd += f",hostfwd=tcp:127.0.0.1:{pf.host}-:{pf.guest}"

        restrict = "off" if config.network_mode is NetworkMode.FULL else "on"

        # Add guestfwd tunnel for SMB access. QEMU spawns a new process per
        # guest TCP connection (inetd-style). QuicksandSMBServer is the SMB
        # server itself; SambaSMBServer uses a TCP relay to an external smbd.
        guestfwd = ""
        guestfwd_ip = NetworkConstants.GUESTFWD_SMB_IP
        guestfwd_port = NetworkConstants.GUESTFWD_SMB_PORT

        guestfwd_cmd = smb_server.get_guestfwd_cmd() if smb_server is not None else None
        if guestfwd_cmd is not None:
            guestfwd = f",guestfwd=tcp:{guestfwd_ip}:{guestfwd_port}-cmd:{guestfwd_cmd}"
        elif config.network_mode is NetworkMode.MOUNTS_ONLY and smb_port is not None:
            import sys

            relay_script = str(Path(__file__).resolve().parent.parent / "_tcp_relay.py")
            guestfwd = (
                f",guestfwd=tcp:{guestfwd_ip}:{guestfwd_port}-"
                f"cmd:{sys.executable} {relay_script} "
                f"{NetworkConstants.LOCALHOST} {smb_port}"
            )

        netdev_opts = f"user,id=net0,restrict={restrict},{hostfwd}{guestfwd}"
        virtio_net = self._get_virtio_net_device()

        return [
            "-netdev",
            netdev_opts,
            "-device",
            f"{virtio_net},netdev=net0",
        ]


@lru_cache(maxsize=1)
def get_platform_config() -> PlatformConfig:
    """Get the current platform config (cached singleton)."""
    return PlatformConfig()


def get_platform_config_for_arch(arch: str) -> PlatformConfig:
    """Get a PlatformConfig for a specific guest architecture.

    Uses the given architecture's QEMU config (machine type, virtio devices,
    console) with the host OS config. Used for cross-architecture save loading.

    Args:
        arch: Guest architecture (``"x86_64"``, ``"arm64"``, or any alias
              accepted by :meth:`Architecture.from_str`).
    """
    from ..host.arch import Architecture

    guest = Architecture.from_str(arch)
    if guest == Architecture.X86_64:
        from .arch import X86_64Config

        arch_config = X86_64Config()
    else:
        from .arch import ARM64Config

        arch_config = ARM64Config()
    return PlatformConfig(arch=arch_config)


def detect_accelerator() -> AcceleratorStatus:
    """Detect available hardware accelerator with detailed status."""
    return get_platform_config().detect_accelerator()


def get_accelerator() -> Accelerator:
    """Get the best available accelerator."""
    return detect_accelerator().accelerator


def get_machine_type() -> MachineType:
    """Get the appropriate machine type for the current architecture."""
    return get_platform_config().machine_type


# =============================================================================
# Runtime discovery
# =============================================================================


def get_runtime(config: PlatformConfig | None = None) -> RuntimeInfo:
    """Get the QEMU runtime, trying bundled first then system.

    Args:
        config: Platform config to use for binary name resolution.
                Defaults to the host platform config. Pass a guest-arch
                config for cross-architecture save loading.

    Raises:
        RuntimeError: If no QEMU runtime is found.
    """
    if config is None:
        config = get_platform_config()

    try:
        bundled = _find_bundled_runtime(config)
        if bundled:
            return bundled
    except _WrongArchError:
        raise  # installed but wrong arch — don't fall through to system QEMU

    system = _find_system_runtime(config)
    if system:
        return system

    raise RuntimeError(
        "QEMU not found. Either:\n"
        "  - Install bundled: pip install quicksand-qemu\n"
        "  - Or install system QEMU and ensure it's in PATH\n"
        "  - Or auto-install: quicksand.ensure_runtime()"
    )


class _WrongArchError(RuntimeError):
    """Raised when quicksand-qemu is installed but built for a different architecture."""


def _find_bundled_runtime(config: PlatformConfig | None = None) -> RuntimeInfo | None:
    """Return the bundled QEMU runtime, or None if quicksand-qemu is not installed.

    Raises _WrongArchError if the package is installed but contains
    binaries for a different architecture than the host hardware.
    """
    try:
        from quicksand_qemu import get_bin_dir

        bin_dir = get_bin_dir()
    except ImportError:
        return None
    except FileNotFoundError:
        return None

    if config is None:
        config = get_platform_config()

    # Legacy fat wheel support: older win_amd64 wheels may contain both
    # bin/x86_64/ and bin/arm64/ subdirectories.  Pick the one matching
    # the native hardware architecture.  New slim wheels store binaries
    # directly in bin/ so this check is a backward-compatibility fallback.
    from ..host.arch import _detect_architecture

    arch = _detect_architecture()
    arch_subdir = bin_dir / ("arm64" if arch.image_arch == "arm64" else "x86_64")
    if arch_subdir.is_dir():
        bin_dir = arch_subdir

    qemu_binary = bin_dir / config.qemu_system_binary()
    qemu_img = bin_dir / config.qemu_img_binary()

    if qemu_binary.exists() and qemu_img.exists():
        data_dir = bin_dir / "share" / "qemu"
        module_dir = bin_dir / "lib" / "qemu"
        return RuntimeInfo(
            qemu_binary=qemu_binary,
            qemu_img=qemu_img,
            runtime_dir=bin_dir.parent,
            data_dir=data_dir if data_dir.exists() else None,
            module_dir=module_dir if module_dir.exists() else None,
        )

    # quicksand-qemu is installed but binaries don't match native arch.
    from ..host.arch import _is_emulated

    if _is_emulated():
        raise _WrongArchError(
            f"quicksand-qemu is installed but contains binaries for a different "
            f"architecture ({arch} expected).\n"
            f"Python is running under platform emulation, so pip installed "
            f"the wrong variant.\n"
            f"Reinstall with the correct architecture:\n"
            f"  quicksand install quicksand-qemu"
        )

    raise _WrongArchError(
        f"quicksand-qemu is installed but QEMU binaries for {arch} were not found.\n"
        f"Reinstall with:  quicksand install quicksand-qemu"
    )


def _find_system_runtime(config: PlatformConfig | None = None) -> RuntimeInfo | None:
    import shutil

    if config is None:
        config = get_platform_config()
    qemu_binary = shutil.which(config.qemu_system_binary())
    qemu_img = shutil.which(config.qemu_img_binary())

    if qemu_binary and qemu_img:
        # Infer data_dir from the binary's prefix (e.g. /usr or /opt/homebrew).
        # Handles Homebrew (/opt/homebrew/bin -> /opt/homebrew/share/qemu) and
        # standard Linux installs (/usr/bin -> /usr/share/qemu).
        bin_parent = Path(qemu_binary).parent  # e.g. /opt/homebrew/bin
        candidate_data = bin_parent.parent / "share" / "qemu"
        data_dir = candidate_data if candidate_data.exists() else None
        return RuntimeInfo(
            qemu_binary=Path(qemu_binary),
            qemu_img=Path(qemu_img),
            runtime_dir=bin_parent,
            data_dir=data_dir,
            module_dir=None,
        )
    return None


def is_runtime_available() -> bool:
    """Check if any QEMU runtime is available (bundled or system)."""
    try:
        return _find_bundled_runtime() is not None or _find_system_runtime() is not None
    except _WrongArchError:
        return False
