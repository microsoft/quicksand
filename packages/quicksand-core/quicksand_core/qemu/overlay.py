"""QEMU overlay (qcow2) image management."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("quicksand.overlay")


class OverlayManager:
    """Manages qcow2 overlay creation and resize operations."""

    def __init__(self, qemu_img: Path):
        self._qemu_img = qemu_img

    # ------------------------------------------------------------------
    # Overlay creation
    # ------------------------------------------------------------------

    def create_overlay(
        self,
        image_path: Path,
        overlay_path: Path,
        restore_chain: list[Path] | None = None,
        disk_size: str | None = None,
    ) -> None:
        """Create a qcow2 overlay backed by the base image.

        Args:
            image_path: Path to the base qcow2 image.
            overlay_path: Path to write the overlay file.
            restore_chain: If restoring from a save, the extracted overlay
                chain (bottom-to-top). A fresh overlay is created on top.
            disk_size: Optional size to resize the overlay to (e.g., "2G").
        """
        if restore_chain:
            self._prepare_restored_chain(restore_chain, image_path)
            # Create a fresh overlay backed by the top of the restored chain.
            backing = restore_chain[-1]
            subprocess.run(
                [
                    str(self._qemu_img),
                    "create",
                    "-f",
                    "qcow2",
                    "-b",
                    str(backing.absolute()),
                    "-F",
                    "qcow2",
                    str(overlay_path),
                ],
                check=True,
                capture_output=True,
            )
            if disk_size:
                self.resize_overlay(overlay_path, disk_size)
            return

        subprocess.run(
            [
                str(self._qemu_img),
                "create",
                "-f",
                "qcow2",
                "-b",
                str(image_path.absolute()),
                "-F",
                "qcow2",
                str(overlay_path),
            ],
            check=True,
            capture_output=True,
        )

        if disk_size:
            self.resize_overlay(overlay_path, disk_size)

    def resize_overlay(self, overlay_path: Path, disk_size: str) -> None:
        """Resize an overlay disk image."""
        logger.debug(f"Resizing overlay to {disk_size}")
        subprocess.run(
            [
                str(self._qemu_img),
                "resize",
                str(overlay_path),
                disk_size,
            ],
            check=True,
            capture_output=True,
        )

    # ------------------------------------------------------------------
    # Overlay chain introspection
    # ------------------------------------------------------------------

    def get_backing_file(self, overlay_path: Path) -> str | None:
        """Read the backing file path from a qcow2 overlay.

        Returns None if the overlay has no backing file.
        """
        result = subprocess.run(
            [str(self._qemu_img), "info", "--output=json", str(overlay_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        info = json.loads(result.stdout)
        return info.get("backing-filename")

    def get_overlay_chain(self, top_overlay: Path, base_image: Path) -> list[Path]:
        """Walk the backing chain from top overlay down to the base image.

        Returns a list in bottom-to-top order (base-adjacent overlay first).
        The base image itself is NOT included.
        """
        chain: list[Path] = []
        current = top_overlay
        while True:
            chain.append(current)
            backing = self.get_backing_file(current)
            if backing is None:
                break
            backing_path = Path(backing)
            if backing_path.resolve() == base_image.resolve():
                break
            current = backing_path
        chain.reverse()
        return chain

    # ------------------------------------------------------------------
    # Chain preparation for restore
    # ------------------------------------------------------------------

    def _prepare_restored_chain(self, chain: list[Path], base_image: Path) -> None:
        """Fix backing file references in a restored overlay chain.

        After extraction from a tar, the overlays' internal backing paths
        point to the original temp directory (now deleted). This method
        updates each overlay to reference the correct file using
        ``qemu-img rebase -u`` (metadata-only, instant).

        Args:
            chain: Overlay paths in bottom-to-top order.
            base_image: Current path to the base qcow2 image.
        """
        for i, overlay in enumerate(chain):
            if i == 0:
                expected_backing = str(base_image.absolute())
            else:
                expected_backing = str(chain[i - 1].absolute())

            current_backing = self.get_backing_file(overlay)
            if current_backing is not None:
                try:
                    if Path(current_backing).resolve() == Path(expected_backing).resolve():
                        continue
                except (OSError, ValueError):
                    pass  # stale path that can't be resolved — needs rebase

            logger.debug(
                "Rebasing overlay %s: %s -> %s",
                overlay.name,
                current_backing,
                expected_backing,
            )
            subprocess.run(
                [
                    str(self._qemu_img),
                    "rebase",
                    "-u",
                    "-b",
                    expected_backing,
                    "-F",
                    "qcow2",
                    str(overlay),
                ],
                check=True,
                capture_output=True,
            )
