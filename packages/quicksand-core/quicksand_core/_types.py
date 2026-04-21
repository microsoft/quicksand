"""Type definitions for quicksand-core.

This module provides strongly-typed enums, constants, and dataclasses for the
quicksand codebase. Using these types instead of magic strings improves type
safety, IDE autocomplete, and reduces typo-related bugs.

Note: The guest agent is a standalone Rust binary and cannot share these types.
The agent API contract is defined in quicksand-image-tools/agent-openapi.yaml.

Platform and Architecture enums are in platform.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, Required, TypedDict, runtime_checkable

from pydantic import BaseModel, ConfigDict

from .host import Accelerator

# =============================================================================
# Mount Specification
# =============================================================================


MountType = Literal["cifs", "9p"]
"""Mount protocol. ``cifs`` (default) is hot-pluggable via SMB; ``9p`` is a
virtio-9p device configured at QEMU startup (permanent, no network required)."""


@dataclass
class Mount:
    """Specification for mounting a host directory into the guest."""

    host: str
    guest: str
    readonly: bool = False
    type: MountType = "cifs"


# =============================================================================
# Network Mode
# =============================================================================


class NetworkMode(StrEnum):
    """Network access mode for the guest VM.

    Three modes, from most to least restrictive:

    - ``NONE``: No NIC at all. Complete network isolation.
    - ``MOUNTS_ONLY``: Guest can reach host (mounts work) but not the internet.
      Uses QEMU restrict=on with guestfwd tunnels. Host-enforced. Default.
    - ``FULL``: Full internet access. Guest can reach anything.

    Examples:
        network_mode = NetworkMode.NONE        # air-gapped
        network_mode = NetworkMode.MOUNTS_ONLY # mounts, no internet (default)
        network_mode = NetworkMode.FULL        # internet access
    """

    NONE = "none"
    """No network interface — maximum isolation."""

    MOUNTS_ONLY = "mounts_only"
    """QEMU restrict=on with guestfwd tunnels for host access. Guest cannot
    reach the internet (host-enforced) but can mount host directories via CIFS
    over a host-controlled relay tunnel. Supports both boot-time and dynamic
    mounts."""

    FULL = "full"
    """QEMU user-mode networking with restrict=off. Full bidirectional access —
    guest can reach host gateway (10.0.2.2) and the internet. Mounts work
    directly via the slirp gateway."""


# =============================================================================
# Port Forwarding
# =============================================================================


@dataclass
class PortForward:
    """Forward a TCP port from the host into the guest."""

    host: int
    guest: int


# =============================================================================
# Guest Agent Protocol
# =============================================================================


class Key(StrEnum):
    """QKeyCode names for use with ``press_key()``.

    Values are the QEMU QKeyCode strings. Pass one or more to
    ``sandbox.press_key()``::

        sb.press_key(Key.CTRL, Key.C)
        sb.press_key(Key.RET)
        sb.press_key(Key.F5)
    """

    # Letters
    A = "a"
    B = "b"
    C = "c"
    D = "d"
    E = "e"
    F = "f"
    G = "g"
    H = "h"
    I = "i"  # noqa: E741
    J = "j"
    K = "k"
    L = "l"
    M = "m"
    N = "n"
    O = "o"  # noqa: E741
    P = "p"
    Q = "q"
    R = "r"
    S = "s"
    T = "t"
    U = "u"
    V = "v"
    W = "w"
    X = "x"
    Y = "y"
    Z = "z"

    # Digits
    KEY_0 = "0"
    KEY_1 = "1"
    KEY_2 = "2"
    KEY_3 = "3"
    KEY_4 = "4"
    KEY_5 = "5"
    KEY_6 = "6"
    KEY_7 = "7"
    KEY_8 = "8"
    KEY_9 = "9"

    # Modifiers
    SHIFT = "shift"
    SHIFT_R = "shift_r"
    CTRL = "ctrl"
    CTRL_R = "ctrl_r"
    ALT = "alt"
    ALT_R = "alt_r"
    META_L = "meta_l"
    META_R = "meta_r"
    CAPS_LOCK = "caps_lock"
    NUM_LOCK = "num_lock"
    SCROLL_LOCK = "scroll_lock"

    # Whitespace / Navigation
    RET = "ret"
    SPC = "spc"
    TAB = "tab"
    BACKSPACE = "backspace"
    ESC = "esc"
    DELETE = "delete"
    INSERT = "insert"
    HOME = "home"
    END = "end"
    PGUP = "pgup"
    PGDN = "pgdn"
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"

    # Function keys
    F1 = "f1"
    F2 = "f2"
    F3 = "f3"
    F4 = "f4"
    F5 = "f5"
    F6 = "f6"
    F7 = "f7"
    F8 = "f8"
    F9 = "f9"
    F10 = "f10"
    F11 = "f11"
    F12 = "f12"

    # Symbols
    MINUS = "minus"
    EQUAL = "equal"
    BRACKET_LEFT = "bracket_left"
    BRACKET_RIGHT = "bracket_right"
    BACKSLASH = "backslash"
    SEMICOLON = "semicolon"
    APOSTROPHE = "apostrophe"
    GRAVE_ACCENT = "grave_accent"
    COMMA = "comma"
    DOT = "dot"
    SLASH = "slash"

    # System
    PRINT = "print"
    PAUSE = "pause"
    MENU = "menu"

    # Numpad
    KP_0 = "kp_0"
    KP_1 = "kp_1"
    KP_2 = "kp_2"
    KP_3 = "kp_3"
    KP_4 = "kp_4"
    KP_5 = "kp_5"
    KP_6 = "kp_6"
    KP_7 = "kp_7"
    KP_8 = "kp_8"
    KP_9 = "kp_9"
    KP_ADD = "kp_add"
    KP_SUBTRACT = "kp_subtract"
    KP_MULTIPLY = "kp_multiply"
    KP_DIVIDE = "kp_divide"
    KP_DECIMAL = "kp_decimal"
    KP_ENTER = "kp_enter"


class QuicksandGuestAgentMethod(StrEnum):
    """Methods supported by the guest agent protocol.

    These are the JSON-RPC method names used in host-guest communication.
    """

    EXECUTE = "execute"
    EXECUTE_STREAM = "execute_stream"
    PING = "ping"
    AUTHENTICATE = "authenticate"


# =============================================================================
# File Patterns and Constants
# =============================================================================


class FilePatterns:
    """Standard file extensions and names used by quicksand."""

    KERNEL_SUFFIX = ".kernel"
    INITRD_SUFFIX = ".initrd"
    OVERLAY = "overlay.qcow2"
    OVERLAYS_DIR = "overlays"
    MANIFEST = "manifest.json"
    SAVE_EXT = ".tar.gz"
    SAVE_EXT_LEGACY = ".tar"
    CONSOLE_LOG = "console.log"


class KernelParams:
    """Kernel command line parameter names passed to guest VMs."""

    TOKEN = "quicksand_token"
    PORT = "quicksand_port"
    # SMB mount parameters (Windows)
    SMB_PORT = "quicksand_smb_port"
    SMB_MOUNTS = "quicksand_smb_mounts"  # Base64-encoded JSON
    SMB_GATEWAY = "quicksand_smb_gateway"  # Gateway IP for guest to reach host


# =============================================================================
# Network Constants
# =============================================================================


class NetworkConstants:
    """Network-related constants for VM configuration."""

    LOCALHOST = "127.0.0.1"

    # QEMU user-mode (slirp) network — deterministic, never changes.
    QEMU_SLIRP_GATEWAY = "10.0.2.2"  # Gateway IP
    QEMU_SLIRP_GUEST_IP = "10.0.2.15"  # Guest static IP
    QEMU_SLIRP_DNS = "10.0.2.3"  # Built-in DNS forwarder
    QEMU_SLIRP_NETMASK = "255.255.255.0"

    GUEST_SMB_PORT = 445  # Standard SMB port for CIFS mounts
    GUESTFWD_SMB_IP = "10.0.2.100"  # Virtual IP for guestfwd SMB tunnel (MOUNTS_ONLY mode)
    GUESTFWD_SMB_PORT = 445  # SMB port on the guestfwd virtual IP


# =============================================================================
# Timeout Constants
# =============================================================================


class Timeouts:
    """Timeout values in seconds.

    These constants centralize timeout values that were previously scattered
    throughout the codebase. Names indicate the context where each is used.
    """

    # Boot timeouts
    BOOT_DEFAULT = 60.0
    BOOT_TCG = 600.0  # Software emulation is 10-20x slower
    BOOT_WHPX = 120.0  # Windows hypervisor is slower than KVM/HVF

    # Guest agent communication
    GUEST_AGENT_REQUEST = 30.0  # Default timeout for guest agent requests
    GUEST_AGENT_HTTP = 5.0  # HTTP client timeout
    GUEST_AGENT_CONNECT = 2.0  # HTTP connection timeout

    # File operations
    SMB_WAIT = 30.0  # Wait for SMB mount to complete
    MOUNT_OPERATION = 10.0  # Filesystem mount operations

    # Process management
    PROCESS_TERMINATE = 5.0  # Graceful process termination
    GRACEFUL_SHUTDOWN = 10.0  # Graceful VM shutdown


# =============================================================================
# Image Providers (entry point objects)
# =============================================================================


@dataclass
class BaseImageInfo:
    """Build-time metadata for quicksand-image-tools. Not used at runtime."""

    name: str
    docker_dir: Path
    version: str


@runtime_checkable
class ImageProvider(Protocol):
    """Protocol for image packages registered as ``quicksand.images`` entry points.

    Image packages export a module-level ``ImageProvider`` instance. The resolver
    calls :meth:`resolve` and gets back a ``ResolvedImage`` with everything
    needed to boot.
    """

    name: str
    type: Literal["base", "overlay"]
    images_dir: Path

    def resolve(self, arch: str | None = None) -> ResolvedImage:
        """Resolve to concrete image paths (and overlay chain if applicable).

        Args:
            arch: Image architecture (``"amd64"`` or ``"arm64"``).
                  Defaults to the host architecture.
        """
        ...


# =============================================================================
# I/O Constants
# =============================================================================


class EnvironmentVariables:
    """Environment variable names used by quicksand."""

    QEMU_MODULE_DIR = "QEMU_MODULE_DIR"


# =============================================================================
# Guest Commands
# =============================================================================


class GuestCommands:
    """Shell commands executed inside the guest VM."""

    SHELL = "/bin/sh"
    SYNC = "sync"
    FSTRIM = "fstrim -av 2>/dev/null || true"
    DETECT_DISK_LAYOUT = "test -b /dev/vda1 && echo partitioned || echo whole"
    GROWPART = "growpart /dev/vda 1 2>&1 || true"
    RESIZE_PARTITION = "resize2fs /dev/vda1 2>&1 || xfs_growfs / 2>&1 || true"
    RESIZE_WHOLE_DISK = "resize2fs /dev/vda 2>&1 || xfs_growfs / 2>&1 || true"


# =============================================================================
# Execute Protocol Types
# =============================================================================


@dataclass
class ExecuteParams:
    """Parameters for an execute request to the guest agent."""

    command: str
    timeout: float
    shell: str
    cwd: str | None = None
    exclusive: bool = False


@dataclass
class ExecuteResponseResult:
    """Result body returned by the guest agent execute method."""

    stdout: str
    stderr: str
    exit_code: int


@dataclass
class AgentErrorBody:
    """Error body returned by the guest agent on failure."""

    message: str
    code: int = -1


@dataclass
class ExecuteResult:
    """Result of executing a command in the sandbox."""

    stdout: str
    stderr: str
    exit_code: int


# =============================================================================
# Mount Options
# =============================================================================


@dataclass(frozen=True)
class MountHandle:
    """Opaque handle returned by Sandbox.mount()."""

    host: str
    guest: str
    readonly: bool
    _share_name: str  # internal SMB share name


class MountOptions:
    """Constants and helpers for host-guest filesystem mounts."""

    STABILIZATION_DELAY = 2.0  # seconds to wait for guest to stabilize after boot
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0  # seconds between mount retries

    @staticmethod
    def cifs_opts(username: str, password: str) -> str:
        """Return CIFS mount options string.

        Uses sec=none for guest/anonymous access, sec=ntlmssp for authenticated.
        """
        sec = "none" if not password else "ntlmssp"
        return f"username={username},password={password},sec={sec},vers=3.0"


# =============================================================================
# Resolution Outputs (frozen, immutable after start)
# =============================================================================


@dataclass(frozen=True)
class ResolvedImage:
    """The resolved VM disk — produced by ImageResolver, consumed by lifecycle.

    ``name`` is the canonical image name (e.g. ``"ubuntu"``).
    ``chain`` is the full disk chain from base image to topmost overlay,
    where ``chain[0]`` is the root base qcow2 (no backing file) and
    ``chain[1:]`` are overlay layers in bottom-to-top order.
    ``guest_arch`` is set when a cross-architecture save is loaded — the
    resolver detected that the save was created on a different architecture
    and the sandbox should force TCG emulation.
    """

    name: str
    chain: list[Path]
    kernel: Path | None = None
    initrd: Path | None = None
    guest_arch: str | None = None


@dataclass(frozen=True)
class ResolvedAccelerator:
    """Resolved acceleration — produced by accelerator detection during start."""

    accel: Accelerator
    nested_virt: bool = False
    boot_timeout: float = Timeouts.BOOT_DEFAULT


@dataclass
class BootTiming:
    """Phase-level timing for a sandbox boot sequence."""

    resolve_image_s: float = 0.0
    detect_accelerator_s: float = 0.0
    load_runtime_s: float = 0.0
    setup_disk_s: float = 0.0
    launch_process_s: float = 0.0
    connect_agent_s: float = 0.0
    connect_qmp_s: float = 0.0
    post_boot_s: float = 0.0

    # Sub-breakdown of connect_agent_s, parsed from console log.
    # kernel_boot_s: time from QEMU launch to init system starting.
    # init_system_s: time spent in init (services, DHCP, etc.).
    # agent_startup_s: time from agent start to successful HTTP connection.
    kernel_boot_s: float | None = None
    init_system_s: float | None = None
    agent_startup_s: float | None = None
    console_log: str | None = None

    @property
    def total_s(self) -> float:
        return (
            self.resolve_image_s
            + self.detect_accelerator_s
            + self.load_runtime_s
            + self.setup_disk_s
            + self.launch_process_s
            + self.connect_agent_s
            + self.connect_qmp_s
            + self.post_boot_s
        )

    def __str__(self) -> str:
        phases: list[tuple[str, float]] = [
            ("resolve_image", self.resolve_image_s),
            ("detect_accelerator", self.detect_accelerator_s),
            ("load_runtime", self.load_runtime_s),
            ("setup_disk", self.setup_disk_s),
            ("launch_process", self.launch_process_s),
            ("connect_agent", self.connect_agent_s),
        ]
        if self.kernel_boot_s is not None:
            phases.append(("  ├ kernel_boot", self.kernel_boot_s))
        if self.init_system_s is not None:
            phases.append(("  ├ init_system", self.init_system_s))
        if self.agent_startup_s is not None:
            phases.append(("  └ agent_startup", self.agent_startup_s))
        phases.extend(
            [
                ("connect_qmp", self.connect_qmp_s),
                ("post_boot", self.post_boot_s),
            ]
        )
        lines = []
        for name, duration in phases:
            pct = (duration / self.total_s * 100) if self.total_s > 0 else 0
            bar_len = int(min(pct / 100, 1.0) * 20)
            bar = "#" * bar_len + "-" * (20 - bar_len)
            lines.append(f"  {name:<22} {bar} {duration:>7.3f}s ({pct:>5.1f}%)")
        lines.append(f"  {'total':<22} {'':>20} {self.total_s:>7.3f}s")
        return "\n".join(lines)


# =============================================================================
# Sandbox Configuration (frozen user input)
# =============================================================================


class SandboxConfig(BaseModel):
    """Configuration for a sandbox VM.

    ``image`` is a string resolved at start time: a base image name
    (``"ubuntu"``), a save name (``"my-save"``), or a filesystem path.

    Frozen after construction — never mutated.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    image: str
    arch: str | None = None
    memory: str = "512M"
    cpus: int = 1
    mounts: list[Mount] = []
    port_forwards: list[PortForward] = []
    network_mode: NetworkMode = NetworkMode.MOUNTS_ONLY
    extra_qemu_args: list[str] = []
    boot_timeout: float = Timeouts.BOOT_DEFAULT
    accel: Accelerator | Literal["auto"] | None = "auto"
    disk_size: str | None = None
    enable_display: bool = False


class SandboxConfigParams(TypedDict, total=False):
    """TypedDict mirror of :class:`SandboxConfig` for ``Unpack``-based kwargs.

    Kept in sync by the module-level assertion below.
    """

    image: Required[str]
    arch: str | None
    memory: str
    cpus: int
    mounts: list[Mount]
    port_forwards: list[tuple[int, int]]
    network_mode: NetworkMode
    extra_qemu_args: list[str]
    boot_timeout: float
    accel: Accelerator | Literal["auto"] | None
    disk_size: str | None
    enable_display: bool


_td_keys = set(SandboxConfigParams.__annotations__)
_bm_keys = set(SandboxConfig.model_fields)
assert _td_keys == _bm_keys, (
    f"SandboxConfigParams / SandboxConfig out of sync — "
    f"only in TypedDict: {_td_keys - _bm_keys}, "
    f"only in BaseModel: {_bm_keys - _td_keys}"
)


# =============================================================================
# Save Manifest (persistence format)
# =============================================================================


class SaveManifest(BaseModel):
    """Manifest stored in a save directory's ``manifest.json``.

    ``config`` is a ``SandboxConfig`` with ``image`` set to the canonical
    base image name and ``mounts`` cleared. The save directory's
    ``overlays/`` subdirectory is the canonical source for the overlay chain.
    """

    version: int
    config: SandboxConfig
    arch: str | None = None
