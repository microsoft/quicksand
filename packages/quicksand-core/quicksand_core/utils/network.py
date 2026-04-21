"""Network utilities for quicksand-core."""

from __future__ import annotations

import socket

from .._types import NetworkConstants


def find_free_port() -> int:
    """Find an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((NetworkConstants.LOCALHOST, 0))
        return s.getsockname()[1]


def find_free_vnc_port() -> int:
    """Find an available VNC port on localhost (5900-5999).

    QEMU VNC uses display numbers: port = 5900 + display.
    This returns the actual port (5900-5999); callers pass display = port - 5900
    to QEMU's -display vnc=host:display argument.
    """
    for port in range(5900, 6000):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((NetworkConstants.LOCALHOST, port))
                return port
        except OSError:
            continue
    raise RuntimeError("No free VNC ports available in range 5900-5999")
