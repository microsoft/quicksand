"""
Quicksand QEMU bundles QEMU binaries for the quicksand VM harness.

This package contains pre-built QEMU binaries that work out of the box.
Install with: pip install quicksand[qemu]
"""

from pathlib import Path

__version__ = "0.1.0"

_PACKAGE_DIR = Path(__file__).parent
_BIN_DIR = _PACKAGE_DIR / "bin"


def get_bin_dir() -> Path:
    """Get the path to the bundled QEMU bin directory.

    Returns:
        Path to the directory containing qemu-system-* and qemu-img binaries.

    Raises:
        FileNotFoundError: If the bin directory doesn't exist (package not built correctly).
    """
    if not _BIN_DIR.exists():
        raise FileNotFoundError(
            f"Bundled QEMU binaries not found at {_BIN_DIR}. "
            "The package may not have been built correctly."
        )
    return _BIN_DIR


__all__ = ["__version__", "get_bin_dir"]
