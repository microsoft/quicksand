"""Integration tests for quicksand-image-tools CLI commands.

These tests require Docker to be installed and running.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.conftest import skip_no_docker
from tests.integration.dev.conftest import docker_image_exists


@pytest.mark.docker
@pytest.mark.integration
@skip_no_docker
class TestBuildBase:
    """Integration tests for build-base command."""

    def test_build_base_ubuntu(self, clean_test_image):
        """Test building Ubuntu base image."""
        # Skip if quicksand-ubuntu not installed
        try:
            from quicksand_ubuntu import DISTRO_VERSION as ubuntu_version
        except ImportError:
            pytest.skip("quicksand-ubuntu not installed")

        versioned_tag = f"quicksand/ubuntu-base:{ubuntu_version}"
        latest_tag = "quicksand/ubuntu-base:latest"
        clean_test_image(versioned_tag)
        clean_test_image(latest_tag)

        result = subprocess.run(
            ["quicksand-image-tools", "build-base", "ubuntu"],
            capture_output=True,
            text=True,
            timeout=300,
        )

        assert result.returncode == 0, f"build-base failed: {result.stderr}"
        assert docker_image_exists(versioned_tag)
        assert docker_image_exists(latest_tag)
        assert f"Built: {versioned_tag}" in result.stdout

    def test_build_base_alpine(self, clean_test_image):
        """Test building Alpine base image."""
        # Skip if quicksand-alpine not installed
        try:
            from quicksand_alpine import DISTRO_VERSION as alpine_version
        except ImportError:
            pytest.skip("quicksand-alpine not installed")

        versioned_tag = f"quicksand/alpine-base:{alpine_version}"
        latest_tag = "quicksand/alpine-base:latest"
        clean_test_image(versioned_tag)
        clean_test_image(latest_tag)

        result = subprocess.run(
            ["quicksand-image-tools", "build-base", "alpine"],
            capture_output=True,
            text=True,
            timeout=300,
        )

        assert result.returncode == 0, f"build-base failed: {result.stderr}"
        assert docker_image_exists(versioned_tag)
        assert docker_image_exists(latest_tag)


@pytest.mark.docker
@pytest.mark.integration
@skip_no_docker
class TestInit:
    """Integration tests for init command."""

    def test_init_creates_dockerfile(self, build_dir, clean_test_image):
        """Test that init creates a Dockerfile."""
        # Skip if quicksand-ubuntu not installed
        try:
            from quicksand_ubuntu import DISTRO_VERSION as ubuntu_version
        except ImportError:
            pytest.skip("quicksand-ubuntu not installed")

        versioned_tag = f"quicksand/ubuntu-base:{ubuntu_version}"
        clean_test_image(versioned_tag)
        clean_test_image("quicksand/ubuntu-base:latest")

        result = subprocess.run(
            ["quicksand-image-tools", "init", str(build_dir), "ubuntu"],
            capture_output=True,
            text=True,
            timeout=300,
        )

        assert result.returncode == 0, f"init failed: {result.stderr}"

        dockerfile = build_dir / "Dockerfile"
        assert dockerfile.exists()

        content = dockerfile.read_text()
        assert f"FROM {versioned_tag}" in content

    def test_init_builds_base_if_missing(self, build_dir, clean_test_image):
        """Test that init builds base image if it doesn't exist."""
        try:
            from quicksand_ubuntu import DISTRO_VERSION as ubuntu_version
        except ImportError:
            pytest.skip("quicksand-ubuntu not installed")

        versioned_tag = f"quicksand/ubuntu-base:{ubuntu_version}"
        latest_tag = "quicksand/ubuntu-base:latest"

        # Remove the image first
        subprocess.run(["docker", "rmi", "-f", versioned_tag], capture_output=True)
        subprocess.run(["docker", "rmi", "-f", latest_tag], capture_output=True)

        clean_test_image(versioned_tag)
        clean_test_image(latest_tag)

        result = subprocess.run(
            ["quicksand-image-tools", "init", str(build_dir), "ubuntu"],
            capture_output=True,
            text=True,
            timeout=300,
        )

        assert result.returncode == 0, f"init failed: {result.stderr}"
        assert "not found, building" in result.stdout
        assert docker_image_exists(versioned_tag)

    def test_init_skips_existing_dockerfile(self, build_dir):
        """Test that init doesn't overwrite existing Dockerfile."""
        dockerfile = build_dir / "Dockerfile"
        original_content = "FROM my-custom-image:latest\n"
        dockerfile.write_text(original_content)

        result = subprocess.run(
            ["quicksand-image-tools", "init", str(build_dir), "ubuntu"],
            capture_output=True,
            text=True,
        )

        # Should succeed but not modify the Dockerfile
        assert result.returncode == 0
        assert dockerfile.read_text() == original_content

    def test_init_error_without_base(self, build_dir):
        """Test that init errors when no base specified and no Dockerfile."""
        result = subprocess.run(
            ["quicksand-image-tools", "init", str(build_dir)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        assert "No Dockerfile found" in result.stderr
        assert "ubuntu|alpine" in result.stderr


@pytest.mark.docker
@pytest.mark.integration
@pytest.mark.slow
@skip_no_docker
class TestBuildImage:
    """Integration tests for build-image command.

    These tests are slow because they actually build Docker images
    and convert them to qcow2.
    """

    def test_build_image_simple(self, build_dir, clean_test_image):
        """Test building a simple custom image."""
        try:
            from quicksand_ubuntu import DISTRO_VERSION as ubuntu_version
        except ImportError:
            pytest.skip("quicksand-ubuntu not installed")

        # First ensure base image exists
        versioned_tag = f"quicksand/ubuntu-base:{ubuntu_version}"
        if not docker_image_exists(versioned_tag):
            subprocess.run(
                ["quicksand-image-tools", "build-base", "ubuntu"],
                capture_output=True,
                timeout=300,
            )

        clean_test_image(versioned_tag)
        clean_test_image("quicksand/ubuntu-base:latest")

        # Create Dockerfile
        dockerfile = build_dir / "Dockerfile"
        dockerfile.write_text(f"""FROM {versioned_tag}
RUN echo "test" > /tmp/test.txt
""")

        output_path = build_dir / "test-image.qcow2"

        result = subprocess.run(
            ["quicksand-image-tools", "build-image", str(dockerfile), "-o", str(output_path)],
            capture_output=True,
            text=True,
            timeout=600,
        )

        assert result.returncode == 0, f"build-image failed: {result.stderr}"
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    def test_build_image_with_cache(self, build_dir, clean_test_image):
        """Test that build-image uses cache directory."""
        try:
            from quicksand_ubuntu import DISTRO_VERSION as ubuntu_version
        except ImportError:
            pytest.skip("quicksand-ubuntu not installed")

        versioned_tag = f"quicksand/ubuntu-base:{ubuntu_version}"
        if not docker_image_exists(versioned_tag):
            subprocess.run(
                ["quicksand-image-tools", "build-base", "ubuntu"],
                capture_output=True,
                timeout=300,
            )

        clean_test_image(versioned_tag)
        clean_test_image("quicksand/ubuntu-base:latest")

        # Create Dockerfile
        dockerfile = build_dir / "Dockerfile"
        dockerfile.write_text(f"FROM {versioned_tag}\n")

        cache_dir = build_dir / "cache"
        output_path = build_dir / "test-image.qcow2"

        result = subprocess.run(
            [
                "quicksand-image-tools",
                "build-image",
                str(dockerfile),
                "-o",
                str(output_path),
                "--cache-dir",
                str(cache_dir),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )

        # Build should succeed
        assert result.returncode == 0, f"build-image failed: {result.stderr}"
        assert output_path.exists()


@pytest.mark.docker
@pytest.mark.integration
@skip_no_docker
class TestEndToEndWorkflow:
    """End-to-end workflow tests."""

    def test_full_workflow_init_and_build(self, build_dir, clean_test_image):
        """Test the complete workflow: init -> customize -> build."""
        try:
            from quicksand_ubuntu import DISTRO_VERSION as ubuntu_version
        except ImportError:
            pytest.skip("quicksand-ubuntu not installed")

        versioned_tag = f"quicksand/ubuntu-base:{ubuntu_version}"
        clean_test_image(versioned_tag)
        clean_test_image("quicksand/ubuntu-base:latest")

        # Step 1: Initialize
        result = subprocess.run(
            ["quicksand-image-tools", "init", str(build_dir), "ubuntu"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, f"init failed: {result.stderr}"

        # Step 2: Customize Dockerfile
        dockerfile = build_dir / "Dockerfile"
        original = dockerfile.read_text()
        dockerfile.write_text(original + "\nRUN echo 'customized' > /customized.txt\n")

        # Step 3: Build
        output_path = build_dir / "custom.qcow2"
        result = subprocess.run(
            ["quicksand-image-tools", "build-image", str(dockerfile), "-o", str(output_path)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert result.returncode == 0, f"build failed: {result.stderr}"
        assert output_path.exists()
