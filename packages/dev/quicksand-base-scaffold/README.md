# quicksand-base-scaffold

Scaffold a new base image package for quicksand.

Base image packages define a VM from a Dockerfile. The Dockerfile installs a Linux kernel, init system, networking, and the quicksand guest agent. The build process converts the Docker image into a qcow2 disk + kernel + initramfs that QEMU can boot.

## Usage

```bash
quicksand-base-scaffold mylinux
```

This copies `quicksand-ubuntu` into `packages/quicksand-mylinux/`, renames everything, and writes a skeleton Dockerfile.

To copy from Alpine instead:

```bash
quicksand-base-scaffold mylinux --base alpine
```

## Output Structure

```
packages/quicksand-mylinux/
├── pyproject.toml
├── hatch_build.py              # Builds image during uv build
├── README.md
└── quicksand_mylinux/
    ├── __init__.py             # Python API + entry points
    ├── images/                 # Built images (populated during uv build)
    └── docker/
        ├── Dockerfile          # Edit this
        ├── agent/              # Copied automatically at build time
        └── ...                 # Init configs
```

## Next Steps

1. Set `DISTRO_VERSION` in `quicksand_mylinux/__init__.py`

2. Edit `quicksand_mylinux/docker/Dockerfile` — your image must include:
   - Linux kernel with initramfs (for virtio drivers)
   - Init system (systemd or OpenRC)
   - 9p kernel modules (`9p`, `9pnet`, `9pnet_virtio`)
   - `quicksand` user with passwordless sudo
   - DHCP networking
   - The Rust guest agent (compiled via multi-stage Docker build)
   - `CMD ["/sbin/init"]`

3. Build:

```bash
uv sync
uv build --package quicksand-mylinux
```

4. Test:

```bash
quicksand run mylinux
```

## Dockerfile Example

See `packages/contrib/quicksand-ubuntu/quicksand_ubuntu/docker/Dockerfile` for a complete Ubuntu example, or `packages/contrib/quicksand-alpine/quicksand_alpine/docker/Dockerfile` for Alpine.

The key pattern is a multi-stage build:

```dockerfile
# Stage 1: Compile Rust agent
FROM rust:bookworm AS agent-builder
WORKDIR /build
COPY agent/Cargo.toml agent/Cargo.lock* ./
COPY agent/src ./src
RUN cargo build --release && strip /build/target/release/quicksand-guest-agent

# Stage 2: Your image
FROM your-base:version
RUN install-kernel && install-init && configure-networking
COPY --from=agent-builder /build/target/release/quicksand-guest-agent /usr/local/bin/
CMD ["/sbin/init"]
```
