"""quicksand-cua: overlay image package for quicksand."""

from __future__ import annotations

from pathlib import Path

from .sandbox import CuaSandbox

try:
    from importlib.metadata import version as _get_version

    __version__ = _get_version("quicksand-cua")
except Exception:
    __version__ = "0.0.0"

PACKAGE_DIR = Path(__file__).parent
IMAGES_DIR = PACKAGE_DIR / "images"


class _CuaSandboxImageProvider:
    """ImageProvider for the bundled quicksand-cua overlay."""

    name = "quicksand-cua"
    type = "overlay"
    images_dir = IMAGES_DIR

    def resolve(self, arch: str | None = None):
        from quicksand_core._image_cache import resolve_dir
        from quicksand_core.qemu.image_resolver import ImageResolver

        save_dir = resolve_dir("quicksand-cua", IMAGES_DIR)
        if save_dir is None:
            from quicksand_core._auto_install import auto_install_images

            if not auto_install_images("quicksand-cua", IMAGES_DIR):
                raise FileNotFoundError(
                    "No bundled save found. If you installed from PyPI, download images with:\n"
                    "  quicksand install quicksand-cua"
                )
            save_dir = resolve_dir("quicksand-cua", IMAGES_DIR)
            if save_dir is None:
                raise FileNotFoundError("Auto-install did not produce a usable save")

        return ImageResolver()._resolve_save(save_dir)


# Module-level instance — registered as quicksand.images entry point
image = _CuaSandboxImageProvider()


__all__ = [
    "IMAGES_DIR",
    "PACKAGE_DIR",
    "CuaSandbox",
    "__version__",
    "image",
]
