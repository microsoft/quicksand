"""Tests for host RAM detection and the per-sandbox budget warning."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

from quicksand_core import Sandbox
from quicksand_core._types import ResolvedAccelerator, ResolvedImage
from quicksand_core.host import Accelerator, get_host_memory_bytes

_HOST_RAM = "quicksand_core.host.get_host_memory_bytes"
_LOGGER = "quicksand.sandbox"


class TestGetHostMemoryBytes:
    def test_returns_positive_int_on_unix(self):
        # Linux + macOS both implement SC_PHYS_PAGES.
        result = get_host_memory_bytes()
        assert result is None or (isinstance(result, int) and result > 0)


def _prep_sandbox(memory: str, fake_image_set, mock_runtime) -> Sandbox:
    sandbox = Sandbox(image="ubuntu", memory=memory)
    sandbox._runtime_info = mock_runtime
    sandbox._image = ResolvedImage(name="ubuntu", chain=[fake_image_set["qcow2"]])
    sandbox._accel = ResolvedAccelerator(accel=Accelerator.HVF)
    sandbox._overlay_path = Path("/tmp/overlay.qcow2")
    return sandbox


class TestMemoryBudgetWarning:
    def test_silent_when_well_under_budget(self, fake_image_set, mock_runtime, caplog):
        sandbox = _prep_sandbox("256M", fake_image_set, mock_runtime)
        # Pretend host has 64G of RAM regardless of actual host.
        with (
            patch(_HOST_RAM, return_value=64 * 1024**3),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            sandbox._check_memory_budget()
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_warns_above_80_percent(self, fake_image_set, mock_runtime, caplog):
        sandbox = _prep_sandbox("4G", fake_image_set, mock_runtime)
        # 100% — triggers the stronger branch.
        with (
            patch(_HOST_RAM, return_value=4 * 1024**3),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            sandbox._check_memory_budget()
        assert any(
            "100%" in r.getMessage() or "exceeds host RAM" in r.getMessage() for r in caplog.records
        )

    def test_warns_when_over_host_ram(self, fake_image_set, mock_runtime, caplog):
        sandbox = _prep_sandbox("16G", fake_image_set, mock_runtime)
        with (
            patch(_HOST_RAM, return_value=8 * 1024**3),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            sandbox._check_memory_budget()
        assert any("exceeds host RAM" in r.getMessage() for r in caplog.records)

    def test_warns_at_85_percent(self, fake_image_set, mock_runtime, caplog):
        sandbox = _prep_sandbox("3400M", fake_image_set, mock_runtime)  # 85% of 4G
        with (
            patch(_HOST_RAM, return_value=4 * 1024**3),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            sandbox._check_memory_budget()
        msgs = [r.getMessage() for r in caplog.records]
        assert any("of host RAM" in m for m in msgs)
        assert not any("exceeds host RAM" in m for m in msgs)

    def test_silent_when_host_ram_unknown(self, fake_image_set, mock_runtime, caplog):
        sandbox = _prep_sandbox("64G", fake_image_set, mock_runtime)
        with (
            patch(_HOST_RAM, return_value=None),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            sandbox._check_memory_budget()
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
