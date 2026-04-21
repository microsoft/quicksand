"""Unit tests for quicksand_dev build module."""

from __future__ import annotations

from unittest.mock import patch

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
        (tmp_dir / "link").symlink_to(tmp_dir / "file")

        size = _get_dir_size(tmp_dir)
        assert size == 100


class TestBuildImage:
    """Tests for build_image function."""

    def test_dockerfile_path_input(self, tmp_dir, cache_dir):
        """Test using Dockerfile path as input."""
        dockerfile_path = tmp_dir / "Dockerfile"
        dockerfile_path.write_text("FROM alpine:3.20\n")

        # Create cached image
        import hashlib

        content_hash = hashlib.sha256(b"FROM alpine:3.20\n").hexdigest()[:16]
        cached_image = cache_dir / f"custom-{content_hash}.qcow2"
        cached_image.touch()

        with patch("shutil.which", return_value="/usr/bin/docker"):
            result = build_image(dockerfile_path, cache_dir=cache_dir)
            assert result == cached_image
