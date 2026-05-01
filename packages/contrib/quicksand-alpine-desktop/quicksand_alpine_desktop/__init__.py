"""
Quicksand Alpine Desktop bundles a minimal Alpine Linux VM with Xfce4 desktop.

Lightweight (~300MB image) and boots quickly (~15s). Built as an overlay on
quicksand-alpine — both execute() and GUI input (screenshot/type_text/click)
work.

Quick start:
    import asyncio
    from quicksand import Key
    from quicksand_alpine_desktop import AlpineDesktopSandbox

    async def main():
        async with AlpineDesktopSandbox() as sb:
            await sb.screenshot("desktop.png")
            await sb.type_text("hello world")
            await sb.press_key(Key.RET)
            result = await sb.execute("cat /etc/os-release")

    asyncio.run(main())
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Unpack

from quicksand_core import Sandbox
from quicksand_core._types import SandboxConfigParams

__version__ = "0.2.3"

DISTRO_VERSION = "3.21"

_PACKAGE_DIR = Path(__file__).parent
_IMAGES_DIR = _PACKAGE_DIR / "images"


class _AlpineDesktopImageProvider:
    """ImageProvider for the bundled Alpine Desktop overlay."""

    name = "alpine-desktop"
    type = "overlay"
    images_dir = _IMAGES_DIR

    def resolve(self, arch: str | None = None):
        from quicksand_core.qemu.image_resolver import ImageResolver

        if not (_IMAGES_DIR / "manifest.json").exists():
            from quicksand_core._auto_install import auto_install_images

            if not auto_install_images("quicksand-alpine-desktop", _IMAGES_DIR):
                raise FileNotFoundError(
                    "No bundled save found. If you installed from PyPI, download images with:\n"
                    "  quicksand install quicksand-alpine-desktop"
                )
        return ImageResolver()._resolve_save(_IMAGES_DIR)


# Module-level instance — registered as quicksand.images entry point
image = _AlpineDesktopImageProvider()


class AlpineDesktopSandbox(Sandbox):
    """Pre-configured Sandbox with an Alpine Linux Xfce4 desktop.

    Lightweight alternative to UbuntuDesktopSandbox: ~300MB image,
    ~15s boot, and full guest agent support (execute() works).

    Usage::

        async with AlpineDesktopSandbox() as sb:
            await sb.screenshot("boot.png")
            await sb.type_text("hello")
            await sb.press_key(Key.RET)
            result = await sb.execute("uname -a")
    """

    def __init__(
        self,
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
        save: str | None = None,
        workspace: str | Path | None = None,
        **kwargs: Unpack[SandboxConfigParams],
    ) -> None:
        kwargs.setdefault("image", "alpine-desktop")
        kwargs.setdefault("memory", "1G")
        kwargs.setdefault("cpus", 2)
        kwargs.setdefault("enable_display", True)
        super().__init__(
            progress_callback=progress_callback,
            save=save,
            workspace=workspace,
            **kwargs,
        )


__all__ = [
    "AlpineDesktopSandbox",
    "__version__",
]
