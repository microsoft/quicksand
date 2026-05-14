"""Install optional quicksand extras from PyPI.

Delegates version resolution and platform-wheel selection to ``pip``: a
package on PyPI under 100 MB ships as a fat wheel with images bundled in;
larger ones ship as a pure ``py3-none-any`` stub that pulls the matching
fat wheel from GitHub Releases on first install (via
:func:`quicksand_core._auto_install.auto_install_images`).

Programmatic API::

    from quicksand import install

    install("qemu", "ubuntu")          # install latest from PyPI
    install("ubuntu@0.4.0")            # pin a version
    install("ubuntu", arch="amd64")    # cross-arch image install
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

# Short aliases → actual package names on PyPI
ALIASES: dict[str, list[str]] = {
    "qemu": ["quicksand-qemu"],
    "ubuntu": ["quicksand-ubuntu"],
    "alpine": ["quicksand-alpine"],
    "alpine-desktop": ["quicksand-alpine-desktop"],
    "ubuntu-desktop": ["quicksand-ubuntu-desktop"],
    "agent": ["quicksand-agent"],
    "cua": ["quicksand-cua"],
    "dev": ["quicksand-image-tools", "quicksand-overlay-scaffold", "quicksand-base-scaffold"],
}


# ── Helpers ───────────────────────────────────────────────────────────


def _parse_extra(raw: str) -> tuple[str, str | None]:
    """Parse ``"name"`` or ``"name@version"`` into ``(name, version|None)``."""
    if "@" in raw:
        name, ver = raw.split("@", 1)
        return name, ver
    return raw, None


def _resolve_packages(name: str) -> list[str]:
    """Resolve a name to a list of package names.

    Aliases (e.g. ``"ubuntu"``) expand to their mapped packages.
    All other names are treated as literal package names.
    """
    return ALIASES.get(name, [name])


# ── Public API ────────────────────────────────────────────────────────


def install(*extras: str, arch: str | None = None) -> None:
    """Install quicksand extras from PyPI.

    Uses ``pip install`` so pip's resolver picks the version. Use
    ``"name@version"`` to pin a specific version.

    For image packages that ship as pure-Python stubs on PyPI (because the
    fat wheel exceeds the 100 MB limit), images are fetched from the
    matching GitHub release after pip install — pass ``arch`` to fetch
    a different architecture's images for use with ``quicksand run --arch``.

    Args:
        *extras: One or more extra/package names (e.g. ``"qemu"``, ``"ubuntu"``,
            ``"ubuntu@0.4.0"``, ``"quicksand-agent"``).
        arch: Target architecture for cross-arch image installs
            (``"amd64"`` / ``"arm64"``).

    Raises:
        RuntimeError: If pip install fails.

    Examples::

        from quicksand import install

        install("qemu", "ubuntu")
        install("alpine@0.4.0")
        install("ubuntu", arch="amd64")  # cross-arch
    """
    if not extras:
        raise ValueError("At least one extra name is required")

    packages: list[str] = []
    versions: dict[str, str | None] = {}
    for raw in extras:
        name, ver = _parse_extra(raw)
        for pkg in _resolve_packages(name):
            if pkg not in packages:
                packages.append(pkg)
                versions[pkg] = ver

    rc = _install_packages(packages, versions, arch=arch)
    if rc != 0:
        raise RuntimeError(f"Failed to install packages: {packages}")


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the install subcommand."""
    all_aliases = ", ".join(ALIASES.keys())
    parser = subparsers.add_parser(
        "install",
        help="Install packages from PyPI",
    )
    parser.add_argument(
        "extras",
        nargs="+",
        metavar="NAME[@VERSION]",
        help=(
            f"Packages to install (use @version to pin, e.g. ubuntu@0.4.0). "
            f"Aliases: {all_aliases}. "
            "Other names are installed directly from PyPI."
        ),
    )
    parser.add_argument(
        "--arch",
        default=None,
        help="Install images for a specific architecture (e.g. amd64, arm64). "
        "Fetches cross-platform images from GitHub for use with quicksand run --arch.",
    )


def cmd(args: argparse.Namespace) -> int:
    """Install packages from PyPI."""
    packages: list[str] = []
    versions: dict[str, str | None] = {}
    for raw in args.extras:
        name, ver = _parse_extra(raw)
        for pkg in _resolve_packages(name):
            if pkg not in packages:
                packages.append(pkg)
                versions[pkg] = ver

    return _install_packages(packages, versions, arch=args.arch)


# ── Implementation ────────────────────────────────────────────────────


def _install_packages(
    packages: list[str],
    versions: dict[str, str | None],
    arch: str | None = None,
) -> int:
    """Run ``pip install`` then fetch any missing images from GitHub."""
    print(f"Installing: {', '.join(packages)}")
    print(f"Platform: {platform.system()} {platform.machine()}")
    if arch:
        print(f"Target image arch: {arch}")
    print()

    rc = _run_pip_install(packages, versions)
    if rc != 0:
        print("\nError: pip install failed.")
        return rc

    _ensure_images(packages, arch=arch)

    print(f"\nInstalled {len(packages)} package(s)")
    return 0


def _run_pip_install(packages: list[str], versions: dict[str, str | None]) -> int:
    """Invoke ``pip install`` and let pip resolve versions from PyPI."""
    pip_args = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--upgrade-strategy",
        "only-if-needed",
    ]
    for pkg in packages:
        ver = versions.get(pkg)
        pip_args.append(f"{pkg}=={ver}" if ver else pkg)

    print(f"Running: pip install {' '.join(pip_args[4:])}")
    return subprocess.run(pip_args).returncode


def _ensure_images(packages: list[str], arch: str | None = None) -> None:
    """Fetch images from GitHub for any image-provider package in *packages*.

    Skipped automatically for packages whose fat wheel was installable from
    PyPI (their ``manifest.json`` already exists). Forced when *arch* is set,
    to overlay images for a different architecture.
    """
    requested = set(packages)
    providers = _discover_image_providers(requested)
    if not providers:
        return

    from quicksand_core._auto_install import auto_install_images

    for pkg_name, images_dir in providers:
        manifest = images_dir / "manifest.json"
        if manifest.exists() and arch is None:
            continue  # fat wheel already on disk for the host
        print(f"Fetching images for {pkg_name} (arch: {arch or 'host'})")
        auto_install_images(pkg_name, images_dir, arch=arch)


def _discover_image_providers(requested: set[str]) -> list[tuple[str, Path]]:
    """Return ``(pkg_name, images_dir)`` for every ``quicksand.images`` entry
    point whose distribution is in *requested*."""
    found: list[tuple[str, Path]] = []
    for ep in entry_points(group="quicksand.images"):
        dist = ep.dist
        if dist is None or dist.name not in requested:
            continue
        try:
            provider = ep.load()
        except Exception:
            continue
        images_dir = getattr(provider, "images_dir", None)
        if isinstance(images_dir, Path):
            found.append((dist.name, images_dir))
    return found
