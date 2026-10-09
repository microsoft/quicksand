"""Tests for Forward(Guest, Host): host services reachable from an offline guest."""

from __future__ import annotations

import pytest
from quicksand_core._types import NetworkConstants

from .conftest import GUEST_PORT

_URL = f"http://{NetworkConstants.GUESTFWD_DEFAULT_IP}:{GUEST_PORT}"

# Ubuntu images ship curl; Alpine images only have BusyBox wget. ``fetch``
# is a shell function that writes the response body to stdout either way.
_FETCH = (
    'fetch() { if command -v curl >/dev/null 2>&1; then curl -sS --max-time 10 "$1"; '
    'else wget -qO- -T 10 "$1"; fi; }; '
)


@pytest.mark.integration
@pytest.mark.slow
class TestGuestToHostForward:
    """Guest-forward tests using a shared MOUNTS_ONLY sandbox."""

    @pytest.mark.asyncio
    async def test_guest_reaches_host_server(self, shared_sandbox):
        """A plain GET through the tunnel returns the host server's response."""
        result = await shared_sandbox.execute(f"{_FETCH} fetch {_URL}/hello", timeout=15.0)
        assert result.exit_code == 0, f"fetch failed: {result.stderr}"
        assert result.stdout.strip() == "path=/hello"

    @pytest.mark.asyncio
    async def test_concurrent_connections_are_independent(self, shared_sandbox):
        """Parallel guest connections each get their own host connection.

        This is the property the per-connection relay buys over a chardev
        target, which would interleave all guest connections on one stream.
        """
        n = 8
        script = _FETCH + " ".join(f"fetch {_URL}/req{i} > /tmp/gf_{i} &" for i in range(n))
        script += " wait; cat " + " ".join(f"/tmp/gf_{i}" for i in range(n))
        result = await shared_sandbox.execute(script, timeout=30.0)
        assert result.exit_code == 0, f"fetch failed: {result.stderr}"
        lines = sorted(result.stdout.split())
        assert lines == sorted(f"path=/req{i}" for i in range(n))

    @pytest.mark.asyncio
    async def test_sequential_connections_reopen_cleanly(self, shared_sandbox):
        """Each request opens and closes its own tunnel connection."""
        for i in range(3):
            result = await shared_sandbox.execute(f"{_FETCH} fetch {_URL}/seq{i}", timeout=15.0)
            assert result.exit_code == 0, f"fetch failed: {result.stderr}"
            assert result.stdout.strip() == f"path=/seq{i}"

    @pytest.mark.asyncio
    async def test_internet_still_blocked(self, shared_sandbox):
        """The tunnel does not loosen MOUNTS_ONLY isolation."""
        result = await shared_sandbox.execute(
            f"{_FETCH} fetch http://1.1.1.1/ >/dev/null", timeout=20.0
        )
        assert result.exit_code != 0
