"""Save lifecycle operations for sandbox VMs."""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
from pathlib import Path

from .._types import FilePatterns, SaveManifest

logger = logging.getLogger("quicksand.save")

SAVES_DIR = ".quicksand/sandboxes"
"""Default directory for saves, relative to cwd."""


class SaveWriter:
    """Writes sandbox saves to disk.

    Two on-disk formats:

    * **v7 (default)** — manifest only. The manifest's ``chain`` field
      lists absolute paths of cached overlays this save depends on. A
      ``save``-kind state file under ``<cache_dir>/state/`` refcounts
      those cache entries so GC keeps them alive until ``save delete``.
      No data is copied; the save is created with one rename + one JSON
      write + one state file.
    * **v6 (fallback)** — overlays are copied into ``<save>/overlays/``.
      Used when the caller requests ``compress=True`` (v7 stores raw cached
      overlays, no compression hook) or when a chain element lives
      outside the overlay cache (e.g., a Sandbox loaded from a v6 save). Loading
      still works for any existing v6 save on disk.
    """

    def __init__(self, name: str, workspace: Path | None = None):
        self.name = name
        self.workspace = workspace or (Path.cwd() / SAVES_DIR)
        self.path = self.workspace / name

    def write(
        self,
        overlay_chain: list[Path],
        manifest: SaveManifest,
        *,
        bundle: bool = False,
        compress: bool = False,
        qemu_img: Path | None = None,
        backing_files: list[Path] | None = None,
    ) -> SaveManifest:
        """Write a save directory.

        If a save already exists at this path, it is replaced atomically.
        The manifest is pre-built by the caller; SaveWriter writes either
        a v7 manifest-only save (when possible) or a v6 copy-based save.

        Args:
            overlay_chain: Overlay files in bottom-to-top order.
            manifest: Pre-built SaveManifest to serialize.
            compress: Force a v6 copy-based save with compressed overlays.
                Requires qemu_img. Slower but produces smaller saves.
            qemu_img: Path to qemu-img binary (required if compress=True).
            backing_files: Backing file for each overlay (parallel to
                overlay_chain). Required when compress=True so that
                ``qemu-img convert`` preserves the delta rather than
                flattening the entire backing chain.

        Returns:
            The SaveManifest (same object passed in), with ``chain`` and
            ``version`` reflecting the format actually written.
        """
        if compress and qemu_img is None:
            raise ValueError("qemu_img path required when compress=True")
        if compress and backing_files is None:
            raise ValueError("backing_files required when compress=True")

        # On-disk layout is the caller's choice. Cache-mode (the default
        # when everything fits) records cache-resident basenames and writes
        # only the manifest; bundled-mode copies overlays into the save dir.
        if bundle or compress:
            return self._write_bundled(
                overlay_chain,
                manifest,
                compress=compress,
                qemu_img=qemu_img,
                backing_files=backing_files,
            )
        return self._write_pool(overlay_chain, manifest)

    # ----- cache-mode: manifest only, claim cached overlays via state file --------

    def _write_pool(
        self,
        overlay_chain: list[Path],
        manifest: SaveManifest,
    ) -> SaveManifest:
        from .._overlay_cache import clear_save_state, write_save_state

        # Cache-mode save: manifest records overlay basenames, the actual
        # qcow2 files stay in the overlay cache. The loader resolves each basename
        # by searching the save dir then the overlay cache.
        manifest = manifest.model_copy(
            update={
                "chain": [p.name for p in overlay_chain],
            }
        )

        self.workspace.mkdir(parents=True, exist_ok=True)
        tmp_dir = self.path.parent / f"{self.name}.tmp"
        try:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            tmp_dir.mkdir(parents=True)
            (tmp_dir / FilePatterns.MANIFEST).write_text(manifest.model_dump_json(indent=2))

            # If we're replacing an existing save, free its old claim first
            # so the GC can reclaim any overlays that were exclusively its.
            if self.path.exists():
                with contextlib.suppress(Exception):
                    clear_save_state(self.path)
                shutil.rmtree(self.path)
            tmp_dir.rename(self.path)

            # Now register the claim against the canonical save dir path.
            write_save_state(self.path, list(overlay_chain))
            return manifest
        except Exception:
            with contextlib.suppress(OSError):
                shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    # ----- bundled: copy chain into save dir (self-contained) -------------

    def _write_bundled(
        self,
        overlay_chain: list[Path],
        manifest: SaveManifest,
        *,
        compress: bool,
        qemu_img: Path | None,
        backing_files: list[Path] | None,
    ) -> SaveManifest:
        # Bundled save: copy overlays into <save>/overlays/<i>.qcow2 and
        # record their basenames in the manifest. The loader finds them
        # via its "save dir first" search.
        manifest = manifest.model_copy(
            update={
                "chain": [f"{i}.qcow2" for i in range(len(overlay_chain))],
            }
        )

        self.workspace.mkdir(parents=True, exist_ok=True)
        tmp_dir = self.path.parent / f"{self.name}.tmp"
        try:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            tmp_dir.mkdir(parents=True)
            overlays_dir = tmp_dir / FilePatterns.OVERLAYS_DIR
            overlays_dir.mkdir()

            for i, overlay_path in enumerate(overlay_chain):
                dest = overlays_dir / f"{i}.qcow2"
                if compress:
                    assert qemu_img is not None and backing_files is not None
                    self._compress_overlay(qemu_img, overlay_path, dest, backing=backing_files[i])
                else:
                    shutil.copy2(overlay_path, dest)

            (tmp_dir / FilePatterns.MANIFEST).write_text(manifest.model_dump_json(indent=2))

            if self.path.exists():
                # If the previous save was v7, free its cache claim.
                from .._overlay_cache import clear_save_state

                with contextlib.suppress(Exception):
                    clear_save_state(self.path)
                shutil.rmtree(self.path)
            tmp_dir.rename(self.path)
            return manifest

        except Exception:
            with contextlib.suppress(OSError):
                shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    @staticmethod
    def _compress_overlay(qemu_img: Path, src: Path, dest: Path, *, backing: Path) -> None:
        """Compress a qcow2 overlay using qemu-img convert -c.

        ``-B`` preserves the overlay's backing file reference so that only
        the delta clusters are written (and compressed).  Without it,
        ``qemu-img convert`` flattens the entire backing chain into the
        destination, producing a much larger standalone image.
        """
        result = subprocess.run(
            [
                str(qemu_img),
                "convert",
                "-f",
                "qcow2",
                "-O",
                "qcow2",
                "-c",
                "-B",
                str(backing),
                "-F",
                "qcow2",
                str(src),
                str(dest),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to compress overlay: {result.stderr}")
