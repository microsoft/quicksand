"""Shared pytest fixtures for quicksand tests."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ============================================================================
# Markers
# ============================================================================


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "integration: integration tests (require QEMU)")
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "docker: marks tests that require Docker")


# ============================================================================
# Skip conditions
# ============================================================================


def has_qemu() -> bool:
    """Check if bundled QEMU is available from quicksand-core."""
    try:
        from quicksand_core.qemu.platform import get_runtime

        runtime = get_runtime()
        return runtime.qemu_binary.exists()
    except Exception:
        return False


def has_docker() -> bool:
    """Check if Docker is available."""
    return shutil.which("docker") is not None


def has_mke2fs() -> bool:
    """Check if mke2fs is available."""
    if shutil.which("mke2fs"):
        return True
    # Check Homebrew keg-only locations on macOS
    for path in [
        "/opt/homebrew/opt/e2fsprogs/sbin/mke2fs",  # Apple Silicon
        "/usr/local/opt/e2fsprogs/sbin/mke2fs",  # Intel Mac
    ]:
        if Path(path).exists():
            return True
    return False


skip_no_qemu = pytest.mark.skipif(not has_qemu(), reason="QEMU not installed")
skip_no_docker = pytest.mark.skipif(not has_docker(), reason="Docker not installed")
skip_no_mke2fs = pytest.mark.skipif(not has_mke2fs(), reason="mke2fs not installed")


# ============================================================================
# Fixtures - Temporary directories
# ============================================================================


@pytest.fixture
def tmp_dir():
    """Create a temporary directory that is cleaned up after the test."""
    path = Path(tempfile.mkdtemp(prefix="quicksand-test-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def cache_dir(tmp_dir):
    """Create a temporary cache directory."""
    cache = tmp_dir / "cache"
    cache.mkdir()
    return cache


# ============================================================================
# Fixtures - Mock runtime
# ============================================================================


@pytest.fixture
def mock_runtime(tmp_dir):
    """Create a mock runtime with fake QEMU binaries."""
    from quicksand_core.qemu.platform import RuntimeInfo

    # Create fake binaries
    runtime_dir = tmp_dir / "runtime"
    runtime_dir.mkdir()
    qemu_binary = tmp_dir / "qemu-system-x86_64"
    qemu_img = tmp_dir / "qemu-img"
    qemu_binary.touch()
    qemu_img.touch()
    qemu_binary.chmod(0o755)
    qemu_img.chmod(0o755)

    return RuntimeInfo(
        qemu_binary=qemu_binary,
        qemu_img=qemu_img,
        runtime_dir=runtime_dir,
    )


@pytest.fixture
def mock_get_runtime(mock_runtime):
    """Patch get_runtime to return mock runtime."""
    with patch("quicksand_core.qemu.platform.get_runtime", return_value=mock_runtime):
        yield mock_runtime


# ============================================================================
# Fixtures - Mock images
# ============================================================================


@pytest.fixture
def fake_qcow2(tmp_dir):
    """Create a fake qcow2 image file."""
    image_path = tmp_dir / "test-image.qcow2"
    # Write a minimal qcow2 header (just enough to be recognized)
    image_path.write_bytes(b"QFI\xfb" + b"\x00" * 100)
    return image_path


@pytest.fixture
def fake_kernel(tmp_dir):
    """Create a fake kernel file."""
    kernel_path = tmp_dir / "test-image.kernel"
    kernel_path.write_bytes(b"fake kernel")
    return kernel_path


@pytest.fixture
def fake_initrd(tmp_dir):
    """Create a fake initrd file."""
    initrd_path = tmp_dir / "test-image.initrd"
    initrd_path.write_bytes(b"fake initrd")
    return initrd_path


@pytest.fixture
def fake_image_set(tmp_dir):
    """Create a complete fake image set (qcow2 + kernel + initrd)."""
    base = tmp_dir / "test-image"

    qcow2 = base.with_suffix(".qcow2")
    kernel = base.with_suffix(".kernel")
    initrd = base.with_suffix(".initrd")

    qcow2.write_bytes(b"QFI\xfb" + b"\x00" * 100)
    kernel.write_bytes(b"fake kernel")
    initrd.write_bytes(b"fake initrd")

    return {"qcow2": qcow2, "kernel": kernel, "initrd": initrd}


# ============================================================================
# Fixtures - Dockerfile content
# ============================================================================


@pytest.fixture
def simple_dockerfile():
    """A simple Dockerfile for testing."""
    return """
FROM alpine:3.20
RUN apk add --no-cache python3
CMD ["/bin/sh"]
"""


@pytest.fixture
def ubuntu_dockerfile():
    """Ubuntu Dockerfile with kernel."""
    return """
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y linux-image-virtual
CMD ["/bin/bash"]
"""


# ============================================================================
# Fixtures - Mock subprocess
# ============================================================================


@pytest.fixture
def mock_subprocess():
    """Mock subprocess.run for testing commands without execution."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )
        yield mock_run


@pytest.fixture
def mock_popen():
    """Mock subprocess.Popen for testing process creation."""
    with patch("subprocess.Popen") as mock_popen_cls:
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.returncode = None
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = b""
        mock_popen_cls.return_value = mock_process
        yield mock_popen_cls, mock_process


# ============================================================================
# Fixtures - Mock socket
# ============================================================================


@pytest.fixture
def mock_socket():
    """Mock socket for testing virtio-serial communication."""
    with patch("socket.socket") as mock_socket_cls:
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        yield mock_socket_cls, mock_sock
