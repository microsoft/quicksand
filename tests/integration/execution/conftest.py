"""Fixtures for execution tests - shared sandbox for efficiency."""

from __future__ import annotations

import pytest
import pytest_asyncio

from tests.conftest import has_qemu
from tests.integration.conftest import get_vm_type


def _get_sandbox_class():
    """Get the appropriate sandbox class based on VM type."""
    vm_type = get_vm_type()

    if vm_type == "ubuntu":
        from quicksand import UbuntuSandbox

        return UbuntuSandbox
    elif vm_type == "alpine":
        from quicksand import AlpineSandbox

        return AlpineSandbox
    else:
        raise ValueError(f"Unknown VM type: {vm_type}")


@pytest_asyncio.fixture(scope="module")
async def shared_sandbox():
    """Module-scoped sandbox for execution tests.

    All tests in this group share one sandbox for fast execution.
    Tests should not leave the sandbox in a broken state.
    VM type is determined by QUICKSAND_TEST_VM environment variable.
    """
    try:
        SandboxClass = _get_sandbox_class()
    except ImportError:
        pytest.skip(f"quicksand-{get_vm_type()} not installed")

    if not has_qemu():
        pytest.skip("QEMU not installed")

    sandbox = SandboxClass()
    await sandbox.start()
    yield sandbox
    await sandbox.stop()
