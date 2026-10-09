"""Fixtures for guest-forward tests: a host HTTP server exposed inside an offline sandbox."""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
import pytest_asyncio
from quicksand import Forward, Guest, Host, NetworkMode, Sandbox

from tests.conftest import has_qemu
from tests.integration.conftest import get_vm_type

GUEST_PORT = 8080


class _EchoPathHandler(BaseHTTPRequestHandler):
    """Answers every GET with ``path=<request path>`` so callers can tell responses apart."""

    def do_GET(self):
        body = f"path={self.path}\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def host_http_server():
    """Loopback-only HTTP server on a random port, served on a background thread."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EchoPathHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest_asyncio.fixture(scope="module")
async def shared_sandbox(real_image, real_kernel, host_http_server):
    """Module-scoped MOUNTS_ONLY sandbox with the host HTTP server forwarded in."""
    if not has_qemu():
        pytest.skip("QEMU not installed")

    sandbox = Sandbox(
        image=get_vm_type(),
        network_mode=NetworkMode.MOUNTS_ONLY,
        port_forwards=[
            Forward(Guest(GUEST_PORT), Host(host_http_server.server_port)),
        ],
    )
    await sandbox.start()
    await _wait_for_network(sandbox)
    yield sandbox
    await sandbox.stop()


async def _wait_for_network(sandbox: Sandbox, timeout: float = 15.0) -> None:
    """Wait until the guest has a default route.

    The agent channel comes up over virtio-serial before guest networking
    does, so the first TCP connection can otherwise race DHCP. Reads
    ``/proc/net/route`` rather than ``ip``, which minimal images may lack.
    """
    for _ in range(int(timeout / 0.5)):
        result = await sandbox.execute(
            "awk '$2 == \"00000000\" { found = 1 } END { exit !found }' /proc/net/route",
            timeout=5.0,
        )
        if result.exit_code == 0:
            return
        await asyncio.sleep(0.5)
    raise RuntimeError("guest network did not come up")
