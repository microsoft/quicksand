"""Fixtures for readonly mount tests - shared sandbox with readonly mount."""

from __future__ import annotations

import pytest
import pytest_asyncio
from quicksand import OS, Mount, NetworkMode, Sandbox, get_platform_config

from tests.conftest import has_qemu


@pytest_asyncio.fixture(scope="module")
async def shared_sandbox(real_image, real_kernel, integration_tmp_dir):
    """Module-scoped sandbox with readonly mount configured.

    All tests in this group share one sandbox with a readonly mount.
    """
    if not has_qemu():
        pytest.skip("QEMU not installed")

    # Skip on Windows - SMB mounts require specific server configuration
    # that isn't available in CI environments
    platform_config = get_platform_config()
    if platform_config.os.os_type == OS.WINDOWS:
        pytest.skip("SMB mounts not supported in Windows CI environment")

    mount_dir = integration_tmp_dir / "readonly"
    mount_dir.mkdir(exist_ok=True)
    (mount_dir / "test.txt").write_text("host content")

    sandbox = Sandbox(
        image="ubuntu",
        mounts=[Mount(str(mount_dir), "/mnt/host", readonly=True)],
        network_mode=NetworkMode.FULL,
    )
    await sandbox.start()
    yield sandbox
    await sandbox.stop()


@pytest.fixture(scope="module")
def mount_dir(integration_tmp_dir):
    """Return the mount directory path for assertions."""
    return integration_tmp_dir / "readonly"
