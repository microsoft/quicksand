#!/usr/bin/env python3
"""TCP relay for QEMU guestfwd cmd: tunnels.

QEMU spawns a new instance of this script per guest TCP connection (inetd-style).
stdin/stdout are connected to the guest TCP stream. This script relays
bidirectionally between that stream and a host-side TCP connection.

Usage (invoked by QEMU, not directly):
    python3 _tcp_relay.py <host> <port>
"""

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


def main():
    host, port = sys.argv[1], int(sys.argv[2])
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))

    stdin_fd = sys.stdin.buffer.fileno()
    stdout_fd = sys.stdout.buffer.fileno()

    # stdin → socket (guest → host SMB server)
    t1 = threading.Thread(
        target=_relay,
        args=(lambda n: os.read(stdin_fd, n), sock.sendall),
        daemon=True,
    )
    # socket → stdout (host SMB server → guest)
    t2 = threading.Thread(
        target=_relay,
        args=(sock.recv, lambda d: os.write(stdout_fd, d)),
        daemon=True,
    )
    t1.start()
    t2.start()
    # Wait for either direction to close (guest disconnect or server disconnect)
    t1.join()


if __name__ == "__main__":
    main()
