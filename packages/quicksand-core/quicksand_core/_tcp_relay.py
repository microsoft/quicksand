#!/usr/bin/env python3
"""TCP relay for QEMU guestfwd cmd: tunnels.

QEMU spawns a new instance of this script per guest TCP connection (inetd-style).
stdin/stdout are connected to the guest TCP stream. This script relays
bidirectionally between that stream and a host-side TCP connection.

Usage (invoked by QEMU, not directly):
    python3 _tcp_relay.py <host> <port>
"""

import contextlib
import os
import socket
import sys
import threading


def _relay(src_read, dst_write):
    """Copy data from src_read to dst_write until EOF or error."""
    try:
        while True:
            data = src_read(4096)
            if not data:
                break
            dst_write(data)
    except (OSError, BrokenPipeError):
        pass


def _guest_to_host(stdin_fd, sock):
    _relay(lambda n: os.read(stdin_fd, n), sock.sendall)
    # The guest closed its write side. Pass the EOF on and keep the socket
    # open for reading, since the host may still be answering.
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_WR)


def main():
    host, port = sys.argv[1], int(sys.argv[2])
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))

    stdin_fd = sys.stdin.buffer.fileno()
    stdout_fd = sys.stdout.buffer.fileno()

    # stdin → socket (guest → host service)
    t1 = threading.Thread(target=_guest_to_host, args=(stdin_fd, sock), daemon=True)
    # socket → stdout (host service → guest)
    t2 = threading.Thread(
        target=_relay,
        args=(sock.recv, lambda d: os.write(stdout_fd, d)),
        daemon=True,
    )
    t1.start()
    t2.start()
    # The session ends when the host stops sending (or the guest is gone and
    # writes to it fail). Exiting closes the guest's stream.
    t2.join()


if __name__ == "__main__":
    main()
