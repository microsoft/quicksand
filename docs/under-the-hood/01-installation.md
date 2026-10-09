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

### Windows ARM64 and interpreter architecture

quicksand-qemu 0.5.12 packages are single-architecture wheels: `win_amd64` contains x86_64
executables and `win_arm64` contains ARM64 executables, directly in `bin/`.
Windows ARM64 can run x86_64 Python through emulation, which affects wheel selection:

| | Python arch | pip accepts | QEMU needed |
|---|---|---|---|
| Real x86_64 Windows | x86_64 | `win_amd64` | x86_64 |
| ARM64 Windows, native Python | ARM64 | `win_arm64` | ARM64 |
| **ARM64 Windows, emulated Python** | **x86_64** | **`win_amd64`** | **ARM64** |

The third row is the problem. pip's wheel compatibility tags (PEP 425) match the Python interpreter's platform, not the hardware. An emulated x86_64 Python will only install `win_amd64` wheels — but the machine needs ARM64 QEMU binaries for hardware acceleration (WHPX).

Use native ARM64 Python on Windows ARM64 so pip selects the ARM64 QEMU and image
wheels. `quicksand install` delegates to pip; it does not override the
interpreter's platform tags. Installing a single-architecture x64 QEMU wheel
under emulated Python can produce an architecture-mismatch error at runtime.

The current pipeline publishes both Windows wheels without merging them.
`_find_bundled_runtime()` still supports legacy combined wheels with
`bin/x86_64/` and `bin/arm64/` subdirectories, selecting by native hardware
architecture. That is backward compatibility, not the layout of the current release.

### Platform wheel matrix

#### quicksand-qemu: build runners → wheels

quicksand-qemu is in `_SKIP` — never retagged. Each runner produces exactly one wheel.

| Runner | QEMU Binary | Wheel Tag |
|--------|-------------|-----------|
| `[linux, x64]` | qemu-system-x86_64 (Linux) | `manylinux_<major>_<minor>_x86_64` |
| `[linux, arm64]` | qemu-system-aarch64 (Linux) | `manylinux_<major>_<minor>_aarch64` |
| `[macos, arm64]` | qemu-system-aarch64 (macOS) | `macosx_11_0_arm64` |
| `[windows, x64]` | qemu-system-x86_64.exe | `win_amd64` |
| `windows-11-arm` | qemu-system-aarch64.exe | `win_arm64` ¹ |

Linux manylinux versions are derived from versioned symbols in the bundled ELF
binaries and libraries. They are not fixed at glibc 2.17; pip selects a wheel
compatible with the host's glibc version.
The published quicksand-qemu 0.5.12 Linux wheels require glibc 2.38 or newer.
On older systems, use a compatible system QEMU instead.

With quicksand-build-tools 0.6.0, custom Linux build hooks must pass the bundled
binary directory as `bin_dir` to `BinaryBundler.set_platform_wheel_tag()` and
include auditwheel in their build dependencies. The QEMU hook supplies both;
applications using `Sandbox` do not need to change their build configuration.

¹ **Windows ARM64 tag override:** When an ARM64 runner uses x86_64 Python through transparent emulation, `sysconfig.get_platform()` returns `win-amd64`. Without intervention, it would produce a **second** `win_amd64` wheel containing ARM64 binaries, colliding with the x64 wheel. `BinaryBundler.set_platform_wheel_tag()` (`quicksand-build-tools`) and `set_platform_wheel_tag()` (`quicksand-image-tools`) detect the native architecture through the Windows Registry (`HKLM\...\PROCESSOR_ARCHITECTURE`) and apply `win_arm64`. Native ARM64 Python already reports the correct tag.

#### Image wheels (ubuntu, alpine, etc.): build runners → retag

Image wheels contain architecture-specific VM data that is portable across host
operating systems. Retag runs only on `RETAG_RUNNERS`; these are the two image builders.

| Runner | Builds | Retag produces |
|--------|--------|----------------|
| `[linux, x64]` | `manylinux_2_17_x86_64` | + `macosx_10_13_x86_64`, `win_amd64` |
| `[macos, arm64]` | `macosx_11_0_arm64` | + `manylinux_2_17_aarch64`, `win_arm64` |

#### quicksand-qemu: host → pip install

| Host OS | Hardware | Python Arch | pip picks | QEMU binary used | HW accel |
|---------|----------|-------------|-----------|------------------|----------|
| Linux | x86_64 | x86_64 | compatible `manylinux_*_x86_64` | qemu-system-x86_64 | KVM ✅ |
| Linux | arm64 | arm64 | compatible `manylinux_*_aarch64` | qemu-system-aarch64 | KVM ✅ |
| macOS Intel | x86_64 | x86_64 | — (no wheel) | system QEMU (Homebrew) | HVF ✅ |
| macOS Apple Silicon | arm64 | arm64 | `macosx_11_0_arm64` | qemu-system-aarch64 | HVF ✅ |
| macOS Rosetta | arm64 | x86_64 | — (no wheel) | system QEMU (Homebrew) | TCG ❌ |
| Windows | x86_64 | x86_64 | `win_amd64` | qemu-system-x86_64 | WHPX ✅ |
| Windows | arm64 | x86_64 (emulated) | `win_amd64` | architecture mismatch; use native Python | — |
| Windows | arm64 | arm64 (native) | `win_arm64` | qemu-system-aarch64 | WHPX ✅ |

**Notes:**
- **No macOS x86_64 runner** — macOS Intel users fall back to system QEMU via Homebrew.
- **Rosetta Python** — rare; gets no bundled wheel, falls back to system QEMU with software emulation (TCG).
- **Acceleration** — requires support in both the host and QEMU build. The table lists the preferred accelerator, not a guarantee that it is available on every host.
- **Single-architecture wheels** — all current QEMU wheels contain one architecture. Large image wheels also use the term "fat", but that means they carry VM image data rather than being a small PyPI stub.

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
