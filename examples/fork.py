"""fork() example for quicksand.

fork() branches a running sandbox into a sibling. Both sandboxes start from
the same on-disk state at the moment of the fork; their disks diverge from
that point. The fork is disk-only — the child boots fresh from the frozen
disk state. No RAM is carried over.

Use cases:
  - Run two experiments in parallel from a shared baseline
  - Try a risky operation in a child while the parent stays clean
  - A/B test configurations against the same starting environment

The returned Sandbox is unstarted; boot it with ``async with`` or ``.start()``.
Parent and child have independent lifetimes — either can stop first.
"""

import asyncio

from quicksand import UbuntuSandbox


async def main():
    print("Booting parent sandbox")
    async with UbuntuSandbox() as parent:
        # Set up some shared baseline state in the parent.
        await parent.execute("echo 'shared baseline' > /root/baseline.txt")
        print("Parent baseline set")

        # Fork the parent. The child inherits the disk state at this moment
        # but writes to its own overlay from here on. fork() pivots the
        # parent onto a new active overlay and returns the child unstarted.
        child = await parent.fork()
        print("Forked")

        # Boot the child alongside the parent and run them in parallel.
        async with child:
            # Both sandboxes see the pre-fork baseline.
            for label, sb in (("parent", parent), ("child", child)):
                result = await sb.execute("cat /root/baseline.txt")
                print(f"  {label} sees baseline: {result.stdout.strip()}")

            # Each sandbox makes a different decision. The disks diverge.
            await parent.execute("echo 'parent path' > /root/decision.txt")
            await child.execute("echo 'child path' > /root/decision.txt")

            for label, sb in (("parent", parent), ("child", child)):
                result = await sb.execute("cat /root/decision.txt")
                print(f"  {label} wrote: {result.stdout.strip()}")

            # Writes are isolated: parent doesn't see child's later writes
            # and vice versa.
            await child.execute("touch /root/child-only.txt")
            r = await parent.execute("test -f /root/child-only.txt && echo present || echo absent")
            print(f"  parent sees child-only file: {r.stdout.strip()}")

        # Child has stopped; parent is still running normally.
        print("Child stopped — parent continues")
        result = await parent.execute("cat /root/decision.txt")
        print(f"  parent's final state: {result.stdout.strip()}")


asyncio.run(main())
