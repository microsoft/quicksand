"""Scaffold a new base image package by copying this template.

Copies the quicksand-base-scaffold package, renames everything to
the target name, and resets versions.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import tomlkit
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import Table

TEMPLATE_DIR = Path(__file__).resolve().parent.parent

_ARTIFACT_EXTENSIONS = {".qcow2", ".kernel", ".initrd"}


def _to_under(name: str) -> str:
    return name.replace("-", "_")


def _to_title(name: str) -> str:
    return "".join(p.capitalize() for p in name.replace("-", " ").split())


def _clean_pyproject(pyproject: Path) -> None:
    """Strip monorepo-only config from a scaffolded pyproject.toml."""
    doc = tomlkit.parse(pyproject.read_text())
    # Remove [tool.uv.sources] (workspace refs don't apply outside monorepo)
    if "tool" in doc:
        tool = doc["tool"]
        assert isinstance(tool, (Table, OutOfOrderTableProxy))
        if "uv" in tool:
            uv = tool["uv"]
            assert isinstance(uv, (Table, OutOfOrderTableProxy))
            if "sources" in uv:
                del uv["sources"]
            if not uv:
                del tool["uv"]
        if not tool:
            del doc["tool"]
    # Reset version, remove scaffold entry point, strip scaffold-only deps
    project = doc["project"]
    assert isinstance(project, (Table, OutOfOrderTableProxy))
    project["version"] = "0.1.0"
    if "scripts" in project:
        del project["scripts"]
    deps = list(project.get("dependencies", []))
    project["dependencies"] = [d for d in deps if "tomlkit" not in str(d)]
    pyproject.write_text(tomlkit.dumps(doc))


def _is_text_file(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        return b"\x00" not in chunk
    except OSError:
        return False


def _ignore_artifacts(_directory: str, files: list[str]) -> set[str]:
    ignored = set()
    for f in files:
        if Path(f).suffix in _ARTIFACT_EXTENSIONS:
            ignored.add(f)
        if f in ("__pycache__", ".pytest_cache", "scaffold.py") or f.endswith(".egg-info"):
            ignored.add(f)
    return ignored


def scaffold(
    name: str,
    output_dir: Path | None = None,
) -> None:
    """Scaffold a new base image package.

    Args:
        name: Package name (e.g. ``quicksand-mylinux``).
        output_dir: Where to create the package. Defaults to ``./<name>``.
    """
    name = name.lower()
    if "/" in name or "\\" in name:
        raise ValueError("name must be a plain package name, not a path")

    if output_dir is None:
        output_dir = Path(name)

    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"{output_dir} already exists and is non-empty")

    output_dir.parent.mkdir(parents=True, exist_ok=True)

    # Copy template, excluding artifacts and scaffold script
    shutil.copytree(TEMPLATE_DIR, output_dir, ignore=_ignore_artifacts)

    # Rename module directory
    old_module = output_dir / "quicksand_base_scaffold"
    new_module = output_dir / _to_under(name)
    if old_module.exists():
        old_module.rename(new_module)

    # String replacements
    old_name = "quicksand-base-scaffold"
    title = _to_title(name)
    # Avoid QuicksandBaseScaffoldSandbox -> MySandboxSandbox
    if title.endswith("Sandbox"):
        title = title[: -len("Sandbox")]
    replacements = [
        ("quicksand_base_scaffold", _to_under(name)),
        (old_name, name),
        ("QuicksandBaseScaffold", title),
    ]

    for filepath in output_dir.rglob("*"):
        if not filepath.is_file() or not _is_text_file(filepath):
            continue
        content = filepath.read_text()
        original = content
        for old, new in replacements:
            content = content.replace(old, new)
        if content != original:
            filepath.write_text(content)

    # Clean up pyproject.toml for standalone use
    pyproject = output_dir / "pyproject.toml"
    if pyproject.exists():
        _clean_pyproject(pyproject)

    # Reset README
    readme = output_dir / "README.md"
    readme.write_text(f"# {name}\n\n```bash\npip install {name}\n```\n")

    print(f"Scaffolded base image package: {output_dir}")
    print()
    print("Next steps:")
    print(f"  1. Set DISTRO_VERSION in {_to_under(name)}/__init__.py")
    print(f"  2. Edit {_to_under(name)}/docker/Dockerfile")
    print("  3. pip install -e . && uv build")
    print()
    print("The guest agent source is copied into docker/agent/ automatically at build time.")


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="quicksand-base-scaffold",
        description="Scaffold a new base image package from a template",
    )
    parser.add_argument("name", help="Package name (e.g. quicksand-mylinux)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: ./<name>)",
    )
    args = parser.parse_args()

    try:
        scaffold(name=args.name, output_dir=args.output_dir)
    except (ValueError, FileExistsError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
