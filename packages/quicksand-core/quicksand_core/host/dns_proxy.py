"""Host-side DNS proxy for macOS VPN/split-DNS resolution.

On macOS, libslirp discovers a single upstream nameserver via libresolv and
forwards all guest DNS to it from an unbound socket. It does not honor the
system's scoped resolvers (``scutil --dns``), so when a VPN is active the
guest's DNS fails — often entirely — because the chosen resolver is reachable
only through the VPN interface, or the wrong scope is picked. Toggling the VPN
just reshuffles which resolver gets picked, which is the intermittent symptom
users hit.

The bundled libslirp is patched so that, when ``$QUICKSAND_DNS_PROXY`` names a
port, it redirects the DNS it forwards (guest -> 10.0.2.3) to
``127.0.0.1:<port>``. This module is the host-side endpoint of that redirect: a
tiny DNS server that answers A/AAAA via :func:`socket.getaddrinfo`, the same OS
resolver every working macOS app uses — so it honors scoped/VPN/split-DNS
automatically.

The guest's slirp network is IPv4-only, so AAAA answers are intentionally left
empty (macOS returns v4-mapped addresses for AAAA, which are not real IPv6);
clients fall back to A.
"""

from __future__ import annotations

import logging
import socket
from typing import TYPE_CHECKING

from ..utils import find_free_port

if TYPE_CHECKING:
    from dnslib import DNSRecord
    from dnslib.server import DNSHandler

logger = logging.getLogger("quicksand.dns_proxy")


class _GetAddrInfoResolver:
    """dnslib resolver that answers A/AAAA via the host's system resolver."""

    def resolve(self, request: DNSRecord, handler: DNSHandler) -> DNSRecord:
        from dnslib import AAAA, QTYPE, RR, A

        reply = request.reply()
        qtype = QTYPE[request.q.qtype]
        name = str(request.q.qname).rstrip(".")

        # Only A/AAAA are answered via getaddrinfo. Other types get an empty
        # NOERROR so the guest's resolver falls back cleanly instead of hanging.
        if qtype not in ("A", "AAAA"):
            return reply

        family = socket.AF_INET if qtype == "A" else socket.AF_INET6
        try:
            infos = socket.getaddrinfo(name, None, family, socket.SOCK_STREAM)
        except socket.gaierror:
            return reply  # name does not resolve — empty answer

        seen: set[str] = set()
        for info in infos:
            fam = info[0]
            ip = str(info[4][0]).split("%")[0]  # strip any IPv6 scope id
            if ip in seen:
                continue
            try:
                if qtype == "A" and fam == socket.AF_INET:
                    rdata = A(ip)
                elif qtype == "AAAA" and fam == socket.AF_INET6:
                    # macOS returns v4-mapped (::ffff:1.2.3.4) for AAAA; those
                    # are not real IPv6 and the guest network is IPv4-only.
                    if ip.startswith("::ffff:") or "." in ip:
                        continue
                    rdata = AAAA(ip)
                else:
                    continue
            except Exception:
                logger.debug("Skipping unparseable address %s for %s", ip, name)
                continue
            seen.add(ip)
            reply.add_answer(RR(request.q.qname, request.q.qtype, rdata=rdata, ttl=60))

        return reply


class HostDnsProxy:
    """A loopback DNS server that resolves via the host OS resolver.

    Lifecycle mirrors :class:`~quicksand_core.host.smb.SMBServer`: construct,
    :meth:`start`, then :meth:`stop`. The chosen :attr:`port` is passed to the
    patched libslirp via the ``QUICKSAND_DNS_PROXY`` environment variable.
    """

    def __init__(self) -> None:
        self._port = find_free_port()
        self._servers: list = []

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        from dnslib.server import DNSLogger, DNSServer

        resolver = _GetAddrInfoResolver()
        # Route dnslib's per-request/reply chatter to our logger at DEBUG so it
        # is suppressed at the default level (prefix=False drops dnslib's own
        # timestamp since the logging framework adds one).
        dns_logger = DNSLogger(prefix=False, logf=logger.debug)
        for tcp in (False, True):
            server = DNSServer(
                resolver, port=self._port, address="127.0.0.1", tcp=tcp, logger=dns_logger
            )
            server.start_thread()
            self._servers.append(server)
        logger.info("Host DNS proxy listening on 127.0.0.1:%d (udp+tcp)", self._port)

    def stop(self) -> None:
        for server in self._servers:
            try:
                server.stop()
            except Exception:
                logger.debug("Error stopping DNS proxy server", exc_info=True)
        self._servers = []
