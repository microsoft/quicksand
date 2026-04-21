"""Custom image example for quicksand.

Example for Persona 2: "I Want My Own Image"

Build a custom image from a Dockerfile using quicksand-image-tools CLI.
Install with: pip install 'quicksand[dev,ubuntu]'

Prerequisites (run once):
    # Initialize build directory (creates Dockerfile, builds base if needed)
    quicksand-image-tools init ./my-image ubuntu

Then customize ./my-image/Dockerfile:
    FROM quicksand/ubuntu-base
    RUN apt-get update && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

And build:
    quicksand-image-tools build-image ./my-image/Dockerfile -o my-image.qcow2
"""

import asyncio

from quicksand import Sandbox


async def main():
    # Use a pre-built custom image
    async with Sandbox(image="my-image", memory="2G", cpus=4) as sb:
        # Node.js is available because we installed it in the Dockerfile
        result = await sb.execute("node --version")
        print(f"Node version: {result.stdout}")

        result = await sb.execute("python3 --version")
        print(f"Python version: {result.stdout}")


asyncio.run(main())
