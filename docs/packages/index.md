# Packages

Quicksand is a monorepo with 15 packages organized into four groups.

## Runtime

| Package | What it is |
|---------|-----------|
| [quicksand](quicksand.md) | User-facing wrapper — re-exports core + images, CLI |
| [quicksand-core](quicksand-core.md) | Core VM sandbox implementation |
| [quicksand-qemu](quicksand-qemu.md) | Bundled QEMU binaries (per-platform) |
| [quicksand-smb](quicksand-smb.md) | Pure-Python SMB3 server for host-guest mounts |

## Images

| Package | What it is |
|---------|-----------|
| [quicksand-ubuntu](quicksand-ubuntu.md) | Ubuntu 24.04 headless (~341 MB) |
| [quicksand-alpine](quicksand-alpine.md) | Alpine 3.21 headless (~78 MB) |
| [quicksand-ubuntu-desktop](quicksand-ubuntu-desktop.md) | Ubuntu 24.04 + Xfce4 + Firefox |
| [quicksand-alpine-desktop](quicksand-alpine-desktop.md) | Alpine 3.21 + Xfce4 + Chromium |
| [aif-agent-sandbox](aif-agent-sandbox.md) | Ubuntu + Python 3.12, uv, build tools |
| [aif-cua-agent-sandbox](aif-cua-agent-sandbox.md) | AIF Agent Sandbox + Playwright, Chromium |

## Dev tools

| Package | What it is |
|---------|-----------|
| [quicksand-image-tools](quicksand-image-tools.md) | CLI and API for building custom VM images |
| [quicksand-base-scaffold](quicksand-base-scaffold.md) | Scaffold new base image packages |
| [quicksand-overlay-scaffold](quicksand-overlay-scaffold.md) | Scaffold new overlay image packages |
| [quicksand-build-tools](quicksand-build-tools.md) | Shared build utilities for native binary bundling |
| [quicksand-gh-runners](quicksand-gh-runners.md) | GitHub Actions self-hosted runner management |
