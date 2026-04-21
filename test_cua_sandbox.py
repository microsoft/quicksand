"""Load aif-cua-agent-sandbox-slim and verify chromium-vnc service starts."""

import asyncio
import random
import time

from quicksand import PortForward, Sandbox
from quicksand_core._types import NetworkMode

HOST_PORT = random.randint(20000, 50000)


async def main():
    sb = Sandbox(
        image="aif-cua-agent-sandbox-slim.tar.gz",
        port_forwards=[PortForward(host=HOST_PORT, guest=5901)],
        network_mode=NetworkMode.FULL,
    )
    async with sb:
        time.sleep(5)

        r = await sb.execute("rc-status", shell="/bin/sh")
        print(r.stdout)

        r = await sb.execute("ss -tlnp | grep 5901", shell="/bin/sh")
        print(f"VNC listening: {r.stdout.strip()}")

        print(f"\nConnect VNC viewer to localhost:{HOST_PORT}")
        print("Ctrl+C to stop")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


asyncio.run(main())
