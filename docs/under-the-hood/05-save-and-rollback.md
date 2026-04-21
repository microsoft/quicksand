# Save and Rollback: Under the Hood

Companion to [Save and Rollback](../user-guide/05-save-and-rollback.md).

## qcow2 overlay chain

Every sandbox runs on a copy-on-write overlay, never the base image directly.

```
base image (read-only)          overlay (read-write)
ubuntu-24.04-arm64.qcow2  ←──  overlay-1234.qcow2
```

The overlay starts empty. Reads fall through to the base. Writes go to the overlay. This is why sandbox creation is instant. No disk copying is needed.

The drive flags that enable this:

```bash
-drive file=overlay.qcow2,format=qcow2,if=none,id=drive0,cache=writethrough,discard=unmap,detect-zeroes=unmap
```

| Flag | Purpose |
|---|---|
| `cache=writethrough` | Writes go to disk immediately — safe for snapshots |
| `discard=unmap` | Guest TRIM commands free qcow2 clusters, keeping overlays small |
| `detect-zeroes=unmap` | Writing zeroes is treated as a discard — also shrinks overlays |

## Checkpoints

```python
await sb.checkpoint("before-experiment")
```

Sends a QMP command through the QEMU Machine Protocol (a JSON-RPC channel on a localhost TCP port):

```bash
# QEMU is started with:
-qmp tcp:127.0.0.1:4444,server,nowait
```

```json
{"execute": "human-monitor-command", "arguments": {"command-line": "savevm before-experiment"}}
```

`savevm` captures the entire VM state (RAM contents, CPU registers, device state, and a disk snapshot) into the qcow2 file. The VM keeps running.

## Revert

```python
await sb.revert("before-experiment")
```

```json
{"execute": "human-monitor-command", "arguments": {"command-line": "loadvm before-experiment"}}
```

`loadvm` restores the VM to the exact state when `savevm` was called. Running processes, open files, and network connections all snap back. The VM resumes execution from that point.

## Saves

```python
await sb.save("dev-env")
```

Saves use a different mechanism. QMP `blockdev-snapshot-sync` pivots the overlay chain without pausing the VM.

**Step 1: Flush guest and host buffers**

```python
await sb.execute("sync")                              # Guest: flush filesystem
await sb.execute("fstrim -a")                          # Guest: TRIM freed blocks
```

```json
{"execute": "human-monitor-command", "arguments": {"command-line": "qemu-io drive0 \"flush\""}}
```

**Step 2: Atomic overlay pivot**

```json
{"execute": "blockdev-snapshot-sync", "arguments": {"device": "drive0", "snapshot-file": "/tmp/new-overlay.qcow2", "format": "qcow2", "mode": "absolute-paths"}}
```

This tells QEMU to start writing to a new overlay. The old one is now frozen. The VM never pauses. New writes go to the new overlay while the old one is copied to the save directory.

```
Before pivot:
  base.qcow2  ←  overlay-old.qcow2 (active, being written to)

After pivot:
  base.qcow2  ←  overlay-old.qcow2 (frozen)  ←  overlay-new.qcow2 (active)
```

**Step 3: Write to save directory**

The frozen overlay chain is copied to `.quicksand/sandboxes/dev-env/`, along with a `manifest.json` recording the config and architecture.

## Checkpoints vs saves at the QEMU level

| | Checkpoint (`savevm`) | Save (`blockdev-snapshot-sync`) |
|---|---|---|
| What's captured | RAM + disk + device state | Disk only |
| Where it lives | Inside the qcow2 file | Separate directory on host |
| VM pauses | Briefly (< 1s) | Never |
| Survives VM stop | No | Yes |
| QEMU mechanism | Internal snapshot | Overlay pivot |
