"""quicksand-agent: pre-built overlay on quicksand-ubuntu."""

from __future__ import annotations

from pathlib import Path

from .sandbox import AgentSandbox

try:
    from importlib.metadata import version as _get_version

    __version__ = _get_version("quicksand-agent")
except Exception:
    __version__ = "0.0.0"

PACKAGE_DIR = Path(__file__).parent
IMAGES_DIR = PACKAGE_DIR / "images"


class _AgentSandboxImageProvider:
    """ImageProvider for the bundled agent sandbox overlay."""

    name = "quicksand-agent"
    type = "overlay"
    images_dir = IMAGES_DIR

    def resolve(self, arch: str | None = None):
        from quicksand_core._image_cache import resolve_dir
        from quicksand_core.qemu.image_resolver import ImageResolver

        save_dir = resolve_dir("quicksand-agent", IMAGES_DIR)
        if save_dir is None:
            from quicksand_core._auto_install import auto_install_images

            if not auto_install_images("quicksand-agent", IMAGES_DIR):
                raise FileNotFoundError(
                    "No bundled save found. If you installed from PyPI, download images with:\n"
                    "  quicksand install quicksand-agent"
                )
            save_dir = resolve_dir("quicksand-agent", IMAGES_DIR)
            if save_dir is None:
                raise FileNotFoundError("Auto-install did not produce a usable save")

        return ImageResolver()._resolve_save(save_dir)


# Module-level instance — registered as quicksand.images entry point
image = _AgentSandboxImageProvider()


__all__ = [
    "IMAGES_DIR",
    "PACKAGE_DIR",
    "AgentSandbox",
    "__version__",
    "image",
]
