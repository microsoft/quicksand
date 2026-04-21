# Quicksand QEMU

This package bundles QEMU binaries for the [quicksand](https://github.com/microsoft/quicksand) VM harness.

## Installation

Install as part of quicksand:

```bash
pip install quicksand[qemu]
```

Or standalone:

```bash
pip install quicksand-qemu
```

## What's Included

Platform-specific QEMU binaries:
- `qemu-system-x86_64` or `qemu-system-aarch64` (depending on architecture)
- `qemu-img` for disk image manipulation
- Required BIOS/firmware files
- Bundled shared libraries (no system dependencies)

## Using System QEMU Instead

If you prefer to use your system's QEMU installation, just install quicksand without the `[qemu]` extra:

```bash
pip install quicksand[ubuntu]
```

Quicksand will automatically detect QEMU in your PATH.

## Package Size

The wheel is ~80-150MB because it includes QEMU and all dependencies. This ensures quicksand works out of the box without system configuration.

## License

MIT (quicksand-qemu wrapper)

QEMU binaries are licensed under GPL v2. See the bundled `licenses/` directory for full license texts.
