"""Unit tests for quicksand_dev build module."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from quicksand_image_tools.build import (
    _find_initrd,
    _find_kernel,
    _get_dir_size,
    build_image,
)


class TestFindKernel:
    """Tests for _find_kernel function."""

    def test_find_vmlinuz_versioned(self, tmp_dir):
        """Test finding versioned vmlinuz."""
        boot = tmp_dir / "boot"
        boot.mkdir()
        (boot / "vmlinuz-5.15.0-generic").touch()
        (boot / "vmlinuz-6.1.0-generic").touch()

        kernel = _find_kernel(tmp_dir)
        # Should return newest version
        assert kernel is not None
        assert "6.1.0" in kernel.name

    def test_find_vmlinuz_unversioned(self, tmp_dir):
        """Test finding unversioned vmlinuz."""
        boot = tmp_dir / "boot"
        boot.mkdir()
        (boot / "vmlinuz").touch()

        kernel = _find_kernel(tmp_dir)
        assert kernel is not None
        assert kernel.name == "vmlinuz"

    def test_no_kernel_found(self, tmp_dir):
        """Test when no kernel is found."""
        boot = tmp_dir / "boot"
        boot.mkdir()
        # No kernel files

        kernel = _find_kernel(tmp_dir)
        assert kernel is None

    def test_no_boot_dir(self, tmp_dir):
        """Test when boot directory doesn't exist."""
        kernel = _find_kernel(tmp_dir)
        assert kernel is None


class TestFindInitrd:
    """Tests for _find_initrd function."""

    def test_find_initrd_img_versioned(self, tmp_dir):
        """Test finding versioned initrd.img."""
        boot = tmp_dir / "boot"
        boot.mkdir()
        (boot / "initrd.img-5.15.0-generic").touch()
        (boot / "initrd.img-6.1.0-generic").touch()

        initrd = _find_initrd(tmp_dir)
        assert initrd is not None
        assert "6.1.0" in initrd.name

    def test_find_initramfs(self, tmp_dir):
        """Test finding initramfs (Alpine style)."""
        boot = tmp_dir / "boot"
        boot.mkdir()
        (boot / "initramfs-virt").touch()

        initrd = _find_initrd(tmp_dir)
        assert initrd is not None
        assert "initramfs" in initrd.name

    def test_no_initrd_found(self, tmp_dir):
        """Test when no initrd is found."""
        boot = tmp_dir / "boot"
        boot.mkdir()

        initrd = _find_initrd(tmp_dir)
        assert initrd is None


class TestGetDirSize:
    """Tests for _get_dir_size function."""

    def test_empty_dir(self, tmp_dir):
        """Test size of empty directory."""
        size = _get_dir_size(tmp_dir)
        assert size == 0

    def test_dir_with_files(self, tmp_dir):
        """Test size of directory with files."""
        (tmp_dir / "file1").write_bytes(b"a" * 100)
        (tmp_dir / "file2").write_bytes(b"b" * 200)

        size = _get_dir_size(tmp_dir)
        assert size == 300

    def test_nested_dirs(self, tmp_dir):
        """Test size includes nested directories."""
        subdir = tmp_dir / "subdir"
        subdir.mkdir()
        (tmp_dir / "file1").write_bytes(b"a" * 100)
        (subdir / "file2").write_bytes(b"b" * 200)

        size = _get_dir_size(tmp_dir)
        assert size == 300

    def test_ignores_symlinks(self, tmp_dir):
        """Test that symlinks are not counted."""
        (tmp_dir / "file").write_bytes(b"a" * 100)
        try:
            (tmp_dir / "link").symlink_to(tmp_dir / "file")
        except (OSError, NotImplementedError) as e:
            # Windows needs Administrator or Developer Mode to create symlinks.
            pytest.skip(f"cannot create symlink on this platform: {e}")

        size = _get_dir_size(tmp_dir)
        assert size == 100

    def test_skips_unstattable_entries(self, tmp_dir):
        """Entries that raise OSError on stat are skipped, not fatal.

        On Windows the rootfs is extracted from a Linux tar and some entries
        (device nodes, restricted dirs) cannot be stat-ed; the walk must
        tolerate that instead of aborting the build.
        """
        (tmp_dir / "good").write_bytes(b"a" * 100)
        (tmp_dir / "bad").write_bytes(b"b" * 999)

        real_stat = Path.stat

        def flaky_stat(self, *args, **kwargs):
            if self.name == "bad":
                raise PermissionError("cannot stat")
            return real_stat(self, *args, **kwargs)

        with patch.object(Path, "stat", flaky_stat):
            size = _get_dir_size(tmp_dir)

        # Only the stat-able file counts; no exception is raised.
        assert size == 100


class TestBuildImage:
    """Tests for build_image function."""

    def test_dockerfile_path_input(self, tmp_dir, cache_dir):
        """Test using Dockerfile path as input."""
        dockerfile_path = tmp_dir / "Dockerfile"
        dockerfile_path.write_text("FROM alpine:3.20\n")

        # Create cached image. The cache key covers the Dockerfile plus the
        # agent source the build copies into the context.
        import hashlib

        from quicksand_image_tools.build import _agent_source_hash

        content_hash = hashlib.sha256(
            b"FROM alpine:3.20\n" + _agent_source_hash().encode()
        ).hexdigest()[:16]
        cached_image = cache_dir / f"custom-{content_hash}.qcow2"
        cached_image.touch()

        with patch("shutil.which", return_value="/usr/bin/docker"):
            result = build_image(dockerfile_path, cache_dir=cache_dir)
            assert result == cached_image


@pytest.mark.parametrize(
    ("distro", "hook_name"),
    [("alpine", "AlpineImageBuildHook"), ("ubuntu", "UbuntuImageBuildHook")],
)
def test_base_image_hook_checks_existing_image_inputs(tmp_path, distro, hook_name):
    hook_path = (
        Path(__file__).parents[2]
        / "packages"
        / "contrib"
        / f"quicksand-{distro}"
        / "hatch_build.py"
    )
    spec = importlib.util.spec_from_file_location(f"{distro}_build_hook", hook_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hook_class = getattr(module, hook_name)

    package_dir = tmp_path / f"quicksand_{distro}"
    images_dir = package_dir / "images"
    images_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text('DISTRO_VERSION = "1.0"\n')
    image_path = images_dir / f"{distro}-1.0-arm64.qcow2"
    image_path.touch()
    hook = hook_class(
        root=str(tmp_path),
        config={},
        build_config=MagicMock(),
        metadata=MagicMock(),
        directory=str(tmp_path),
        target_name="wheel",
        app=MagicMock(),
    )

    with (
        patch("quicksand_image_tools.build_utils.set_platform_wheel_tag", return_value=True),
        patch("quicksand_image_tools.build_utils.get_image_arch", return_value="arm64"),
        patch.object(hook, "_build_image") as build,
    ):
        hook.initialize("standard", {})

    build.assert_called_once_with(package_dir / "docker" / "Dockerfile", image_path)
