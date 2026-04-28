#!/usr/bin/env python3
"""Remove wheels from a directory that don't match the current platform.

uv incorrectly prefers linux_aarch64 over macosx_arm64 when both are in
--find-links.  This script removes non-native platform-specific wheels
so only compatible ones remain.

Usage:
    python scripts/ci/filter_deps_platform.py deps/
"""

from __future__ import annotations

import platform
import re
import sys
from pathlib import Path

_WHEEL_RE = re.compile(r"^.+?-.+?-\w+-\w+-(?P<platform>.+)\.whl$")

# Map (system, machine) to platform tag prefixes that are compatible.
# Use exact platform tags (not just prefix) to avoid e.g. uv selecting
# macosx_10_13_x86_64 on arm64 via Rosetta compatibility.
_PLATFORM_TAGS: dict[tuple[str, str], list[str]] = {
    ("Darwin", "arm64"): ["macosx_11_0_arm64", "any"],
    ("Darwin", "x86_64"): ["macosx_10_13_x86_64", "any"],
    ("Linux", "x86_64"): ["linux_x86_64", "any"],
    ("Linux", "aarch64"): ["linux_aarch64", "any"],
    ("Windows", "AMD64"): ["win_amd64", "any"],
    ("Windows", "ARM64"): ["win_arm64", "any"],
}


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: filter_deps_platform.py <directory>", file=sys.stderr)
        return 1

    deps_dir = Path(sys.argv[1])
    if not deps_dir.exists():
        return 0

    system = platform.system()
    machine = platform.machine()
    tags = _PLATFORM_TAGS.get((system, machine))
    if not tags:
        print(f"Unknown platform ({system}, {machine}), skipping filter")
        return 0

    removed = 0
    for whl in sorted(deps_dir.glob("*.whl")):
        m = _WHEEL_RE.match(whl.name)
        if not m:
            continue
        plat = m.group("platform")
        if plat == "any":
            continue
        if plat not in tags:
            print(f"  Removing incompatible wheel: {whl.name}")
            whl.unlink()
            removed += 1

    if removed:
        print(f"Removed {removed} incompatible wheel(s) from {deps_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
