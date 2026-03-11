"""Shared helper for connector health/webhook server sockets.

Creates pre-bound sockets with SO_REUSEADDR so health servers can restart
quickly without hitting EADDRINUSE from TIME_WAIT sockets left by a
previous process.
"""

from __future__ import annotations

import socket


def make_health_socket(host: str, port: int, backlog: int = 128) -> socket.socket:
    """Create a TCP socket with SO_REUSEADDR, bound and listening.

    Pass the returned socket to ``uvicorn.Server.serve(sockets=[sock])``
    so uvicorn skips its own bind and inherits SO_REUSEADDR.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(backlog)
    sock.setblocking(False)
    return sock
