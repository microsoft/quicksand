"""Regression coverage for Git index updates through CIFS mounts."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio


def host_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Quicksand Tests",
            "-c",
            "user.email=quicksand@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest_asyncio.fixture(scope="module")
async def git_sandbox(shared_sandbox):
    result = await shared_sandbox.execute(
        "command -v git || (sudo apt-get update -qq && sudo apt-get install -y -qq git)",
        timeout=180,
    )
    assert result.exit_code == 0, result.stderr or result.stdout
    return shared_sandbox


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.parametrize("staged_changes", [False, True])
@pytest.mark.parametrize("ignore_filemode", [False, True])
async def test_git_status_preserves_host_index(
    git_sandbox, mount_dir, staged_changes, ignore_filemode
):
    repo = mount_dir / f"git-index-{staged_changes}-{ignore_filemode}"
    repo.mkdir()
    host_git(repo, "init", "--quiet")
    tracked_file = repo / "a.txt"
    tracked_file.write_text("committed\n")
    host_git(repo, "add", "a.txt")
    host_git(
        repo,
        "commit",
        "--quiet",
        "-m",
        "Initial fixture\n\n"
        "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>",
    )

    if staged_changes:
        tracked_file.write_text("staged\n")
        host_git(repo, "add", "a.txt")
        tracked_file.write_text("unstaged\n")

    expected_status = "MM a.txt\n" if staged_changes else ""
    entries_before = host_git(repo, "ls-files", "--stage")
    guest_repo = shlex.quote(f"/mnt/host/{repo.name}")
    guest_git = f"git -c safe.directory={guest_repo}"
    if ignore_filemode:
        # CIFS synthesizes execute bits; also exercise an otherwise clean index refresh.
        guest_git += " -c core.filemode=false"

    for _ in range(2):
        result = await git_sandbox.execute(f"{guest_git} -C {guest_repo} status --porcelain=v1")
        assert result.exit_code == 0, result.stderr
        if ignore_filemode:
            assert result.stdout == expected_status
        assert (repo / ".git" / "index").is_file()
        assert host_git(repo, "ls-files", "--stage") == entries_before
        assert host_git(repo, "status", "--porcelain=v1") == expected_status
