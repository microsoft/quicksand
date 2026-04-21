#!/usr/bin/env python3
"""Clean build artifacts from the quicksand monorepo."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def main() -> int:
    print("Cleaning build artifacts...")

    # Remove dist directories
    _rmtree(REPO_ROOT / "dist")
    for pkg in (REPO_ROOT / "packages").iterdir():
        if pkg.is_dir():
            _rmtree(pkg / "dist")

    # Remove egg-info and __pycache__
    for pattern in ["**/*.egg-info", "**/__pycache__"]:
        for path in REPO_ROOT.glob(pattern):
            _rmtree(path)

    # Remove built VM images (keep directory structure)
    for ext in ["*.qcow2", "*.initrd", "*.kernel", "manifest.json"]:
        for path in REPO_ROOT.glob(f"packages/**/images/**/{ext}"):
            path.unlink(missing_ok=True)
            print(f"  Removed: {path.relative_to(REPO_ROOT)}")
        for path in REPO_ROOT.glob(f"packages/**/images/{ext}"):
            path.unlink(missing_ok=True)
            print(f"  Removed: {path.relative_to(REPO_ROOT)}")

    # Remove bundled QEMU binaries (keep .gitkeep)
    bin_dir = REPO_ROOT / "packages/quicksand-core/quicksand_core/bin"
    if bin_dir.exists():
        for item in bin_dir.iterdir():
            if item.name != ".gitkeep":
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                print(f"  Removed: {item.relative_to(REPO_ROOT)}")

    # Remove temp quicksand directories
    tmp = Path(tempfile.gettempdir())
    for path in tmp.glob("quicksand-*"):
        _rmtree(path)
        print(f"  Removed: {path}")

    print("Clean complete.")
    return 0


def _rmtree(path: Path) -> None:
    """Remove a directory tree if it exists."""
    if path.exists():
        shutil.rmtree(path)


if __name__ == "__main__":
    sys.exit(main())
