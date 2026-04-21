# Sandbox Lifecycle: Under the Hood

Companion to [Sandbox Lifecycle](../user-guide/02-sandbox-lifecycle.md).

## Sandbox kwargs to QEMU command

```python
Sandbox(
    image="ubuntu",
    memory="2G",
    cpus=4,
    accel="auto",
)
```

```bash
qemu-system-aarch64 \
  -nodefaults \
  -machine virt \
  -m 2G \
  -smp 4 \
  -L /path/to/share/qemu \
  -accel hvf \
  -cpu host \
  ...
```

| Config field | QEMU flag | Notes |
|---|---|---|
| `memory="2G"` | `-m 2G` | Passed through as-is |
| `cpus=4` | `-smp 4` | Number of virtual CPU cores |
| `accel="auto"` | `-accel hvf` (macOS) | Auto-detected per platform (see below) |
| `image="ubuntu"` | `-kernel`, `-initrd`, `-drive` | Resolved to image files on disk |

`-nodefaults` strips QEMU's built-in devices (floppy, CD-ROM, serial ports, etc.) so the command builds the VM from scratch with only what's needed.

## Machine type

The `-machine` flag is selected automatically based on architecture and platform:

| Architecture | Platform | Machine | Why |
|---|---|---|---|
| ARM64 | Any | `virt` | ARM's standard virtual machine type |
| x86_64 | Linux + KVM | `microvm` | Minimal machine, ~4x faster boot |
| x86_64 | macOS / Windows / TCG | `q35` | Full-featured Intel chipset |

`microvm` is only used on Linux x86_64 with KVM and when the firmware file `bios-microvm.bin` exists in the data directory. It skips PCI bus emulation entirely, using MMIO (memory-mapped) virtio devices instead.

## Hardware acceleration

```python
Sandbox(image="ubuntu")  # accel defaults to "auto"
```

Auto-detection selects the best available accelerator:

| Platform | Accelerator | QEMU flags | CPU performance |
|---|---|---|---|
| macOS | HVF | `-accel hvf -cpu host` | Near-native |
| Linux | KVM | `-accel kvm -cpu host` | Near-native |
| Windows | WHPX | `-accel whpx -cpu host` | Near-native |
| Any (fallback) | TCG | `-accel tcg -cpu max` | 10-50x slower |

With hardware acceleration (`-cpu host`), the guest runs on the real CPU. With TCG (`-cpu max`), QEMU translates every instruction in software.

On Windows inside a Hyper-V VM (nested virtualization), WHPX gets an extra flag: `-accel whpx,kernel-irqchip=off`.

## Device naming by machine type

The machine type determines how virtio devices are attached:

| Machine | Block device | Network device | Bus type |
|---|---|---|---|
| `virt` (ARM64) | `virtio-blk-device` | `virtio-net-device` | MMIO |
| `q35` (x86_64) | `virtio-blk-pci` | `virtio-net-pci` | PCI |
| `microvm` (x86_64+KVM) | `virtio-blk-device` | `virtio-net-device` | MMIO |

PCI devices have a small overhead from bus emulation. MMIO devices (used by `virt` and `microvm`) skip this entirely.

## Full assembled command

Putting it all together, here's what Quicksand generates for a typical macOS ARM64 headless sandbox:

```bash
qemu-system-aarch64 \
  -nodefaults \
  -machine virt \
  -m 512M \
  -smp 1 \
  -L /path/to/share/qemu \
  -object iothread,id=iothread0 \
  -drive file=overlay.qcow2,format=qcow2,if=none,id=drive0,cache=writethrough,discard=unmap,detect-zeroes=unmap \
  -device virtio-blk-device,drive=drive0,iothread=iothread0 \
  -serial stdio \
  -nographic -vga none \
  -cpu host \
  -accel hvf \
  -qmp tcp:127.0.0.1:4444,server,nowait \
  -kernel /path/to/ubuntu-24.04-arm64.kernel \
  -append "root=/dev/vda rw rootflags=rw console=ttyAMA0 rootfstype=ext4 quiet loglevel=0 raid=noautodetect quicksand_token=abc123 quicksand_port=8080" \
  -initrd /path/to/ubuntu-24.04-arm64.initrd \
  -netdev user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:8080-:8080,guestfwd=tcp:10.0.2.100:445-cmd:/path/to/smb_server \
  -device virtio-net-device,netdev=net0
```

Every flag here maps back to either a `Sandbox` kwarg or an automatic platform decision.
