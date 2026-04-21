"""aif-cua-agent-sandbox: overlay image package for quicksand."""

from __future__ import annotations

from pathlib import Path

from .sandbox import AifCuaAgentSandbox

__version__ = "0.1.7"

PACKAGE_DIR = Path(__file__).parent
IMAGES_DIR = PACKAGE_DIR / "images"


class _AifCuaAgentImageProvider:
    """ImageProvider for the bundled aif-cua-agent-sandbox overlay."""

    name = "aif-cua-agent-sandbox"
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
image = _AifCuaAgentImageProvider()


__all__ = [
    "IMAGES_DIR",
    "PACKAGE_DIR",
    "AifCuaAgentSandbox",
    "__version__",
    "image",
]
