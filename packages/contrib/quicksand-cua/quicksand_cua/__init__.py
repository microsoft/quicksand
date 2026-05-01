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
            from quicksand_core._auto_install import auto_install_images

            if not auto_install_images("quicksand-cua", IMAGES_DIR):
                raise FileNotFoundError(
                    "No bundled save found. If you installed from PyPI, download images with:\n"
                    "  quicksand install quicksand-cua"
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
