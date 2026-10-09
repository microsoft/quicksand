"""Unit tests for the guestfwd TCP relay (``quicksand_core/_tcp_relay.py``).

QEMU runs the relay once per guest connection with the guest's TCP stream on
stdin/stdout. These tests stand in for QEMU with pipes and for the host
service with a loopback listener.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import quicksand_core

RELAY = Path(quicksand_core.__file__).parent / "_tcp_relay.py"


@pytest.fixture
def host_service() -> Iterator[Callable[[Callable[[socket.socket], None]], int]]:
    """Start a one-connection loopback service; returns its port."""
    sockets: list[socket.socket] = []

    def start(handle: Callable[[socket.socket], None]) -> int:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        sockets.append(server)

        def serve() -> None:
            conn, _ = server.accept()
            with conn:
                handle(conn)

        threading.Thread(target=serve, daemon=True).start()
        return server.getsockname()[1]

    yield start
    for s in sockets:
        s.close()


def _recv_until_eof(conn: socket.socket) -> bytes:
    data = b""
    while chunk := conn.recv(4096):
        data += chunk
    return data


def _relay(port: int, **kwargs) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(RELAY), "127.0.0.1", str(port)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        **kwargs,
    )


def test_reply_after_guest_half_close_reaches_guest(host_service):
    """A guest that shuts down its write side still gets the host's answer."""

    def upper_until_eof(conn: socket.socket) -> None:
        conn.sendall(_recv_until_eof(conn).upper())

    port = host_service(upper_until_eof)
    proc = _relay(port)
    try:
        out, _ = proc.communicate(b"hello from the guest\n", timeout=10)
    finally:
        proc.kill()

    assert out == b"HELLO FROM THE GUEST\n"


def test_host_close_ends_the_session(host_service):
    """When the host closes, the relay exits so the guest sees EOF."""

    def answer_one_line(conn: socket.socket) -> None:
        line = b""
        while not line.endswith(b"\n"):
            line += conn.recv(4096)
        conn.sendall(b"got " + line)

    port = host_service(answer_one_line)
    proc = _relay(port)
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(b"ping\n")
        proc.stdin.flush()
        # stdin stays open: the guest has not closed its side.
        proc.wait(timeout=10)
        out = proc.stdout.read()
    finally:
        proc.kill()

    assert out == b"got ping\n"
