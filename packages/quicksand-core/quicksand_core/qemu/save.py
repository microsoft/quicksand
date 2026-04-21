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

SAVE_VERSION = 6


class SaveWriter:
    """Writes sandbox saves to disk.

    Centralizes path construction, atomic writes, and overwrite semantics.
    Saves are always written as directories under ``<workspace>/<name>/``.

    Args:
        name: Save name (e.g. "my-env").
        workspace: Parent directory for saves. Defaults to ``$CWD/.quicksand/sandboxes/``.
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
        compress: bool = False,
        qemu_img: Path | None = None,
        backing_files: list[Path] | None = None,
    ) -> SaveManifest:
        """Write a save directory.

        If a save already exists at this path, it is replaced atomically.
        The manifest is pre-built by the caller; SaveWriter copies overlay
        files and serializes the manifest to disk.

        Args:
            overlay_chain: Overlay files in bottom-to-top order.
            manifest: Pre-built SaveManifest to serialize.
            compress: Compress overlay qcow2 files to reduce save size.
                Requires qemu_img. Slower but produces smaller saves.
            qemu_img: Path to qemu-img binary (required if compress=True).
            backing_files: Backing file for each overlay (parallel to
                overlay_chain). Required when compress=True so that
                ``qemu-img convert`` preserves the delta rather than
                flattening the entire backing chain.

        Returns:
            The SaveManifest (same object passed in).
        """
        if compress and qemu_img is None:
            raise ValueError("qemu_img path required when compress=True")
        if compress and backing_files is None:
            raise ValueError("backing_files required when compress=True")

        self.workspace.mkdir(parents=True, exist_ok=True)

        tmp_dir = self.path.parent / f"{self.name}.tmp"
        try:
            # Write to tmp directory first for crash safety
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

            # Write manifest
            (tmp_dir / FilePatterns.MANIFEST).write_text(manifest.model_dump_json(indent=2))

            # Atomic swap: remove old, rename tmp -> final
            if self.path.exists():
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


def _get_version() -> str:
    """Get the quicksand-core version without circular imports."""
    try:
        from importlib.metadata import version

        return version("quicksand-core")
    except Exception:
        return "unknown"
