# Quicksand

[![PyPI](https://img.shields.io/pypi/v/quick-sandbox)](https://pypi.org/project/quick-sandbox/)
[![Changelog](https://img.shields.io/github/v/release/microsoft/quicksand?label=changelog&logo=github)](https://github.com/microsoft/quicksand/blob/main/CHANGELOG.md)
[![Docs](https://img.shields.io/badge/docs-quicksand-blue)](https://microsoft.github.io/quicksand/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

![Quicksand](docs/banner-light.png)

Quicksand is an async Python API to launch, control, and snapshot [QEMU](https://www.qemu.org) virtual machines with a particular focus on sandboxing AI agents. Quicksand provides pre-built Linux VMs for Ubuntu and Alpine distros and supports x86_64 and ARM64 across macOS, Linux, and Windows. Running sandboxes needs no root privileges or Docker; install the QEMU and image extras for a bundled runtime on supported platforms.

## Installation

```bash
pip install 'quick-sandbox[qemu,alpine]'
```

Or install the core package and add QEMU/images separately:

```bash
pip install quick-sandbox
quicksand install qemu alpine
```

## Usage

### Hello, World!

```python
import asyncio
from quicksand import Sandbox

async def main():
    async with Sandbox(image="ubuntu") as sb:
        result = await sb.execute("echo 'Hello from the sandbox!'")
        print(result.stdout)

asyncio.run(main())
```

### Run commands

```python
result = await sb.execute("apt update && apt install -y python3")
print(result.stdout, result.exit_code)
```

### Create guest users

Give workloads their own Linux accounts and home directories inside one VM.
Commands run with the user's identity and default to their home directory.

```python
async with Sandbox(image="ubuntu") as sb:
    alice = await sb.create_user("alice")
    await alice.execute("cat > hello.txt", stdin="Hello from Alice!\n")

    result = await alice.execute("whoami && pwd && cat hello.txt")
    print(result.stdout)

    await sb.delete_user("alice")  # Also removes the user's home directory
```

Users share one VM, not separate VM isolation boundaries. Requires Ubuntu or
Alpine image packages 0.10.0 or newer, or a custom image with the updated guest agent.

### Mount host directories

Share host directories into the VM at boot or on the fly.

```python
# At boot
async with Sandbox(
    image="ubuntu",
    mounts=[Mount("./workspace", "/mnt/workspace")],
) as sb:
    ...

# Or dynamically on a running sandbox
handle = await sb.mount("/tmp/data", "/mnt/data")
await sb.execute("ls /mnt/data")
await sb.unmount(handle)
```

### Configure networking

Sandboxes are network-isolated by default. Opt in to internet access and port forwarding with `NetworkMode.FULL`.

```python
async with Sandbox(
    image="ubuntu",
    network_mode=NetworkMode.FULL,
    port_forwards=[PortForward(host=8080, guest=80)],
) as sb:
    ...
```

### Save and load

Save the VM's disk state to a directory. Load it later, even on a different machine.

```python
await sb.execute("pip install numpy pandas")
await sb.save("my-env")  # VM keeps running

# Load later
async with Sandbox(image="my-env") as sb:
    await sb.execute("python3 -c 'import numpy; print(numpy.__version__)'")
```


### Checkpoint and revert

Capture the full VM state and roll back if something goes wrong.

```python
await sb.checkpoint("before-experiment")
await sb.execute("apt install -y something-risky")
await sb.revert("before-experiment")  # the VM snaps back to the checkpoint
```

### Control a desktop

Desktop images provide a full Xfce4 graphical environment with a browser. Install one with `quicksand install ubuntu-desktop` or `quicksand install alpine-desktop`.

```python
async with Sandbox(image="ubuntu-desktop", enable_display=True) as sb:
    await sb.screenshot("screen.png")
    await sb.type_text("hello world")
    await sb.press_key(Key.RET)
    await sb.mouse_move(500, 300)
    await sb.mouse_click("left")
```

### Configuration

Here are all of the Sandbox configuration options:

```python
Sandbox(
    # Image or save name to boot
    image="ubuntu",
    # Guest RAM (default: "512M")
    memory="2G",
    # Virtual CPU cores (default: 1)
    cpus=4,
    # Host directories shared into the VM at boot
    mounts=[Mount("/host", "/guest")],
    # NONE, MOUNTS_ONLY (default), or FULL internet access
    network_mode=NetworkMode.FULL,
    # Forward host TCP ports into the guest
    port_forwards=[PortForward(host=8080, guest=80)],
    # Expand the guest filesystem on boot
    disk_size="10G",
    # Attach virtual GPU, keyboard, and mouse for screenshot/type_text/mouse control
    enable_display=True,
    # Auto-save VM state on stop
    save="my-save-name",
)
```

## Available images

| Image | Type | Wheel size | Install command | What is it |
|-------|------|-----------|-----------------|------------|
| `ubuntu` | Base | ~309-357 MB | `quicksand install ubuntu` | Ubuntu 24.04 headless |
| `alpine` | Base | ~82-85 MB | `quicksand install alpine` | Alpine 3.23 headless (faster boot) |
| `ubuntu-desktop` | Overlay (`ubuntu`) | ~273-281 MB | `quicksand install ubuntu-desktop` | Ubuntu 24.04 + Xfce4 + Firefox |
| `alpine-desktop` | Overlay (`alpine`) | ~346-363 MB | `quicksand install alpine-desktop` | Alpine 3.23 + Xfce4 + Chromium |
| `quicksand-agent` | Overlay (`ubuntu`) | ~306-335 MB | `quicksand install quicksand-agent` | Ubuntu + Python 3.12, uv, build-essential, requests, pyyaml, ddgs, markitdown |
| `quicksand-cua` | Overlay (`quicksand-agent`) | ~489-500 MB | `quicksand install quicksand-cua` | Agent Sandbox + Xvfb, x11vnc, noVNC, Playwright, Chromium |

Sizes are approximate full image-wheel downloads for the September 14, 2026 releases
and vary by architecture. Overlay sizes exclude their base images.

## Building from source

```bash
git clone https://github.com/microsoft/quicksand.git
cd quicksand
uv sync
uv run uvr build --all-packages
```

## Documentation

| Topic | Guide | Under the Hood |
|-------|-------|----------------|
| Installation | [Installing packages](docs/user-guide/01-installation.md) | [QEMU binaries, kernels, qcow2 disks](docs/under-the-hood/01-installation.md) |
| Sandbox Lifecycle | [Creating and configuring sandboxes](docs/user-guide/02-sandbox-lifecycle.md) | [`-m`, `-smp`, `-accel`, machine types](docs/under-the-hood/02-sandbox-lifecycle.md) |
| Running Commands | [`execute()`, streaming, exit codes](docs/user-guide/03-running-commands.md) | [Kernel boot, agent tokens, `hostfwd`](docs/under-the-hood/03-running-commands.md) |
| File Exchange | [Mounts, hot-mounts, getting data in/out](docs/user-guide/04-file-exchange.md) | [CIFS via `guestfwd`, 9p via `-fsdev`](docs/under-the-hood/04-file-exchange.md) |
| Save and Rollback | [Checkpoints, reverts, persistent saves](docs/user-guide/05-save-and-rollback.md) | [qcow2 overlays, `savevm`, `blockdev-snapshot-sync`](docs/under-the-hood/05-save-and-rollback.md) |
| Desktop Control | [Screenshots, keyboard, mouse](docs/user-guide/06-desktop-control.md) | [VNC, GPU, USB tablet, QMP input injection](docs/under-the-hood/06-desktop-control.md) |
| Network and Isolation | [Network modes, port forwarding](docs/user-guide/07-network-and-isolation.md) | [SLIRP NAT, `restrict=on`, `guestfwd`](docs/under-the-hood/07-network-and-isolation.md) |
| Performance | [What makes it fast](docs/user-guide/08-performance.md) | [`io_uring`, IOThreads, TCG vs KVM](docs/under-the-hood/08-performance.md) |

### Contributing

| Guide | When to use |
|-------|------------|
| [Creating Images](docs/contributor-guide/01-creating-images.md) | Build a new base or overlay image package |
| [Extending the Sandbox](docs/contributor-guide/02-extending-the-sandbox.md) | Add a method, OS, architecture, or QEMU flag |
| [Testing](docs/contributor-guide/03-testing.md) | Run or write tests |
| [Releasing](docs/contributor-guide/04-releasing.md) | Cut a release |

Full guides: [User Guide](docs/user-guide/) | [Under the Hood](docs/under-the-hood/) | [Contributor Guide](docs/contributor-guide/)
