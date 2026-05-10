"""Tests for memory size parsing and SandboxConfig.memory validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from quicksand_core import SandboxConfig
from quicksand_core.utils.memory import format_bytes, parse_memory_size


class TestParseMemorySize:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("512M", 512 * 1024**2),
            ("2G", 2 * 1024**3),
            ("1.5G", int(1.5 * 1024**3)),
            ("4K", 4 * 1024),
            ("1T", 1024**4),
            ("2048", 2048 * 1024**2),  # bare number is MiB
            (2048, 2048 * 1024**2),  # bare int is MiB
            ("512m", 512 * 1024**2),  # case-insensitive
            ("512MiB", 512 * 1024**2),  # KiB/MiB style accepted
            ("2GB", 2 * 1024**3),
            ("4GiB", 4 * 1024**3),
            (" 1G ", 1024**3),  # whitespace tolerated
        ],
    )
    def test_valid(self, value, expected):
        assert parse_memory_size(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "abc",
            "1X",
            "1.5.5G",
            "-1G",
            "0",
            "0G",
            "0.0001K",  # rounds to zero bytes
            "G",
        ],
    )
    def test_invalid(self, value):
        with pytest.raises(ValueError):
            parse_memory_size(value)

    def test_negative_int_rejected(self):
        with pytest.raises(ValueError):
            parse_memory_size(-1)


class TestFormatBytes:
    @pytest.mark.parametrize(
        "n,expected",
        [
            (512 * 1024**2, "512M"),
            (1024**3, "1.0G"),
            (int(1.5 * 1024**3), "1.5G"),
            (16 * 1024**3, "16G"),
            (4 * 1024, "4.0K"),
            (500, "500B"),
        ],
    )
    def test_format(self, n, expected):
        assert format_bytes(n) == expected


class TestSandboxConfigMemoryValidation:
    def test_default_parses(self):
        config = SandboxConfig(image="ubuntu")
        assert config.memory == "512M"
        assert config.memory_bytes == 512 * 1024**2

    def test_valid_memory(self):
        config = SandboxConfig(image="ubuntu", memory="2G")
        assert config.memory_bytes == 2 * 1024**3

    @pytest.mark.parametrize("bad", ["abc", "-2G", "0", "1XY", ""])
    def test_invalid_memory_rejected(self, bad):
        with pytest.raises(ValidationError):
            SandboxConfig(image="ubuntu", memory=bad)
