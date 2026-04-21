"""Mount mixin — host-guest filesystem mounting operations for the Sandbox class.

Boot-time mounts support two protocols:
- ``cifs`` (default): CIFS/SMB3 over QEMU slirp networking. Hot-pluggable via
  mount()/unmount(). Requires network_mode=FULL or MOUNTS_ONLY.
- ``9p``: virtio-9p device configured at QEMU startup. No network required;
  works with network_mode=NONE. Init-time only (not hot-pluggable).

The SMB server is lazily started only when CIFS mounts are present.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .._types import MountHandle, MountOptions, NetworkConstants, NetworkMode, Timeouts
from ..host.smb import SMBServer, create_smb_server
from ._protocol import _SandboxProtocol

logger = logging.getLogger("quicksand.mounts")


class _MountMixin(_SandboxProtocol):
    """Mixin providing host-guest mount operations via CIFS.

    Supports both boot-time mounts (from config.mounts) and dynamic
    mounts via mount()/unmount() on a running sandbox.
    """

    def _ensure_smb_server(self) -> SMBServer:
        """Lazily start the SMB server on first mount."""
        if self._smb_server is None:
            self._smb_server = create_smb_server()
            self._smb_server.start()
        return self._smb_server

    async def _mount_9p_share(self, tag: str, guest_path: str, readonly: bool) -> None:
        """Mount a virtio-9p share inside the guest."""
        await self.execute(
            f"sudo mkdir -p {guest_path}",
            timeout=Timeouts.MOUNT_OPERATION,
        )
        ro_opt = ",ro" if readonly else ""
        result = await self.execute(
            f"sudo mount -t 9p -o trans=virtio,version=9p2000.L{ro_opt} {tag} {guest_path}",
            timeout=Timeouts.MOUNT_OPERATION,
        )
        if result.exit_code != 0:
            raise RuntimeError(
                f"Failed to mount 9p share {tag} at {guest_path}: {result.stderr or result.stdout}"
            )

    async def _mount_cifs_share(self, share_name: str, guest_path: str, readonly: bool) -> None:
        """Mount a single CIFS share inside the guest."""
        assert self._smb_server is not None

        await self.execute(
            f"sudo mkdir -p {guest_path}",
            timeout=Timeouts.MOUNT_OPERATION,
        )

        ro_opt = ",ro" if readonly else ""

        # QuicksandSMBServer uses guestfwd in ALL modes (no TCP port).
        # SambaSMBServer uses guestfwd in MOUNTS_ONLY, direct TCP in FULL.
        uses_guestfwd = self._smb_server.get_guestfwd_cmd() is not None

        if uses_guestfwd or self.config.network_mode is NetworkMode.MOUNTS_ONLY:
            gateway = NetworkConstants.GUESTFWD_SMB_IP
            port = NetworkConstants.GUESTFWD_SMB_PORT
        else:
            gateway = NetworkConstants.QEMU_SLIRP_GATEWAY
            port = self._smb_server.port

        username, password = self._smb_server.credentials
        cifs_opts = MountOptions.cifs_opts(username, password)
        mount_cmd = (
            f"sudo mount -t cifs //{gateway}/{share_name} {guest_path} "
            f"-o {cifs_opts},port={port}{ro_opt}"
        )

        last_error = ""
        for attempt in range(MountOptions.MAX_RETRIES):
            result = await self.execute(mount_cmd, timeout=Timeouts.MOUNT_OPERATION)
            if result.exit_code == 0:
                return
            last_error = result.stderr or result.stdout
            if attempt < MountOptions.MAX_RETRIES - 1:
                logger.debug(
                    "Mount attempt %d/%d failed for %s, retrying in %ss: %s",
                    attempt + 1,
                    MountOptions.MAX_RETRIES,
                    share_name,
                    MountOptions.RETRY_DELAY,
                    last_error,
                )
                await asyncio.sleep(MountOptions.RETRY_DELAY)

        raise RuntimeError(f"Failed to mount CIFS share {share_name}: {last_error}")

    async def _mount_shares(self) -> None:
        """Mount boot-time shares (from config.mounts) using CIFS or 9p."""
        if not self.config.mounts:
            return

        cifs_mounts = [m for m in self.config.mounts if m.type == "cifs"]

        if cifs_mounts and self.config.network_mode not in (
            NetworkMode.FULL,
            NetworkMode.MOUNTS_ONLY,
        ):
            raise ValueError(
                "CIFS mounts require network_mode=FULL or MOUNTS_ONLY. "
                "Use type='9p' for mounts with network_mode=NONE."
            )

        await asyncio.sleep(MountOptions.STABILIZATION_DELAY)

        server = self._ensure_smb_server() if cifs_mounts else None
        mounted_cifs: list[tuple[str, str]] = []  # (share_name, guest_path)
        mounted_9p: list[str] = []  # guest_paths (for rollback umount only)

        p9_idx = 0

        try:
            for mount in self.config.mounts:
                if mount.type == "cifs":
                    assert server is not None
                    share_name = server.add_share(mount.host, mount.readonly)
                    await self._mount_cifs_share(share_name, mount.guest, mount.readonly)
                    mounted_cifs.append((share_name, mount.guest))
                elif mount.type == "9p":
                    tag = f"pb9p{p9_idx}"
                    await self._mount_9p_share(tag, mount.guest, mount.readonly)
                    mounted_9p.append(mount.guest)
                    p9_idx += 1
        except Exception:
            # Rollback: unmount in reverse order
            for guest_path in reversed(mounted_9p):
                with contextlib.suppress(Exception):
                    await self.execute(
                        f"sudo umount {guest_path}",
                        timeout=Timeouts.MOUNT_OPERATION,
                    )
            for share_name, guest_path in reversed(mounted_cifs):
                with contextlib.suppress(Exception):
                    await self.execute(
                        f"sudo umount {guest_path}",
                        timeout=Timeouts.MOUNT_OPERATION,
                    )
                with contextlib.suppress(Exception):
                    if server is not None:
                        server.remove_share(share_name)
            raise

    # ------------------------------------------------------------------
    # Dynamic mount API
    # ------------------------------------------------------------------

    async def mount(self, host: str, guest: str, readonly: bool = False) -> MountHandle:
        """Mount a host directory into the running sandbox.

        Can be called any time after start(). Uses CIFS over QEMU slirp networking.

        Args:
            host: Absolute path to the host directory.
            guest: Path inside the guest where the directory will be mounted.
            readonly: Whether the mount should be read-only.

        Returns:
            A MountHandle that can be passed to unmount().

        Raises:
            RuntimeError: If the sandbox is not running, network is disabled,
                or the mount fails.
        """
        if not self.is_running:
            raise RuntimeError("Sandbox is not running")

        if self.config.network_mode not in (NetworkMode.FULL, NetworkMode.MOUNTS_ONLY):
            raise RuntimeError(
                "Dynamic mounts require network_mode=FULL or MOUNTS_ONLY. "
                "NONE disables networking entirely."
            )

        server = self._ensure_smb_server()
        share_name = server.add_share(host, readonly)

        try:
            await self._mount_cifs_share(share_name, guest, readonly)
        except Exception:
            server.remove_share(share_name)
            raise

        handle = MountHandle(
            host=host,
            guest=guest,
            readonly=readonly,
            _share_name=share_name,
        )
        self._dynamic_mounts.append(handle)
        return handle

    async def unmount(self, handle: MountHandle) -> None:
        """Unmount a previously mounted directory.

        Args:
            handle: The MountHandle returned by mount().
        """
        result = await self.execute(f"sudo umount {handle.guest}", timeout=Timeouts.MOUNT_OPERATION)
        if result.exit_code != 0:
            logger.warning(
                "umount %s failed: %s",
                handle.guest,
                result.stderr or result.stdout,
            )

        if self._smb_server is not None:
            self._smb_server.remove_share(handle._share_name)

        if handle in self._dynamic_mounts:
            self._dynamic_mounts.remove(handle)

    @property
    def active_mounts(self) -> list[MountHandle]:
        """List currently active dynamic mounts."""
        return list(self._dynamic_mounts)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def _cleanup_mounts(self) -> list[tuple[str, Exception]]:
        """Stop SMB server if running. Returns list of (component, error) pairs."""
        errors: list[tuple[str, Exception]] = []

        # Unmount dynamic mounts in guest
        for handle in reversed(self._dynamic_mounts):
            with contextlib.suppress(Exception):
                await self.execute(
                    f"sudo umount {handle.guest}",
                    timeout=Timeouts.MOUNT_OPERATION,
                )
        self._dynamic_mounts.clear()

        # Stop SMB server
        if self._smb_server is not None:
            try:
                self._smb_server.stop()
            except Exception as e:
                errors.append(("SMB server", e))
                logger.warning("Failed to stop SMB server: %s", e)
            self._smb_server = None

        return errors
