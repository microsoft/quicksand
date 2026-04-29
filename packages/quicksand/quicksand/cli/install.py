"""Install optional quicksand extras from GitHub releases.

Programmatic API::

    from quicksand import install

    install("qemu", "ubuntu")          # install specific extras
    install("all")                     # install all main packages
    install("ubuntu", version="0.4.0") # pin a version
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = "microsoft/quicksand"

# Short aliases → actual package names on GitHub
ALIASES: dict[str, list[str]] = {
    "qemu": ["quicksand-qemu"],
    "ubuntu": ["quicksand-ubuntu"],
    "alpine": ["quicksand-alpine"],
    "alpine-desktop": ["quicksand-alpine-desktop"],
    "ubuntu-desktop": ["quicksand-ubuntu-desktop"],
    "dev": ["quicksand-image-tools", "quicksand-overlay-scaffold", "quicksand-base-scaffold"],
    "all": [
        "quicksand-qemu",
        "quicksand-ubuntu",
        "quicksand-alpine",
        "quicksand-alpine-desktop",
        "quicksand-ubuntu-desktop",
    ],
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

    Aliases (e.g. "ubuntu") expand to their mapped packages.
    All other names are treated as literal package names.
    """
    return ALIASES.get(name, [name])


# ── Public API ────────────────────────────────────────────────────────


def install(*extras: str, arch: str | None = None) -> None:
    """Install quicksand extras from GitHub releases.

    Downloads all wheels for requested packages into a temporary directory,
    then uses ``pip install --find-links`` to resolve platform compatibility
    and dependencies automatically.

    Use ``"name@version"`` to pin a specific version; without ``@`` the
    latest release is used.

    Args:
        *extras: One or more extra/package names (e.g. ``"qemu"``, ``"ubuntu"``,
            ``"all"``, ``"ubuntu@0.4.0"``, ``"quicksand-agent"``).
        arch: Target architecture (e.g. ``"amd64"``, ``"arm64"``).
            Downloads cross-platform wheels for use with ``quicksand run --arch``.

    Raises:
        RuntimeError: If ``gh`` CLI is missing or not authenticated, or
            if no matching wheels are found.

    Examples::

        from quicksand import install

        install("qemu", "ubuntu")
        install("all")
        install("alpine@0.4.0")
        install("ubuntu", arch="amd64")  # cross-arch
    """
    if not extras:
        raise ValueError("At least one extra name is required")

    _require_gh()

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
        help="Install packages from GitHub releases",
    )
    parser.add_argument(
        "extras",
        nargs="+",
        metavar="NAME[@VERSION]",
        help=(
            f"Packages to install (use @version to pin, e.g. ubuntu@0.4.0). "
            f"Aliases: {all_aliases}. "
            "Other names are looked up as package releases on GitHub."
        ),
    )
    parser.add_argument(
        "--arch",
        default=None,
        help="Install for a specific architecture (e.g. amd64, arm64). "
        "Downloads cross-platform wheels for use with quicksand run --arch.",
    )


def cmd(args: argparse.Namespace) -> int:
    """Install packages from GitHub releases."""
    if not shutil.which("gh"):
        print("Error: gh CLI not found. Install from https://cli.github.com/")
        return 1

    result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if result.returncode != 0:
        print("Error: gh CLI not authenticated. Run: gh auth login")
        return 1

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


def _require_gh() -> None:
    """Check gh CLI is available and authenticated."""
    if not shutil.which("gh"):
        raise RuntimeError("gh CLI not found. Install from https://cli.github.com/")
    result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("gh CLI not authenticated. Run: gh auth login")


def _install_packages(
    packages: list[str],
    versions: dict[str, str | None],
    arch: str | None = None,
) -> int:
    """Download wheels to a temp dir, then pip install --find-links."""
    print(f"Installing: {', '.join(packages)}")
    emulated = _is_platform_emulated()
    if arch:
        print(f"Platform: {platform.system()} {platform.machine()} (target arch: {arch})")
    elif emulated:
        print(
            f"Platform: {platform.system()} {platform.machine()} "
            f"(Python arch: {_get_pip_arch_tag()}, native: {_get_host_arch_tag()})"
        )
    else:
        print(f"Platform: {platform.system()} {platform.machine()}")
    # Resolve compatible versions based on installed quicksand version
    quicksand_ver = _get_quicksand_version()
    quicksand_date: str | None = None
    all_releases: dict[str, str] = {}
    if quicksand_ver:
        all_releases = _get_all_releases_with_dates()
        quicksand_date = all_releases.get(f"quicksand/v{quicksand_ver}")
        if quicksand_date:
            print(f"Resolving versions compatible with quicksand {quicksand_ver}")
        else:
            print(f"Warning: no release found for quicksand {quicksand_ver}, using latest versions")
    print()

    with tempfile.TemporaryDirectory() as tmp:
        wheel_dir = Path(tmp)

        # Download requested packages and their transitive quicksand deps
        seen: set[str] = set()
        queue = list(packages)
        while queue:
            pkg_name = queue.pop(0)
            if pkg_name in seen:
                continue
            seen.add(pkg_name)

            ver = versions.get(pkg_name)
            if ver:
                tag = f"{pkg_name}/v{ver}"
            elif quicksand_date:
                tag = _resolve_compatible_tag(pkg_name, quicksand_date, all_releases)
            else:
                tag = _get_latest_release_tag(pkg_name)
            if not tag:
                if pkg_name in packages:
                    print(f"Error: No compatible release found for {pkg_name}")
                    print(f"  Check releases: gh release list --repo {REPO}")
                    return 1
                continue  # not a quicksand package, pip will resolve from PyPI

            resolved_ver = tag.split("/v")[-1]
            print(f"  {pkg_name} → v{resolved_ver}")

            new_wheels = _download_release_wheels(tag, wheel_dir)
            # Scan downloaded wheels for transitive deps that have GH releases
            for whl in new_wheels:
                for dep in _extract_dep_names(whl):
                    if dep not in seen:
                        queue.append(dep)

        if arch:
            _retag_wheels_for_arch(wheel_dir)
        elif emulated:
            print("Retagging image wheels for pip compatibility (emulated platform)")
            _retag_wheels_for_arch(wheel_dir)
            _cleanup_incompatible_wheels(wheel_dir)

        # Let pip handle platform matching, dep resolution, and conflicts.
        # --force-reinstall ensures downloaded wheels overwrite editable installs
        # or same-version packages that may shadow the correct entry points.
        pip_args = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--find-links",
            str(wheel_dir),
        ]
        pip_args.extend(packages)

        print(f"Running: pip install --find-links {wheel_dir.name} {' '.join(packages)}")
        result = subprocess.run(pip_args)
        if result.returncode != 0:
            print("\nError: pip install failed.")
            return 1

    print(f"\nInstalled {len(packages)} package(s)")
    return 0


def _get_latest_release_tag(pkg_name: str) -> str | None:
    """Find the latest per-package release tag (e.g. quicksand-core/v0.3.4)."""
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{REPO}/git/matching-refs/tags/{pkg_name}/v",
            "--jq",
            '.[].ref | ltrimstr("refs/tags/")',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    tags = [t for t in result.stdout.strip().splitlines() if t and not t.endswith("-dev")]
    return tags[-1] if tags else None


def _get_quicksand_version() -> str | None:
    """Return the installed quicksand version, or ``None`` if unavailable."""
    try:
        from importlib.metadata import version

        return version("quicksand")
    except Exception:
        return None


def _version_tuple(ver: str) -> tuple[int, ...]:
    """Parse ``'1.2.3'`` into ``(1, 2, 3)`` for comparison."""
    parts: list[int] = []
    for p in ver.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts)


def _get_all_releases_with_dates() -> dict[str, str]:
    """Fetch every GitHub release in the repo → ``{tag_name: published_at}``."""
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{REPO}/releases",
            "--paginate",
            "--jq",
            ".[] | [.tag_name, .published_at] | @tsv",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}
    releases: dict[str, str] = {}
    for line in result.stdout.strip().splitlines():
        if "\t" not in line:
            continue
        tag, date = line.split("\t", 1)
        releases[tag] = date
    return releases


def _resolve_compatible_tag(
    pkg_name: str,
    quicksand_date: str,
    all_releases: dict[str, str],
) -> str | None:
    """Pick the best release tag for *pkg_name* given a quicksand release date.

    1. Find the latest release published **at or before** *quicksand_date* to
       establish a baseline ``major.minor``.
    2. Return the newest ``major.minor.*`` release (patch bumps are always
       compatible, even if published after the quicksand release).

    Returns ``None`` if the package has no releases, or none were published
    before *quicksand_date*.
    """
    prefix = f"{pkg_name}/v"
    pkg_releases: list[tuple[str, str, str]] = []  # (tag, version, date)
    for tag, date in all_releases.items():
        if tag.startswith(prefix) and not tag.endswith("-dev"):
            pkg_releases.append((tag, tag[len(prefix) :], date))

    if not pkg_releases:
        return None

    pkg_releases.sort(key=lambda x: _version_tuple(x[1]))

    # Baseline: latest version released at or before quicksand
    baseline = None
    for tag, ver, date in pkg_releases:
        if date <= quicksand_date:
            baseline = (tag, ver)

    if not baseline:
        return None

    parts = baseline[1].split(".")
    if len(parts) < 2:
        return baseline[0]
    major_minor = f"{parts[0]}.{parts[1]}"

    # Newest patch in the same major.minor series
    best = baseline[0]
    for tag, ver, _date in pkg_releases:
        vp = ver.split(".")
        if len(vp) >= 2 and f"{vp[0]}.{vp[1]}" == major_minor:
            best = tag

    return best


def _get_host_arch_tag() -> str:
    """Return the architecture tag substring for the native hardware.

    On Windows, detects the native hardware architecture to prefer
    the correct wheel even when Python runs under x86_64 emulation.
    """
    from quicksand_core.host.arch import _detect_architecture

    arch = _detect_architecture()
    return "arm64" if arch.image_arch == "arm64" else "x86_64"


def _get_pip_arch_tag() -> str:
    """Return the architecture tag that pip will accept on this host.

    Unlike :func:`_get_host_arch_tag` (which reports native hardware),
    this returns the arch from Python's perspective — matching what pip
    uses for wheel compatibility checks.
    """
    import sysconfig

    plat = sysconfig.get_platform()  # e.g. "win-amd64", "macosx-14.0-arm64"
    if "arm64" in plat or "aarch64" in plat:
        return "arm64"
    return "x86_64"


def _is_platform_emulated() -> bool:
    """True when native hardware arch differs from Python's interpreter arch.

    This happens on Windows ARM64 where Python runs under x86_64 emulation.
    The native hardware is ARM64 but ``sysconfig.get_platform()`` reports
    ``win-amd64``, causing pip to reject ``win_arm64`` wheels.
    """
    return _get_host_arch_tag() != _get_pip_arch_tag()


def _macos_version_compatible(name: str) -> bool:
    """Check if a macosx wheel targets a version this host can run.

    macOS is backwards-compatible: a host running 15.x can install wheels
    built for 11.0 through 15.0. Parse the version from the wheel tag
    (e.g. ``macosx_14_0_arm64`` → 14) and accept if <= host major version.
    """
    import re

    match = re.search(r"macosx_(\d+)_", name)
    if not match:
        return True  # not a macosx tag, let other checks handle it
    wheel_major = int(match.group(1))
    host_major = int(platform.mac_ver()[0].split(".")[0])
    return wheel_major <= host_major


def _wheel_compatible_with_host(name: str) -> bool:
    """Quick check: could this wheel possibly install on this host?

    Rejects wheels that are clearly for another OS, architecture, or
    macOS version. pip does the final precise matching — this just avoids
    downloading hundreds of MB of obviously-wrong wheels.

    When platform emulation is detected (e.g. Windows ARM64 with x86_64
    Python), accepts wheels for *either* the native arch or the
    pip-compatible arch so we can download both arm64 image wheels
    (correct VM images) and amd64 binary wheels (pip-installable).
    """
    if "py3-none-any" in name:
        return True

    native_tag = _get_host_arch_tag()
    pip_tag = _get_pip_arch_tag()

    if native_tag != pip_tag:
        # Emulated: accept wheels matching either architecture
        if native_tag not in name and pip_tag not in name:
            return False
    else:
        if native_tag not in name:
            return False

    system = platform.system().lower()
    if system == "darwin":
        return "macosx" in name and _macos_version_compatible(name)
    if system == "linux":
        return "linux" in name or "manylinux" in name
    if system == "windows":
        return "win" in name
    return False


def _download_release_wheels(tag: str, dest_dir: Path) -> list[Path]:
    """Download host-compatible .whl assets from a GitHub release into dest_dir."""
    result = subprocess.run(
        ["gh", "api", f"repos/{REPO}/releases/tags/{tag}", "--jq", ".assets[] | {name, size, url}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    token = _get_gh_token()
    downloaded: list[Path] = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        asset = json.loads(line)
        if not asset["name"].endswith(".whl"):
            continue
        if not _wheel_compatible_with_host(asset["name"]):
            continue
        dest = dest_dir / asset["name"]
        if dest.exists():
            continue
        print(f"Downloading {asset['name']}")
        _download_with_progress(asset["url"], dest, token, asset["size"])
        downloaded.append(dest)
    return downloaded


def _extract_dep_names(wheel_path: Path) -> list[str]:
    """Extract dependency package names from a wheel's METADATA."""
    import zipfile
    from email.parser import Parser

    with zipfile.ZipFile(wheel_path) as zf:
        metadata_files = [n for n in zf.namelist() if n.endswith(".dist-info/METADATA")]
        if not metadata_files:
            return []
        metadata_text = zf.read(metadata_files[0]).decode("utf-8")

    msg = Parser().parsestr(metadata_text)
    requires = msg.get_all("Requires-Dist") or []
    names = []
    for r in requires:
        r = r.split(";")[0]  # drop environment markers
        for ch in "(<>=!~":
            r = r.split(ch)[0]  # drop version specifiers
        names.append(r.strip().lower().replace("_", "-"))
    return names


def _is_image_wheel(wheel_path: Path) -> bool:
    """Check if a wheel provides a quicksand.images entry point."""
    import zipfile

    with zipfile.ZipFile(wheel_path) as zf:
        entry_points_files = [n for n in zf.namelist() if n.endswith(".dist-info/entry_points.txt")]
        if not entry_points_files:
            return False
        content = zf.read(entry_points_files[0]).decode("utf-8")
        return "quicksand.images" in content


def _retag_wheels_for_arch(wheel_dir: Path) -> None:
    """Retag cross-arch image wheels so pip accepts them on this host.

    Only retags wheels that provide quicksand.images entry points (VM image
    packages). Native binary packages (like quicksand-qemu) must not be
    retagged — they contain real binaries that only work on their target OS.
    """
    import sysconfig

    host_tag = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    for whl in wheel_dir.glob("*.whl"):
        parts = whl.stem.rsplit("-", 1)
        if len(parts) != 2:
            continue
        prefix, old_tag = parts
        if old_tag == "any" or host_tag in old_tag:
            continue
        if not _is_image_wheel(whl):
            continue
        new_path = whl.parent / f"{prefix}-{host_tag}.whl"
        whl.rename(new_path)


def _cleanup_incompatible_wheels(wheel_dir: Path) -> None:
    """Remove non-image wheels that pip cannot install on this host.

    After downloading both arch variants (native + pip-compatible) and
    retagging image wheels, non-image wheels for the native arch remain.
    pip cannot install them, so remove any that have a pip-compatible
    counterpart already present in the directory.
    """
    import sysconfig

    pip_plat = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    native_tag = _get_host_arch_tag()

    # Build the native platform tag (e.g. "win_arm64")
    if platform.system().lower() == "windows":
        native_plat = f"win_{native_tag}"
    elif platform.system().lower() == "darwin":
        native_plat = native_tag  # part of macosx_..._arm64
    else:
        native_plat = f"linux_{native_tag}"

    for whl in list(wheel_dir.glob("*.whl")):
        if native_plat not in whl.name:
            continue
        # Check if a pip-compatible variant exists
        pip_name = whl.name.replace(native_plat, pip_plat)
        pip_variant = whl.parent / pip_name
        if pip_variant.exists() and pip_variant != whl:
            whl.unlink()


def _get_gh_token() -> str:
    """Get GitHub token from gh CLI."""
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _format_size(size_bytes: int) -> str:
    """Format size in bytes to human readable string."""
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _download_with_progress(url: str, dest: Path, token: str, size: int) -> None:
    """Download a file with progress bar."""
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/octet-stream")

    start_time = time.time()
    downloaded = 0

    with urllib.request.urlopen(req) as response, open(dest, "wb") as out_file:
        block_size = 8192
        while True:
            buffer = response.read(block_size)
            if not buffer:
                break
            out_file.write(buffer)
            downloaded += len(buffer)

            elapsed = time.time() - start_time
            speed = downloaded / elapsed if elapsed > 0 else 0
            percent = (downloaded / size * 100) if size > 0 else 0

            bar_width = 30
            filled = int(bar_width * downloaded / size) if size > 0 else 0
            bar = "#" * filled + "-" * (bar_width - filled)

            sys.stdout.write(
                f"\r  {bar} {percent:5.1f}% {_format_size(downloaded)}/{_format_size(size)} "
                f"({_format_size(int(speed))}/s)"
            )
            sys.stdout.flush()

    sys.stdout.write("\n")
