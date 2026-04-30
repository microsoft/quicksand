"""
Quicksand Alpine bundles a pre-built Alpine Linux 3.23 VM image for the quicksand
agent harness. No downloads required after installation.

Alpine is lightweight (~75MB vs ~300MB for Ubuntu) and boots quickly. It uses
the apk package manager instead of apt.

Quick start:
    import asyncio
    from quicksand import AlpineSandbox

    async def main():
        async with AlpineSandbox() as sb:
            result = await sb.execute("cat /etc/os-release")
            print(result.stdout)

    asyncio.run(main())

Or with custom configuration:
    from quicksand import Sandbox

    async with Sandbox(image="alpine", memory="512M", cpus=2) as sb:
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

DISTRO_VERSION = "3.23"

_PACKAGE_DIR = Path(__file__).parent
_IMAGES_DIR = _PACKAGE_DIR / "images"
_DOCKER_DIR = _PACKAGE_DIR / "docker"


class _AlpineImageProvider:
    """ImageProvider for the bundled Alpine base image."""

    name = "alpine"
    type = "base"
    images_dir = _IMAGES_DIR

    def resolve(self, arch: str | None = None) -> ResolvedImage:
        return _get_image_artifacts(arch)


# Module-level instance — registered as quicksand.images entry point
image = _AlpineImageProvider()


def _get_image_artifacts(arch: str | None = None) -> ResolvedImage:
    if arch is None:
        config = get_platform_config()
        arch = "arm64" if config.arch.arch_type == Architecture.ARM64 else "amd64"

    image_path = _IMAGES_DIR / f"alpine-{DISTRO_VERSION}-{arch}.qcow2"

    if not image_path.exists():
        image_path = _IMAGES_DIR / f"alpine-{DISTRO_VERSION}.qcow2"

    if not image_path.exists():
        from quicksand_core._auto_install import auto_install_images

        if auto_install_images("quicksand-alpine", _IMAGES_DIR):
            return _get_image_artifacts(arch)

        available = list(_IMAGES_DIR.glob("*.qcow2")) if _IMAGES_DIR.exists() else []
        if available:
            available_str = ", ".join(p.name for p in available)
            from quicksand_core.host.arch import _is_emulated

            if _is_emulated():
                raise FileNotFoundError(
                    f"Alpine image for {arch} not found. Available: {available_str}\n"
                    "Python is running under platform emulation, so pip installed "
                    "the wrong architecture variant.\n"
                    "Reinstall with:  quicksand install quicksand-alpine"
                )
            raise FileNotFoundError(
                f"Alpine image for {arch} not found. Available: {available_str}\n"
                "Reinstall with:  quicksand install quicksand-alpine"
            )
        raise FileNotFoundError(
            "No Alpine images found. If you installed from PyPI, download images with:\n"
            "  quicksand install alpine"
        )

    return ResolvedImage(
        name="alpine",
        chain=[image_path],
        kernel=image_path.with_suffix(".kernel"),
        initrd=image_path.with_suffix(".initrd"),
    )


class AlpineSandbox(Sandbox):
    """Pre-configured Sandbox using the bundled Alpine image.

    Alpine Linux is lightweight (~75MB image) and boots faster than Ubuntu.

    Usage::

        async with AlpineSandbox() as sb:
            result = await sb.execute("cat /etc/alpine-release")

        async with AlpineSandbox(save="my-env") as sb:
            await sb.execute("apk add python3")

        async with AlpineSandbox(image="my-env") as sb:
            await sb.execute("python3 --version")
    """

    def __init__(
        self,
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
        save: str | None = None,
        workspace: str | Path | None = None,
        **kwargs: Unpack[SandboxConfigParams],
    ) -> None:
        kwargs.setdefault("image", "alpine")
        super().__init__(
            progress_callback=progress_callback,
            save=save,
            workspace=workspace,
            **kwargs,
        )


__all__ = [
    "DISTRO_VERSION",
    "AlpineSandbox",
    "__version__",
]
