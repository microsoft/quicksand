"""Auto-install images from GitHub releases into site-packages.

Contrib packages can call :func:`auto_install_images` to download the fat
wheel from GitHub Releases and extract image files directly into the
installed package's ``images/`` directory — no pip reinstall needed.

Enabled by default; set ``QUICKSAND_AUTO_INSTALL=0`` to opt out.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import urllib.request
import zipfile
from importlib.metadata import version as _get_version
from pathlib import Path

logger = logging.getLogger("quicksand.auto_install")

REPO = "microsoft/quicksand"
_FALSY = {"0", "false", "no", "off"}

# File extensions and paths that are image artifacts inside a wheel.
_IMAGE_GLOBS = ("*.qcow2", "*.kernel", "*.initrd", "manifest.json")


def auto_install_images(
    package_name: str,
    images_dir: Path,
    arch: str | None = None,
) -> bool:
    """Download images from the fat wheel on GitHub into *images_dir*.

    Enabled by default; set ``QUICKSAND_AUTO_INSTALL=0`` to opt out.
    Returns ``True`` if images were successfully extracted.

    Args:
        package_name: PyPI package name (e.g. ``"quicksand-ubuntu"``).
        images_dir: Destination directory for extracted images
            (typically the package's ``images/`` folder in site-packages).
        arch: Target arch (``"amd64"``/``"x86_64"``/``"arm64"``/``"aarch64"``).
            When ``None``, picks the wheel matching the host arch and OS.
            When set, picks any wheel for the target arch (cross-arch image
            install — image content is OS-agnostic when extracted).
    """
    val = os.environ.get("QUICKSAND_AUTO_INSTALL", "").strip().lower()
    if val in _FALSY:
        return False

    try:
        ver = _get_version(package_name)
    except Exception:
        logger.debug("Could not determine version for %s", package_name)
        return False

    tag = f"{package_name}/v{ver}"
    logger.info("Auto-installing images for %s (tag: %s) ...", package_name, tag)

    assets = _get_release_assets(tag)
    if not assets:
        logger.warning("No GitHub release found for tag %s", tag)
        return False

    wheel_url = _pick_compatible_wheel(assets, arch=arch)
    if not wheel_url:
        logger.warning("No compatible wheel found in release %s", tag)
        return False

    return _download_and_extract_images(wheel_url, images_dir)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _github_request(url: str) -> urllib.request.Request:
    """Build a GitHub API request."""
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    return req


def _get_release_assets(tag: str) -> list[dict]:
    """Fetch the asset list for a GitHub release by tag name."""
    url = f"https://api.github.com/repos/{REPO}/releases/tags/{tag}"
    try:
        with urllib.request.urlopen(_github_request(url)) as resp:
            data = json.load(resp)
        return data.get("assets", [])
    except Exception:
        logger.debug("Failed to fetch release %s", tag, exc_info=True)
        return []


def _host_arch() -> str:
    """Return the host architecture tag substring (``x86_64`` or ``arm64``)."""
    import platform

    machine = platform.machine().lower()
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return "x86_64"


def _host_os_tag() -> str:
    """Return a substring that must appear in a compatible wheel filename."""
    import platform

    system = platform.system().lower()
    if system == "darwin":
        return "macosx"
    if system == "linux":
        return "linux"
    if system == "windows":
        return "win"
    return system


_ARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "x86_64": ("x86_64", "amd64"),
    "amd64": ("x86_64", "amd64"),
    "arm64": ("arm64", "aarch64"),
    "aarch64": ("arm64", "aarch64"),
}


def _pick_compatible_wheel(assets: list[dict], arch: str | None = None) -> str | None:
    """Pick the best wheel URL for the host (or *arch*) from release assets.

    Without *arch*: returns the wheel matching both host arch and host OS.
    With *arch*: returns any non-pure wheel whose name contains the target
    arch tag — image content is OS-agnostic once extracted, so a foreign-OS
    wheel works for cross-arch image installs.
    """
    if arch is None:
        target_archs = (_host_arch(),)
        require_os = _host_os_tag()
    else:
        target_archs = _ARCH_ALIASES.get(arch.lower(), (arch.lower(),))
        require_os = None

    for asset in assets:
        name = asset.get("name", "")
        if not name.endswith(".whl"):
            continue
        if "py3-none-any" in name:
            continue  # skip pure wheels
        if not any(a in name for a in target_archs):
            continue
        if require_os is not None and require_os not in name:
            continue
        return asset.get("browser_download_url") or asset.get("url")
    return None


def _download_and_extract_images(wheel_url: str, images_dir: Path) -> bool:
    """Download a wheel and extract image files into *images_dir*."""
    images_dir.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(suffix=".whl", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        logger.info("Downloading %s ...", wheel_url.rsplit("/", 1)[-1])
        req = _github_request(wheel_url)
        req.add_header("Accept", "application/octet-stream")
        with urllib.request.urlopen(req) as resp, open(tmp_path, "wb") as out:
            shutil.copyfileobj(resp, out)

        extracted = 0
        with zipfile.ZipFile(tmp_path) as zf:
            for entry in zf.namelist():
                if not _is_image_file(entry):
                    continue
                # Flatten: images/foo.qcow2 → images_dir/foo.qcow2
                # Handle nested: images/overlays/001.qcow2 → images_dir/overlays/001.qcow2
                parts = Path(entry).parts
                # Find the "images" segment and keep everything after it
                try:
                    idx = parts.index("images")
                except ValueError:
                    continue
                rel = Path(*parts[idx + 1 :]) if len(parts) > idx + 1 else Path(parts[-1])
                dest = images_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(entry) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted += 1

        logger.info("Extracted %d image file(s) to %s", extracted, images_dir)
        return extracted > 0
    except Exception:
        logger.warning("Failed to download/extract images", exc_info=True)
        return False
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _is_image_file(entry: str) -> bool:
    """Check if a zip entry is an image artifact."""
    if entry.endswith("/"):
        return False
    name = entry.rsplit("/", 1)[-1]
    for glob in _IMAGE_GLOBS:
        pattern = glob.lstrip("*")
        if name.endswith(pattern) or name == glob:
            return True
    return False
