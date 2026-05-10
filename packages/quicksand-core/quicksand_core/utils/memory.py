"""Parse and validate QEMU-style memory size strings.

QEMU's ``-m`` flag accepts a bare integer (interpreted as MiB) or a number
with a unit suffix: ``K``, ``M``, ``G``, ``T`` (case-insensitive, binary
units — 1024-based). For ergonomics we also accept the ``KiB``/``MiB``/...
forms and a trailing ``B``, all of which mean the same as the bare suffix.
"""

from __future__ import annotations

import re

__all__ = ["format_bytes", "parse_memory_size"]


_UNIT_MULTIPLIERS = {
    "": 1024**2,  # bare number -> MiB (matches QEMU's -m default)
    "k": 1024,
    "m": 1024**2,
    "g": 1024**3,
    "t": 1024**4,
}

_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)\s*([kmgtKMGT]?)(?:i?[bB]?)$")


def parse_memory_size(value: str | int) -> int:
    """Parse a QEMU memory size into bytes.

    Accepts a bare int (interpreted as MiB, matching QEMU's default), or a
    string of the form ``"<number>[<unit>]"`` where unit is one of K/M/G/T
    (case-insensitive). Fractional values are allowed (``"1.5G"``). Optional
    ``i`` and/or trailing ``B`` are accepted (``"512MiB"``, ``"2GB"``).

    Raises ``ValueError`` for unparseable input or non-positive sizes.
    """
    if isinstance(value, int):
        if value <= 0:
            raise ValueError(f"memory must be positive, got {value}")
        return value * _UNIT_MULTIPLIERS["m"]

    s = value.strip()
    if not s:
        raise ValueError("memory must not be empty")

    m = _PATTERN.match(s)
    if not m:
        raise ValueError(
            f"invalid memory size {value!r}: expected forms like '512M', '2G', "
            f"'1.5G', '2048' (MiB), '512MiB', '4GB'"
        )

    number_str, unit = m.group(1), m.group(2).lower()
    number = float(number_str)
    if number <= 0:
        raise ValueError(f"memory must be positive, got {value!r}")

    bytes_ = int(number * _UNIT_MULTIPLIERS[unit])
    if bytes_ <= 0:
        raise ValueError(f"memory rounds to zero bytes: {value!r}")
    return bytes_


def format_bytes(n: int) -> str:
    """Render a byte count as a short human-readable string (e.g. ``'1.5G'``)."""
    for unit, mult in (("T", 1024**4), ("G", 1024**3), ("M", 1024**2), ("K", 1024)):
        if n >= mult:
            value = n / mult
            return f"{value:.1f}{unit}" if value < 10 else f"{value:.0f}{unit}"
    return f"{n}B"
