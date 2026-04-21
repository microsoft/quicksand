# Changelog

All notable changes to the quicksand project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [v0.9.4] - 2026-04-01

### Changed
- **aif-cua-agent-sandbox:** Reduced overlay image size from ~604 MB to ~445 MB by removing unused Playwright headless shell, Vulkan GPU drivers, and system Node.js

### Fixed
- **quicksand-core:** Virtio-serial agent client now keeps connection open across auth retries. Disconnecting caused QEMU to stop accepting new chardev connections
- **quicksand-core:** Pin httpx<1.0 to prevent breaking API change
- **quicksand-core:** Fix `MountSpec` → `Mount` in README exports
- ARM64 image wheels built on macOS are now retagged for Linux via uvr post_build hook
- **quicksand-alpine, quicksand-ubuntu:** Restore editable install guard in build hooks
- **quicksand-alpine-desktop, quicksand-ubuntu-desktop:** Break up large package install commands to avoid build timeouts

## [v0.9.3] - 2026-03-30

### Added
- **aif-cua-agent-sandbox:** noVNC web client (port 6080), websockify, socat, and rsync in the overlay image

### Fixed
- `quicksand run -p HOST:GUEST` port forward parsing now works correctly

## [v0.9.2] - 2026-03-29

### Added
- `quicksand clean` CLI command to remove local `.quicksand/` and optionally global `~/.quicksand/` data directories

## [v0.9.0] - 2026-03-26

### Added
- Virtio-serial guest agent transport with 25x boot speedup (Alpine p50: 11.5s → 0.45s)
- `Sandbox.qemu_command` property to inspect the QEMU command line
- `Sandbox.boot_timing` property with phase-level profiling (kernel, init, agent breakdown)
- `BootTiming` dataclass with `__str__` for human-readable boot phase display
- `quicksand benchmark` CLI command with percentile stats, progress bar, and `--json` output
- `quicksand uninstall` CLI command to remove installed extras
- `-v`/`--mount` option on benchmark command for measuring boot with mounts

### Changed
- Guest images now use static IP (10.0.2.15/24) instead of DHCP. This eliminates ~8s dhcpcd overhead
- Alpine: enabled `rc_parallel="YES"` for parallel OpenRC service startup
- Alpine: guest agent moved to `boot` runlevel (from `default`)
- Ubuntu: guest agent moved to `After=sysinit.target` (from `After=basic.target`)
- Rust guest agent supports dual transport: virtio-serial first, HTTP fallback

## [v0.7.1] - 2026-03-24

### Changed
- `quicksand-alpine-desktop` and `quicksand-ubuntu-desktop` converted from standalone base images to overlay images built on top of their respective base packages
- Desktop image wheels are significantly smaller (Alpine: 359MB → 286MB, Ubuntu: 575MB → 254MB) since they only contain the overlay delta
- Desktop packages now depend on `quicksand-alpine` / `quicksand-ubuntu` respectively
- Docker is no longer required to build desktop image packages

### Removed
- Dockerfiles for desktop image packages (build now uses overlay mechanism)

## [v0.7.0] - 2026-03-24

### Changed
- **Breaking:** `ResolvedImage.base` and `ResolvedImage.overlays` replaced with unified `chain: list[Path]`. `chain[0]` is the root base qcow2, and `chain[1:]` are overlay layers in bottom-to-top order
- **Breaking:** Save format bumped to v6. Only session-local overlays are stored. Installed package overlays are resolved by name at load time
- `ImageProvider` protocol now requires an `images_dir: Path` attribute
- `qemu-img convert -c` preserves backing file references (`-B`) instead of flattening the full chain

### Added
- `aif-cua-agent-sandbox` overlay package (first release)
- `_verify_overlay_from_package()` validates that non-session overlays belong to installed packages at save time

## [v0.6.1] - 2026-03-24

### Added
- `SandboxConfig.arch` parameter for cross-architecture VM builds via TCG emulation
- `quicksand run --arch` flag for cross-arch boot from the CLI
- `quicksand install --arch` flag to download cross-platform wheels
- `quicksand run IMAGE` now takes image as a required positional argument (was `-b/--base`)
- Overlay build phase in release pipeline (`build-overlay` job, runs after base images)
- `--reuse-base-build` and `--reuse-overlay-build` flags for independent build reuse
- `quicksand-base-scaffold` package for scaffolding new base image packages
- `quicksand-overlay-scaffold --base` flag to choose which base image to overlay on

### Changed
- Release pipeline `build` job renamed to `build-base` for clarity
- `quicksand install` removed legacy save-download path. All names are package installs
- Scaffold packages output to fixed directories (`packages/` for base, `packages/contrib/` for overlay)
- Workflow conditions use `fromJSON` null checks instead of `contains` string matching

### Fixed
- Release artifacts from multiple runs now merge correctly (tri-source downloads)
- KVM setup is best-effort in overlay builds (falls back to TCG gracefully)
- `_to_title` no longer uppercases short words. This avoids `SandboxSandbox` in scaffold output

## [v0.6.0] - 2026-03-23

### Changed
- **Breaking:** `SandboxConfig.image` is now `str` only (was `str | Path | ImagePaths`)
- **Breaking:** `save()` returns `SaveManifest` instead of `SaveInfo`
- **Breaking:** Removed `load` parameter from `Sandbox.__init__()`. Use `SandboxConfig(image="save-name")` instead
- **Breaking:** Save format bumped to v5 (directory-based, no tar support)
- **Breaking:** Removed legacy `quicksand.bases` entry point group. Only `quicksand.images` is supported
- `ImageProvider` is now a `typing.Protocol` instead of an abstract class
- Sandbox internal state uses flat fields instead of wrapper dataclasses
- `build_qemu_command()` takes individual path arguments instead of `ImagePaths`/`DiskPaths`

### Removed
- `SavedConfig`, `SaveInfo`, `ImagePaths`, `DiskPaths`, `_BootState`, `_VMConfig`, `SandboxSnapshot` types
- `_WorkspaceOptions`, `_QuicksandGuestAgentState`, `_QMPState`, `_VNCState` state wrappers
- `SavedConfig.from_sandbox_config()` and `SavedConfig.resolve()` methods
- `get_image_path()` and `get_image_artifacts()` legacy functions from image packages
- `sandbox/_state.py` module
- Legacy tar-based save format support (v3/v4 manifests)

### Fixed
- `SandboxConfig` is now truly frozen and never mutated after construction
- Config mutation bug where `config.accel` was overwritten during cross-arch save loading
- Hardcoded `quicksand_version: "0.1.0"` in save manifests

## [v0.5.0] - 2026-03-20

### Changed
- **Breaking**: Entire Sandbox public API is now async/await
  - `Sandbox` uses `async with` (`__aenter__`/`__aexit__`) and `await start()`/`stop()`
  - All mixins are async: `execute`, `save`, `checkpoint`, `revert`, `mount`, `unmount`, `type_text`, `press_key`, `mouse_move`, `mouse_click`, `screenshot`, etc.
  - `QMPClient` uses asyncio streams instead of blocking sockets
  - `QuicksandGuestAgentClient` uses `httpx.AsyncClient`
  - Examples updated with `asyncio.run()` wrappers
  - Tests updated to pytest-asyncio + AsyncMock

## [v0.3.3] - 2026-03-18

### Added
- `python -m quicksand` entrypoint (equivalent to `quicksand` CLI)

### Fixed
- Bumped GitHub Actions to latest versions (Node.js 24 support)

## [v0.3.2] - 2026-03-17

### Fixed
- `quicksand install` failing to resolve third-party dependencies (httpx) due to `--no-index` flag blocking PyPI access

## [v0.3.0] - 2026-03-17

### Added
- CIFS hot-mount API: `sb.mount()` / `sb.unmount()` with `MountHandle`
- `quicksand-smb` package: pure-Python SMB3 server spawned via QEMU guestfwd (no TCP port, no Samba, zero dependencies)
- `NetworkMode.MOUNTS_ONLY` for mounts without internet access via guestfwd tunnels
- `NetworkMode` enum now has three modes: `NONE`, `MOUNTS_ONLY` (default), `FULL`
- QEMU block layer flush before snapshot pivot to prevent data loss on large writes
- Path traversal protection in SMB server (symlink + `..` escape checks)
- `examples/mounts.py` demonstrating boot-time, dynamic, and readonly mount patterns

### Changed
- Replaced `quicksand-smbd` (bundled Samba binary, 50MB+) with `quicksand-smb` (pure Python, ~2K LOC)
- Default network mode is now `MOUNTS_ONLY` (was `HOST_ONLY`)
- Mounts use CIFS over guestfwd in all network modes (no TCP port opened on host)

### Removed
- `quicksand-smbd` package (replaced by `quicksand-smb`)
- 9p as default mount protocol (replaced by CIFS). 9p remains available via `type="9p"`

### Fixed
- macOS SIP `PermissionError` when copying binaries
- `NetworkMode` enum definition to properly support three modes

## [v0.2.3] - 2026-03-12

### Added
- `quicksand-ubuntu-desktop` package with full Ubuntu 24.04 desktop sandbox support
- `quicksand-alpine-desktop` package
- `Key` enum for keyboard input
- Image install CLI (`quicksand images install <name>`)
- `query_display_size()` API for display introspection
- Double-click support in input API
- Overlay chain support in core snapshot/checkpoint system

### Changed
- Refactored core to use `NetworkMode` enum, overlay chains, and display/input subsystem
- Updated Alpine and Ubuntu base packages for `NetworkMode` API
- Renamed `quicksand-agent` to `quicksand-guest-agent`
- Updated guest agent and dev tools for new core API
- Improved desktop images with browser support, software cursor, and DNS fixes

### Fixed
- Checkpoint integration tests for overlay chain format
- Typecheck issues with `overlay_chain` in `SandboxSnapshot`
- CI version bump to push from latest main instead of tag HEAD

## [v0.2.2] - 2026-02-27

### Changed
- Renamed `quicksand-agent` to `quicksand-guest-agent` and added package init scaffold

### Fixed
- Integration test compatibility with renamed package

## [v0.2.1] - 2026-02-26

### Changed
- Refactored core internals
- `install` command now supports multi-argument package installation

## [v0.2.0] - 2026-02-26

### Added
- Streaming `execute()` API for real-time command output
- README install instructions using `quicksand` CLI installer

### Changed
- Refactored core internals and removed `write_file`/`read_file` in favor of `execute()`
- Updated integration tests to use `execute()` API

### Fixed
- Missing `readline` import error on Windows
- Binary file tests to use POSIX-compatible tools (`od` instead of `xxd`)

## [v0.1.27] - 2026-02-24

### Fixed
- Mount hangs when `restrict_network=True`

## [v0.1.26] - 2026-02-24

### Fixed
- DNS resolution in Ubuntu sandbox (enabled `systemd-resolved`)
- Reverted DNS changes that broke mount tests

## [v0.1.25] - 2026-02-24

### Changed
- QEMU bundling is now optional

## [v0.1.24] - 2026-02-24

### Added
- CLI to `quicksand` package
- `quicksand-qemu` CI build jobs

## [v0.1.23] - 2026-02-23

### Added
- Rust guest agent, replacing the Python agent for smaller images and faster startup
- `curl` added to Alpine image for network tests

## [v0.1.22] - 2026-02-20

### Fixed
- microvm + Ubuntu compatibility: match `eth*` interfaces in `systemd-networkd`

## [v0.1.21] - 2026-02-20

### Changed
- Disabled microvm temporarily due to Ubuntu compatibility issues

## [v0.1.20] - 2026-02-20

### Fixed
- microvm networking by using explicit `-netdev`/`-device` QEMU args

## [v0.1.19] - 2026-02-20

### Added
- GPL license files for QEMU binary distribution (Section 3(c) compliance)
- QEMU version tracking

### Removed
- Redundant `bundle_qemu.py` script (inlined into `hatch_build.py`)

## [v0.1.18] - 2026-02-20

### Added
- microvm support: bundle `bios-microvm.bin` for ~4x faster boot on Linux x86_64
- QEMU performance optimizations

### Fixed
- microvm `bios-microvm.bin` existence check before enabling
- `_build_mount_args` signature change in tests

## [v0.1.16] - 2026-02-19

### Added
- Serial console output for boot debugging

### Changed
- Renamed `install.py` to `installer.py`

### Fixed
- Docker build: remove `/etc/resolv.conf` before symlinking
- Ubuntu DNS by enabling `systemd-resolved`
- Installer tests

## [v0.1.15] - 2026-02-17

### Added
- SMB-based mounting for Windows hosts, replacing `FileSyncAgent`

### Fixed
- Windows SMB mount (standard port 445, explicit CIFS options)
- Build hooks importing from package being built
- Checkpoint tests with mounts on Windows

## [v0.1.14] - 2026-02-15

### Changed
- Use pyproject entry points for optional package discovery

## [v0.1.13] - 2026-02-14

### Changed
- Refactored host module naming. `Platform` enum renamed to `OS` and merged runtime module

## [v0.1.12] - 2026-02-12

### Added
- `ARCHITECTURE.md` documenting codebase structure and QEMU glossary
- Comprehensive tests for `quicksand-image-tools` CLI
- Per-package tags with bundled releases
- Python-based wheel merge script (`merge_wheels.py`)

### Changed
- CI release flow: single tag with incremental builds
- Separated build tasks for core and image packages

### Removed
- Committed `agent.py` from `quicksand-alpine` (now generated)

## [v0.1.11] - 2026-02-12

### Changed
- Refactored `sandbox.py` into modular VM components
- Refactored platform config to subclass-based architecture

### Fixed
- `filesync` import error on Windows guest

## [v0.1.10] - 2026-02-11

### Changed
- Redesigned `image_wrapper` to properly subclass `Sandbox`

## [v0.1.9] - 2026-02-11

### Changed
- Optimized QEMU boot time

## [v0.1.8] - 2026-02-11

### Added
- `install.py` helper script for GitHub releases with download progress bars
- Interactive `test` subcommand with `--mount`, `--network`, and sandbox type options
- DNS resolution and curl HTTPS integration tests
- Cross-platform CI: unit tests on Linux, macOS, and Windows
- Matrix test-installer workflow across OS and image type

### Fixed
- Platform matching in `install.py` for wheel selection
- Progress bar Unicode error on Windows
- Network integration tests

## [v0.1.7] - 2026-02-11

### Changed
- Switched to tag-based releases
- Consolidated CI into single workflow (check, build, test)

## [v0.1.6] - 2026-02-11

### Changed
- Redesigned CI workflows: separate check/build/test stages with proper release flow
- Cross-workflow artifact handling via `gh run download`

### Removed
- Debug KVM ARM64 workflow

## [v0.1.5] - 2026-02-11

### Changed
- Split CI into separate workflows

## [v0.1.4] - 2026-02-11

### Added
- `sync_file()` API for explicit file sync
- Bidirectional filesync with `use_filesync` config option
- Standalone release workflow
- WHPX kernel arg (`noapic`) for Windows

### Fixed
- Windows filesync: path handling, port initialization, atomic rename, bidirectional sync
- Filesync initial sync race condition
- WHPX kernel panic by correcting QEMU arg order

## [v0.1.3] - 2026-02-10

### Fixed
- CI: removed `continue-on-error` from build and test jobs

## [v0.1.2] - 2026-02-10

### Changed
- CI workflow now triggers on version tags

## [v0.1.1] - 2026-02-10

### Added
- Initial release of quicksand: pip-installable Linux sandboxes powered by QEMU
- Core sandbox API with `Sandbox` and `UbuntuSandbox` classes
- Monorepo packages: `quicksand-core`, `quicksand-ubuntu`, `quicksand-alpine`, `quicksand-qemu`
- QEMU binary bundling for macOS, Linux, and Windows
- Direct kernel boot with virtio networking and 9p mounts
- FastAPI-based HTTP guest agent
- P2P file sync for Windows hosts (virtio-9p alternative)
- Docker-based VM image building
- CI/CD pipeline with per-platform wheel builds and integration tests
- MIT License
