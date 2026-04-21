# File Exchange

*See [Under the Hood: File Exchange](../under-the-hood/04-file-exchange.md) for how CIFS and 9p mounts work at the QEMU level.*

All examples below assume:
```python
from quicksand import Sandbox, Mount, NetworkMode
```

## Boot-time mounts

Declare directories to share when creating the sandbox:

```python
async with Sandbox(
    image="ubuntu",
    mounts=[
        Mount("/host/code", "/mnt/code"),
        Mount("/host/data", "/mnt/data", readonly=True),
    ],
) as sb:
    result = await sb.execute("ls /mnt/code")
    await sb.execute("python3 /mnt/code/main.py")
```

Files appear inside the VM at the specified guest path. Changes to non-readonly mounts are visible on both sides immediately.

## Dynamic hot-mounts

Share a directory into an already-running sandbox:

```python
async with Sandbox(image="ubuntu") as sb:
    # Mount at any time after start
    handle = await sb.mount("/host/project", "/mnt/project")

    await sb.execute("ls /mnt/project")
    await sb.execute("echo 'new file' > /mnt/project/output.txt")

    # Unmount when done
    await sb.unmount(handle)
```

Hot-mounts use the same CIFS file-sharing protocol as boot-time mounts. New shares can be added at any time. No restart is needed.

## Read-only mounts

Prevent the guest from modifying host files:

```python
# Boot-time
Mount("/host/data", "/mnt/data", readonly=True)

# Dynamic
handle = await sb.mount("/host/data", "/mnt/data", readonly=True)
```

## Getting files out without mounts

If you just need to read a small file from the guest, `execute()` works:

```python
result = await sb.execute("cat /etc/hostname")
hostname = result.stdout.strip()
```

To write a file into the guest:

```python
await sb.execute("echo 'hello' > /tmp/greeting.txt")
```

For larger file transfers, mounts are more efficient.

## Listing active mounts

```python
for handle in sb.active_mounts:
    print(f"{handle.host_path} → {handle.guest_path} (readonly={handle.readonly})")
```

## 9p mounts (no network)

If you need file sharing with `NetworkMode.NONE` (no network at all), use 9p:

```python
sb = Sandbox(
    image="ubuntu",
    network_mode=NetworkMode.NONE,
    mounts=[Mount("/host/data", "/mnt/data", type="9p")],
)
```

9p mounts are configured at launch time and can't be hot-added. They use a virtual device instead of the network stack.

## Which mount type to use

| | CIFS (default) | 9p |
|---|---|---|
| Hot-mountable | Yes | No (boot-time only) |
| Works with `NONE` | No | Yes |
| Works with `MOUNTS_ONLY` / `FULL` | Yes | Yes |
| Protocol | Network (SMB3) | Virtual device |
