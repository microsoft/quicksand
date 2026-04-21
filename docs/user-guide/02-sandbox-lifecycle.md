# Sandbox Lifecycle

*See [Under the Hood: Sandbox Lifecycle](../under-the-hood/02-sandbox-lifecycle.md) for how these fields translate to QEMU flags.*

## Quick start

```python
from quicksand import Sandbox, Mount, NetworkMode, PortForward

async with Sandbox(image="ubuntu") as sb:
    result = await sb.execute("uname -a")
    print(result.stdout)
# VM is automatically stopped and cleaned up here
```

The `async with` pattern handles `start()` and `stop()` for you. The VM boots in ~2-3 seconds and is fully isolated from the host.

## Configuration

All configuration is passed as keyword arguments to `Sandbox(...)`:

```python
sb = Sandbox(
    image="ubuntu",              # Which OS image to use
    memory="2G",                 # RAM (default: "512M")
    cpus=4,                      # CPU cores (default: 1)
    enable_display=True,         # Enable GUI for screenshots/input (default: False)
    network_mode=NetworkMode.FULL,  # Internet access (default: MOUNTS_ONLY)
    port_forwards=[PortForward(host=8080, guest=80)],
    mounts=[                     # Share host directories into the VM
        Mount("/host/code", "/mnt/code"),
    ],
    disk_size="10G",             # Expand disk (default: image size)
    accel="auto",                # Hardware acceleration (default: auto-detect)
    extra_qemu_args=[],          # Escape hatch for advanced QEMU flags
)
```

Most fields have sensible defaults. The minimal call is just `Sandbox(image="ubuntu")`.

## Start modes

**Ephemeral** (default). The VM starts fresh and is discarded on stop.

```python
async with Sandbox(image="ubuntu") as sb:
    await sb.execute("apt install -y python3")
# python3 is gone — the VM was discarded
```

**Named save.** The VM auto-saves to disk when it stops.

```python
async with Sandbox(image="ubuntu", save="my-env") as sb:
    await sb.execute("apt install -y python3")
# saved to .quicksand/sandboxes/my-env/
```

**From save.** Resume from a previously saved state.

```python
async with Sandbox(image="my-env") as sb:
    result = await sb.execute("python3 --version")
    # Python 3.x — it's still installed
```

**Named + from save.** Load a save and auto-save under a new name.

```python
async with Sandbox(image="my-env", save="my-env-v2") as sb:
    await sb.execute("pip install numpy")
# saved to .quicksand/sandboxes/my-env-v2/
```

## Manual lifecycle

If you need finer control than `async with`:

```python
sb = Sandbox(image="ubuntu")
await sb.start()

# ... use the sandbox ...

await sb.stop()  # auto-saves if save= was set
```

## Properties

```python
sb.is_running       # bool — is the VM running?
sb.accelerator      # Accelerator enum — KVM, HVF, WHPX, or TCG
sb.vnc_port         # int | None — VNC port for debugging (if display enabled)
sb.boot_timeout     # float — effective boot timeout in seconds
sb.active_mounts    # list[MountHandle] — currently mounted directories
```

## Progress callback

Track startup progress:

```python
def on_progress(stage: str, current: int, total: int):
    print(f"{stage}: {current}/{total}")

sb = Sandbox(image="ubuntu", progress_callback=on_progress)
```
