"""Fixtures for guest forward tests: a host HTTP service and a MOUNTS_ONLY sandbox."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import pytest_asyncio
from quicksand import GuestForward, NetworkMode, Sandbox

from tests.conftest import has_qemu
from tests.integration.conftest import get_vm_type

GUEST_ADDRESS = "10.0.2.101"
GUEST_PORT = 8080


class _HostService(BaseHTTPRequestHandler):
    """Answers GET /<anything> with the path; /slow waits a second first."""

    def do_GET(self):
        if self.path == "/slow":
            time.sleep(1)
        body = f"host saw {self.path}\n".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="module")
def host_service():
    """HTTP service on the host loopback; yields its port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HostService)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest_asyncio.fixture(scope="module")
async def forwarded_sandbox(real_image, real_kernel, host_service):
    """MOUNTS_ONLY sandbox where GUEST_ADDRESS:GUEST_PORT reaches the host service."""
    if sys.platform == "win32":
        pytest.skip("guest_forwards are not supported on Windows hosts")
    if not has_qemu():
        pytest.skip("QEMU not installed")

    sandbox = Sandbox(
        image=get_vm_type(),
        network_mode=NetworkMode.MOUNTS_ONLY,
        guest_forwards=[
            GuestForward(guest_address=GUEST_ADDRESS, guest_port=GUEST_PORT, host_port=host_service)
        ],
    )
    await sandbox.start()
    # With virtio-serial the agent answers before the guest has its address.
    for _ in range(60):
        result = await sandbox.execute("ip -4 addr show", timeout=5.0)
        if "10.0.2.15/" in result.stdout:
            break
        await asyncio.sleep(0.5)
    yield sandbox
    await sandbox.stop()
