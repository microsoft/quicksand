# Performance

*See [Under the Hood: Performance](../under-the-hood/08-performance.md) for the specific QEMU flags behind each optimization.*

## Boot time

Measured with `quicksand benchmark -n 5` on macOS ARM64 (Apple M3 Max, HVF):

| Image | Boot p50 | Boot p95 |
|-------|----------|----------|
| `alpine` | 0.37s | 0.45s |
| `ubuntu` | 0.88s | 0.91s |
| `alpine-desktop` | 0.47s | 0.54s |
| `ubuntu-desktop` | 0.90s | 0.96s |
| `quicksand-agent` | 0.91s | 0.92s |
| `quicksand-cua` | 0.85s | 1.01s |

Boot time is measured from `start()` to sandbox ready (agent connected, mounts configured). The biggest factor is hardware acceleration (TCG is 10-50x slower).

## CPU performance

With hardware acceleration (KVM, HVF, WHPX), the guest runs at near-native speed. Code compiles, tests run, and browsers load at essentially the same speed as the host.

Without hardware acceleration (TCG), everything is 10-50x slower. TCG is a fallback for environments that don't support virtualization, such as CI containers and nested VMs without passthrough. If your workload is CPU-intensive, hardware acceleration is essential.

Quicksand auto-detects the best accelerator for the current platform. You can check what's in use:

```python
async with Sandbox(image="ubuntu") as sb:
    print(sb.accelerator)  # Accelerator.KVM, HVF, WHPX, or TCG
```

## Disk I/O

Quicksand uses several optimizations for disk performance:

- **IOThreads.** Disk I/O runs on a dedicated thread, not QEMU's main loop.
- **io_uring** (Linux only). Modern async I/O kernel API, with ~50% lower latency.
- **virtio-blk.** Paravirtualized disk device, much faster than emulated SATA.
- **qcow2 overlays.** Thin files that only store changes, so sandbox creation is instant.

## Snapshot performance

| Operation | Typical time |
|-----------|-------------|
| `checkpoint()` | ~1-2 seconds (saves RAM + disk state) |
| `revert()` | ~1-2 seconds (restores RAM + disk state) |
| `save()` | ~1-5 seconds (copies overlay chain to disk) |
| `save(compress=True)` | ~10-30 seconds (compresses overlays) |

`checkpoint()` and `revert()` use QEMU's built-in snapshot mechanism. `save()` freezes the current overlay and copies it. The VM never pauses.

## Memory

Each sandbox uses as much host RAM as configured:

```python
Sandbox(image="ubuntu", memory="512M")  # 512MB of host RAM
Sandbox(image="ubuntu", memory="2G")    # 2GB of host RAM
```

Plus ~50-100MB of overhead for the QEMU process itself. Multiple sandboxes run concurrently but each gets its own RAM allocation.

## Platform comparison

| | Linux + KVM | macOS + HVF | Windows + WHPX | Any + TCG |
|---|---|---|---|---|
| Boot (Alpine) | < 0.5s | 0.37s | ~1-2s | ~10-30s |
| Boot (Ubuntu) | < 1s | 0.88s | ~2-3s | ~10-30s |
| CPU performance | Near-native | Near-native | Near-native | 10-50x slower |
| Disk AIO | io_uring | threads | threads | threads |
| Machine type | Q35/VIRT | Q35/VIRT | Q35 | Q35/VIRT |

## Tips

- **Use the smallest image that works.** Alpine (~75MB) boots faster than Ubuntu (~340MB).
- **Use `save()` to avoid repeated setup.** Install packages once, save, and resume from the save.
- **Avoid TCG for CPU-heavy workloads.** If you're in CI, ensure KVM is available (`/dev/kvm`).
- **Use `disk_size` sparingly.** Expanding the disk adds time to boot.
- **Concurrent sandboxes share base images.** Running 10 Ubuntu sandboxes doesn't use 10x the disk space, because overlays only store changes.
