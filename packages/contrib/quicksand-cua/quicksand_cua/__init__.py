"""quicksand-cua: overlay image package for quicksand."""

from __future__ import annotations

from pathlib import Path

from .sandbox import CuaSandbox

__version__ = "0.1.7"

PACKAGE_DIR = Path(__file__).parent
IMAGES_DIR = PACKAGE_DIR / "images"


class _CuaSandboxImageProvider:
    """ImageProvider for the bundled quicksand-cua overlay."""

    name = "quicksand-cua"
    type = "overlay"
    images_dir = IMAGES_DIR

    def resolve(self, arch: str | None = None):
        from quicksand_core.qemu.image_resolver import ImageResolver

        if not (IMAGES_DIR / "manifest.json").exists():
            raise FileNotFoundError(
                f"No bundled save found in {IMAGES_DIR}. "
                "The package may not have been built correctly."
            )
        return ImageResolver()._resolve_save(IMAGES_DIR)


# Module-level instance — registered as quicksand.images entry point
image = _CuaSandboxImageProvider()


__all__ = [
    "IMAGES_DIR",
    "PACKAGE_DIR",
    "CuaSandbox",
    "__version__",
    "image",
]
