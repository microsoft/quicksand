"""Tests for guest_forwards: reaching a host service from a MOUNTS_ONLY guest."""

from __future__ import annotations

import pytest

from .conftest import GUEST_ADDRESS, GUEST_PORT

URL = f"http://{GUEST_ADDRESS}:{GUEST_PORT}"


@pytest.mark.integration
class TestGuestForwards:
    @pytest.mark.asyncio
    async def test_guest_reaches_host_service(self, forwarded_sandbox):
        result = await forwarded_sandbox.execute(f"curl -s --max-time 10 {URL}/hello")
        assert result.exit_code == 0, result.stderr
        assert result.stdout == "host saw /hello\n"

    @pytest.mark.asyncio
    async def test_each_connection_reaches_the_host(self, forwarded_sandbox):
        """The host closes every connection; later ones must still work."""
        for i in range(3):
            result = await forwarded_sandbox.execute(f"curl -s --max-time 10 {URL}/{i}")
            assert result.stdout == f"host saw /{i}\n"

    @pytest.mark.asyncio
    async def test_concurrent_connections(self, forwarded_sandbox):
        result = await forwarded_sandbox.execute(
            f"curl -s --max-time 10 {URL}/slow > /tmp/slow & "
            f"sleep 0.3; curl -s --max-time 10 {URL}/fast; wait; cat /tmp/slow",
            timeout=20.0,
        )
        assert result.stdout == "host saw /fast\nhost saw /slow\n"

    @pytest.mark.asyncio
    async def test_rest_of_host_stays_unreachable(self, forwarded_sandbox, host_service):
        """The forward opens one endpoint, not the host gateway."""
        result = await forwarded_sandbox.execute(
            f"curl -s --max-time 3 http://10.0.2.2:{host_service}/", timeout=10.0
        )
        assert result.exit_code != 0
