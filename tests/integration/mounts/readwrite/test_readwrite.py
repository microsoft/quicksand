"""Tests for read-write mounts."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest


def wait_for_file(path, timeout: float = 10.0, content: str | None = None) -> bool:
    """Wait for a file to appear and optionally contain expected content."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            if content is None:
                return True
            try:
                if content in path.read_text():
                    return True
            except OSError:
                pass  # File may be in-progress
        time.sleep(0.1)
    return False


@pytest.mark.integration
@pytest.mark.slow
class TestReadWriteMount:
    """Read-write mount tests using shared sandbox."""

    @pytest.mark.asyncio
    async def test_readwrite_can_write(self, shared_sandbox, mount_dir):
        """Test writing to read-write mount."""
        await shared_sandbox.execute("echo 'from sandbox' > /mnt/host/sandbox.txt")

        # File should appear on host (instant for both 9p and SMB mounts)
        assert wait_for_file(mount_dir / "sandbox.txt", content="from sandbox")

    @pytest.mark.asyncio
    async def test_readwrite_can_read(self, shared_sandbox, mount_dir):
        """Test reading from read-write mount."""
        # Create file on host
        host_file = mount_dir / "host_file.txt"
        host_file.write_text("from host")

        # File should be visible in guest immediately (true filesystem mount)
        result = await shared_sandbox.execute("cat /mnt/host/host_file.txt")
        assert result.exit_code == 0
        assert "from host" in result.stdout

    @pytest.mark.asyncio
    async def test_directory_listing_filenames(self, shared_sandbox, mount_dir):
        """Test that ls inside the guest returns correct filenames.

        Regression test: a bug caused filenames to lose their first ~2
        characters when listed via CIFS mount (e.g. README.md -> ADME.md).
        """
        # Create files with known names on host
        test_files = ["README.md", "LICENSE", ".github", "setup.py", "pyproject.toml"]
        for name in test_files:
            path = mount_dir / name
            if not path.exists():
                if name == ".github":
                    path.mkdir(exist_ok=True)
                else:
                    path.write_text(f"content of {name}")

        # List directory inside guest
        result = await shared_sandbox.execute("ls -1a /mnt/host/")
        assert result.exit_code == 0

        listed = result.stdout.strip().split("\n")
        for name in test_files:
            assert name in listed, f"Expected '{name}' in guest ls output, got: {listed}"

    @pytest.mark.asyncio
    async def test_dynamic_mount_and_unmount(self, shared_sandbox):
        """Test dynamic hot-mount and unmount on a running sandbox."""
        with tempfile.TemporaryDirectory(prefix="quicksand-dynmount-") as tmpdir:
            hot_dir = Path(tmpdir)
            (hot_dir / "dynamic.txt").write_text("dynamic content")

            # Dynamic mount
            handle = await shared_sandbox.mount(str(hot_dir), "/mnt/dynamic")

            result = await shared_sandbox.execute("cat /mnt/dynamic/dynamic.txt")
            assert result.exit_code == 0
            assert "dynamic content" in result.stdout

            # Write from guest
            await shared_sandbox.execute("echo 'from guest' > /mnt/dynamic/guest.txt")
            assert (hot_dir / "guest.txt").exists()
            assert "from guest" in (hot_dir / "guest.txt").read_text()

            # Unmount
            await shared_sandbox.unmount(handle)

            result = await shared_sandbox.execute("ls /mnt/dynamic/ 2>&1")
            # After unmount, directory should be empty or mount point gone
            assert "dynamic.txt" not in result.stdout

    @pytest.mark.asyncio
    async def test_dynamic_readonly_mount(self, shared_sandbox):
        """Test dynamic readonly mount rejects writes."""
        with tempfile.TemporaryDirectory(prefix="quicksand-romount-") as tmpdir:
            ro_dir = Path(tmpdir)
            (ro_dir / "readonly.txt").write_text("read only content")

            handle = await shared_sandbox.mount(str(ro_dir), "/mnt/dynro", readonly=True)

            # Read should work
            result = await shared_sandbox.execute("cat /mnt/dynro/readonly.txt")
            assert result.exit_code == 0
            assert "read only content" in result.stdout

            # Write should fail
            result = await shared_sandbox.execute("touch /mnt/dynro/nope 2>&1")
            assert result.exit_code != 0

            await shared_sandbox.unmount(handle)
