# Installation: Under the Hood

Companion to [Installation](../user-guide/01-installation.md).

## `quicksand install qemu`

Installs a pre-built QEMU binary and its runtime dependencies into a Python package (`quicksand-qemu`). The package provides:

```
quicksand_qemu/bin/
├── qemu-system-aarch64      # or qemu-system-x86_64
├── qemu-img                 # Disk image manipulation tool
├── lib/                     # Shared libraries (libglib, libcrypto, etc.)
└── share/qemu/              # Firmware files (BIOS, EFI, keymaps)
```

At runtime, Quicksand locates this via `quicksand_qemu.get_bin_dir()` and passes the firmware directory to QEMU:

```bash
qemu-system-aarch64 ... -L /path/to/share/qemu
```

The `-L` flag tells QEMU where to find firmware and keymap files. Without it, QEMU falls back to compile-time defaults which may not exist.

If the bundled package isn't installed, Quicksand falls back to system QEMU found on `PATH`.

### Windows ARM64 and fat wheels

On Windows ARM64, most users run x86_64 Python through Microsoft's transparent emulation layer. The user may not even know they're running emulated Python — it's the default. This creates a conflict:

| | Python arch | pip accepts | QEMU needed |
|---|---|---|---|
| Real x86_64 Windows | x86_64 | `win_amd64` | x86_64 |
| ARM64 Windows, native Python | ARM64 | `win_arm64` | ARM64 |
| **ARM64 Windows, emulated Python** | **x86_64** | **`win_amd64`** | **ARM64** |

The third row is the problem. pip's wheel compatibility tags (PEP 425) match the Python interpreter's platform, not the hardware. An emulated x86_64 Python will only install `win_amd64` wheels — but the machine needs ARM64 QEMU binaries for hardware acceleration (WHPX).

This problem is unique to Windows. Linux doesn't transparently emulate x86_64 on ARM64, and macOS users on Apple Silicon install ARM64 Python by default (Rosetta 2 exists but isn't the default Python experience).

**Our solution: fat `win_amd64` wheels.** The `win_amd64` quicksand-qemu wheel ships both x86_64 and ARM64 QEMU binaries:

```
quicksand_qemu/bin/
├── x86_64/                       # x86_64 QEMU binaries
│   ├── qemu-system-x86_64.exe
│   ├── qemu-img.exe
│   └── ...
└── arm64/                        # ARM64 QEMU binaries
    ├── qemu-system-aarch64.exe
    ├── qemu-img.exe
    └── ...
```

At runtime, `_find_bundled_runtime()` reads the native CPU architecture from the Windows Registry (`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment\PROCESSOR_ARCHITECTURE`) — this always reports the true hardware regardless of process emulation — and selects the matching subdirectory. On a single-arch wheel (Linux, macOS, or `win_arm64`), binaries live directly in `bin/` with no subdirectories.

**Build pipeline:**
1. The Windows x64 CI runner builds `win_amd64.whl` with x64 QEMU in `bin/`
2. The Windows ARM64 CI runner builds `win_arm64.whl` with ARM64 QEMU in `bin/`
3. A `pre_release` hook (runs after all builds, before publishing) opens the `win_amd64` wheel, moves its binaries to `bin/x86_64/`, extracts ARM64 binaries from the `win_arm64` wheel into `bin/arm64/`, and rewrites the wheel
4. The `win_arm64` wheel ships unchanged (lean, single-arch) for the rare native ARM64 Python user

This approach doubles the `win_amd64` wheel size (~44 MB → ~80 MB) but ensures `pip install quicksand-qemu` delivers hardware-accelerated QEMU on every Windows configuration without user intervention.

### Platform wheel matrix

#### quicksand-qemu: build runners → wheels

quicksand-qemu is in `_SKIP` — never retagged. Each runner produces exactly one wheel.

| Runner | QEMU Binary | Wheel Tag | After `pre_release` merge |
|--------|-------------|-----------|--------------------------|
| `[linux, x64]` | qemu-system-x86_64 (Linux) | `manylinux_2_17_x86_64` | unchanged |
| `[linux, arm64]` | qemu-system-aarch64 (Linux) | `manylinux_2_17_aarch64` | unchanged |
| `[macos, arm64]` | qemu-system-aarch64 (macOS) | `macosx_11_0_arm64` | unchanged |
| `[windows, x64]` | qemu-system-x86_64.exe | `win_amd64` | → **fat**: x64 in `bin/x86_64/`, arm64 in `bin/arm64/` |
| `[windows, arm64]` | qemu-system-aarch64.exe | `win_arm64` ¹ | unchanged (consumed by merge into fat wheel) |

¹ **Windows ARM64 tag override:** The ARM64 runner runs x86_64 Python through transparent emulation, so `sysconfig.get_platform()` returns `win-amd64`. Without intervention, this runner would produce a **second** `win_amd64` wheel containing ARM64 binaries — colliding with the x64 runner's wheel and making the fat wheel merge impossible (no `win_arm64` to merge from). We override the wheel tag in `BinaryBundler.set_platform_wheel_tag()` (`quicksand-build-tools`, used by quicksand-qemu) and `set_platform_wheel_tag()` (`quicksand-image-tools`, used by image packages) by reading the native architecture from the Windows Registry (`HKLM\...\PROCESSOR_ARCHITECTURE`), which always reports `ARM64` regardless of process emulation.

#### Image wheels (ubuntu, alpine, etc.): build runners → retag

Image wheels contain qcow2 files that are cross-platform. Retag runs only on `RETAG_RUNNERS`.

| Runner | Builds | Retag produces |
|--------|--------|----------------|
| `[linux, x64]` | `linux_x86_64` | + `macosx_10_13_x86_64`, `win_amd64` |
| `[macos, arm64]` | `macosx_11_0_arm64` | + `linux_aarch64`, `win_arm64` |
| `[linux, arm64]` | `linux_aarch64` | none (not in `RETAG_RUNNERS`) |
| `[windows, *]` | — | not an image builder |

#### quicksand-qemu: host → pip install

| Host OS | Hardware | Python Arch | pip picks | QEMU binary used | HW accel |
|---------|----------|-------------|-----------|------------------|----------|
| Linux | x86_64 | x86_64 | `linux_x86_64` | qemu-system-x86_64 | KVM ✅ |
| Linux | arm64 | arm64 | `linux_aarch64` | qemu-system-aarch64 | KVM ✅ |
| macOS Intel | x86_64 | x86_64 | — (no wheel) | system QEMU (Homebrew) | HVF ✅ |
| macOS Apple Silicon | arm64 | arm64 | `macosx_11_0_arm64` | qemu-system-aarch64 | HVF ✅ |
| macOS Rosetta | arm64 | x86_64 | — (no wheel) | system QEMU (Homebrew) | TCG ❌ |
| Windows | x86_64 | x86_64 | `win_amd64` (fat) | picks `bin/x86_64/` | WHPX ✅ |
| Windows | arm64 | x86_64 (emulated) | `win_amd64` (fat) | picks `bin/arm64/` | WHPX ✅ |
| Windows | arm64 | arm64 (native) | `win_arm64` | qemu-system-aarch64 | WHPX ✅ |

**Notes:**
- **No macOS x86_64 runner** — macOS Intel users fall back to system QEMU via Homebrew.
- **Rosetta Python** — rare; gets no bundled wheel, falls back to system QEMU with software emulation (TCG).
- **Fat wheel** — only `win_amd64` is fat (~2x size). All other wheels are single-arch.

## `quicksand install ubuntu`

Installs a base image package (`quicksand-ubuntu`) containing three files per architecture:

```
quicksand_ubuntu/images/
├── ubuntu-24.04-arm64.kernel    # Linux kernel binary (vmlinuz)
├── ubuntu-24.04-arm64.initrd    # Initial RAM disk
└── ubuntu-24.04-arm64.qcow2     # Root filesystem (qcow2 format)
```

These become the `-kernel`, `-initrd`, and `-drive` arguments:

```bash
qemu-system-aarch64 \
  -kernel  /path/to/ubuntu-24.04-arm64.kernel \
  -initrd  /path/to/ubuntu-24.04-arm64.initrd \
  -drive   file=overlay.qcow2,format=qcow2,...
```

The `-drive` never points directly at the base qcow2. Quicksand creates a copy-on-write overlay on top of it (see [Save and Rollback](05-save-and-rollback.md)), so the base image is never modified.

## Desktop overlay images

Desktop images (`quicksand install ubuntu-desktop`) don't contain a full disk. They contain a qcow2 overlay that layers on top of the base image:

```
quicksand_ubuntu_desktop/images/
├── manifest.json             # Config defaults (memory, cpus, disk_size)
└── overlays/
    └── 0.qcow2               # Desktop packages layered on top of ubuntu base
```

The overlay's backing file points to the base image's qcow2. QEMU resolves this chain at boot. Reads go through the overlay first, falling through to the base for unmodified blocks.
