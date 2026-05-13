"""Per-user cache for package-installed VM images.

Package qcow2 / kernel / initrd / manifest files originally ship inside the
installed package's ``images/`` directory under site-packages. That location
is volatile — uv/pip can rebuild the venv at any time. We mirror those files
into a stable per-user cache (``<cache_dir>/images/<pkg>/``) on install and
prefer the cache at lookup time, falling back to the venv directory.

This is Phase 1 of a larger redesign that moves toward a manifest-managed
overlay cache. For now the only behaviour is install-time mirroring plus
cache-first lookup. Both locations are kept readable; nothing is moved or
deleted.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger("quicksand.image_cache")

# File names / suffixes that count as image artifacts and should be mirrored.
_IMAGE_SUFFIXES = (".qcow2", ".kernel", ".initrd")
_IMAGE_FILENAMES = ("manifest.json",)


def get_cache_dir(package_name: str) -> Path:
    """Return the cache directory for ``package_name``.

    Layout: ``<cache_dir>/images/<package_name>/``. The directory may not
    exist yet; callers that need to write should create it.
    """
    from .qemu.platform import get_platform_config

    return get_platform_config().cache_dir / "images" / package_name


def resolve(package_name: str, filename: str, legacy_dir: Path) -> Path | None:
    """Find ``filename`` for ``package_name``: cache first, then ``legacy_dir``.

    Returns the path to an existing file, or ``None`` if neither location has it.
    """
    cache_path = get_cache_dir(package_name) / filename
    if cache_path.exists():
        return cache_path
    legacy_path = legacy_dir / filename
    if legacy_path.exists():
        return legacy_path
    return None


def resolve_dir(package_name: str, legacy_dir: Path) -> Path | None:
    """Return the directory currently holding ``package_name``'s images.

    Prefers the cache directory if it contains any image artifacts; falls back
    to ``legacy_dir`` if that has artifacts; otherwise returns ``None``.

    Useful for save-format providers that pass a whole directory to
    ``ImageResolver._resolve_save``.
    """
    cache = get_cache_dir(package_name)
    if _has_image_artifacts(cache):
        return cache
    if _has_image_artifacts(legacy_dir):
        return legacy_dir
    return None


def mirror_to_cache(package_name: str, src_dir: Path) -> int:
    """Hardlink (or copy) image artifacts from ``src_dir`` into the cache.

    Idempotent: files that already exist in the cache are skipped. Hardlinks
    when possible — free on the same filesystem, and the cached copy survives
    the source being deleted (e.g. when the venv is rebuilt). Falls back to
    ``shutil.copy2`` if hardlinking fails (cross-filesystem, or any other
    ``OSError``).

    Preserves the directory structure within ``src_dir`` — so a save-format
    package's ``overlays/0.qcow2`` lands at ``<cache>/overlays/0.qcow2``.

    Returns the number of files newly mirrored.
    """
    if not src_dir.exists():
        return 0
    cache = get_cache_dir(package_name)
    cache.mkdir(parents=True, exist_ok=True)

    mirrored = 0
    for src in src_dir.rglob("*"):
        if not src.is_file():
            continue
        if not _is_image_file(src.name):
            continue
        rel = src.relative_to(src_dir)
        dst = cache / rel
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
        mirrored += 1

    if mirrored:
        logger.debug("Mirrored %d image file(s) for %s to %s", mirrored, package_name, cache)
    return mirrored


def _is_image_file(name: str) -> bool:
    """Whether a filename looks like a VM image artifact worth caching."""
    if name in _IMAGE_FILENAMES:
        return True
    return any(name.endswith(suffix) for suffix in _IMAGE_SUFFIXES)


def _has_image_artifacts(directory: Path) -> bool:
    """True if ``directory`` exists and contains any image artifact at any depth."""
    if not directory.exists() or not directory.is_dir():
        return False
    return any(entry.is_file() and _is_image_file(entry.name) for entry in directory.rglob("*"))
