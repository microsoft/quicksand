"""Custom configuration example for quicksand.

Example with custom configuration using UbuntuSandbox.
"""

import asyncio

from quicksand import Mount, NetworkMode, PortForward, UbuntuSandbox


async def main():
    async with UbuntuSandbox(
        memory="1G",
        cpus=2,
        # Mount a host directory into the sandbox
        mounts=[
            Mount("/tmp/host-data", "/mnt/data", readonly=True),
        ],
        # Forward ports from guest to host
        port_forwards=[PortForward(host=8080, guest=80)],
        # Block internet access (recommended for untrusted code)
        network_mode=NetworkMode.MOUNTS_ONLY,
        # Boot timeout
        boot_timeout=120.0,
    ) as sb:
        result = await sb.execute("cat /etc/os-release")
        print(f"OS info:\n{result.stdout}")

        # Access mounted directory
        result = await sb.execute("ls -la /mnt/data")
        print(f"Mounted data:\n{result.stdout}")


asyncio.run(main())
