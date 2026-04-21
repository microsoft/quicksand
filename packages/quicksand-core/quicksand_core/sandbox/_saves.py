"""Persisted save/load operations for the Sandbox class.

Saving uses QMP blockdev-snapshot-sync:
  1. Guest sync via guest agent — flush filesystem buffers.
  2. Flush QEMU block layer — ensure host qcow2 is fully up-to-date.
  3. QMP blockdev-snapshot-sync — QEMU atomically pivots writes to a new
     overlay; the old overlay is left frozen and consistent.
  4. Write the frozen overlay chain to a save directory.

QMP is always started alongside QEMU, so save() is always non-destructive.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .._types import GuestCommands, SaveManifest
from ._protocol import _SandboxProtocol

logger = logging.getLogger("quicksand.sandbox")


class _SaveMixin(_SandboxProtocol):
    """Mixin providing persisted save/load operations."""

    async def save(
        self,
        name: str,
        *,
        workspace: str | Path | None = None,
        compress: bool = False,
        delete_checkpoints: bool = False,
    ) -> SaveManifest:
        """Save the sandbox disk state to a directory.

        The sandbox keeps running after the save (hot save via QMP). Only disk
        state is saved — no RAM state is included. The saved directory can be
        shared and loaded on any machine via ``SandboxConfig(image="save-name")``.

        Args:
            name: Save name (e.g. "my-env"). Written to ``<workspace>/<name>/``.
            workspace: Parent directory for saves. Defaults to ``$CWD/.quicksand/``.
            compress: Compress overlay qcow2 files to reduce save size.
                Slower but produces smaller saves for distribution.
            delete_checkpoints: If True, delete all active in-session checkpoint
                snapshots before saving. If False (default) and snapshots exist,
                raises RuntimeError.

        Returns:
            SaveManifest with details about the saved state.

        Raises:
            RuntimeError: If sandbox is not running, has active checkpoint snapshots
                (when delete_checkpoints=False), or save fails.
        """
        if not self.is_running:
            raise RuntimeError("Cannot save a non-running sandbox")

        if self._qmp_client is None:
            raise RuntimeError(
                "QMP is not connected — cannot save a running sandbox. "
                "This should not happen; QMP is started automatically with the VM."
            )

        if self._qmp_checkpoints and not delete_checkpoints:
            tags = self._qmp_checkpoints
            raise RuntimeError(
                f"save() called with active checkpoint snapshots: {tags}. "
                "These snapshots will not be accessible after the save pivot. "
                "Pass delete_checkpoints=True to delete them before saving."
            )

        ws = Path(workspace) if workspace else None
        return await self._save(name, ws, compress, delete_checkpoints)

    async def _save(
        self,
        name: str,
        workspace: Path | None,
        compress: bool,
        delete_checkpoints: bool,
    ) -> SaveManifest:
        """Save using QMP blockdev-snapshot-sync — VM keeps running."""
        from ..host.arch import _detect_architecture
        from ..qemu.image_resolver import SAVE_VERSION
        from ..qemu.save import SaveWriter

        assert self._overlay_path is not None
        assert self._temp_dir is not None
        assert self._image is not None

        # 1. Sync guest filesystem buffers.
        await self.execute(GuestCommands.SYNC, timeout=30.0, exclusive=True)

        # 1b. TRIM freed blocks so QEMU can deallocate qcow2 clusters.
        await self.execute(GuestCommands.FSTRIM, timeout=60.0, exclusive=True)

        # 2. Delete checkpoint snapshots if requested.
        assert self._qmp_client is not None
        if delete_checkpoints:
            for tag in self._qmp_checkpoints:
                logger.debug("Deleting checkpoint snapshot '%s' before save", tag)
                await self._qmp_client.execute(
                    "human-monitor-command",
                    **{"command-line": f"delvm {tag}"},
                )
            self._qmp_checkpoints.clear()

        # 3. Flush QEMU block layer.
        logger.debug("Flushing QEMU block layer for drive0 before snapshot pivot")
        await self._qmp_client.execute(
            "human-monitor-command",
            **{"command-line": 'qemu-io drive0 "flush"'},
        )

        # 4. QMP atomic pivot — VM continues on a new overlay.
        snapshot_overlay = self._overlay_path
        new_overlay = self._temp_dir / f"overlay-{time.time_ns()}.qcow2"

        await self._qmp_client.execute(
            "blockdev-snapshot-sync",
            device="drive0",
            **{"snapshot-file": str(new_overlay)},
            format="qcow2",
            mode="absolute-paths",
        )
        self._overlay_path = new_overlay

        # 5. Collect the overlay chain, keeping only this session's overlays.
        #    Overlays from installed packages (outside our temp dir) are
        #    excluded — they'll be resolved by name at load time.
        #    We also track each overlay's backing file so that compress can
        #    pass -B to qemu-img convert (avoids flattening the chain).
        assert self._overlay_manager is not None
        base = self._image.chain[0]
        full_chain = self._overlay_manager.get_overlay_chain(snapshot_overlay, base)
        chain: list[Path] = []
        backing_files: list[Path] = []
        prev: Path = base
        for p in full_chain:
            if p.resolve().is_relative_to(self._temp_dir.resolve()):
                chain.append(p)
                backing_files.append(prev)
            else:
                _verify_overlay_from_package(p)
            prev = p

        save_config = self.config.model_copy(update={"image": self._image.name, "mounts": []})
        manifest = SaveManifest(
            version=SAVE_VERSION,
            config=save_config,
            arch=str(_detect_architecture()),
        )

        writer = SaveWriter(name, workspace=workspace)
        qemu_img = self._overlay_manager._qemu_img if compress else None
        return writer.write(
            overlay_chain=chain,
            manifest=manifest,
            compress=compress,
            qemu_img=qemu_img,
            backing_files=backing_files if compress else None,
        )

    @staticmethod
    def validate_save(path: str | Path) -> SaveManifest:
        """Validate a save without loading it.

        Args:
            path: Path to the save directory.

        Returns:
            SaveManifest if valid.

        Raises:
            FileNotFoundError: If save path doesn't exist.
            ValueError: If save is invalid.
        """
        from ..qemu.image_resolver import ImageResolver

        save_path = Path(path)
        if not save_path.exists():
            raise FileNotFoundError(f"Save not found: {save_path}")

        return ImageResolver().validate_save(save_path)


def _verify_overlay_from_package(overlay_path: Path) -> None:
    """Verify that an overlay outside the temp dir belongs to an installed package.

    Each ``quicksand.images`` entry point provider exposes an ``images_dir``
    attribute. We check whether the overlay lives under one of those dirs.

    Logs a warning if the overlay can't be attributed to any package.
    """
    from importlib.metadata import entry_points

    resolved = overlay_path.resolve()
    for ep in entry_points(group="quicksand.images"):
        try:
            provider = ep.load()
            if resolved.is_relative_to(provider.images_dir.resolve()):
                return
        except Exception:
            continue

    logger.warning(
        "Overlay %s is outside temp dir but not found in any installed package. "
        "It will be excluded from the save.",
        overlay_path,
    )
