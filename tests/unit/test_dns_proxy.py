"""Unit tests for the macOS host DNS proxy (host/dns_proxy.py) and its config.

The proxy is macOS-only (dnslib is a macOS-only dependency and the patched
libslirp that drives it is only bundled on macOS), so the functional tests
skip elsewhere. The config/env-name tests run everywhere.
"""

from __future__ import annotations

import socket
import sys

import pytest
from quicksand_core import SandboxConfig
from quicksand_core._types import EnvironmentVariables

darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="host DNS proxy is macOS-only")


def _query(port: int, name: str, qtype: str = "A"):
    from dnslib import DNSRecord

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3)
    try:
        sock.sendto(DNSRecord.question(name, qtype).pack(), ("127.0.0.1", port))
        data, _ = sock.recvfrom(4096)
    finally:
        sock.close()
    return DNSRecord.parse(data)


@darwin_only
def test_proxy_resolves_localhost():
    """A records come back via getaddrinfo (no network needed for localhost)."""
    from quicksand_core.host.dns_proxy import HostDnsProxy

    proxy = HostDnsProxy()
    proxy.start()
    try:
        reply = _query(proxy.port, "localhost", "A")
        answers = [str(rr.rdata) for rr in reply.rr]
        assert "127.0.0.1" in answers
    finally:
        proxy.stop()


@darwin_only
def test_proxy_empty_for_unresolvable():
    """Unresolvable names yield an empty NOERROR reply, never a crash/hang."""
    from quicksand_core.host.dns_proxy import HostDnsProxy

    proxy = HostDnsProxy()
    proxy.start()
    try:
        reply = _query(proxy.port, "quicksand-nope.invalid", "A")
        assert len(reply.rr) == 0
    finally:
        proxy.stop()


@darwin_only
def test_proxy_aaaa_does_not_return_v4mapped():
    """macOS getaddrinfo returns v4-mapped AAAA; the proxy must drop them
    (guest slirp network is IPv4-only) and never emit a malformed record."""
    from quicksand_core.host.dns_proxy import HostDnsProxy

    proxy = HostDnsProxy()
    proxy.start()
    try:
        reply = _query(proxy.port, "localhost", "AAAA")
        for rr in reply.rr:
            assert "." not in str(rr.rdata)  # no v4-mapped leak
    finally:
        proxy.stop()


def test_config_field_default_is_auto():
    assert SandboxConfig(image="alpine").host_dns_proxy is None


def test_config_field_override():
    assert SandboxConfig(image="alpine", host_dns_proxy=True).host_dns_proxy is True
    assert SandboxConfig(image="alpine", host_dns_proxy=False).host_dns_proxy is False


def test_dns_proxy_env_var_name():
    # The bundled patched libslirp reads exactly this variable.
    assert EnvironmentVariables.DNS_PROXY == "QUICKSAND_DNS_PROXY"
