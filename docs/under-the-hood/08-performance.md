# Performance: Under the Hood

Companion to [Performance](../user-guide/08-performance.md).

## Boot time: machine type

The machine type affects boot time:

| Machine | Boot time | Why |
|---|---|---|
| `q35` / `virt` | ~2-3 seconds | Full chipset emulation, firmware device scan |

Direct kernel boot (see below) is the primary boot-time optimization, skipping BIOS/UEFI entirely.

## Boot time: direct kernel boot

```bash
-kernel /path/to/vmlinuz \
-initrd /path/to/initrd.img \
-append "root=/dev/vda rw rootfstype=ext4 quiet loglevel=0 ..."
```

Passing the kernel directly to QEMU skips BIOS/UEFI firmware entirely. Combined with `quiet loglevel=0`, the kernel suppresses most boot messages, and `raid=noautodetect` skips MD RAID scanning.

## CPU acceleration

```bash
# Hardware acceleration (near-native):
-accel kvm -cpu host    # Linux
-accel hvf -cpu host    # macOS
-accel whpx -cpu host   # Windows

# Software emulation (10-50x slower):
-accel tcg -cpu max
```

`-cpu host` exposes the real CPU features to the guest. The hypervisor runs guest code directly on the hardware. `-cpu max` (TCG) emulates the most capable CPU QEMU supports, but every instruction is translated in software.

## Disk I/O

```bash
-object iothread,id=iothread0 \
-drive file=overlay.qcow2,format=qcow2,if=none,id=drive0,cache=writethrough,discard=unmap,detect-zeroes=unmap,aio=io_uring \
-device virtio-blk-device,drive=drive0,iothread=iothread0
```

| Flag | Impact |
|---|---|
| `iothread` | Disk I/O runs on a dedicated thread, not QEMU's main loop. Prevents disk operations from blocking CPU emulation or display rendering. |
| `aio=io_uring` | Linux's modern async I/O API (kernel 5.8+). ~50% lower disk latency vs the default thread-pool AIO. Only on Linux; macOS/Windows use QEMU's default (threads). |
| `virtio-blk` | Paravirtualized block device — the guest knows it's virtual and uses an optimized driver. Much faster than emulated SATA/IDE. |
| `cache=writethrough` | Writes hit disk immediately. Slower than `writeback` but safe for snapshots — no dirty cache to lose on pivot. |
| `discard=unmap` | Guest TRIM commands release qcow2 clusters back to the host filesystem. Keeps overlays from growing indefinitely. |
| `detect-zeroes=unmap` | Writing a block of zeroes is treated as a discard. Common after `fstrim` or file deletion. |

## Snapshot performance

**Checkpoints** (`savevm`/`loadvm`) capture full VM state inside the qcow2 file:
- Time is proportional to RAM size (must write all of RAM to disk)
- ~1-2 seconds for 512MB-2GB

**Saves** (`blockdev-snapshot-sync`) pivot the overlay chain:
- The pivot itself is instant (just changes where writes go)
- The copy step (writing frozen overlays to the save directory) depends on overlay size
- `compress=True` runs `qemu-img convert` which recompresses. This takes 10-30 seconds depending on data

The `fstrim` + `qemu-io flush` before a save ensures the overlay is as small as possible (freed blocks are reclaimed) and fully consistent on disk.
