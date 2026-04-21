"""Fixtures for multiple mount tests - shared sandbox with multiple mounts."""

from __future__ import annotations

import pytest
import pytest_asyncio
from quicksand import OS, Mount, NetworkMode, Sandbox, get_platform_config

from tests.conftest import has_qemu


@pytest_asyncio.fixture(scope="module")
async def shared_sandbox(real_image, real_kernel, integration_tmp_dir):
    """Module-scoped sandbox with multiple mounts configured.

    All tests in this group share one sandbox with multiple mounts.
    """
    if not has_qemu():
        pytest.skip("QEMU not installed")

    # Skip on Windows - SMB mounts require specific server configuration
    # that isn't available in CI environments
    platform_config = get_platform_config()
    if platform_config.os.os_type == OS.WINDOWS:
        pytest.skip("SMB mounts not supported in Windows CI environment")

    dir1 = integration_tmp_dir / "mount1"
    dir2 = integration_tmp_dir / "mount2"
    dir1.mkdir(exist_ok=True)
    dir2.mkdir(exist_ok=True)
    (dir1 / "file1.txt").write_text("content1")
    (dir2 / "file2.txt").write_text("content2")

    sandbox = Sandbox(
        image="ubuntu",
        mounts=[
            Mount(str(dir1), "/mnt/one"),
            Mount(str(dir2), "/mnt/two"),
        ],
        network_mode=NetworkMode.FULL,
    )
    await sandbox.start()
    yield sandbox
    await sandbox.stop()


@pytest.fixture(scope="module")
def mount_dirs(integration_tmp_dir):
    """Return the mount directory paths for assertions."""
    return {
        "one": integration_tmp_dir / "mount1",
        "two": integration_tmp_dir / "mount2",
    }
