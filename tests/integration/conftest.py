"""Shared fixtures for all integration tests."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from quicksand_core import detect_accelerator
from quicksand_core.host import Accelerator

_accel_status = detect_accelerator()
_has_hw_accel = _accel_status.accelerator in (
    Accelerator.KVM,
    Accelerator.HVF,
    Accelerator.WHPX,
)

pytestmark = pytest.mark.skipif(
    not _has_hw_accel,
    reason=f"No hardware acceleration available ({_accel_status.error or 'TCG only'})",
)


def get_vm_type() -> str:
    """Get VM type from environment variable."""
    return os.environ.get("QUICKSAND_TEST_VM", "ubuntu")


def _get_image_artifacts():
    """Get image path and kernel path for the configured VM type.

    Uses the ImageProvider.resolve() method from the installed image package.
    """
    vm_type = get_vm_type()

    if vm_type == "ubuntu":
        try:
            from quicksand_ubuntu import _UbuntuImageProvider

            resolved = _UbuntuImageProvider().resolve()
            return str(resolved.chain[0]), str(resolved.kernel) if resolved.kernel else None
        except ImportError:
            pytest.skip("quicksand-ubuntu not installed")
    elif vm_type == "alpine":
        try:
            from quicksand_alpine import _AlpineImageProvider

            resolved = _AlpineImageProvider().resolve()
            return str(resolved.chain[0]), str(resolved.kernel) if resolved.kernel else None
        except ImportError:
            pytest.skip("quicksand-alpine not installed")
    else:
        pytest.skip(f"Unknown VM type: {vm_type}")


@pytest.fixture(scope="module")
def real_image():
    """Get path to pre-built VM image.

    VM type is determined by QUICKSAND_TEST_VM environment variable.
    Defaults to 'ubuntu' if not set.
    """
    image_path, _kernel_path = _get_image_artifacts()
    return image_path


@pytest.fixture(scope="module")
def real_kernel():
    """Get path to pre-built VM kernel.

    VM type is determined by QUICKSAND_TEST_VM environment variable.
    Defaults to 'ubuntu' if not set.
    """
    _image_path, kernel_path = _get_image_artifacts()
    return kernel_path


@pytest.fixture(scope="module")
def integration_tmp_dir():
    """Module-scoped temp directory for integration tests.

    Shared across tests in a module for setting up mount directories, etc.
    """
    path = Path(tempfile.mkdtemp(prefix="quicksand-integration-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)
