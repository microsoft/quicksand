"""Fixtures for checkpoint tests - no shared sandbox (exception).

Checkpoint tests inherently require boot -> checkpoint -> stop -> restore cycles,
so each test manages its own sandbox(es).
"""

from __future__ import annotations
