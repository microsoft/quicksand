"""SMB file sharing for host-guest directory mounting.

Provides a base class and platform-specific implementations:
- QuicksandSMBServer: Pure-Python SMB3 server via QEMU guestfwd (macOS/Linux)
- QuicksandSMBTCPServer: Pure-Python SMB3 server as a loopback TCP listener
  (Windows default; no Administrator required)
- WindowsSMBServer: Windows native SMB via PowerShell (opt-in via
  QUICKSAND_WINDOWS_NATIVE_SMB=1)

All mounts (boot-time and dynamic) use CIFS over QEMU slirp networking.
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger("quicksand.smb")


class SMBServer(ABC):
    """Base class for SMB file sharing servers.

    Implementations expose host directories as SMB shares that the guest
    mounts via CIFS over QEMU's slirp gateway (10.0.2.2).
    """

    @abstractmethod
    def start(self) -> None:
        """Start the SMB server."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop the SMB server and clean up all shares."""
        ...

    @abstractmethod
    def add_share(self, host_path: str, readonly: bool = False) -> str:
        """Add an SMB share for a host directory.

        Args:
            host_path: Absolute path to the host directory.
            readonly: Whether the share should be read-only.

        Returns:
            The share name (e.g., "QUICKSAND0") for use in CIFS mount commands.
        """
        ...

    @abstractmethod
    def remove_share(self, share_name: str) -> None:
        """Remove a previously added share."""
        ...

    @property
    @abstractmethod
    def port(self) -> int:
        """The port the SMB server is listening on."""
        ...

    @property
    @abstractmethod
    def credentials(self) -> tuple[str, str]:
        """Returns (username, password) for CIFS mount authentication."""
        ...

    @abstractmethod
    def list_shares(self) -> list[dict]:
        """List all active shares.

        Returns:
            List of dicts with keys: share_name, host_path, readonly.
        """
        ...

    def get_guestfwd_cmd(self) -> str | None:
        """Return the guestfwd command for QEMU, or None if not supported.

        Implementations that run as inetd-style servers (spawned by QEMU's
        guestfwd) return the command string. TCP-based implementations
        return None and use the relay script instead.
        """
        return None


class _ConfigBackedSMBServer(SMBServer):
    """Shared base for the pure-Python SMB servers.

    Holds the share state and the JSON config file that ``quicksand_smb`` reads
    (re-read on every TREE_CONNECT, so shares can be added/removed on a running
    server). Subclasses add the transport: ``QuicksandSMBServer`` is spawned per
    connection by QEMU guestfwd; ``QuicksandSMBTCPServer`` runs a persistent
    loopback TCP listener.
    """

    def __init__(self) -> None:
        self._shares: dict[str, dict] = {}
        self._config_path: Path | None = None
        self._temp_dir: Path | None = None

    @property
    def credentials(self) -> tuple[str, str]:
        return ("guest", "")

    def start(self) -> None:
        self._temp_dir = Path(tempfile.mkdtemp(prefix="quicksand-smb-"))
        self._config_path = self._temp_dir / "smb_config.json"
        self._write_config()

    def stop(self) -> None:
        if self._temp_dir is not None and self._temp_dir.exists():
            import shutil

            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None
        self._config_path = None
        self._shares.clear()

    def add_share(self, host_path: str, readonly: bool = False) -> str:
        share_name = f"QUICKSAND_{secrets.token_hex(8)}"
        Path(host_path).mkdir(parents=True, exist_ok=True)
        self._shares[share_name] = {"host_path": host_path, "readonly": readonly}
        self._write_config()
        logger.info("Added share %s -> %s (readonly=%s)", share_name, host_path, readonly)
        return share_name

    def remove_share(self, share_name: str) -> None:
        self._shares.pop(share_name, None)
        self._write_config()
        logger.info("Removed share %s", share_name)

    def list_shares(self) -> list[dict]:
        return [
            {"share_name": name, "host_path": info["host_path"], "readonly": info["readonly"]}
            for name, info in self._shares.items()
        ]

    def _write_config(self) -> None:
        """Atomically write the shares config JSON file."""
        if self._config_path is None:
            return

        import json

        data = {
            "shares": {
                name: {"host_path": info["host_path"], "readonly": info["readonly"]}
                for name, info in self._shares.items()
            }
        }

        # Atomic write: write to temp file then rename
        tmp_path = self._config_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data))
        os.replace(str(tmp_path), str(self._config_path))


class WindowsSMBServer(SMBServer):
    """Windows native SMB sharing via PowerShell.

    Creates temporary Windows SMB shares using New-SmbShare.
    Shares are auto-cleaned up on stop.
    """

    def __init__(self, username: str, password: str = "") -> None:
        self._username = username
        self._password = password
        self._shares: dict[str, dict] = {}  # share_name → {host_path, readonly}
        self._port_value = 445  # Windows native SMB always uses 445

    def _run_powershell(self, script: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
        )

    @property
    def port(self) -> int:
        return self._port_value

    @property
    def credentials(self) -> tuple[str, str]:
        return (self._username, self._password)

    def start(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("WindowsSMBServer is only available on Windows")
        logger.info("Windows SMB server ready")

    def stop(self) -> None:
        for share_name in list(self._shares.keys()):
            self.remove_share(share_name)
        logger.info("Windows SMB shares cleaned up")

    def add_share(self, host_path: str, readonly: bool = False) -> str:
        share_name = f"QUICKSAND_{secrets.token_hex(8)}"

        path = str(Path(host_path).resolve())
        Path(path).mkdir(parents=True, exist_ok=True)

        # Remove existing share if present
        self._run_powershell(
            f"Remove-SmbShare -Name '{share_name}' -Force -ErrorAction SilentlyContinue"
        )

        user = self._username
        if readonly:
            script = (
                f"New-SmbShare -Name '{share_name}' -Path '{path}' -ReadAccess '{user}' -Temporary"
            )
        else:
            script = (
                f"New-SmbShare -Name '{share_name}' -Path '{path}' -FullAccess '{user}' -Temporary"
            )

        result = self._run_powershell(script)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to create SMB share: {result.stderr}")

        self._shares[share_name] = {"host_path": host_path, "readonly": readonly}
        logger.info("Created SMB share %s -> %s (readonly=%s)", share_name, path, readonly)
        return share_name

    def remove_share(self, share_name: str) -> None:
        result = self._run_powershell(
            f"Remove-SmbShare -Name '{share_name}' -Force -ErrorAction SilentlyContinue"
        )
        if result.returncode == 0:
            logger.info("Removed SMB share %s", share_name)
        self._shares.pop(share_name, None)

    def list_shares(self) -> list[dict]:
        return [
            {"share_name": name, "host_path": info["host_path"], "readonly": info["readonly"]}
            for name, info in self._shares.items()
        ]


class QuicksandSMBServer(_ConfigBackedSMBServer):
    """Pure-Python SMB3 server spawned by QEMU guestfwd (macOS/Linux).

    Does NOT start a background process or open a TCP port. Shares are
    configured in a JSON file; QEMU spawns a new ``python -m quicksand_smb``
    process per guest TCP connection via guestfwd.
    """

    @property
    def port(self) -> int:
        # No real TCP port — guestfwd uses the virtual IP
        return 445

    def start(self) -> None:
        super().start()
        logger.info("QuicksandSMBServer ready (config=%s)", self._config_path)

    def get_guestfwd_cmd(self) -> str:
        """Return the command string for QEMU guestfwd to spawn the SMB server."""
        assert self._config_path is not None
        return f"{sys.executable} -m quicksand_smb --config {self._config_path}"


class QuicksandSMBTCPServer(_ConfigBackedSMBServer):
    """Pure-Python SMB3 server running as a persistent loopback TCP listener.

    Used on Windows, where QEMU's guestfwd ``cmd:`` does not reliably spawn a
    per-connection helper and the native ``New-SmbShare`` path requires
    Administrator. Binds ``127.0.0.1:<random>`` and serves each accepted
    connection on its own daemon thread via ``quicksand_smb.serve_socket``. The
    guest reaches it through QEMU's slirp gateway (``10.0.2.2:<port>``) in FULL
    network mode, so no host port is exposed beyond loopback and no privileges
    are required.

    ``get_guestfwd_cmd`` returns ``None`` so the mount path uses the slirp
    gateway + this server's ``port`` rather than a guestfwd tunnel.
    """

    def __init__(self) -> None:
        super().__init__()
        self._port_value = 0
        self._server_sock: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._conn_threads: list[threading.Thread] = []

    @property
    def port(self) -> int:
        return self._port_value

    def start(self) -> None:
        super().start()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(8)
        self._port_value = sock.getsockname()[1]
        self._server_sock = sock
        self._stopping.clear()
        self._accept_thread = threading.Thread(
            target=self._accept_loop, args=(sock,), name="quicksand-smb-accept", daemon=True
        )
        self._accept_thread.start()
        logger.info(
            "QuicksandSMBTCPServer listening on 127.0.0.1:%d (config=%s)",
            self._port_value,
            self._config_path,
        )

    def stop(self) -> None:
        self._stopping.set()
        if self._server_sock is not None:
            # Closing the listening socket unblocks accept().
            with contextlib.suppress(OSError):
                self._server_sock.close()
            self._server_sock = None
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=5)
            self._accept_thread = None
        self._port_value = 0
        super().stop()

    def get_guestfwd_cmd(self) -> None:
        # Reached directly via the slirp gateway; no guestfwd tunnel.
        return None

    def _accept_loop(self, sock: socket.socket) -> None:
        from quicksand_smb import SMBConfig, serve_socket

        while not self._stopping.is_set():
            try:
                conn, _addr = sock.accept()
            except OSError:
                break  # listener closed during stop()

            # SMB is request/response with small frames; disable Nagle so the
            # guest does not stall waiting on a delayed response (e.g. a WRITE
            # ack), which otherwise shows up as a CIFS request timeout.
            with contextlib.suppress(OSError):
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            def _serve(conn: socket.socket = conn) -> None:
                try:
                    assert self._config_path is not None
                    config = SMBConfig.from_json_file(str(self._config_path))
                    serve_socket(conn, config, config_path=str(self._config_path))
                except Exception as e:
                    logger.debug("SMB connection handler ended: %s", e)
                finally:
                    with contextlib.suppress(OSError):
                        conn.close()

            t = threading.Thread(target=_serve, name="quicksand-smb-conn", daemon=True)
            t.start()
            self._conn_threads.append(t)
            # Drop references to finished handler threads.
            self._conn_threads = [t for t in self._conn_threads if t.is_alive()]


def create_smb_server() -> SMBServer:
    """Factory: returns the appropriate SMBServer for the current platform.

    - Windows: the pure-Python ``QuicksandSMBTCPServer`` (loopback TCP listener),
      which needs no Administrator rights. The native ``WindowsSMBServer``
      (PowerShell ``New-SmbShare``) is available as an opt-in via
      ``QUICKSAND_WINDOWS_NATIVE_SMB=1`` for callers who want OS-native shares
      and have the privileges to create them.
    - macOS/Linux: the pure-Python ``QuicksandSMBServer`` spawned per connection
      by QEMU guestfwd (no TCP port).
    """
    if sys.platform == "win32":
        if os.environ.get("QUICKSAND_WINDOWS_NATIVE_SMB") == "1":
            username = os.environ.get("QUICKSAND_SMB_USERNAME", "")
            password = os.environ.get("QUICKSAND_SMB_PASSWORD", "")
            if not username:
                try:
                    username = os.getlogin()
                except OSError:
                    import getpass as _getpass

                    username = _getpass.getuser()
            return WindowsSMBServer(username, password)
        return QuicksandSMBTCPServer()

    return QuicksandSMBServer()
