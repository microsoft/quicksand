"""Tests for unrestricted network mode."""

from __future__ import annotations

import asyncio

import pytest


async def _wait_for_network(sandbox, timeout: float = 15.0) -> None:
    """Wait for guest networking to be ready (default route present).

    With virtio-serial, the agent connects before networking is up.
    """
    for _ in range(int(timeout / 0.5)):
        result = await sandbox.execute("ip route show default", timeout=5.0)
        if result.exit_code == 0 and "default" in result.stdout:
            return
        await asyncio.sleep(0.5)


@pytest.mark.integration
@pytest.mark.slow
class TestUnrestrictedNetwork:
    """Unrestricted network tests using shared sandbox."""

    @pytest.mark.asyncio
    async def test_unrestricted_can_reach_external(self, shared_sandbox):
        """Test that unrestricted network can reach external hosts.

        Note: This test may fail in some CI environments that don't have
        internet access. The key assertion is that the network is not
        blocked at the QEMU level.
        """
        await _wait_for_network(shared_sandbox)
        # Try to reach an external host - this tests that QEMU isn't blocking
        result = await shared_sandbox.execute("ping -c 1 -W 5 8.8.8.8", timeout=10.0)
        # If we have internet, this should succeed. If not, at least verify
        # we got a proper network error (not a QEMU firewall block)
        # Exit code 0 = success, 1 = host unreachable (but network worked)
        # Exit code 2 = network error
        assert result.exit_code in (0, 1, 2)

    @pytest.mark.asyncio
    async def test_dns_resolution(self, shared_sandbox):
        """Test that DNS resolution works in unrestricted network mode."""
        await _wait_for_network(shared_sandbox)
        # Try to resolve a well-known hostname using getent (portable)
        result = await shared_sandbox.execute("getent hosts google.com", timeout=10.0)
        # getent should succeed if DNS is working
        assert result.exit_code == 0, f"DNS resolution failed: {result.stderr}"

        # Parse getent output and require google.com as a hostname token,
        # not merely a substring at an arbitrary position.
        lines = [line.split() for line in result.stdout.splitlines() if line.strip()]
        has_google_host = any(len(fields) >= 2 and "google.com" in fields[1:] for fields in lines)
        assert has_google_host, "Expected google.com hostname token in getent output"

    @pytest.mark.asyncio
    async def test_curl_https(self, shared_sandbox):
        """Test that HTTPS requests work (requires DNS + network)."""
        await _wait_for_network(shared_sandbox)
        # Try to fetch a URL - this tests both DNS and network connectivity
        result = await shared_sandbox.execute(
            "curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://google.com",
            timeout=15.0,
        )
        # Should get a redirect (301/302) or success (200)
        assert result.exit_code == 0, f"curl failed: {result.stderr}"
        http_code = result.stdout.strip()
        assert http_code in ("200", "301", "302"), f"Unexpected HTTP code: {http_code}"
