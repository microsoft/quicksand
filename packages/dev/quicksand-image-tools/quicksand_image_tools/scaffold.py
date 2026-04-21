"""Scaffolding for new quicksand image packages.

Copies an existing base image package (e.g. quicksand-ubuntu) and renames it
to create a new image package, then registers it in the monorepo.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

from quicksand_core import BaseImageInfo

_ARTIFACT_EXTENSIONS = {".qcow2", ".kernel", ".initrd"}


def _discover_bases() -> dict[str, BaseImageInfo]:
    """Discover installed base image packages via entry points."""
    import importlib
    import logging

    bases: dict[str, BaseImageInfo] = {}
    eps = entry_points(group="quicksand.images")
    for ep in eps:
        try:
            provider = ep.load()
            if getattr(provider, "type", None) != "base":
                continue
            mod_name = ep.value.split(":")[0]
            mod = importlib.import_module(mod_name)
            docker_dir = getattr(mod, "_DOCKER_DIR", None) or getattr(mod, "DOCKER_DIR", None)
            version = getattr(mod, "DISTRO_VERSION", getattr(mod, "__version__", "unknown"))
            if docker_dir is None:
                continue
            bases[provider.name] = BaseImageInfo(
                name=provider.name, docker_dir=docker_dir, version=version
            )
        except Exception as e:
            logging.debug(f"Failed to load base entry point {ep.name}: {e}")
    return bases


def find_repo_root() -> Path | None:
    """Find the repository root via git."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return Path(result.stdout.strip())
    return None


def _ignore_artifacts(_directory: str, files: list[str]) -> set[str]:
    """shutil.copytree ignore callback — skip built images and caches."""
    ignored = set()
    for f in files:
        p = Path(f)
        if p.suffix in _ARTIFACT_EXTENSIONS:
            ignored.add(f)
        if f in ("__pycache__", ".pytest_cache") or f.endswith(".egg-info"):
            ignored.add(f)
    return ignored


def _is_text_file(path: Path) -> bool:
    """Check if a file is likely text (no null bytes in first 8KB)."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        return b"\x00" not in chunk
    except OSError:
        return False


def _to_under(name: str) -> str:
    return name.replace("-", "_")


def _to_title(name: str) -> str:
    parts = _to_under(name).split("_")
    return "".join(p.capitalize() for p in parts)


def copy_base_package(base_pkg_dir: Path, output_dir: Path) -> None:
    """Copy a base image package, excluding built artifacts."""
    shutil.copytree(base_pkg_dir, output_dir, ignore=_ignore_artifacts)


def rename_module(output_dir: Path, old_pkg: str, new_pkg: str) -> None:
    """Rename the Python module directory."""
    old_module = output_dir / _to_under(old_pkg)
    new_module = output_dir / _to_under(new_pkg)
    if old_module.exists():
        old_module.rename(new_module)


def replace_in_tree(root: Path, old_pkg: str, new_pkg: str, base: str) -> None:
    """Find-and-replace old package names with new names in all text files.

    old_pkg/new_pkg are full package names like "quicksand-ubuntu" / "aif-agent-harness".
    base is the short base name like "ubuntu".
    """
    old_under = _to_under(old_pkg)
    new_under = _to_under(new_pkg)
    base_title = _to_title(base)
    new_title = _to_title(new_pkg)

    # Ordered most-specific first to avoid partial matches
    replacements = [
        (old_under, new_under),
        (old_pkg, new_pkg),
        (base_title, new_title),
        (base, new_pkg),
    ]

    for filepath in root.rglob("*"):
        if not filepath.is_file():
            continue
        if not _is_text_file(filepath):
            continue

        content = filepath.read_text()
        original = content
        for old, new in replacements:
            content = content.replace(old, new)
        if content != original:
            filepath.write_text(content)


def reset_versions(output_dir: Path, pkg_name: str) -> None:
    """Reset version fields to 0.1.0 and DISTRO_VERSION to placeholder."""
    pkg_under = _to_under(pkg_name)

    pyproject = output_dir / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text()
        content = re.sub(
            r'^version\s*=\s*"[^"]+"', 'version = "0.1.0"', content, flags=re.MULTILINE
        )
        pyproject.write_text(content)

    init_py = output_dir / pkg_under / "__init__.py"
    if init_py.exists():
        content = init_py.read_text()
        content = re.sub(
            r'^__version__\s*=\s*"[^"]+"', '__version__ = "0.1.0"', content, flags=re.MULTILINE
        )
        content = re.sub(
            r'^DISTRO_VERSION\s*=\s*"[^"]+"',
            'DISTRO_VERSION = "VERSION"',
            content,
            flags=re.MULTILINE,
        )
        init_py.write_text(content)


def reset_readme(output_dir: Path, pkg_name: str) -> None:
    """Overwrite README with a minimal install command."""
    readme = output_dir / "README.md"
    readme.write_text(f"# {pkg_name}\n\n```bash\nquicksand install {pkg_name}\n```\n")


def reset_docker_dir(output_dir: Path, pkg_name: str, base: str) -> None:
    """Clear docker dir and write a minimal FROM-base Dockerfile."""
    pkg_under = _to_under(pkg_name)
    docker_dir = output_dir / pkg_under / "docker"
    if not docker_dir.exists():
        return

    # Keep .gitignore, remove everything else
    for item in docker_dir.iterdir():
        if item.name == ".gitignore":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    # Discover the base version for the FROM tag
    bases = _discover_bases()
    info = bases.get(base)
    version = info.version if info else "latest"

    versioned_tag = f"quicksand/{base}-base:{version}"
    dockerfile = docker_dir / "Dockerfile"
    dockerfile.write_text(f"FROM {versioned_tag}\n\n# Add your customizations here\n")


def register_package(pkg_name: str, repo_root: Path) -> bool:
    """Register the new package in quicksand optional-dependencies via uv add."""
    quicksand_pyproject = repo_root / "packages" / "quicksand" / "pyproject.toml"
    if not quicksand_pyproject.exists():
        return False

    # Add as its own optional extra
    result = subprocess.run(
        ["uv", "add", "--optional", pkg_name, pkg_name, "--package", "quicksand", "--frozen"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    if result.returncode != 0:
        print(
            f"Warning: failed to register optional dep '{pkg_name}': {result.stderr}",
            file=sys.stderr,
        )
        return False

    # Add to the 'all' extras
    result = subprocess.run(
        ["uv", "add", "--optional", "all", pkg_name, "--package", "quicksand", "--frozen"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    if result.returncode != 0:
        print(f"Warning: failed to add to 'all' extras: {result.stderr}", file=sys.stderr)

    return True
