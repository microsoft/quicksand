"""File locking within a guest using the bundled SMB server."""

from __future__ import annotations

import pytest
import pytest_asyncio


@pytest.mark.integration
@pytest.mark.asyncio
async def test_flock_acquires_lock(shared_sandbox):
    result = await shared_sandbox.execute("flock -n /mnt/host/flock.lock -c 'printf locked'")
    assert result.exit_code == 0, result.stderr
    assert result.stdout == "locked"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_flock_on_dynamic_mount(shared_sandbox, mount_dir):
    directory = mount_dir / "dynamic-locking"
    directory.mkdir()
    handle = await shared_sandbox.mount(str(directory), "/mnt/dynamic-locking")
    try:
        result = await shared_sandbox.execute(
            "flock -n /mnt/dynamic-locking/flock.lock -c 'printf locked'"
        )
        assert result.exit_code == 0, result.stderr
        assert result.stdout == "locked"
    finally:
        await shared_sandbox.unmount(handle)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_flock_excludes_another_process_and_releases(shared_sandbox):
    result = await shared_sandbox.execute(
        "flock -n /mnt/host/exclusive.lock sh -c "
        "'flock -n /mnt/host/exclusive.lock true; test \"$?\" -eq 1'"
    )
    assert result.exit_code == 0, result.stderr

    result = await shared_sandbox.execute("flock -n /mnt/host/exclusive.lock true")
    assert result.exit_code == 0, result.stderr


@pytest_asyncio.fixture(scope="module")
async def sqlite_sandbox(shared_sandbox):
    result = await shared_sandbox.execute(
        "command -v sqlite3 || (sudo apt-get update -qq && sudo apt-get install -y -qq sqlite3)",
        timeout=180,
    )
    assert result.exit_code == 0, result.stderr or result.stdout
    return shared_sandbox


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sqlite_can_commit_on_mount(sqlite_sandbox):
    result = await sqlite_sandbox.execute(
        "sqlite3 /mnt/host/locking.sqlite "
        "'CREATE TABLE records (value TEXT);"
        ' BEGIN IMMEDIATE; INSERT INTO records VALUES ("committed"); COMMIT;\''
    )
    assert result.exit_code == 0, result.stderr

    result = await sqlite_sandbox.execute(
        "sqlite3 /mnt/host/locking.sqlite 'SELECT value FROM records;'"
    )
    assert result.exit_code == 0, result.stderr
    assert result.stdout == "committed\n"
