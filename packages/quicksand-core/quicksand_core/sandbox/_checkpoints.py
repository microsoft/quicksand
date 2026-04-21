"""Ephemeral in-session QMP snapshot operations (checkpoint/revert) for the Sandbox class."""

from __future__ import annotations

import logging

from ._protocol import _SandboxProtocol

logger = logging.getLogger("quicksand.sandbox")


class _CheckpointMixin(_SandboxProtocol):
    """Mixin providing ephemeral in-session QMP snapshots: checkpoint and revert.

    Dependencies (is_running, _qmp_client, _qmp_checkpoints) are declared
    by _SandboxProtocol.
    """

    async def checkpoint(self, tag: str) -> None:
        """Save the current VM state (RAM + disk) as an ephemeral in-session snapshot.

        The snapshot lives only in the current session's overlay. It is NOT
        persisted to disk and is NOT included when save() is called — save()
        deletes all checkpoint snapshots before freezing the overlay.

        Use checkpoint/revert for in-session branching and rollback:

            await sb.checkpoint("before-install")
            await sb.execute("pip install heavy-package")
            # something went wrong — roll back
            await sb.revert("before-install")

        Args:
            tag: Name for the snapshot (e.g. "before-install").
        """
        if not self.is_running:
            raise RuntimeError("Cannot checkpoint a non-running sandbox")
        if self._qmp_client is None:
            raise RuntimeError("QMP is not connected")
        await self._qmp_client.execute(
            "human-monitor-command",
            **{"command-line": f"savevm {tag}"},
        )
        if tag not in self._qmp_checkpoints:
            self._qmp_checkpoints.append(tag)

    async def revert(self, tag: str) -> None:
        """Restore the VM state (RAM + disk) from an ephemeral in-session snapshot.

        The VM resumes execution from the point when checkpoint(tag) was called.
        Only snapshots created in the current session are restorable.

        Args:
            tag: Name of the snapshot to restore.

        Raises:
            ValueError: If no snapshot with that tag exists in this session.
        """
        if not self.is_running:
            raise RuntimeError("Cannot revert a non-running sandbox")
        if self._qmp_client is None:
            raise RuntimeError("QMP is not connected")
        if tag not in self._qmp_checkpoints:
            available = self._qmp_checkpoints or ["(none)"]
            raise ValueError(
                f"No checkpoint '{tag}' in current session. Available: {', '.join(available)}"
            )
        await self._qmp_client.execute(
            "human-monitor-command",
            **{"command-line": f"loadvm {tag}"},
        )
