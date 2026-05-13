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

    async def _pivot_overlay(self, *, delete_checkpoints: bool) -> Path:
        """Atomically pivot the VM onto a fresh active overlay.

        Returns the path of the now-frozen previous top overlay. The new
        active overlay path is written into ``self._overlay_path`` and the
        on-disk state file is refreshed so a crash after this point still
        sees both overlays via the reaper / startup GC.

        Used by both ``_save`` and ``_ForkMixin.fork``.
        """
        import os as _os

        from .._overlay_cache import allocate_overlay_path, write_session_state

        assert self._overlay_path is not None
        assert self._qmp_client is not None

        # 1. Sync guest filesystem buffers.
        await self.execute(GuestCommands.SYNC, timeout=30.0, exclusive=True)
        # 1b. TRIM freed blocks so QEMU can deallocate qcow2 clusters.
        await self.execute(GuestCommands.FSTRIM, timeout=60.0, exclusive=True)

        # 2. Delete checkpoint snapshots if requested.
        if delete_checkpoints:
            for tag in self._qmp_checkpoints:
                logger.debug("Deleting checkpoint snapshot '%s' before pivot", tag)
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

        # 4. QMP atomic pivot. Claim the new path FIRST so a concurrent
        #    orphan sweep can't reap the freshly-created qcow2 in the
        #    window between QMP creating it and us recording the claim.
        frozen = self._overlay_path
        new_overlay = allocate_overlay_path()
        self._session_overlays.append(new_overlay)
        write_session_state(self._sandbox_id, _os.getpid(), self._session_overlays)
        try:
            await self._qmp_client.execute(
                "blockdev-snapshot-sync",
                device="drive0",
                **{"snapshot-file": str(new_overlay)},
                format="qcow2",
                mode="absolute-paths",
            )
        except Exception:
            # Pivot failed — undo the speculative claim so the state file
            # doesn't dangle a never-created path.
            self._session_overlays.remove(new_overlay)
            write_session_state(self._sandbox_id, _os.getpid(), self._session_overlays)
            raise
        self._overlay_path = new_overlay

        return frozen

    async def _save(
        self,
        name: str,
        workspace: Path | None,
        compress: bool,
        delete_checkpoints: bool,
    ) -> SaveManifest:
        """Save using QMP blockdev-snapshot-sync — VM keeps running.

        Picks the on-disk layout (cache-referenced vs self-contained) based
        on the chain. The manifest is the same shape either way; only the
        physical disposition of the overlay files differs.
        """
        from .._overlay_cache import get_overlays_dir
        from ..host.arch import _detect_architecture
        from ..qemu.save import SaveWriter

        assert self._overlay_path is not None
        assert self._temp_dir is not None
        assert self._image is not None
        assert self._qmp_client is not None
        assert self._overlay_manager is not None

        snapshot_overlay = await self._pivot_overlay(delete_checkpoints=delete_checkpoints)

        base = self._image.chain[0]
        full_chain = self._overlay_manager.get_overlay_chain(snapshot_overlay, base)
        pool_root = get_overlays_dir().resolve()

        # When every overlay already lives in the overlay cache we can write the
        # save as a manifest-only reference (no file copies). When the
        # chain has package-shipped or save-dir overlays in it — or the
        # caller asked for compression — we fall back to copying the
        # session overlays into <save>/overlays/.
        all_in_pool = all(_safely_under_pool(p, pool_root) for p in full_chain)
        bundle = compress or not all_in_pool

        save_config = self.config.model_copy(update={"image": self._image.name, "mounts": []})
        manifest = SaveManifest(
            config=save_config,
            arch=str(_detect_architecture()),
            chain=[],  # SaveWriter fills this in based on layout
        )

        writer = SaveWriter(name, workspace=workspace)

        if not bundle:
            # Cache layout — pass the full chain so all backing references
            # are recorded.
            return writer.write(overlay_chain=list(full_chain), manifest=manifest)

        # Bundled layout — only copy session overlays into <save>/overlays/.
        # Non-session entries (package / prior-save overlays) are referenced
        # by name through the parent image at load time. This matches the
        # pre-cache behaviour and keeps the wire format portable.
        session_set = {p.resolve() for p in self._session_overlays}
        bundle_chain: list[Path] = []
        bundle_backing: list[Path] = []
        prev: Path = base
        for p in full_chain:
            if p.resolve() in session_set:
                bundle_chain.append(p)
                bundle_backing.append(prev)
            else:
                _verify_overlay_from_package(p)
            prev = p

        qemu_img = self._overlay_manager._qemu_img if compress else None
        return writer.write(
            overlay_chain=bundle_chain,
            manifest=manifest,
            bundle=True,
            compress=compress,
            qemu_img=qemu_img,
            backing_files=bundle_backing if compress else None,
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


def _safely_under_pool(path: Path, pool_root: Path) -> bool:
    """``True`` if ``path`` resolves to something inside ``pool_root``."""
    try:
        return path.resolve().is_relative_to(pool_root)
    except OSError:
        return False


def _verify_overlay_from_package(overlay_path: Path) -> None:
    """Verify that an overlay outside the temp dir belongs to an installed package.

    Each ``quicksand.images`` entry point provider exposes an ``images_dir``
    attribute pointing at its venv location. Image artifacts may also live in
    the per-user cache (``<cache_dir>/images/<pkg>/``). We accept either.

    Logs a warning if the overlay can't be attributed to any package.
    """
    from importlib.metadata import entry_points

    from .._image_cache import get_cache_dir

    resolved = overlay_path.resolve()
    for ep in entry_points(group="quicksand.images"):
        try:
            provider = ep.load()
        except Exception:
            continue
        try:
            if resolved.is_relative_to(provider.images_dir.resolve()):
                return
        except Exception:
            pass
        pkg_name = getattr(ep, "dist", None)
        pkg_name = pkg_name.name if pkg_name is not None else None
        if pkg_name:
            try:
                cache = get_cache_dir(pkg_name)
                if cache.exists() and resolved.is_relative_to(cache.resolve()):
                    return
            except Exception:
                continue

    logger.warning(
        "Overlay %s is outside temp dir but not found in any installed package. "
        "It will be excluded from the save.",
        overlay_path,
    )
