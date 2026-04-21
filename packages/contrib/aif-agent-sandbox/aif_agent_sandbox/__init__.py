"""AIFAgentSandbox: pre-built overlay on quicksand-ubuntu."""

from __future__ import annotations

from pathlib import Path

from .sandbox import AIFAgentSandbox

__version__ = "0.2.5"

PACKAGE_DIR = Path(__file__).parent
IMAGES_DIR = PACKAGE_DIR / "images"


class _AIFAgentImageProvider:
    """ImageProvider for the bundled AIF agent overlay."""

    name = "aif-agent-sandbox"
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
image = _AIFAgentImageProvider()


__all__ = [
    "IMAGES_DIR",
    "PACKAGE_DIR",
    "AIFAgentSandbox",
    "__version__",
    "image",
]
