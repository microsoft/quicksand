"""Fixtures for full network tests - shared sandbox with NetworkMode.FULL."""

from __future__ import annotations

import pytest
import pytest_asyncio
from quicksand import NetworkMode, Sandbox

from tests.conftest import has_qemu


@pytest_asyncio.fixture(scope="module")
async def shared_sandbox(real_image, real_kernel):
    """Module-scoped sandbox with full network.

    All tests in this group share one sandbox with NetworkMode.FULL.
    """
    if not has_qemu():
        pytest.skip("QEMU not installed")

    sandbox = Sandbox(
        image="ubuntu",
        network_mode=NetworkMode.FULL,
    )
    await sandbox.start()

    # Configure DNS (not configured by default in minimal images)
    await sandbox.execute('echo "nameserver 8.8.8.8" > /etc/resolv.conf')

    yield sandbox
    await sandbox.stop()
