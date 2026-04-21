# Quicksand Build Tools

Shared build utilities for bundling native binaries into quicksand wheels.

Used by `quicksand-qemu` build hooks. Provides `BinaryBundler` with platform-specific logic for copying shared libraries, rewriting paths (`install_name_tool` on macOS, `patchelf` on Linux), and codesigning.

## Usage

This is a build-time dependency and not intended for direct use. It's consumed by `hatch_build.py` hooks in packages that bundle native binaries.

## License

MIT
