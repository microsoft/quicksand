#!/usr/bin/env python3
"""Re-tag wheel files for different platforms.

VM image wheels contain qcow2 files that are architecture-specific but OS-agnostic.
This script creates copies with platform tags for macOS and Windows so they can
be installed on any host. Output is flat (no subdirectories).

Usage:
    python scripts/retag_wheels.py dist/
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

WHEEL_PATTERN = re.compile(
    r"^(?P<name>.+?)-(?P<version>.+?)-(?P<python>\w+)-(?P<abi>\w+)-(?P<platform>.+)\.whl$"
)

# Architecture (extracted from the platform tag suffix) -> target platform tags.
# The source OS is irrelevant — these wheels contain OS-agnostic qcow2 images.
_ARCH_RE = re.compile(r"(?:x86_64|aarch64|arm64|amd64)$")

ARCH_TARGETS = {
    "x86_64": ["linux_x86_64", "macosx_10_13_x86_64", "win_amd64"],
    "amd64": ["linux_x86_64", "macosx_10_13_x86_64", "win_amd64"],
    "aarch64": ["linux_aarch64", "macosx_11_0_arm64"],
    "arm64": ["linux_aarch64", "macosx_11_0_arm64"],
}


SKIP_RETAG = {"quicksand_qemu"}


def retag_wheel(wheel_path: Path, output_dir: Path) -> list[Path]:
    """Re-tag a wheel for multiple platforms. Returns created file paths."""
    match = WHEEL_PATTERN.match(wheel_path.name)
    if not match:
        print(f"  Skipping (not a valid wheel): {wheel_path.name}", file=sys.stderr)
        return []

    parts = match.groupdict()

    if parts["name"] in SKIP_RETAG:
        print(f"  Skipping (natively built on all platforms): {wheel_path.name}")
        return [wheel_path]
    source_platform = parts["platform"]
    arch_match = _ARCH_RE.search(source_platform)

    if not arch_match:
        print(f"  Skipping (unknown arch in platform {source_platform}): {wheel_path.name}")
        dest = output_dir / wheel_path.name
        if dest.resolve() != wheel_path.resolve():
            shutil.copy2(wheel_path, dest)
        return [dest]

    targets = ARCH_TARGETS[arch_match.group()]

    created = []
    for target_platform in targets:
        new_name = f"{parts['name']}-{parts['version']}-py3-none-{target_platform}.whl"
        dest = output_dir / new_name
        if dest.resolve() != wheel_path.resolve():
            shutil.copy2(wheel_path, dest)
        created.append(dest)
        print(f"  -> {new_name}")

    return created


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-tag wheels for multiple platforms")
    parser.add_argument("wheels", nargs="+", type=Path, help="Wheel files or directories")
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="Output directory (default: same as input)"
    )
    args = parser.parse_args()

    wheel_files: list[Path] = []
    for path in args.wheels:
        if path.is_dir():
            wheel_files.extend(path.glob("*.whl"))
        elif path.suffix == ".whl":
            wheel_files.append(path)

    if not wheel_files:
        print("No wheel files found", file=sys.stderr)
        return 1

    output_dir = args.output or wheel_files[0].parent
    output_dir.mkdir(parents=True, exist_ok=True)

    all_created: list[Path] = []
    for wheel in sorted(wheel_files):
        print(f"Processing: {wheel.name}")
        all_created.extend(retag_wheel(wheel, output_dir))

    print(f"\nCreated {len(all_created)} wheels:")
    for w in sorted(all_created):
        size_mb = w.stat().st_size / (1024 * 1024)
        print(f"  {w.name}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
