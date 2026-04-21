"""Save/load stress test with large file writes.

Installs Chromium in a sandbox, saves the state, then loads it in a new
sandbox and verifies the installation survived.

This exercises the block flush path: without flushing QEMU's write cache
before the snapshot pivot, large recent writes can be lost in the save.

Supports both Alpine (fast, ~60s) and Ubuntu Desktop (heavier, ~3min).
Usage:
    python examples/save_load_large.py              # Alpine (default)
    python examples/save_load_large.py --desktop    # Ubuntu Desktop
"""

import argparse
import asyncio
import shutil
import tempfile
from pathlib import Path

from quicksand import NetworkMode, Sandbox

SAVE_PATH = Path(tempfile.mkdtemp(prefix="quicksand-save-")) / "chromium-env.tar"


def _stream(**kwargs):
    return dict(
        on_stdout=lambda s: print(s, end="", flush=True),
        on_stderr=lambda s: print(s, end="", flush=True),
        **kwargs,
    )


def run_alpine():
    from quicksand_alpine import AlpineSandbox

    sandbox_cls = AlpineSandbox
    sandbox_kwargs = dict(
        network_mode=NetworkMode.FULL,
        memory="1G",
        disk_size="4G",
    )
    install_cmd = "apk add --no-cache chromium"
    version_cmd = "chromium --version"
    size_cmd = "du -sh /usr/lib/chromium/"
    binary_check = "ls -la /usr/lib/chromium/chromium && echo OK || echo MISSING"
    return sandbox_cls, sandbox_kwargs, install_cmd, version_cmd, size_cmd, binary_check


def run_desktop():
    from quicksand_ubuntu_desktop import UbuntuDesktopSandbox

    sandbox_cls = UbuntuDesktopSandbox
    sandbox_kwargs = dict(
        network_mode=NetworkMode.FULL,
        memory="4G",
        disk_size="8G",
    )
    # Use imagemagick -- a real deb package with substantial size (~200MB installed)
    # (chromium-browser on Ubuntu 24.04 is a snap wrapper, not a real deb)
    install_cmd = (
        "sudo apt-get update -qq && "
        "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq imagemagick"
    )
    version_cmd = "convert --version | head -1"
    size_cmd = (
        "dpkg-query -W --showformat='${Installed-Size}' imagemagick"
        " | awk '{printf \"%.0f MB\\n\", $1/1024}'"
    )
    binary_check = "which convert && echo OK || echo MISSING"
    return sandbox_cls, sandbox_kwargs, install_cmd, version_cmd, size_cmd, binary_check


async def main():
    parser = argparse.ArgumentParser(description="Save/load stress test with Chromium")
    parser.add_argument(
        "--desktop", action="store_true", help="Use Ubuntu Desktop instead of Alpine"
    )
    args = parser.parse_args()

    if args.desktop:
        print("Using Ubuntu Desktop image")
        sandbox_cls, sandbox_kwargs, install_cmd, version_cmd, size_cmd, binary_check = (
            run_desktop()
        )
    else:
        print("Using Alpine image (use --desktop for Ubuntu Desktop)")
        sandbox_cls, sandbox_kwargs, install_cmd, version_cmd, size_cmd, binary_check = run_alpine()

    # --- Phase 1: Install Chromium and save ---
    print("\n=== Phase 1: Install Chromium and save ===")
    async with sandbox_cls(**sandbox_kwargs) as sb:
        print("Installing chromium (this may take a few minutes)...")
        result = await sb.execute(install_cmd, timeout=600.0, **_stream())
        print()
        if result.exit_code != 0:
            print(f"Install failed (exit={result.exit_code})")
            raise SystemExit(1)

        result = await sb.execute(version_cmd)
        print(f"Installed: {result.stdout.strip()}")

        result = await sb.execute(size_cmd)
        print(f"Size: {result.stdout.strip()}")

        print(f"Saving to {SAVE_PATH}...")
        await sb.save(str(SAVE_PATH))
        print(f"Save complete ({SAVE_PATH.stat().st_size / 1024 / 1024:.1f} MB)")

    # --- Phase 2: Load and verify ---
    print("\n=== Phase 2: Load saved environment and verify ===")
    async with Sandbox(
        image=str(SAVE_PATH),
        memory=sandbox_kwargs.get("memory"),
        cpus=sandbox_kwargs.get("cpus"),
    ) as sb:
        result = await sb.execute(version_cmd)
        version = result.stdout.strip()
        print(f"After load: {version}")

        if result.exit_code == 0 and version:
            print("SUCCESS: Installation survived save/load")
        else:
            print("FAILURE: Package not found after load!")
            raise SystemExit(1)

        result = await sb.execute(binary_check)
        print(f"Binary check: {result.stdout.strip()}")

    # Cleanup
    shutil.rmtree(SAVE_PATH.parent, ignore_errors=True)
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
