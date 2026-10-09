"""Build-time utilities for quicksand image package hooks.

Centralizes architecture detection and platform wheel tagging that was
previously duplicated across every ``hatch_build.py`` in the monorepo.
"""

from __future__ import annotations

import json
import os
import shlex
import sysconfig
from collections.abc import Awaitable, Callable

_X86_NAMES = {"x86_64", "amd64", "x64"}
_ARM_NAMES = {"arm64", "aarch64"}

_PYTHON_INDEX_VARIABLES = (
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "UV_DEFAULT_INDEX",
    "UV_INDEX_URL",
    "UV_EXTRA_INDEX_URL",
)


async def run_python_install(
    shell: Callable[..., Awaitable[None]],
    command: str,
    *,
    timeout: float = 300,
) -> None:
    """Run a Python installer in a Unix guest using the host's package indexes.

    The guest needs Python 3 and a stdin-capable agent. Index settings apply only
    to this command and travel over stdin, not command arguments or guest config.
    """
    index_env = {name: os.environ[name] for name in _PYTHON_INDEX_VARIABLES if name in os.environ}
    if not index_env:
        await shell(command, timeout=timeout)
        return

    # URLs can contain credentials: pass them through stdin, not command arguments.
    launcher = (
        "import json,os,sys;"
        "os.environ.update(json.load(sys.stdin));"
        "os.execl('/bin/sh','sh','-c',sys.argv[1])"
    )
    await shell(
        f"python3 -c {shlex.quote(launcher)} {shlex.quote(command)}",
        stdin=json.dumps(index_env).encode(),
        timeout=timeout,
    )


def get_image_arch() -> str:
    """Return the image architecture string for the host (``"amd64"`` or ``"arm64"``).

    On Windows, detects the native hardware architecture to see through
    x86_64-on-ARM64 transparent emulation.
    """
    from quicksand_core.host.arch import _detect_architecture

    return _detect_architecture().image_arch


def set_platform_wheel_tag(
    build_data: dict,
    *,
    target_name: str = "wheel",
    version: str = "",
) -> bool:
    """Mark the wheel as platform-specific and set the ``py3-none-<platform>`` tag.

    On Windows ARM64, ``sysconfig.get_platform()`` returns ``win-amd64`` when
    Python runs under x86_64 emulation.  We override the tag to ``win_arm64``
    based on native hardware detection so the wheel is correctly tagged.

    Returns ``True`` if the build should proceed (real wheel build) or ``False``
    if it should be skipped (editable install or non-wheel target).

    Raises:
        RuntimeError: If the host architecture is unsupported (only for real builds).
    """
    if target_name != "wheel" or version == "editable":
        return False
    if os.environ.get("QUICKSAND_PURE_WHEEL", "").strip() == "1":
        build_data["pure_python"] = True
        return False
    native_arch = get_image_arch()  # validates arch, raises on unsupported
    build_data["pure_python"] = False
    platform_tag = sysconfig.get_platform().replace("-", "_").replace(".", "_")

    # Override tag on Windows ARM64 with emulated x64 Python
    if native_arch == "arm64" and "win_amd64" in platform_tag:
        platform_tag = "win_arm64"

    # PyPI requires manylinux tags for Linux wheels (PEP 600)
    if platform_tag.startswith("linux_"):
        platform_tag = platform_tag.replace("linux_", "manylinux_2_17_", 1)

    build_data["tag"] = f"py3-none-{platform_tag}"
    return True
