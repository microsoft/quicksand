"""Save/load example for quicksand.

save() saves the sandbox disk state (filesystem overlay) to a directory
so you can skip setup work on subsequent runs. The VM keeps running
after save() returns. Use Sandbox(image=str(save_path)) to restore.
"""

import asyncio
from pathlib import Path

from quicksand import Sandbox, UbuntuSandbox

# Saves are stored as directories under .quicksand/
save_path = Path(".quicksand/sandboxes/my-save")


async def main():
    # Check if we have a saved directory
    if save_path.exists():
        # Load from save - skips setup work
        print("Loading from save...")
        sandbox = Sandbox(image=str(save_path))
        await sandbox.start()
    else:
        # First run - do setup and save
        print("First run - setting up...")
        sandbox = UbuntuSandbox()
        await sandbox.start()

        # Do expensive setup work
        await sandbox.execute("apt-get update && apt-get install -y python3-pip")
        await sandbox.execute("pip install requests --break-system-packages")

        # Save for next time (writes to .quicksand/sandboxes/my-save/)
        info = await sandbox.save("my-save")
        print(f"Saved: {info.version} bytes")

    # Use the sandbox (setup already done if loaded from save)
    result = await sandbox.execute("python3 -c 'import requests; print(requests.__version__)'")
    print(f"Requests version: {result.stdout}")

    await sandbox.stop()


asyncio.run(main())
