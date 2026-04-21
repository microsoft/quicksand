"""Tests for save/load (restore) cycle."""

from __future__ import annotations

import warnings

import pytest
from quicksand import OS, Mount, NetworkMode, Sandbox, get_platform_config

from tests.conftest import skip_no_qemu


@pytest.mark.integration
@pytest.mark.slow
class TestSaveLoad:
    """Save and load (restore) tests."""

    @skip_no_qemu
    @pytest.mark.asyncio
    async def test_save_load_cycle(self, real_image, real_kernel, tmp_dir):
        """Test full save and load cycle."""
        # Use /home/quicksand since /tmp is cleared on boot by systemd
        async with Sandbox(image="ubuntu") as sb:
            await sb.execute("echo 'save test' > /home/quicksand/marker.txt")
            await sb.save("my-save", workspace=tmp_dir)

        loaded = Sandbox(image=str(tmp_dir / "my-save"))
        async with loaded:
            result = await loaded.execute("cat /home/quicksand/marker.txt")
            assert result.exit_code == 0
            assert "save test" in result.stdout

    @skip_no_qemu
    @pytest.mark.asyncio
    async def test_load_clears_mounts_by_default(self, real_image, real_kernel, tmp_dir):
        """Test that load clears mounts by default."""
        # Skip on Windows - SMB mounts not supported in CI
        platform_config = get_platform_config()
        if platform_config.os.os_type == OS.WINDOWS:
            pytest.skip("SMB mounts not supported in Windows CI environment")

        mount_dir = tmp_dir / "mount"
        mount_dir.mkdir()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            async with Sandbox(
                image="ubuntu",
                mounts=[Mount(str(mount_dir), "/mnt/test")],
                network_mode=NetworkMode.FULL,
            ) as sb:
                await sb.save("my-save", workspace=tmp_dir)

        loaded = Sandbox(image=str(tmp_dir / "my-save"))
        assert len(loaded.config.mounts) == 0

    @skip_no_qemu
    @pytest.mark.asyncio
    async def test_load_with_config_overrides(self, real_image, real_kernel, tmp_dir):
        """Test that config overrides work when loading a save."""
        async with Sandbox(image="ubuntu", memory="512M") as sb:
            await sb.save("my-save", workspace=tmp_dir)

        loaded = Sandbox(image=str(tmp_dir / "my-save"), memory="1G")
        assert loaded.config.memory == "1G"

    @skip_no_qemu
    @pytest.mark.asyncio
    async def test_load_with_new_mounts(self, real_image, real_kernel, tmp_dir):
        """Test that new mounts can be specified on load."""
        # Skip on Windows - SMB mounts not supported in CI
        platform_config = get_platform_config()
        if platform_config.os.os_type == OS.WINDOWS:
            pytest.skip("SMB mounts not supported in Windows CI environment")

        mount_dir = tmp_dir / "new_mount"
        mount_dir.mkdir()
        (mount_dir / "testfile.txt").write_text("from host")

        async with Sandbox(image="ubuntu") as sb:
            await sb.save("my-save", workspace=tmp_dir)

        loaded = Sandbox(
            image=str(tmp_dir / "my-save"),
            mounts=[Mount(str(mount_dir), "/mnt/data")],
            network_mode=NetworkMode.FULL,
        )

        async with loaded:
            # File should be visible immediately (true filesystem mount)
            result = await loaded.execute("cat /mnt/data/testfile.txt")
            assert result.exit_code == 0
            assert "from host" in result.stdout

    @skip_no_qemu
    @pytest.mark.asyncio
    async def test_multiple_saves(self, real_image, real_kernel, tmp_dir):
        """Test creating and loading multiple saves."""
        # Use /home/quicksand since /tmp is cleared on boot by systemd
        async with Sandbox(image="ubuntu") as sb:
            await sb.execute("echo 'state1' > /home/quicksand/state.txt")
            await sb.save("save1", workspace=tmp_dir)

        async with Sandbox(image="ubuntu") as sb:
            await sb.execute("echo 'state2' > /home/quicksand/state.txt")
            await sb.save("save2", workspace=tmp_dir)

        loaded1 = Sandbox(image=str(tmp_dir / "save1"))
        async with loaded1:
            result = await loaded1.execute("cat /home/quicksand/state.txt")
            assert "state1" in result.stdout

        loaded2 = Sandbox(image=str(tmp_dir / "save2"))
        async with loaded2:
            result = await loaded2.execute("cat /home/quicksand/state.txt")
            assert "state2" in result.stdout

    @skip_no_qemu
    @pytest.mark.asyncio
    async def test_save_load_save_cycle(self, real_image, real_kernel, tmp_dir):
        """Test that data persists across load -> write -> save (same name) -> load."""
        # First save: create a file on rootfs
        async with Sandbox(image="ubuntu") as sb:
            await sb.execute("echo 'round1' > /home/quicksand/persist.txt")
            await sb.save("reusable", workspace=tmp_dir)

        # Load, write more, save again to the same name
        loaded = Sandbox(
            image=str(tmp_dir / "reusable"),
            save="reusable",
            workspace=tmp_dir,
        )
        async with loaded:
            result = await loaded.execute("cat /home/quicksand/persist.txt")
            assert "round1" in result.stdout
            await loaded.execute("echo 'round2' >> /home/quicksand/persist.txt")
        # stop() auto-saves to tmp_dir/reusable (overwrites existing)

        # Load the re-saved directory -- both rounds should be present
        loaded2 = Sandbox(image=str(tmp_dir / "reusable"))
        async with loaded2:
            result = await loaded2.execute("cat /home/quicksand/persist.txt")
            assert "round1" in result.stdout
            assert "round2" in result.stdout

    @skip_no_qemu
    @pytest.mark.asyncio
    async def test_multi_save_in_session(self, real_image, real_kernel, tmp_dir):
        """Test two save() calls on the same running sandbox produce valid saves."""
        async with Sandbox(image="ubuntu") as sb:
            await sb.execute("echo 'first' > /home/quicksand/state.txt")
            await sb.save("snap-a", workspace=tmp_dir)

            await sb.execute("echo 'second' >> /home/quicksand/state.txt")
            await sb.save("snap-b", workspace=tmp_dir)

        loaded_a = Sandbox(image=str(tmp_dir / "snap-a"))
        async with loaded_a:
            result = await loaded_a.execute("cat /home/quicksand/state.txt")
            assert "first" in result.stdout
            assert "second" not in result.stdout

        loaded_b = Sandbox(image=str(tmp_dir / "snap-b"))
        async with loaded_b:
            result = await loaded_b.execute("cat /home/quicksand/state.txt")
            assert "first" in result.stdout
            assert "second" in result.stdout
