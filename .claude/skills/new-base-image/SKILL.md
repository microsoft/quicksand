---
name: new-base-image
description: >
  How to create base image packages that bundle a VM image built from a Dockerfile.
  Use when creating a new distro base like quicksand-ubuntu or quicksand-alpine,
  scaffolding a base image, or modifying Dockerfiles for base image builds.
---

# Creating Base Image Packages

Base image packages (like `quicksand-ubuntu`, `quicksand-alpine`) bundle a VM image built from a Dockerfile.

## Quick Start

Use the scaffolding tool:

```bash
quicksand dev scaffold base quicksand-{distro}
```

This creates `quicksand-{distro}/` from the base image template and renames everything. Then customize the Dockerfile and build with `uv build`.

## Package Structure

```
quicksand-{distro}/
├── pyproject.toml
├── hatch_build.py              # Builds image during `uv build`
├── README.md
└── quicksand_{distro}/
    ├── __init__.py             # Python API
    ├── images/                 # Built images (git-ignored)
    └── docker/
        ├── Dockerfile
        ├── quicksand-sudoers
        ├── hostname
        └── [init configs]
```

The guest agent source is copied into `docker/agent/` automatically at build time by `quicksand-image-tools`.

## Dockerfile Requirements

Every base image MUST include:

### 1. Kernel + Initramfs
```dockerfile
# Ubuntu
RUN apt-get install -y linux-image-virtual \
    && KVER=$(ls /lib/modules/ | head -1) \
    && apt-get install -y initramfs-tools \
    && update-initramfs -c -k ${KVER}

# Alpine
RUN apk add --no-cache linux-virt
```

### 2. Init System
```dockerfile
# Ubuntu (systemd)
RUN apt-get install -y systemd systemd-sysv

# Alpine (OpenRC)
RUN apk add --no-cache openrc busybox-openrc
```

### 3. 9p Modules, User, Networking, Agent
```dockerfile
# 9p for host file sharing
RUN echo "9p" >> /etc/modules && echo "9pnet" >> /etc/modules && echo "9pnet_virtio" >> /etc/modules

# quicksand user with passwordless sudo
RUN useradd -m -s /bin/bash quicksand
COPY quicksand-sudoers /etc/sudoers.d/quicksand

# Rust agent via multi-stage build
FROM rust:bookworm AS agent-builder
WORKDIR /build
COPY agent/Cargo.toml agent/Cargo.lock* ./
COPY agent/src ./src
RUN cargo build --release && strip /build/target/release/quicksand-guest-agent

# In final stage:
COPY --from=agent-builder /build/target/release/quicksand-guest-agent /usr/local/bin/

# DHCP networking + CMD
CMD ["/sbin/init"]
```

For Alpine, use `rust:alpine` with `musl-dev` for static linking.

## Python API (__init__.py)

Reference: `packages/quicksand-ubuntu/quicksand_ubuntu/__init__.py`

Must export:
- `image` — `_ImageProvider` instance (registered as `quicksand.images` entry point)
- `{Distro}Sandbox` — `Sandbox` subclass with default image kwarg

## pyproject.toml

```toml
[project]
name = "quicksand-{distro}"
version = "0.1.0"
dependencies = ["quicksand-core"]

[build-system]
requires = ["hatchling", "quicksand-image-tools"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["quicksand_{distro}"]
artifacts = [
  "quicksand_{distro}/images/*.qcow2",
  "quicksand_{distro}/images/*.kernel",
  "quicksand_{distro}/images/*.initrd",
  "quicksand_{distro}/docker/*",
]

[tool.hatch.build.targets.wheel.hooks.custom]

[project.entry-points."quicksand.images"]
{distro} = "quicksand_{distro}:image"
```

## hatch_build.py

Reference: `packages/quicksand-ubuntu/hatch_build.py`

The build hook detects architecture, sets platform-specific wheel tags, and builds the Docker image if the qcow2 doesn't exist yet.

## Release

The release pipeline automatically discovers base image packages via `provider.type = "base"` on the `quicksand.images` entry point. No manual registration needed.

## Testing

```bash
uv build --package quicksand-{distro}
```

```python
from quicksand_{distro} import {Distro}Sandbox

async with {Distro}Sandbox() as sb:
    result = await sb.execute("cat /etc/os-release")
    print(result.stdout)
```
