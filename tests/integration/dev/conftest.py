"""Fixtures for quicksand-image-tools integration tests."""

from __future__ import annotations

import itertools
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_build_dir_counter = itertools.count()


@pytest.fixture(scope="module")
def dev_tmp_dir():
    """Create a temporary directory for dev integration tests."""
    path = Path(tempfile.mkdtemp(prefix="quicksand-image-tools-test-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def build_dir(dev_tmp_dir):
    """Create a fresh build directory for each test."""
    path = dev_tmp_dir / f"build-{next(_build_dir_counter)}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def docker_image_exists(tag: str) -> bool:
    """Check if a Docker image exists locally."""
    result = subprocess.run(
        ["docker", "image", "inspect", tag],
        capture_output=True,
    )
    return result.returncode == 0


def remove_docker_image(tag: str) -> None:
    """Remove a Docker image if it exists."""
    subprocess.run(
        ["docker", "rmi", "-f", tag],
        capture_output=True,
    )


@pytest.fixture
def clean_test_image():
    """Ensure test image is cleaned up after test."""
    tags_to_clean = []

    def register(tag: str):
        tags_to_clean.append(tag)

    yield register

    for tag in tags_to_clean:
        remove_docker_image(tag)
