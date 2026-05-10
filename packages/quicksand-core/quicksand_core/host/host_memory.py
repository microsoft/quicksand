"""Cross-platform host physical RAM detection.

Best-effort: returns ``None`` on platforms where we can't determine the
value. Used by the sandbox lifecycle to emit a budget warning when a
sandbox is configured to use a large fraction of host RAM.
"""

from __future__ import annotations

import os

__all__ = ["get_host_memory_bytes"]


def get_host_memory_bytes() -> int | None:
    """Return total physical RAM in bytes, or ``None`` if unavailable.

    Works on Linux and macOS via ``sysconf``. Returns ``None`` on Windows
    or any platform where the values can't be read.
    """
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, ValueError, OSError):
        return None
    if pages <= 0 or page_size <= 0:
        return None
    return pages * page_size
