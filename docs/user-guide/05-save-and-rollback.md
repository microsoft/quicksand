# Save and Rollback

*See [Under the Hood: Save and Rollback](../under-the-hood/05-save-and-rollback.md) for how checkpoints use QMP `savevm`/`loadvm` and saves use overlay pivoting.*

Quicksand gives agents two kinds of snapshots. **Checkpoints** are for in-session rollback, and **saves** are for persisting to disk.

## Checkpoints (in-session rollback)

A checkpoint captures the entire VM state (RAM, disk, running processes) at a point in time. Reverting snaps the VM back to that exact state.

```python
async with Sandbox(image="ubuntu") as sb:
    await sb.execute("apt update")

    # Checkpoint before doing something risky
    await sb.checkpoint("before-experiment")

    result = await sb.execute("apt install -y some-experimental-package")
    if result.exit_code != 0:
        # Something went wrong — roll back everything
        await sb.revert("before-experiment")
        # The VM is now exactly as it was before the install attempt
```

Checkpoints are ephemeral. They live inside the VM's disk image and are lost when the sandbox stops. They're for in-session branching, not for persistence.

You can create multiple checkpoints:

```python
await sb.checkpoint("step-1")
await sb.execute("pip install pandas")

await sb.checkpoint("step-2")
await sb.execute("pip install sklearn")

# Jump back to any checkpoint
await sb.revert("step-1")  # pandas is gone, sklearn is gone
```

Reverting restores the VM completely, including running processes, open files, and everything else. The VM continues from the exact point when `checkpoint()` was called.

## Saves (persist to disk)

A save copies the VM's disk state to a directory on the host. Unlike checkpoints, saves survive across sessions.

**Manual save.** The VM keeps running.

```python
async with Sandbox(image="ubuntu") as sb:
    await sb.execute("apt install -y python3 git")
    await sb.save("dev-env")
    # VM is still running — you can keep working
    await sb.execute("git clone https://...")
    await sb.save("dev-env-with-repo")
```

**Auto-save on stop:**

```python
async with Sandbox(image="ubuntu", save="my-env") as sb:
    await sb.execute("apt install -y python3")
# Automatically saved to .quicksand/sandboxes/my-env/ on exit
```

**Resume from a save:**

```python
async with Sandbox(image="my-env") as sb:
    result = await sb.execute("python3 --version")
    # Python is still installed
```

Saves only capture disk state (not RAM). The VM boots fresh when loaded from a save, but all installed packages, files, and configuration are preserved.

## Save options

```python
manifest = await sb.save(
    "my-env",
    workspace="/custom/path",    # Save location (default: .quicksand/)
    compress=True,               # Compress overlays for smaller saves
    delete_checkpoints=True,     # Delete in-session checkpoints before saving
)
```

`save()` returns a `SaveManifest` with the save's metadata.

By default, `save()` raises an error if there are active checkpoints (because they can't be restored after the save). Pass `delete_checkpoints=True` to clear them first.

## Validating a save

Check if a save directory is valid without loading it:

```python
manifest = Sandbox.validate_save(".quicksand/sandboxes/my-env")
print(manifest.version, manifest.config.image)
```

## Checkpoint vs save

| | Checkpoint | Save |
|---|---|---|
| What's captured | RAM + disk (entire VM state) | Disk only |
| Persists after stop | No | Yes |
| VM keeps running | Yes | Yes |
| Resume behavior | Exact state (running processes, everything) | Fresh boot (but disk state preserved) |
| Use case | Try something, revert if it fails | Persist progress across sessions |

## Common patterns

**Explore-and-revert:**
```python
await sb.checkpoint("clean")
for approach in approaches:
    result = await sb.execute(approach)
    if result.exit_code == 0:
        break
    await sb.revert("clean")
```

**Progressive saves:**
```python
await sb.execute("apt install -y build-essential")
await sb.save("base-tools")
await sb.execute("apt install -y python3 python3-pip")
await sb.save("python-env")
await sb.execute("pip install -r requirements.txt")
await sb.save("project-ready")
```

**Save before long-running work:**
```python
await sb.save("before-training")
result = await sb.execute("python3 train.py", timeout=3600)
if result.exit_code == 0:
    await sb.save("after-training")
```
