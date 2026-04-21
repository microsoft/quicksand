"""Build custom VM images from Dockerfiles."""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from quicksand_core._types import FilePatterns

logger = logging.getLogger(__name__)

# Path to the Rust agent source directory in this package
AGENT_SOURCE_DIR = Path(__file__).parent / "quicksand-guest-agent"

# Default cache directory
DEFAULT_CACHE_DIR = Path.home() / ".cache/quicksand/images"


def get_agent_source_dir() -> Path:
    """Get the path to the Rust agent source directory.

    This is used by image packages to set up symlinks for multi-stage
    Docker builds that compile the Rust agent.
    """
    if not AGENT_SOURCE_DIR.exists():
        raise RuntimeError(f"Agent source directory not found at {AGENT_SOURCE_DIR}")
    return AGENT_SOURCE_DIR


def build_image(
    dockerfile: str | Path,
    output_path: Path | None = None,
    cache_dir: Path | None = None,
    force: bool = False,
    log: logging.Logger | None = None,
) -> Path:
    """
    Build a VM image from a Dockerfile.

    The Dockerfile should use a multi-stage build to:
    - Compile the Rust agent from source (stage 1)
    - Install a kernel (linux-image-virtual for Ubuntu, linux-virt for Alpine)
    - Copy the compiled agent binary and configure a service to run it

    The agent source is copied to the build context if an agent symlink exists.

    Args:
        dockerfile: Path to a Dockerfile.
        output_path: Where to save the resulting qcow2 image.
                    If None, saves to cache directory with a hash-based name.
        cache_dir: Directory for cached images. Defaults to ~/.cache/quicksand/images/

    Returns:
        Path to the built qcow2 image.

    Raises:
        RuntimeError: If Docker is not available or build fails.
    """
    log = log or logger

    if not shutil.which("docker"):
        raise RuntimeError(
            "Docker is required to build custom images. "
            "Install Docker from https://docs.docker.com/get-docker/"
        )

    cache = cache_dir or DEFAULT_CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)

    # Handle Dockerfile input
    dockerfile_path = Path(dockerfile)
    if not dockerfile_path.exists():
        raise RuntimeError(f"Dockerfile not found: {dockerfile_path}")

    dockerfile_content = dockerfile_path.read_text()
    context_dir = dockerfile_path.parent

    # Compute hash for caching
    content_hash = hashlib.sha256(dockerfile_content.encode()).hexdigest()[:16]

    if output_path is None:
        output_path = cache / f"custom-{content_hash}.qcow2"

    # Check cache
    if output_path.exists():
        if force:
            output_path.unlink()
        else:
            log.info("Using cached image: %s", output_path)
            return output_path

    # Copy agent source to build context (Dockerfiles expect it at agent/)
    agent_dest = context_dir / "agent"
    if agent_dest.exists():
        shutil.rmtree(agent_dest)
    shutil.copytree(
        AGENT_SOURCE_DIR,
        agent_dest,
        ignore=shutil.ignore_patterns("target", ".git"),
    )

    try:
        log.info("[1/5] Building Docker image...")

        with tempfile.TemporaryDirectory(prefix="quicksand-build-") as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Build Docker image
            tag = f"quicksand-build:{content_hash}"
            _run_docker_build(dockerfile_path, context_dir, tag, log=log)

            try:
                log.info("[2/5] Exporting filesystem...")
                _create_and_export_container(tag, tmpdir_path)

                log.info("[3/5] Converting to VM image...")
                tar_path = tmpdir_path / "rootfs.tar"
                _tar_to_qcow2(tar_path, output_path, log=log)

            finally:
                log.info("[4/5] Cleaning up...")
                _remove_docker_image(tag)

        log.info("[5/5] Done!")
        final_size_mb = output_path.stat().st_size / (1024 * 1024)
        log.info("Output: %s (%.1f MB)", output_path, final_size_mb)

        return output_path

    finally:
        # Clean up copied agent source
        shutil.rmtree(agent_dest, ignore_errors=True)


def _run_docker_build(
    dockerfile: Path,
    context: Path,
    tag: str,
    log: logging.Logger = logger,
) -> None:
    """Build a Docker image, streaming output to the caller's logger."""
    cmd = [
        "docker",
        "build",
        "-f",
        str(dockerfile),
        "-t",
        tag,
        str(context),
    ]

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert process.stdout is not None
    for line in process.stdout:
        log.info("%s", line.rstrip())
    returncode = process.wait()
    if returncode != 0:
        raise RuntimeError(f"Docker build failed (exit code {returncode})")


def _create_and_export_container(image_tag: str, tmpdir: Path) -> str:
    """Create a container and export its filesystem."""
    # Create container
    result = subprocess.run(
        ["docker", "create", image_tag],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create container: {result.stderr}")

    container_id = result.stdout.strip()

    try:
        # Export filesystem
        tar_path = tmpdir / "rootfs.tar"
        with open(tar_path, "wb") as f:
            result = subprocess.run(
                ["docker", "export", container_id],
                stdout=f,
                stderr=subprocess.PIPE,
            )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to export container: {result.stderr.decode()}")
    finally:
        # Remove container
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True)

    return container_id


def _remove_docker_image(tag: str) -> None:
    """Remove a Docker image."""
    subprocess.run(["docker", "rmi", "-f", tag], capture_output=True)


def _tar_to_qcow2(tar_path: Path, qcow2_path: Path, log: logging.Logger = logger) -> None:
    """
    Convert a rootfs tar to a qcow2 image with kernel/initrd extracted.

    Uses Docker to create the ext4 image, ensuring proper root ownership.
    """
    # Find qemu-img: prefer bundled from quicksand-core, fall back to system
    qemu_img: str | None = None
    try:
        from quicksand_core.qemu.platform import _find_bundled_runtime

        bundled = _find_bundled_runtime()
        if bundled and bundled.qemu_img.exists():
            qemu_img = str(bundled.qemu_img)
    except ImportError:
        pass

    if not qemu_img:
        qemu_img = shutil.which("qemu-img") or shutil.which("qemu-img.exe")

    if not qemu_img:
        raise RuntimeError(
            "qemu-img not found. Install QEMU or quicksand-core:\n"
            "  pip install quicksand-core\n"
            "  Or install QEMU directly:\n"
            "    macOS: brew install qemu\n"
            "    Linux: sudo apt install qemu-utils\n"
            "    Windows: https://www.qemu.org/download/#windows"
        )

    with tempfile.TemporaryDirectory(prefix="quicksand-convert-") as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Extract tar on host to get kernel/initrd
        rootfs_dir = tmpdir_path / "rootfs"
        rootfs_dir.mkdir()

        log.debug("Extracting tar for kernel/initrd discovery...")
        result = subprocess.run(
            ["tar", "-xf", str(tar_path), "-C", str(rootfs_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to extract tar: {result.stderr}")

        # Find kernel and initrd
        kernel_src = _find_kernel(rootfs_dir)
        initrd_src = _find_initrd(rootfs_dir)

        if not kernel_src:
            raise RuntimeError(
                "No kernel found in image. Your Dockerfile must install a kernel package.\n"
                "For Ubuntu: RUN apt-get install -y linux-image-virtual\n"
                "For Alpine: RUN apk add linux-virt"
            )

        log.info("Kernel: %s", kernel_src.name)
        if initrd_src:
            log.info("Initrd: %s", initrd_src.name)

        # Copy kernel and initrd to output location
        kernel_dst = qcow2_path.with_suffix(FilePatterns.KERNEL_SUFFIX)
        shutil.copy(kernel_src, kernel_dst)

        if initrd_src:
            initrd_dst = qcow2_path.with_suffix(FilePatterns.INITRD_SUFFIX)
            shutil.copy(initrd_src, initrd_dst)

        # Calculate filesystem size
        rootfs_size = _get_dir_size(rootfs_dir)
        fs_size_bytes = max(int(rootfs_size * 1.5), 1024 * 1024 * 1024)
        fs_size_bytes = ((fs_size_bytes + 256 * 1024 * 1024 - 1) // (256 * 1024 * 1024)) * (
            256 * 1024 * 1024
        )
        fs_size_mb = fs_size_bytes // (1024 * 1024)

        # Create ext4 image inside Docker to preserve root ownership
        raw_path = tmpdir_path / "rootfs.ext4"
        _create_ext4_in_docker(tar_path, raw_path, fs_size_mb, log=log)

        # Convert to qcow2
        log.debug("Converting to qcow2 (compressed)...")
        result = subprocess.run(
            [
                qemu_img,
                "convert",
                "-f",
                "raw",
                "-O",
                "qcow2",
                "-c",
                str(raw_path),
                str(qcow2_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to convert to qcow2: {result.stderr}")


def _create_ext4_in_docker(
    tar_path: Path, output_path: Path, size_mb: int, log: logging.Logger = logger
) -> None:
    """Create an ext4 filesystem image from a tar file using Docker.

    Uses docker cp instead of volume mounts for Docker-in-Docker compatibility.
    Volume mounts fail in Docker-in-Docker because paths inside the outer container
    don't exist on the Docker host where the daemon runs.
    """
    builder_image = "ubuntu:24.04"
    import time as _time

    container_name = f"quicksand-builder-{int(_time.time())}"

    # Use /tmp which always exists in the container
    build_script = f"""#!/bin/bash
set -e
apt-get update -qq
apt-get install -y -qq e2fsprogs zerofree >/dev/null
mkdir -p /rootfs
tar -xf /tmp/rootfs.tar -C /rootfs
mke2fs -q -t ext4 -d /rootfs -L rootfs -O ^metadata_csum,^64bit /tmp/rootfs.ext4 {size_mb}M
zerofree /tmp/rootfs.ext4
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove any leftover container from a previous failed build
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

    # Create container (but don't start it yet)
    result = subprocess.run(
        ["docker", "create", "--name", container_name, builder_image, "bash", "-c", build_script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create Docker container:\n{result.stderr or result.stdout}")

    try:
        # Copy tar file into container (use /tmp which always exists)
        result = subprocess.run(
            ["docker", "cp", str(tar_path), f"{container_name}:/tmp/rootfs.tar"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to copy tar into container:\n{result.stderr}")

        # Start container and wait for completion, streaming output
        process = subprocess.Popen(
            ["docker", "start", "-a", container_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert process.stdout is not None
        for line in process.stdout:
            log.info("%s", line.rstrip())
        returncode = process.wait()
        if returncode != 0:
            raise RuntimeError(f"Failed to create ext4 image in Docker (exit code {returncode})")

        # Copy ext4 file out of container
        result = subprocess.run(
            ["docker", "cp", f"{container_name}:/tmp/rootfs.ext4", str(output_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to copy ext4 from container:\n{result.stderr}")

    finally:
        # Always clean up the container
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

    if not output_path.exists():
        raise RuntimeError(f"Docker build completed but output file not found: {output_path}")


def _find_kernel(rootfs: Path) -> Path | None:
    """Find the kernel in a rootfs directory."""
    boot_dir = rootfs / "boot"
    if not boot_dir.exists():
        return None

    kernels = list(boot_dir.glob("vmlinuz-*"))
    if not kernels:
        kernels = list(boot_dir.glob("vmlinuz"))

    if kernels:
        return sorted(kernels, reverse=True)[0]
    return None


def _find_initrd(rootfs: Path) -> Path | None:
    """Find the initrd/initramfs in a rootfs directory."""
    boot_dir = rootfs / "boot"
    if not boot_dir.exists():
        return None

    for pattern in ["initrd.img-*", "initrd-*", "initramfs-*"]:
        initrds = list(boot_dir.glob(pattern))
        if initrds:
            return sorted(initrds, reverse=True)[0]

    for name in ["initrd.img", "initrd", "initramfs"]:
        path = boot_dir / name
        if path.exists():
            return path

    return None


def _get_dir_size(path: Path) -> int:
    """Get the total size of a directory in bytes."""
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            total += entry.stat().st_size
    return total
