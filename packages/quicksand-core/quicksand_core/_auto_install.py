"""Auto-fetch fat-wheel images from the quicksand simple index.

When a contrib package is installed from PyPI as a pure-Python stub (the
fat wheel exceeded PyPI's 100 MB cap), this module re-runs ``pip install``
against the quicksand simple index — which serves every wheel from every
per-package GitHub release — to upgrade the install to the platform-specific
fat wheel carrying the actual images.

Enabled by default; set ``QUICKSAND_AUTO_INSTALL=0`` to opt out.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from importlib.metadata import version as _get_version
from pathlib import Path

from ._index import QUICKSAND_INDEX_URL

logger = logging.getLogger("quicksand.auto_install")

_TRUTHY = {"1", "true", "yes", "on"}


def auto_install_images(package_name: str, images_dir: Path) -> bool:
    """Re-install *package_name* against the quicksand simple index.

    The package is assumed to already be installed (typically as a PyPI
    pure-Python stub with no images). This re-runs pip against the simple
    index, which serves the fat wheel from the matching per-package GitHub
    release; pip drops the images into the package's site-packages dir.

    Enabled by default; set ``QUICKSAND_AUTO_INSTALL=0`` to opt out.

    Args:
        package_name: PyPI package name (e.g. ``"quicksand-ubuntu"``).
        images_dir: Expected images directory — used only to verify that
            the reinstall actually placed images.

    Returns:
        True if ``manifest.json`` is present in *images_dir* afterwards.
    """
    val = os.environ.get("QUICKSAND_AUTO_INSTALL", "1").strip().lower()
    if val not in _TRUTHY:
        return False

    try:
        ver = _get_version(package_name)
    except Exception:
        logger.debug("Could not determine version for %s", package_name)
        return False

    logger.info("Re-installing %s==%s from quicksand simple index ...", package_name, ver)

    pip_args = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        "--no-deps",
        "--index-url",
        QUICKSAND_INDEX_URL,
        f"{package_name}=={ver}",
    ]

    try:
        result = subprocess.run(pip_args, check=False)
    except OSError:
        logger.warning("Failed to invoke pip", exc_info=True)
        return False

    if result.returncode != 0:
        logger.warning("pip install failed (exit %s)", result.returncode)
        return False

    return (images_dir / "manifest.json").exists()
