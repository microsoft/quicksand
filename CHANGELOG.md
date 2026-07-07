# Changelog

All notable changes to the quicksand project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [quicksand-core v0.12.0, quicksand-smb v0.5.0, quicksand-image-tools v0.5.14] - 2026-07-07

Windows file sharing no longer needs Administrator rights, and SMB remount reliability is fixed on all platforms.

### Added
- **quicksand-smb:** New public `serve_socket(sock, config)` entry point that serves one SMB connection over a connected socket. It complements the existing inetd-style `serve_stdio`. The server now runs on Windows as well, using positional I/O, binary-mode opens, and a statvfs fallback.
- **quicksand-core:** New `QuicksandSMBTCPServer`, the default SMB server on Windows. It runs the pure-Python SMB3 server in-process on a loopback-only TCP listener, so mounts no longer require Administrator rights. The PowerShell `New-SmbShare` implementation (`WindowsSMBServer`) remains available by setting `QUICKSAND_WINDOWS_NATIVE_SMB=1`.
- **quicksand-core:** `PlatformConfig.build_command` accepts a new `agent_socket_port` keyword. The virtio-serial agent channel can now run over a loopback TCP socket, which Windows hosts use instead of a Unix socket path.

### Fixed
- **quicksand-smb:** `FileFsSectorSizeInformation` responses are now the spec-correct 28 bytes instead of 20.
- **quicksand-core:** CIFS mount options always include `nosharesock`. Without it, a mount/unmount/mount cycle wedged because the kernel CIFS client tried to resume a session the server had already closed.
- **quicksand-image-tools:** The guest agent runs commands concurrently, so a timed-out command can no longer wedge the serial channel.
- **quicksand-image-tools:** Image builds tolerate Windows-unstattable rootfs entries and temp-dir cleanup failures.

### Released (no user-visible changes)
- quick-sandbox v0.11.15, quicksand-agent v0.4.9, quicksand-alpine v0.9.10, quicksand-alpine-desktop v0.9.9, quicksand-base-scaffold v0.3.10, quicksand-cua v0.3.11, quicksand-overlay-scaffold v0.3.10, quicksand-ubuntu v0.9.11, quicksand-ubuntu-desktop v0.9.9. Dependency repins for quicksand-core 0.12 and quicksand-smb 0.5.

## [quicksand-core v0.11.14, quicksand-qemu v0.5.11] - 2026-06-22

Stable release of the macOS VPN/split-DNS fix (previously alpha).

### Added
- **quicksand-core:** `SandboxConfig.host_dns_proxy` and a host-side DNS proxy (`quicksand_core.host.dns_proxy.HostDnsProxy`) that resolves guest DNS via the host OS resolver (`getaddrinfo`). Auto-enabled on macOS hosts with `network_mode=FULL`. Fixes intermittent — often total — guest DNS failures on macOS while connected to a VPN, where stock libslirp forwards DNS to a single libresolv-picked resolver and ignores the system's scoped/split-DNS configuration.
- **quicksand-qemu:** The bundled macOS libslirp is rebuilt from source with a patch that redirects guest DNS to `$QUICKSAND_DNS_PROXY` (the host proxy above). Inert unless that variable is set; Linux and Windows wheels are unchanged.

### Fixed
- **quicksand-core:** Silenced dnslib's per-request/reply logging, which flooded host logs (even at DEBUG) once a guest did any DNS. Only error logging remains, routed to the `quicksand.dns_proxy` logger at DEBUG.

## [quicksand-core v0.11.14a0, quicksand-qemu v0.5.11a0] - 2026-06-04

Alpha release fixing macOS guest DNS failures under a VPN.

### Added
- **quicksand-core:** `SandboxConfig.host_dns_proxy` and a host-side DNS proxy (`quicksand_core.host.dns_proxy.HostDnsProxy`) that resolves guest DNS via the host OS resolver (`getaddrinfo`). Auto-enabled on macOS hosts with `network_mode=FULL`. Fixes intermittent — often total — guest DNS failures on macOS while connected to a VPN, where stock libslirp forwards DNS to a single libresolv-picked resolver and ignores the system's scoped/split-DNS configuration.
- **quicksand-qemu:** The bundled macOS libslirp is rebuilt from source with a patch that redirects guest DNS to `$QUICKSAND_DNS_PROXY` (the host proxy above). Inert unless that variable is set; Linux and Windows wheels are unchanged.

## [v0.11.14] - 2026-05-14

### Fixed
- **quicksand-core:** `auto_install_images` now trusts pip's exit code instead of additionally requiring `manifest.json` to appear in the images directory. v0.11.12's check happened to work for save-format wheels (`quicksand-cua`, `quicksand-agent`) but spuriously failed for base-image wheels like `quicksand-ubuntu`, which ship `qcow2`/`kernel`/`initrd` directly (no manifest). The contrib provider's own existing file checks decide whether the install delivered what was needed.
- **quick-sandbox:** Removes the `os.execv` workaround added in 0.11.13 — no longer needed. With the manifest check gone, the resolver chain walks cua → agent → ubuntu cleanly in a single in-process run. Three sequential pip installs, no restarts.

### Removed
- **quicksand-core:** `ImagesInstalled` exception class. Resolution doesn't need a "restart" signal now that auto-install behaves correctly.

## [v0.11.13] - 2026-05-14

### Fixed
- **quicksand-core / quick-sandbox:** Fresh `quicksand run` of a multi-layer image (e.g. `quicksand-cua` → `quicksand-agent` → `quicksand-ubuntu`) used to download all three fat wheels via `auto_install_images` and *still* fail with `Image not found` on the first invocation — pip wrote new wheels to disk but the parent Python process still held references to the pre-install (pure-stub) modules, their `IMAGES_DIR` paths, and the stale `entry_points()` lookup. `auto_install_images` now raises a new `ImagesInstalled` exception after a successful pip install, `ImageResolver._resolve_base_by_name` re-raises it past its broad `except`, and the CLI catches it at `main()` and `os.execv`s back into the same command. End-to-end, a cold install of an N-layer overlay still produces N pip downloads but interleaves them with N re-execs and the user only types the command once.

## [v0.11.12] - 2026-05-14

### Fixed
- **quick-sandbox:** Declare `packaging` as a runtime dependency. `quicksand.cli.install` imports `packaging.requirements.Requirement` (added in 0.11.11 to parse PEP 508 specifier strings) but `packaging` wasn't in the dependency list — fresh installs hit `ModuleNotFoundError: No module named 'packaging'` at import.

## [v0.11.11] - 2026-05-13

### Added
- **simple index:** every wheel attached to every per-package GitHub release is now exposed as a PEP 503 simple repository at `https://microsoft.github.io/quicksand/simple/`. A new `scripts/ci/build_simple_index.py` regenerates it inside the Pages deploy; a `workflow_run` trigger on the docs workflow refreshes it after each successful release. pip handles version + platform-wheel resolution end-to-end, removing the bespoke GitHub-API resolver previously baked into `quicksand install`.
- **quicksand-core:** sandbox memory validation — `Sandbox` checks requested memory against the host budget and warns/raises when overrun.

### Changed
- **quick-sandbox:** `quicksand install` rewritten to shell out to `pip install --index-url https://microsoft.github.io/quicksand/simple/ --extra-index-url https://pypi.org/simple/`. Requirements use standard PEP 508 syntax (`quicksand-qemu==0.5.9`, `ubuntu>=0.4,<0.5`) instead of the old `@version` shorthand; short aliases (`qemu`, `ubuntu`, `dev`, …) still work. Pip's resolver picks versions and platform wheels.
- **quicksand-core:** `auto_install_images` rewritten to re-install via pip against the simple index instead of fetching wheels directly from the GitHub API. Drops the embedded GitHub API client, the wheel unzipper, and the host-arch / host-OS substring helpers (~150 lines).

### Removed
- **quick-sandbox:** `quicksand install --arch` flag — pip now selects host wheels and the simple index obviates the previous cross-arch retag dance.
- **quick-sandbox:** `name@version` syntax in `quicksand install` — use PEP 508 specifiers (`name==version`) instead.
- **quicksand-core:** `arch` parameter on `auto_install_images` — no remaining callers.

### Released (no user-visible changes)
- **quicksand-agent, quicksand-alpine, quicksand-alpine-desktop, quicksand-base-scaffold, quicksand-cua, quicksand-image-tools, quicksand-overlay-scaffold, quicksand-qemu, quicksand-smb, quicksand-ubuntu, quicksand-ubuntu-desktop:** internal refactor — versions are now read via `importlib.metadata` instead of hardcoded constants.

## [v0.11.9] - 2026-05-07

### Fixed
- **quicksand-core:** Drain only auth writes that actually queued. The post-auth drain loop in `VirtioSerialAgentClient.connect` counted every loop iteration as a stale auth write, including iterations that hit `FileNotFoundError` on `open_unix_connection` and never wrote anything — so on macOS, where the host typically beats QEMU to the chardev socket and gets 1–2 spurious failures, the drain loop sat 2.0 s waiting for phantom replies on every healthy boot. Alpine boot benchmark p50: 2.474 s → 0.523 s.
- **quicksand-qemu:** Hard-fail Linux bundling when `patchelf` is missing or any NEEDED library is unresolved. quicksand-qemu 0.5.8 shipped on PyPI with no bundled shared libraries because `BinaryBundler.bundle_linux_libs` silently early-returned when `patchelf` was absent on the build runner; `verify` then passed because `LD_LIBRARY_PATH` fell through to `/etc/ld.so.cache`. Wheels broke on hosts (e.g. fresh WSL Ubuntu) without libnuma/liburing/libaio/libpixman/libslirp pre-installed.
- **quicksand-qemu:** Extend the same isolation verification to macOS and Windows — `otool -L` now requires every load command to be `@loader_path/...` or a system path, and Windows DLL loads are re-verified under a stripped PATH. Catches the silent-bundling failure mode on all three platforms.

### Released (no user-visible changes)
- **quick-sandbox, quicksand-agent, quicksand-alpine, quicksand-alpine-desktop, quicksand-base-scaffold, quicksand-build-tools, quicksand-cua, quicksand-image-tools, quicksand-overlay-scaffold, quicksand-ubuntu, quicksand-ubuntu-desktop:** version bumps only — needed to pick up updated dep pins.

## [v0.11.8] - 2026-05-06

### Fixed
- **quicksand-core:** Kill QEMU when the parent Python process dies (Ctrl+C, crash, `kill -9`, OOM). A tiny launcher between the parent and QEMU sees parent death via stdin EOF and tears the VM down, preventing leaked RAM and forwarded ports.

### Released (no user-visible changes)
- **quick-sandbox, quicksand-agent, quicksand-alpine, quicksand-alpine-desktop, quicksand-base-scaffold, quicksand-cua, quicksand-image-tools, quicksand-overlay-scaffold, quicksand-ubuntu, quicksand-ubuntu-desktop:** version bumps only — needed to pick up the `quicksand-core` dep pin update.

## [v0.11.7] - 2026-05-05

### Fixed
- **quicksand-core:** Demultiplex virtio-serial agent responses by request id — concurrent requests on the agent client no longer race for replies
- **quicksand-core:** Shell-quote host and guest paths in mount/umount commands — paths with spaces or special characters now mount correctly
- **quick-sandbox:** Fall back to legacy `quickand-` entry-point prefix when the canonical name is not found
- **build:** Use manylinux tags in retag targets so wheels publish with the correct compatibility tags
- **CI:** Restore the `build` poe task wiring that the previous release inadvertently broke

### Changed
- **quicksand-core:** `image` is now an explicit required keyword on `Sandbox.__init__`; the `image` field was removed from `SandboxConfigParams` so subclasses can declare their own default. Runtime behavior is unchanged — `image` was effectively required already — but `ty 0.0.34` now enforces it statically.
- **contrib / dev subclasses:** `UbuntuSandbox`, `UbuntuDesktopSandbox`, `AlpineSandbox`, `AlpineDesktopSandbox`, `AgentSandbox`, `CuaSandbox`, `QuicksandBaseScaffoldSandbox`, and `QuicksandOverlayScaffoldSandbox` all expose `image` as an explicit keyword argument with a class-specific default instead of injecting it via `**kwargs`. Existing call sites continue to work unchanged.

### Released (no user-visible changes)
- **quicksand-build-tools, quicksand-image-tools, quicksand-qemu, quicksand-smb:** version bumps only — needed to pick up the `quicksand-core` dep pin update.

## [v0.11.5] - 2026-04-30

### Added
- **quicksand-core:** Auto-install images from GitHub releases when `QUICKSAND_AUTO_INSTALL=1` is set — downloads the fat wheel and extracts images directly into site-packages
- **quicksand-image-tools:** `QUICKSAND_PURE_WHEEL=1` env var to build pure `py3-none-any` wheels without images
- **uvr_hooks:** Build pure wheels during `post_build` and filter by size before PyPI publish (>100MB → pure wheel, ≤100MB → fat wheel)

### Changed
- **quick-sandbox:** `quicksand install` now uses the public GitHub REST API — no `gh` CLI or authentication required
- **contrib packages:** Error messages when images are missing now direct users to `quicksand install`
- **README:** Clone URLs updated from SSH to HTTPS

### Fixed
- **quicksand-qemu:** Windows installer now uses a temp directory to avoid requiring admin privileges

## [v0.10.3] - 2026-04-21

### Changed
- **CI:** Switched from self-hosted runners to GitHub-hosted runners (ubuntu-latest, macos-latest, windows-latest)
- **CI:** Added `environment: release` protection to all release workflow jobs
- Removed ad-hoc test scripts

## [v0.10.2] - 2026-04-15

### Fixed
- **quicksand:** `quicksand install` now handles Windows ARM64 with emulated (x86_64) Python — downloads both arch variants, retags image wheels for pip compatibility
- **quicksand-core:** QEMU runtime resolution distinguishes "not installed" from "wrong architecture installed" with actionable error messages
- **quicksand-core:** Added `_is_emulated()` detection for platform emulation (Windows ARM64 x86_64 Python)
- **quicksand-ubuntu/alpine:** Image-not-found errors now detect emulation and guide users to `quicksand install`
- **quicksand-alpine:** README now correctly references Alpine 3.23 (was 3.21)

### Removed
- **uvr_hooks:** Removed fat QEMU wheel merge from release pipeline — slim per-arch wheels are now published separately

## [v0.10.1] - 2026-04-13

### Added
- **quicksand-qemu:** Windows ARM64 support — detects ARM64 Windows and uses the dedicated Stefan Weil ARM installer (`qemu-arm-setup-20260401.exe`)
- **quicksand-qemu:** Added `[self-hosted, windows, arm64]` build runner
- **quicksand-qemu:** ARM64 wheels now include `win_arm64` platform tag

### Fixed
- **quicksand-core:** Reverted `quicksand-qemu` from required to optional dependency — the hard dependency broke git-based installs because `quicksand-build-tools` is not published to PyPI
- **quicksand:** Restored `qemu` optional extra (`pip install quick-sandbox[qemu]`)

## [v0.10.0] - 2026-04-13

### Changed
- **quicksand-core:** `quicksand-qemu` is now a required dependency instead of optional. This ensures QEMU binaries are always available and fixes build cascade issues where rebuilding `quicksand-qemu` didn't trigger rebuilds of packages that depend on it transitively through `quicksand-core`.
- **quicksand:** Removed `qemu` optional extra (now redundant — `quicksand-core` always includes it).

## [v0.9.6] - 2026-04-08

### Added
- **quicksand-qemu:** Build hook auto-installs QEMU when not on PATH (Homebrew on macOS, apt/dnf on Linux, Stefan Weil installer on Windows)
- **quicksand-qemu:** SOURCES.md generated at build time with upstream source URLs for every bundled GPL/LGPL component
- **quicksand-qemu:** CI license verification validates SOURCES.md presence and content in built wheels

### Changed
- QEMU installation moved from CI workflow steps into the build hook for self-contained builds
- Test install step uses dynamic per-VM commands from the release plan

## [v0.9.5] - 2026-04-08

### Fixed
- **quicksand-core:** Drain stale auth responses from virtio-serial socket buffer after connect retries. Fixes `ExecuteResponseResult.__init__() got an unexpected keyword argument 'authenticated'` on slow-booting VMs (TCG emulation, no KVM).
- **quicksand-core:** Validate request/response ID correlation in `send_request` and `send_stream_request`. Mismatched IDs now return a clear error instead of silently returning the wrong result.

## [v0.9.4] - 2026-04-01

### Changed
- **quicksand-cua:** Reduced overlay image size from ~604 MB to ~445 MB by removing unused Playwright headless shell, Vulkan GPU drivers, and system Node.js

### Fixed
- **quicksand-core:** Virtio-serial agent client now keeps connection open across auth retries — disconnecting caused QEMU to stop accepting new chardev connections
- **quicksand-core:** Pin httpx<1.0 to prevent breaking API change
- **quicksand-core:** Fix `MountSpec` → `Mount` in README exports
- ARM64 image wheels built on macOS are now retagged for Linux via uvr post_build hook
- **quicksand-alpine, quicksand-ubuntu:** Restore editable install guard in build hooks
- **quicksand-alpine-desktop, quicksand-ubuntu-desktop:** Break up large package install commands to avoid build timeouts

## [v0.9.3] - 2026-03-30

### Added
- **quicksand-cua:** noVNC web client (port 6080), websockify, socat, and rsync in the overlay image

### Fixed
- `quicksand run -p HOST:GUEST` port forward parsing now works correctly

## [v0.9.2] - 2026-03-29

### Added
- `quicksand clean` CLI command to remove local `.quicksand/` and optionally global `~/.quicksand/` data directories

## [v0.9.0] - 2026-03-26

### Added
- Virtio-serial guest agent transport — 25x boot speedup (Alpine p50: 11.5s → 0.45s)
- `Sandbox.qemu_command` property to inspect the QEMU command line
- `Sandbox.boot_timing` property with phase-level profiling (kernel, init, agent breakdown)
- `BootTiming` dataclass with `__str__` for human-readable boot phase display
- `quicksand benchmark` CLI command with percentile stats, progress bar, and `--json` output
- `quicksand uninstall` CLI command to remove installed extras
- `-v`/`--mount` option on benchmark command for measuring boot with mounts

### Changed
- Guest images now use static IP (10.0.2.15/24) instead of DHCP — eliminates ~8s dhcpcd overhead
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
- **Breaking:** `ResolvedImage.base` and `ResolvedImage.overlays` replaced with unified `chain: list[Path]` — `chain[0]` is the root base qcow2, `chain[1:]` are overlay layers in bottom-to-top order
- **Breaking:** Save format bumped to v6 — only session-local overlays are stored; installed package overlays are resolved by name at load time
- `ImageProvider` protocol now requires an `images_dir: Path` attribute
- `qemu-img convert -c` preserves backing file references (`-B`) instead of flattening the full chain

### Added
- `quicksand-cua` overlay package (first release)
- `_verify_overlay_from_package()` validates that non-session overlays belong to installed packages at save time

## [v0.6.1] - 2026-03-24

### Added
- `SandboxConfig.arch` parameter for cross-architecture VM builds via TCG emulation
- `quicksand run --arch` flag for cross-arch boot from the CLI
- `quicksand install --arch` flag to download cross-platform wheels
- `quicksand run IMAGE` — image is now a required positional argument (was `-b/--base`)
- Overlay build phase in release pipeline (`build-overlay` job, runs after base images)
- `--reuse-base-build` and `--reuse-overlay-build` flags for independent build reuse
- `quicksand-base-scaffold` package for scaffolding new base image packages
- `quicksand-overlay-scaffold --base` flag to choose which base image to overlay on

### Changed
- Release pipeline `build` job renamed to `build-base` for clarity
- `quicksand install` removed legacy save-download path — all names are package installs
- Scaffold packages output to fixed directories (`packages/` for base, `packages/contrib/` for overlay)
- Workflow conditions use `fromJSON` null checks instead of `contains` string matching

### Fixed
- Release artifacts from multiple runs now merge correctly (tri-source downloads)
- KVM setup is best-effort in overlay builds (falls back to TCG gracefully)
- `_to_title` no longer uppercases short words; avoids `SandboxSandbox` in scaffold output

## [v0.6.0] - 2026-03-23

### Changed
- **Breaking:** `SandboxConfig.image` is now `str` only (was `str | Path | ImagePaths`)
- **Breaking:** `save()` returns `SaveManifest` instead of `SaveInfo`
- **Breaking:** Removed `load` parameter from `Sandbox.__init__()` — use `SandboxConfig(image="save-name")` instead
- **Breaking:** Save format bumped to v5 (directory-based, no tar support)
- **Breaking:** Removed legacy `quicksand.bases` entry point group — only `quicksand.images` is supported
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
- `SandboxConfig` is now truly frozen — never mutated after construction
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
- `NetworkMode.MOUNTS_ONLY` — mounts without internet access via guestfwd tunnels
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
- 9p as default mount protocol (replaced by CIFS; 9p remains available via `type="9p"`)

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
- Renamed `quicksand-agent` to `quicksand-guest-agent`; added package init scaffold

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
- Refactored core internals; removed `write_file`/`read_file` in favor of `execute()`
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
- Refactored host module naming: `Platform` enum renamed to `OS`; merged runtime module

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
