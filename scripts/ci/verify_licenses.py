#!/usr/bin/env python3
"""Verify GPL license files are present in quicksand-qemu wheels.

quicksand-qemu bundles GPL-licensed QEMU binaries. This script verifies
that the required license files are present for GPL compliance.

Usage:
    python scripts/verify_licenses.py dist/*.whl
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

# Required license files by platform
REQUIRED_FILES: dict[str, list[str]] = {
    "darwin": ["COPYING", "LICENSE"],
    "linux": ["copyright"],
    "win": ["COPYING"],
}


def _detect_platform(wheel_name: str) -> str | None:
    """Map a wheel filename to a platform key."""
    name = wheel_name.lower()
    if "macosx" in name:
        return "darwin"
    if "linux" in name:
        return "linux"
    if "win" in name:
        return "win"
    return None


def verify_wheel(wheel_path: Path) -> bool:
    """Check that required license files exist in the wheel."""
    plat = _detect_platform(wheel_path.name)
    if plat is None:
        print(f"  FAIL: Unknown platform in {wheel_path.name}")
        return False

    required = REQUIRED_FILES[plat]
    ok = True

    with zipfile.ZipFile(wheel_path) as zf:
        names = zf.namelist()
        license_files = [n for n in names if "/licenses/" in n]

        print(f"\n{wheel_path.name}:")
        print(f"  Platform: {plat}")
        print(f"  License files found: {[Path(f).name for f in license_files]}")

        # --- Check required license texts ---------------------------------
        missing = []
        for req in required:
            if not any(req in f for f in license_files):
                missing.append(req)

        if missing:
            print(f"  FAIL: Missing required license files: {missing}")
            ok = False
        else:
            print("  OK: All required license files present")

        # --- Check SOURCES.md ---------------------------------------------
        sources_files = [n for n in license_files if n.endswith("SOURCES.md")]
        if not sources_files:
            print("  FAIL: SOURCES.md not found in licenses/")
            ok = False
        else:
            content = zf.read(sources_files[0]).decode()
            ok = _verify_sources_md(content) and ok

    return ok


def _verify_sources_md(content: str) -> bool:
    """Validate that SOURCES.md is well-formed and non-trivial."""
    ok = True

    # Must have the header row and at least QEMU
    lines = [ln for ln in content.splitlines() if ln.startswith("|") and "---" not in ln]
    # First line is header, rest are data rows
    data_rows = lines[1:] if len(lines) > 1 else []

    if not data_rows:
        print("  FAIL: SOURCES.md contains no component entries")
        return False

    # QEMU itself must be listed
    if not any("QEMU" in row for row in data_rows):
        print("  FAIL: SOURCES.md does not list QEMU")
        ok = False

    # Every row must have a source URL (non-empty 4th column)
    empty_source = []
    for row in data_rows:
        cols = [c.strip() for c in row.split("|")]
        # cols[0] is empty (before first |), cols are: '', name, ver, lic, src, ''
        if len(cols) >= 5 and not cols[4]:
            empty_source.append(cols[1])

    if empty_source:
        print(f"  FAIL: SOURCES.md entries missing source URL: {empty_source}")
        ok = False

    if ok:
        print(f"  OK: SOURCES.md lists {len(data_rows)} component(s) with source URLs")

    return ok


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: verify_licenses.py <wheel> [wheel ...]")
        return 1

    wheels = [Path(p) for p in sys.argv[1:]]
    qemu_wheels = [w for w in wheels if "quicksand_qemu" in w.name or "quicksand-qemu" in w.name]

    if not qemu_wheels:
        print("No quicksand-qemu wheels found to verify.")
        return 0

    print(f"Verifying {len(qemu_wheels)} quicksand-qemu wheel(s)...")
    results = [verify_wheel(w) for w in qemu_wheels]

    print(f"\n{'=' * 60}")
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} wheels passed license verification")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
