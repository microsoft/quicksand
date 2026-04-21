"""checkpoint/revert example for quicksand.

checkpoint/revert are ephemeral in-session snapshots -- they save full VM state
(RAM + disk) inside the current overlay so you can roll back during a run.

Unlike save(), checkpoint snapshots:
  - Are not persisted to a save directory on disk
  - Are lost when the sandbox stops or when save() is called
  - Can be restored instantly (no reboot) via revert()

Use them for try/rollback patterns within a single session.
"""

import asyncio

from quicksand import NetworkMode, UbuntuSandbox


async def main():
    print("Starting sandbox")
    async with UbuntuSandbox(network_mode=NetworkMode.FULL) as sb:
        # Install a baseline package
        print("Sandbox ready")
        await sb.execute(
            "apt-get update -qq && apt-get install -y -qq curl",
            on_stdout=lambda s: print(s, end="", flush=True),
            on_stderr=lambda s: print(s, end="", flush=True),
        )
        print()
        print("Baseline ready")

        # Save a snapshot before a risky operation
        await sb.checkpoint("before-experiment")
        print("Snapshot saved")

        # Do something that might fail or produce unwanted side effects
        await sb.execute("echo 'experimental config' > /etc/myapp.conf")
        await sb.execute("touch /tmp/experiment-artifact")

        result = await sb.execute("cat /etc/myapp.conf")
        print(f"After experiment: {result.stdout.strip()}")

        # Roll back to the pre-experiment state
        await sb.revert("before-experiment")
        print("Rolled back")

        # Confirm the changes are gone
        result = await sb.execute("test -f /etc/myapp.conf && echo exists || echo gone")
        print(f"Config file: {result.stdout.strip()}")  # gone

        result = await sb.execute("test -f /tmp/experiment-artifact && echo exists || echo gone")
        print(f"Artifact: {result.stdout.strip()}")  # gone

        # Continue from the clean state
        await sb.execute("echo 'stable config' > /etc/myapp.conf")
        print("Clean run complete")

        # save() requires explicit opt-in to delete checkpoint snapshots
        # (they can't survive the overlay pivot anyway)
        await sb.save("after-stable-run", delete_checkpoints=True)
        print("Save complete (checkpoint snapshots cleared)")


asyncio.run(main())
