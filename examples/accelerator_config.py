"""Hardware acceleration configuration example for quicksand.

By default, quicksand auto-detects the best accelerator:
- Linux: KVM
- macOS: HVF (Hypervisor.framework)
- Windows: WHPX (Windows Hypervisor Platform)

Falls back to TCG (software emulation) when hardware accel unavailable.

Performance optimizations are applied automatically based on platform:
- Linux x86_64 + KVM: Uses 'microvm' machine type (~4x faster boot)
- Linux: Uses io_uring for disk I/O (~50% lower latency)
- All platforms: Uses IOThreads for better concurrent disk I/O
"""

import asyncio
import platform

from quicksand import Accelerator, UbuntuSandbox

# Auto-detect accelerator (default behavior)
# This will use KVM on Linux, HVF on macOS, WHPX on Windows
accel = "auto"

# Alternatively, force software emulation (slower but always works)
accel = None

# Or force a specific accelerator based on platform
if platform.system() == "Linux":
    # KVM on Linux (requires /dev/kvm access)
    accel = Accelerator.KVM
elif platform.system() == "Darwin":
    # HVF on macOS (Hypervisor.framework)
    accel = Accelerator.HVF
elif platform.system() == "Windows":
    # WHPX on Windows (requires Hyper-V enabled)
    accel = Accelerator.WHPX


async def main():
    async with UbuntuSandbox(accel=accel) as sb:
        result = await sb.execute("uname -a")
        print(f"Kernel: {result.stdout}")


asyncio.run(main())
