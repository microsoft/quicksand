"""File hashing utilities.

This module consolidates hash computation that was previously duplicated
across checkpoint.py, image.py, and manifest.py.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# Optimal chunk size for file operations (64KB)
DEFAULT_CHUNK_SIZE = 65536


def compute_file_sha256(path: Path, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    """Compute SHA-256 hash of a file using streaming.

    Uses chunked reading to handle large files without loading
    the entire file into memory.

    Args:
        path: Path to the file to hash.
        chunk_size: Size of chunks to read at a time.

    Returns:
        Hexadecimal SHA-256 digest of the file contents.
    """
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            sha256.update(chunk)
    return sha256.hexdigest()
