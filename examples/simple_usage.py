"""Simple usage example for quicksand.

Example for Persona 1: "Just Give Me a Sandbox"

This shows the simplest possible usage with the bundled Ubuntu image.
Install with: pip install quicksand[ubuntu]
"""

import asyncio

from quicksand import UbuntuSandbox


async def main():
    # Uses the bundled Ubuntu image - no downloads or config needed
    async with UbuntuSandbox() as sb:
        # Execute shell commands
        result = await sb.execute("ls -la /")
        print(f"Directory listing:\n{result.stdout}")

        # Run multiple commands
        result = await sb.execute("echo 'hello world' > /tmp/test.txt && cat /tmp/test.txt")
        print(f"File content: {result.stdout}")

        # Check exit codes
        result = await sb.execute("exit 42")
        print(f"Exit code: {result.exit_code}")

        # Run Python (pre-installed in Ubuntu image)
        result = await sb.execute("python3 -c \"print('Hello from Python!')\"")
        print(f"Python output: {result.stdout}")


asyncio.run(main())
