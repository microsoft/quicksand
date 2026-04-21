"""Mount examples for quicksand.

Demonstrates three mount patterns:
1. Boot-time mounts via constructor (available immediately after start)
2. Dynamic hot-mounts on a running sandbox
3. Unmounting to free a share
"""

import asyncio
import os
import tempfile
from pathlib import Path

from quicksand import Mount, NetworkMode, UbuntuSandbox


async def main():
    # Create some host directories with test data
    with tempfile.TemporaryDirectory(prefix="quicksand-mount-") as tmpdir:
        # Directory available at boot
        init_dir = Path(tmpdir) / "init-data"
        init_dir.mkdir()
        (init_dir / "greeting.txt").write_text("Hello from the host (boot-time mount)!\n")

        # Directory mounted later at runtime
        hot_dir = Path(tmpdir) / "hot-data"
        hot_dir.mkdir()
        (hot_dir / "message.txt").write_text("Hello from the host (dynamic mount)!\n")

        # --- 1. Boot-time mount (declared in constructor) ---
        # CIFS mounts require FULL network (guest must reach host's SMB server)
        async with UbuntuSandbox(
            mounts=[Mount(host=str(init_dir), guest="/mnt/init", readonly=True)],
            network_mode=NetworkMode.FULL,
        ) as sb:
            # The boot-time mount is ready immediately
            result = await sb.execute("cat /mnt/init/greeting.txt")
            print(f"Boot-time mount: {result.stdout.strip()}")

            # --- 2. Dynamic hot-mount ---
            handle = await sb.mount(str(hot_dir), "/mnt/hot")

            result = await sb.execute("cat /mnt/hot/message.txt")
            print(f"Dynamic mount:   {result.stdout.strip()}")

            # Write from inside the guest (bidirectional)
            await sb.execute("echo 'Written by guest' > /mnt/hot/from_guest.txt")
            result = await sb.execute("cat /mnt/hot/from_guest.txt")
            print(f"Guest wrote:     {result.stdout.strip()}")
            print(f"  Host view: {os.listdir(hot_dir)} {(hot_dir / 'from_guest.txt').read_text()}")

            # Dynamic readonly mount
            ro_handle = await sb.mount(str(init_dir), "/mnt/hot-ro", readonly=True)
            result = await sb.execute("cat /mnt/hot-ro/greeting.txt")
            print(f"Readonly mount:  {result.stdout.strip()}")

            result = await sb.execute("touch /mnt/hot-ro/nope 2>&1; echo $?")
            print(f"Write attempt:   exit code {result.stdout.strip()} (expected non-zero)")
            await sb.unmount(ro_handle)

            # --- 3. Unmount ---
            await sb.unmount(handle)

            result = await sb.execute("ls /mnt/hot/")
            out = result.stdout.strip() or "(empty)"
            print(f"After unmount:   {out}")

            print("\nDone.")


asyncio.run(main())
