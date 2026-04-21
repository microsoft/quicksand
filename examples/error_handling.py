"""Error handling example for quicksand.

Example showing error handling patterns.
"""

import asyncio

from quicksand import UbuntuSandbox


async def main():
    async with UbuntuSandbox() as sb:
        # Command that fails
        result = await sb.execute("exit 42")
        if result.exit_code != 0:
            print(f"Command failed with exit code: {result.exit_code}")

        # Command that produces stderr
        result = await sb.execute("ls /nonexistent 2>&1")
        if result.stderr:
            print(f"Error output: {result.stderr}")

        # Command with timeout
        result = await sb.execute("sleep 100", timeout=2)
        if result.exit_code == -1:
            print(f"Command timed out: {result.stderr}")


asyncio.run(main())
