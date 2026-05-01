"""
Quicksand Ubuntu bundles a pre-built Ubuntu 24.04 VM image for the quicksand
agent harness. No downloads required after installation.

Ubuntu is ideal for AI agents that need a full Linux environment with the apt
package manager and broad software compatibility.

Quick start:
    import asyncio
    from quicksand import UbuntuSandbox

    async def main():
        async with UbuntuSandbox() as sb:
            result = await sb.execute("cat /etc/os-release")
            print(result.stdout)

    asyncio.run(main())

Or with custom configuration:
    from quicksand import Sandbox

    async with Sandbox(image="ubuntu", memory="4G", cpus=4) as sb:
        result = await sb.execute("cat /etc/os-release")
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Unpack

from quicksand_core import ResolvedImage, Sandbox
from quicksand_core._types import SandboxConfigParams
from quicksand_core.host import Architecture
from quicksand_core.qemu.platform import get_platform_config

__version__ = "0.1.14"

DISTRO_VERSION = "24.04"

_PACKAGE_DIR = Path(__file__).parent
_IMAGES_DIR = _PACKAGE_DIR / "images"
_DOCKER_DIR = _PACKAGE_DIR / "docker"


class _UbuntuImageProvider:
    """ImageProvider for the bundled Ubuntu base image."""

    name = "ubuntu"
    type = "base"
    images_dir = _IMAGES_DIR

    def resolve(self, arch: str | None = None) -> ResolvedImage:
        return _get_image_artifacts(arch)


# Module-level instance — registered as quicksand.images entry point
image = _UbuntuImageProvider()


def _get_image_artifacts(arch: str | None = None) -> ResolvedImage:
    if arch is None:
        config = get_platform_config()
        arch = "arm64" if config.arch.arch_type == Architecture.ARM64 else "amd64"

    image_path = _IMAGES_DIR / f"ubuntu-{DISTRO_VERSION}-{arch}.qcow2"

    if not image_path.exists():
        image_path = _IMAGES_DIR / f"ubuntu-{DISTRO_VERSION}.qcow2"

    if not image_path.exists():
        from quicksand_core._auto_install import auto_install_images

        if auto_install_images("quicksand-ubuntu", _IMAGES_DIR):
            return _get_image_artifacts(arch)

        available = list(_IMAGES_DIR.glob("*.qcow2")) if _IMAGES_DIR.exists() else []
        if available:
            available_str = ", ".join(p.name for p in available)
            from quicksand_core.host.arch import _is_emulated

            if _is_emulated():
                raise FileNotFoundError(
                    f"Ubuntu image for {arch} not found. Available: {available_str}\n"
                    "Python is running under platform emulation, so pip installed "
                    "the wrong architecture variant.\n"
                    "Reinstall with:  quicksand install quicksand-ubuntu"
                )
            raise FileNotFoundError(
                f"Ubuntu image for {arch} not found. Available: {available_str}\n"
                "Reinstall with:  quicksand install quicksand-ubuntu"
            )
        raise FileNotFoundError(
            "No Ubuntu images found. If you installed from PyPI, download images with:\n"
            "  quicksand install ubuntu"
        )

    return ResolvedImage(
        name="ubuntu",
        chain=[image_path],
        kernel=image_path.with_suffix(".kernel"),
        initrd=image_path.with_suffix(".initrd"),
    )


class UbuntuSandbox(Sandbox):
    """Pre-configured Sandbox using the bundled Ubuntu image.

    Usage::

        async with UbuntuSandbox() as sb:
            result = await sb.execute("cat /etc/os-release")

        async with UbuntuSandbox(save="my-env") as sb:
            await sb.execute("pip install numpy")

        async with UbuntuSandbox(image="my-env") as sb:
            await sb.execute("python3 -c 'import numpy'")
    """

    def __init__(
        self,
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
        save: str | None = None,
        workspace: str | Path | None = None,
        **kwargs: Unpack[SandboxConfigParams],
    ) -> None:
        kwargs.setdefault("image", "ubuntu")
        super().__init__(
            progress_callback=progress_callback,
            save=save,
            workspace=workspace,
            **kwargs,
        )


__all__ = [
    "DISTRO_VERSION",
    "UbuntuSandbox",
    "__version__",
]
