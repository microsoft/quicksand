"""
Quicksand Ubuntu Desktop bundles an Ubuntu 24.04 VM with Xfce4 desktop.

Full apt/deb ecosystem with guest agent support — both execute() and
GUI input (screenshot/type_text/click) work. Built as an overlay on
quicksand-ubuntu.

Quick start:
    import asyncio
    from quicksand import Key
    from quicksand_ubuntu_desktop import UbuntuDesktopSandbox

    async def main():
        async with UbuntuDesktopSandbox() as sb:
            await sb.screenshot("desktop.png")
            await sb.type_text("hello world")
            await sb.press_key(Key.RET)
            result = await sb.execute("apt list --installed")

    asyncio.run(main())
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Unpack

from quicksand_core import Sandbox
from quicksand_core._types import SandboxConfigParams

__version__ = "0.2.3"

DISTRO_VERSION = "24.04"

_PACKAGE_DIR = Path(__file__).parent
_IMAGES_DIR = _PACKAGE_DIR / "images"


class _UbuntuDesktopImageProvider:
    """ImageProvider for the bundled Ubuntu Desktop overlay."""

    name = "ubuntu-desktop"
    type = "overlay"
    images_dir = _IMAGES_DIR

    def resolve(self, arch: str | None = None):
        from quicksand_core.qemu.image_resolver import ImageResolver

        if not (_IMAGES_DIR / "manifest.json").exists():
            raise FileNotFoundError(
                f"No bundled save found in {_IMAGES_DIR}. "
                "The package may not have been built correctly."
            )
        return ImageResolver()._resolve_save(_IMAGES_DIR)


# Module-level instance — registered as quicksand.images entry point
image = _UbuntuDesktopImageProvider()


class UbuntuDesktopSandbox(Sandbox):
    """Pre-configured Sandbox with an Ubuntu 24.04 Xfce4 desktop.

    Full apt/deb ecosystem with guest agent support.

    Usage::

        async with UbuntuDesktopSandbox() as sb:
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
        kwargs.setdefault("image", "ubuntu-desktop")
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
    "UbuntuDesktopSandbox",
    "__version__",
]
