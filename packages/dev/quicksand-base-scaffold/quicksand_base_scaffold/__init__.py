"""QuicksandBaseScaffold: template base image package for quicksand."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Unpack

from quicksand_core import ResolvedImage, Sandbox
from quicksand_core._types import SandboxConfigParams
from quicksand_core.host import Architecture
from quicksand_core.qemu.platform import get_platform_config

__version__ = "0.1.0"

DISTRO_VERSION = "VERSION"

_PACKAGE_DIR = Path(__file__).parent
_IMAGES_DIR = _PACKAGE_DIR / "images"
_DOCKER_DIR = _PACKAGE_DIR / "docker"


class _QuicksandBaseScaffoldImageProvider:
    """ImageProvider for the bundled base image."""

    name = "quicksand-base-scaffold"
    type = "base"
    images_dir = _IMAGES_DIR

    def resolve(self, arch: str | None = None) -> ResolvedImage:
        return _get_image_artifacts(arch)


# Module-level instance — registered as quicksand.images entry point
image = _QuicksandBaseScaffoldImageProvider()


def _get_image_artifacts(arch: str | None = None) -> ResolvedImage:
    if arch is None:
        config = get_platform_config()
        arch = "arm64" if config.arch.arch_type == Architecture.ARM64 else "amd64"

    image_path = _IMAGES_DIR / f"quicksand-base-scaffold-{DISTRO_VERSION}-{arch}.qcow2"

    if not image_path.exists():
        image_path = _IMAGES_DIR / f"quicksand-base-scaffold-{DISTRO_VERSION}.qcow2"

    if not image_path.exists():
        available = list(_IMAGES_DIR.glob("*.qcow2")) if _IMAGES_DIR.exists() else []
        if available:
            available_str = ", ".join(p.name for p in available)
            raise FileNotFoundError(f"Image for {arch} not found. Available: {available_str}")
        raise FileNotFoundError(
            f"No images found in {_IMAGES_DIR}. The package may not have been built correctly."
        )

    return ResolvedImage(
        name="quicksand-base-scaffold",
        chain=[image_path],
        kernel=image_path.with_suffix(".kernel"),
        initrd=image_path.with_suffix(".initrd"),
    )


class QuicksandBaseScaffoldSandbox(Sandbox):
    """Pre-configured Sandbox using the bundled image.

    Usage::

        async with QuicksandBaseScaffoldSandbox() as sb:
            result = await sb.execute("cat /etc/os-release")
    """

    def __init__(
        self,
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
        save: str | None = None,
        workspace: str | Path | None = None,
        **kwargs: Unpack[SandboxConfigParams],
    ) -> None:
        kwargs.setdefault("image", "quicksand-base-scaffold")
        super().__init__(
            progress_callback=progress_callback,
            save=save,
            workspace=workspace,
            **kwargs,
        )


__all__ = [
    "DISTRO_VERSION",
    "QuicksandBaseScaffoldSandbox",
    "__version__",
    "image",
]
