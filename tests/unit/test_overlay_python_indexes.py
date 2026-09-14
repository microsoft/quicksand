"""Python package indexes used by overlay build commands."""

from __future__ import annotations

import json
import os
import runpy
import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from quicksand_image_tools.build_utils import run_python_install

_INDEX_VARIABLES = (
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "UV_DEFAULT_INDEX",
    "UV_INDEX_URL",
    "UV_EXTRA_INDEX_URL",
)


@pytest.fixture(autouse=True)
def clear_indexes(monkeypatch):
    for name in _INDEX_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
async def test_python_install_preserves_default_behavior_without_indexes():
    shell = AsyncMock()
    await run_python_install(shell, "pip install example", timeout=20)
    shell.assert_awaited_once_with("pip install example", timeout=20)


@pytest.mark.asyncio
async def test_python_install_passes_only_index_settings_without_logging_urls(monkeypatch):
    indexes = {
        "PIP_INDEX_URL": "https://example-user:example-password@mirror.invalid/simple/",
        "PIP_EXTRA_INDEX_URL": "https://extra.invalid/simple/",
        "UV_DEFAULT_INDEX": "https://uv.invalid/simple/",
        "UV_INDEX_URL": "https://legacy.invalid/simple/",
        "UV_EXTRA_INDEX_URL": "https://uv-extra.invalid/simple/",
    }
    for name, value in indexes.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("UNRELATED_SECRET", "not-for-the-guest")
    shell = AsyncMock()

    await run_python_install(shell, "uv venv /opt/python --seed", timeout=120)

    call = shell.await_args
    assert call is not None
    assert json.loads(call.kwargs["stdin"]) == indexes
    assert call.kwargs["timeout"] == 120
    assert all(value not in call.args[0] for value in indexes.values())
    assert b"UNRELATED_SECRET" not in call.kwargs["stdin"]


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="The launcher executes inside a Unix guest")
async def test_python_index_launcher_applies_settings_only_to_installer(monkeypatch):
    index = "https://mirror.invalid/simple/?value='quoted'&more=value"
    monkeypatch.setenv("PIP_INDEX_URL", index)
    shell = AsyncMock()
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        "import os; print(os.environ['PIP_INDEX_URL'])"
    )
    await run_python_install(shell, command)

    call = shell.await_args
    assert call is not None
    env = os.environ.copy()
    env.pop("PIP_INDEX_URL")
    result = subprocess.run(
        shlex.split(call.args[0]),
        input=call.kwargs["stdin"],
        env=env,
        capture_output=True,
        check=True,
    )
    assert result.stdout.decode().strip() == index
    assert "PIP_INDEX_URL" not in env


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("package", "install_count"), [("quicksand-agent", 2), ("quicksand-cua", 1)]
)
async def test_overlay_installer_commands_use_configured_indexes(
    monkeypatch, package, install_count
):
    index = "https://mirror.invalid/simple/"
    monkeypatch.setenv("PIP_INDEX_URL", index)
    hook_path = Path(__file__).parents[2] / "packages" / "contrib" / package / "hatch_build.py"
    hook = runpy.run_path(str(hook_path))
    shell = AsyncMock()

    await hook["_setup"](shell)

    installs = [
        call
        for call in shell.await_args_list
        if "uv venv" in call.args[0] or "pip install" in call.args[0]
    ]
    assert len(installs) == install_count
    for call in installs:
        assert json.loads(call.kwargs["stdin"]) == {"PIP_INDEX_URL": index}
        assert index not in call.args[0]
