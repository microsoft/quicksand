"""Scaffold a new overlay package from this template.

Copies the entire package, renames everything, and removes this scaffold script
from the destination.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import tomlkit
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import Table


def _to_under(name: str) -> str:
    return name.replace("-", "_")


def _to_title(name: str) -> str:
    parts = _to_under(name).split("_")
    return "".join(p.capitalize() for p in parts)


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


def scaffold(
    name: str,
    base: str,
    output_dir: Path | None = None,
) -> None:
    """Scaffold a new overlay image package.

    Args:
        name: Package name (e.g. ``my-agent-sandbox``).
        base: Base image to overlay on (e.g. ``ubuntu``, ``alpine``).
        output_dir: Where to create the package. Defaults to ``./<name>``.
    """
    name = name.lower()
    if "/" in name or "\\" in name:
        raise ValueError("name must be a plain package name, not a path")

    base = base.lower()
    if output_dir is None:
        output_dir = Path(name)

    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"{output_dir} already exists and is non-empty")

    output_dir.parent.mkdir(parents=True, exist_ok=True)

    # Copy this package as template
    pkg_root = Path(__file__).parent.parent
    shutil.copytree(
        pkg_root,
        output_dir,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.egg-info"),
    )

    # Rename module directory
    old_module = output_dir / "quicksand_overlay_scaffold"
    new_module = output_dir / _to_under(name)
    if old_module.exists():
        old_module.rename(new_module)

    # String replacements -- package name + base image
    old_name = "quicksand-overlay-scaffold"
    title = _to_title(name)
    # Avoid QuicksandOverlayScaffoldSandbox -> MySandboxSandbox
    if title.endswith("Sandbox"):
        title = title[: -len("Sandbox")]
    base_pkg = f"quicksand-{base}"
    base_under = _to_under(base_pkg)
    base_title = _to_title(base)
    replacements = [
        ("quicksand_overlay_scaffold", _to_under(name)),
        (old_name, name),
        ("QuicksandOverlayScaffold", title),
    ]
    if base != "ubuntu":
        replacements += [
            ("quicksand_ubuntu", base_under),
            ("quicksand-ubuntu", base_pkg),
            ("UbuntuSandbox", f"{base_title}Sandbox"),
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

    # Remove scaffold script from destination
    scaffold_file = new_module / "scaffold.py"
    if scaffold_file.exists():
        scaffold_file.unlink()

    print(f"Scaffolded overlay package: {output_dir}")
    print("\nNext steps:")
    print("  1. Edit hatch_build.py -- add your install steps to _setup()")
    print("  2. pip install -e . && uv build")


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="quicksand-overlay-scaffold",
        description="Scaffold a new overlay image package from a template",
    )
    parser.add_argument("name", help="Package name (e.g. my-agent-sandbox)")
    parser.add_argument(
        "--base",
        required=True,
        help="Base image to overlay on (e.g. ubuntu, alpine)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: ./<name>)",
    )
    args = parser.parse_args()

    try:
        scaffold(name=args.name, base=args.base, output_dir=args.output_dir)
    except (ValueError, FileExistsError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
