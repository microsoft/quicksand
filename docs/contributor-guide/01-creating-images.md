# Creating Images

There are two kinds: **base images** built from Dockerfiles, and **overlay images** built by running setup commands on a running base VM.

## Prerequisites

Install dev tools:
```bash
quicksand install dev
# or: pip install 'quicksand[dev]'
```

This gives you the `quicksand dev` CLI with scaffold and image-building commands.

---

## Base images

Base image packages (like `quicksand-ubuntu`) bundle a kernel, initrd, and qcow2 disk built from a Dockerfile.

### Scaffold

```bash
quicksand dev scaffold base quicksand-mylinux
```

This creates `quicksand-mylinux/` with a Dockerfile, build hook, Python API, and pyproject.toml. In Claude Code, `/new-base-image` wraps this interactively.

Use `--output-dir` to control where the package is created (defaults to `./<name>`).

### Customize the Dockerfile

Edit `quicksand_mylinux/docker/Dockerfile`. Every base image must provide:

**Kernel + initramfs:**
```dockerfile
# Ubuntu
RUN apt-get install -y linux-image-virtual \
    && KVER=$(ls /lib/modules/ | head -1) \
    && update-initramfs -c -k ${KVER}

# Alpine
RUN apk add --no-cache linux-virt
```

**Init system:**
```dockerfile
# Ubuntu (systemd)
RUN apt-get install -y systemd systemd-sysv

# Alpine (OpenRC)
RUN apk add --no-cache openrc busybox-openrc
```

**9p modules, user, networking, agent:**
```dockerfile
RUN echo "9p" >> /etc/modules && echo "9pnet" >> /etc/modules && echo "9pnet_virtio" >> /etc/modules
RUN useradd -m -s /bin/bash quicksand
COPY quicksand-sudoers /etc/sudoers.d/quicksand

# Agent via multi-stage build
FROM rust:bookworm AS agent-builder
COPY agent/ /build/
RUN cd /build && cargo build --release && strip target/release/quicksand-guest-agent

# In final stage:
COPY --from=agent-builder /build/target/release/quicksand-guest-agent /usr/local/bin/
CMD ["/sbin/init"]
```

### Build and test

```bash
uv build
```

The hatch build hook runs `quicksand_image_tools.build_image()` automatically: Docker build, export filesystem, extract kernel/initrd, create ext4, convert to qcow2.

```python
import asyncio
from quicksand import Sandbox

async def main():
    async with Sandbox(image="mylinux") as sb:
        print((await sb.execute("cat /etc/os-release")).stdout)

asyncio.run(main())
```

### Key files

- `docker/Dockerfile` is the VM definition
- `__init__.py` exports `image` (ImageProvider) and `MylinuxSandbox` (thin wrapper accepting `**kwargs`)
- `pyproject.toml` registers the `quicksand.images` entry point
- `hatch_build.py` builds the image during `uv build`

Reference: `packages/quicksand-ubuntu/` for a complete example.

---

## Overlay images

Overlay packages (like `aif-agent-sandbox`) boot a base VM, run setup commands, and save the result. They layer on top of existing base images and can stack on each other.

### Scaffold

```bash
quicksand dev scaffold overlay my-overlay --base ubuntu
```

`--base` is required. It specifies which image the overlay builds on. This can be a base image (`ubuntu`, `alpine`) or another overlay (`aif-agent-sandbox`). Stacking overlays is encouraged because each layer only stores its own changes, keeping wheel sizes small.

Creates `my-overlay/` in the current directory. Use `--output-dir` to override. In Claude Code, `/new-overlay-image` wraps this interactively.

### Write the setup function

Edit `hatch_build.py`:

```python
async def _setup(shell: Shell) -> None:
    await shell("apt-get update", timeout=120)
    await shell("apt-get install -y your-packages", timeout=300)
    await shell("pip install your-python-deps", timeout=300)
```

`shell()` runs commands inside the VM, streams output, and raises on failure.

### Build and test

```bash
uv build
```

The first build boots a real VM, runs `_setup()`, and saves the overlay. Subsequent builds reuse the cached overlay. To force a rebuild, delete the `images/` directory and rebuild.

### Key files

- `hatch_build.py` contains `_setup()` and the build hook
- `__init__.py` exports `image` (ImageProvider)
- `pyproject.toml` registers entry points and declares the base image dependency

Reference: `packages/contrib/aif-agent-sandbox/` for a complete example.

---

## Release integration

The release pipeline auto-discovers image packages via the `quicksand.images` entry point. It uses `provider.type = "base"` for base images and `provider.type = "overlay"` for overlays. No manual registration needed.
