"""Load the saved AIF agent sandboxes and verify everything works via bare commands."""

import asyncio
import sys
from pathlib import Path

from quicksand import UbuntuSandbox

SAVE_DIR = Path(__file__).parent


async def run(sb, cmd, timeout=30.0):
    print(f">>> {cmd}")
    result = await sb.execute(cmd, timeout=timeout, shell="/bin/bash")
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.exit_code != 0:
        raise RuntimeError(f"FAIL (exit {result.exit_code}): {cmd}")
    return result


async def test_agent_harness():
    print("=" * 60)
    print("Testing aif-agent-harness")
    print("=" * 60)

    sb = UbuntuSandbox(image=str(SAVE_DIR / "aif-agent-harness.tar.gz"))
    async with sb:
        # Bare python/pip on PATH (no /opt/python/bin prefix)
        await run(sb, "python3 --version")
        await run(sb, "python --version")
        await run(sb, "pip --version")
        await run(sb, "pip3 --version")

        # Packages importable
        await run(sb, "python -c 'import requests; print(f\"requests {requests.__version__}\")'")
        await run(sb, "python -c 'import markitdown; print(\"markitdown OK\")'")

    print("\naif-agent-harness: PASSED\n")


async def test_cua_agent_harness():
    print("=" * 60)
    print("Testing aif-cua-agent-harness")
    print("=" * 60)

    sb = UbuntuSandbox(image=str(SAVE_DIR / "aif-cua-agent-harness.tar.gz"))
    async with sb:
        # Everything from layer 1 still works
        await run(sb, "python --version")
        await run(sb, "pip --version")
        await run(sb, "python -c 'import requests; import markitdown; print(\"layer 1 OK\")'")

        # Playwright importable and chromium installed
        await run(
            sb,
            "python -c 'from playwright.sync_api import sync_playwright; print(\"playwright OK\")'",
        )
        await run(sb, "playwright --version")

        # VNC stack present
        await run(sb, "which Xvfb")
        await run(sb, "which x11vnc")
        await run(sb, "which openbox")

    print("\naif-cua-agent-harness: PASSED\n")


async def main():
    await test_agent_harness()
    await test_cua_agent_harness()
    print("All tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
