"""The overlay cache — where session qcow2 overlay files live, plus the
state files that record who is claiming which overlays.

Layout under ``<cache_dir>``:

* ``overlays/<uuid>.qcow2`` — every session qcow2 quicksand allocates lives
  here. Paths are stable across venv rebuilds and across the lifetime of
  any Sandbox.
* ``state/sandbox-<sandbox_id>.json`` — claims by a running Sandbox process.
  ``kind: "sandbox"``, includes the parent Python ``pid``. The claim is
  alive iff the PID is alive.
* ``state/save-<hash>.json`` — claims by a persisted save. ``kind: "save"``,
  includes the canonical ``save_dir`` path. The claim is alive iff that
  directory still exists.

A state file's ``overlays`` list refcounts those cache entries: an overlay
is safe to delete only when no other state file claims it AND the owning
state file itself is going away.

Both kinds of state are walked by the same GC routines (``reap_stale``)
and the same cross-claim check (``is_overlay_claimed_elsewhere``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from pathlib import Path

logger = logging.getLogger("quicksand.overlay_pool")


# ---------------------------------------------------------------------------
# Cache paths
# ---------------------------------------------------------------------------


def get_overlays_dir() -> Path:
    """Return the cache subdirectory that holds session overlays.

    Layout: ``<cache_dir>/overlays/``. The directory may not exist yet;
    ``allocate_overlay_path`` creates it lazily.
    """
    from .qemu.platform import get_platform_config

    return get_platform_config().cache_dir / "overlays"


def get_state_dir() -> Path:
    """Return the cache subdirectory holding state files (sandboxes + saves).

    Layout: ``<cache_dir>/state/``. The directory may not exist yet;
    writers create it lazily.
    """
    from .qemu.platform import get_platform_config

    return get_platform_config().cache_dir / "state"


def allocate_overlay_path() -> Path:
    """Reserve a fresh unique path inside the overlay cache.

    Returns an absolute path under ``<cache_dir>/overlays/`` whose basename
    is a random UUID. Does NOT create the file — the caller (typically
    ``qemu-img create``) writes the qcow2 content.
    """
    overlays_dir = get_overlays_dir()
    overlays_dir.mkdir(parents=True, exist_ok=True)
    return overlays_dir / f"{uuid.uuid4().hex}.qcow2"


# ---------------------------------------------------------------------------
# Sandbox state files (PID-based liveness)
# ---------------------------------------------------------------------------


def state_file_path(sandbox_id: str) -> Path:
    """Return the canonical state file path for a sandbox id."""
    return get_state_dir() / f"sandbox-{sandbox_id}.json"


def write_session_state(sandbox_id: str, pid: int, overlays: list[Path]) -> Path:
    """Atomically write the sandbox state file. Returns the state file path.

    The write is atomic via tmpfile + rename, so a crash mid-write either
    leaves the previous valid state or no state file at all.
    """
    target = state_file_path(sandbox_id)
    payload = {
        "kind": "sandbox",
        "sandbox_id": sandbox_id,
        "pid": pid,
        "overlays": [str(p) for p in overlays],
    }
    _atomic_write(target, payload)
    return target


def clear_session_state(sandbox_id: str) -> None:
    """Remove a sandbox's state file. No-op if it doesn't exist."""
    state_file_path(sandbox_id).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Save state files (save_dir-existence liveness)
# ---------------------------------------------------------------------------


def save_state_file_path(save_dir: Path) -> Path:
    """Return the canonical state file path for a save directory.

    Keyed by an MD5 of the absolute save_dir path so renames produce a new
    state file and arbitrary save-name characters don't leak into the
    filesystem. The payload contains the actual ``save_dir`` for liveness
    checks.
    """
    canonical = str(save_dir.resolve() if save_dir.exists() else save_dir.absolute())
    digest = hashlib.md5(canonical.encode("utf-8")).hexdigest()
    return get_state_dir() / f"save-{digest}.json"


def write_save_state(save_dir: Path, overlays: list[Path]) -> Path:
    """Atomically write a save's state file claiming cached overlays.

    The state file lives until ``clear_save_state(save_dir)`` is called (or
    until reap detects that ``save_dir`` no longer exists).
    """
    target = save_state_file_path(save_dir)
    canonical = str(save_dir.resolve() if save_dir.exists() else save_dir.absolute())
    payload = {
        "kind": "save",
        "save_dir": canonical,
        "overlays": [str(p) for p in overlays],
    }
    _atomic_write(target, payload)
    return target


def clear_save_state(save_dir: Path) -> None:
    """Remove a save's state file. No-op if it doesn't exist."""
    save_state_file_path(save_dir).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# GC and claim checks (kind-agnostic)
# ---------------------------------------------------------------------------


def reap_stale_sandboxes() -> int:
    """Sweep state files whose owning entity is gone, plus orphan overlays.

    Two passes:

    1. **Dead claims**: walk ``state/*.json`` and dispatch on ``kind``.
       Sandbox entries are checked via PID liveness, save entries via
       ``save_dir`` existence. Dead claims have their state file removed,
       then any overlay no longer claimed by a surviving state file is
       unlinked.
    2. **Orphan overlays**: any ``overlays/*.qcow2`` not referenced by any
       surviving state file gets unlinked. Catches overlays orphaned by a
       ``save delete``, by a manual state-file removal, or by races/bugs.

    Returns the number of state files reaped (orphan sweep stats are
    swallowed since they're a single number that's less interesting to
    callers). Name retained for backward compatibility.
    """
    state_dir = get_state_dir()
    reaped = 0
    if state_dir.exists():
        for path in state_dir.glob("*.json"):
            if not _is_known_state_file(path):
                continue
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                continue
            if _is_state_alive(data):
                continue
            # Drop the state file first so the claim-elsewhere check
            # correctly excludes the dead entity.
            path.unlink(missing_ok=True)
            _delete_listed(data.get("overlays") or [])
            reaped += 1

    _sweep_orphan_overlays()

    if reaped:
        logger.debug("Reaped %d stale state file(s)", reaped)
    return reaped


def _sweep_orphan_overlays() -> int:
    """Delete cached overlays not referenced by any surviving state file.

    Returns the number swept.
    """
    overlays_dir = get_overlays_dir()
    if not overlays_dir.exists():
        return 0
    claimed = _collect_claimed_paths()
    swept = 0
    for path in overlays_dir.glob("*.qcow2"):
        if str(path) in claimed:
            continue
        try:
            path.unlink()
            swept += 1
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.debug("Skipping orphan overlay %s: %s", path, e)
    return swept


def _collect_claimed_paths() -> set[str]:
    """Aggregate every overlay path mentioned by any live state file."""
    state_dir = get_state_dir()
    if not state_dir.exists():
        return set()
    claimed: set[str] = set()
    for path in state_dir.glob("*.json"):
        if not _is_known_state_file(path):
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for raw in data.get("overlays") or []:
            claimed.add(str(Path(raw)))
    return claimed


def cleanup_for_state_file(state_file: Path) -> None:
    """Read a state file and delete its listed overlays plus the file itself.

    Used by :mod:`quicksand_core._reaper` for eager cleanup when the parent
    Python process dies. Skips overlays that another live state file still
    claims (a sibling fork, parent, or save). Best-effort — failures are
    swallowed so the reaper can finish even when the cache layout is
    unexpected.
    """
    try:
        data = json.loads(state_file.read_text())
    except (OSError, json.JSONDecodeError):
        state_file.unlink(missing_ok=True)
        return
    state_file.unlink(missing_ok=True)
    for raw in data.get("overlays") or []:
        overlay = Path(raw)
        if _is_claimed_elsewhere(overlay):
            continue
        try:
            overlay.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.debug("Skipping orphan overlay %s: %s", overlay, e)


def is_overlay_claimed_elsewhere(
    overlay: Path,
    *,
    exclude_sandbox_id: str | None = None,
    exclude_save_dir: Path | None = None,
) -> bool:
    """True if any state file other than the caller's claims ``overlay``.

    ``exclude_sandbox_id`` and ``exclude_save_dir`` let the caller skip
    their own claim when doing the check.
    """
    exclude_path: Path | None = None
    if exclude_sandbox_id is not None:
        exclude_path = state_file_path(exclude_sandbox_id)
    elif exclude_save_dir is not None:
        exclude_path = save_state_file_path(exclude_save_dir)
    return _is_claimed_elsewhere(overlay, exclude=exclude_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _atomic_write(target: Path, payload: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, target)


def _is_known_state_file(path: Path) -> bool:
    name = path.name
    return name.startswith("sandbox-") or name.startswith("save-")


def _is_state_alive(data: dict) -> bool:
    """Dispatch liveness on the state file's ``kind``."""
    kind = data.get("kind", "sandbox")
    if kind == "sandbox":
        pid = data.get("pid")
        return isinstance(pid, int) and pid > 0 and _is_pid_alive(pid)
    if kind == "save":
        save_dir = data.get("save_dir")
        return isinstance(save_dir, str) and Path(save_dir).is_dir()
    # Unknown kind — treat as dead so it can be reaped.
    return False


def _delete_listed(paths: list, exclude: Path | None = None) -> None:
    for raw in paths:
        overlay = Path(raw)
        if _is_claimed_elsewhere(overlay, exclude=exclude):
            continue
        try:
            overlay.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.debug("Skipping orphan overlay %s: %s", raw, e)


def _is_claimed_elsewhere(overlay: Path, exclude: Path | None = None) -> bool:
    """True if any state file (other than ``exclude``) claims ``overlay``."""
    state_dir = get_state_dir()
    if not state_dir.exists():
        return False
    try:
        exclude_resolved = exclude.resolve() if exclude else None
    except OSError:
        exclude_resolved = None
    overlay_str = str(overlay)
    for state in state_dir.glob("*.json"):
        if not _is_known_state_file(state):
            continue
        try:
            if exclude_resolved and state.resolve() == exclude_resolved:
                continue
        except OSError:
            continue
        try:
            data = json.loads(state.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if overlay_str in (data.get("overlays") or []):
            return True
    return False


def _is_pid_alive(pid: int) -> bool:
    """Cross-platform best-effort PID liveness check.

    POSIX: ``os.kill(pid, 0)`` raises ``ProcessLookupError`` for dead PIDs
    and ``PermissionError`` for live-but-owned-by-someone-else. Windows:
    ``os.kill(pid, 0)`` may raise ``OSError`` with different errnos; we
    treat any non-``ProcessLookupError`` outcome as "alive" so we err on
    the side of preserving files.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
