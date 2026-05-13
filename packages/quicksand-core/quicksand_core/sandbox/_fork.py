"""Sandbox.fork() — branch a running Sandbox into a sibling.

The pivot is the same machinery ``save()`` uses: ``blockdev-snapshot-sync``
atomically freezes the current top overlay and the parent keeps running on
a fresh one. The frozen overlay then forms the bottom of a new
``Sandbox``'s backing chain. Both sandboxes can read it (qcow2 allows
many top overlays sharing one backing file); their lifetimes are decoupled
because the per-Sandbox state files refcount the shared overlay via the
``_overlay_cache.is_overlay_claimed_elsewhere`` check.

The returned Sandbox is unstarted. Caller drives ``start()`` or
``async with`` to boot it. Disk-only — no RAM transfer; the fork boots
fresh from the frozen disk state.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Unpack

from .._types import ResolvedImage, SandboxConfig, SandboxConfigParams
from ._protocol import _SandboxProtocol

if TYPE_CHECKING:
    from ._sandbox import Sandbox


class _ForkMixin(_SandboxProtocol):
    """Adds ``fork()`` to the Sandbox public surface."""

    async def fork(
        self,
        *,
        delete_checkpoints: bool = False,
        progress_callback: Callable[[str, int, int], None] | None = None,
        save: str | None = None,
        workspace: str | Path | None = None,
        **kwargs: Unpack[SandboxConfigParams],
    ) -> Sandbox:
        """Branch this Sandbox onto a sibling running on the frozen state.

        Pivots this Sandbox to a new active overlay (existing VM keeps
        running, parent's RAM/state intact) and returns a fresh unstarted
        Sandbox whose backing chain ends at the just-frozen overlay. The
        child boots from disk — no RAM is transferred.

        Accepts the same constructor options as ``Sandbox.__init__`` except
        ``image`` (the child inherits this Sandbox's image and chain).
        Keyword overrides in ``**kwargs`` shadow the inherited config —
        useful when the child should run with different resources or
        network mode than its parent. ``mounts`` defaults to ``[]`` (they're
        runtime state, not part of the disk) but can be overridden.

        Args:
            delete_checkpoints: If True, delete any active in-session
                checkpoint snapshots before pivoting. If False (default)
                and snapshots exist, raises RuntimeError.
            progress_callback: Optional boot-progress callback for the
                child (forwarded to its constructor).
            save: Optional auto-save name for the child, persisted on
                child stop. Not inherited from the parent.
            workspace: Optional workspace path for the child's saves.
            **kwargs: Any field of ``SandboxConfigParams`` to override
                (e.g. ``memory="4G"``, ``cpus=4``, ``network_mode=...``).

        Returns:
            An unstarted ``Sandbox``. Use ``async with`` or call
            ``await child.start()`` to boot it.

        Raises:
            RuntimeError: If this Sandbox is not running, QMP is not
                connected, or active checkpoints exist (without
                ``delete_checkpoints=True``).
        """
        from .._overlay_cache import get_overlays_dir, write_session_state
        from ._sandbox import Sandbox

        if not self.is_running:
            raise RuntimeError("Cannot fork a non-running sandbox")
        if self._qmp_client is None:
            raise RuntimeError(
                "QMP is not connected — cannot fork a running sandbox. "
                "This should not happen; QMP is started automatically with the VM."
            )
        if self._qmp_checkpoints and not delete_checkpoints:
            tags = self._qmp_checkpoints
            raise RuntimeError(
                f"fork() called with active checkpoint snapshots: {tags}. "
                "These snapshots will not be accessible after the fork pivot. "
                "Pass delete_checkpoints=True to delete them before forking."
            )

        assert self._image is not None
        assert self._overlay_manager is not None

        # 1. Atomic pivot. ``frozen`` is the previous top; this Sandbox now
        #    writes to a fresh new overlay (recorded in self._session_overlays).
        frozen = await self._pivot_overlay(delete_checkpoints=delete_checkpoints)

        # 2. Build the child's backing chain. Start with the base image and
        #    append every overlay below the frozen one (intermediates from
        #    earlier saves/forks), then the frozen overlay itself. Skip the
        #    final element (the new active overlay) — that belongs to this
        #    Sandbox going forward.
        base = self._image.chain[0]
        full_chain = self._overlay_manager.get_overlay_chain(frozen, base)
        # full_chain returns bottom-to-top, ending at ``frozen``.
        child_chain: list = [base, *full_chain]

        child_image = ResolvedImage(
            name=self._image.name,
            chain=child_chain,
            kernel=self._image.kernel,
            initrd=self._image.initrd,
            guest_arch=self._image.guest_arch,
        )

        # 3. Inherit parent's config, default mounts to [] (they're runtime
        #    state, not part of the disk), then let user-supplied kwargs
        #    override anything they want for the child. Pass through
        #    SandboxConfig validation so overrides get the same coercion
        #    rules as the constructor.
        config_dict = self.config.model_dump()
        config_dict["mounts"] = []
        config_dict.update(kwargs)
        child_config = SandboxConfig.model_validate(config_dict)

        # 4. Construct an unstarted Sandbox with the pre-resolved image.
        child = Sandbox._from_resolved(
            image=child_image,
            config=child_config,
            progress_callback=progress_callback,
            save=save,
            workspace=workspace,
        )

        # 5. Claim the shared cached overlays in the child's state file
        #    immediately, before returning, so a stop on this Sandbox in the
        #    window between fork() and child.start() respects the claim.
        pool_root = get_overlays_dir().resolve()
        for p in child_chain[1:]:
            try:
                resolved = p.resolve()
            except OSError:
                continue
            if resolved.is_relative_to(pool_root):
                child._session_overlays.append(p)
        if child._session_overlays:
            write_session_state(child._sandbox_id, os.getpid(), child._session_overlays)

        return child
