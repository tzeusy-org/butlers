"""Shared helper for connector health/webhook server sockets.

Creates pre-bound sockets with SO_REUSEADDR so health servers can restart
quickly without hitting EADDRINUSE from TIME_WAIT sockets left by a
previous process.
"""

from __future__ import annotations

import errno
import logging
import socket
import time

logger = logging.getLogger(__name__)

_BIND_RETRIES = 3
_BIND_RETRY_DELAY = 1.0  # seconds


def make_health_socket(host: str, port: int, backlog: int = 128) -> socket.socket:
    """Create a TCP socket with SO_REUSEADDR, bound and listening.

    Pass the returned socket to ``uvicorn.Server.serve(sockets=[sock])``
    so uvicorn skips its own bind and inherits SO_REUSEADDR.

    Retries up to 3 times on EADDRINUSE to handle the brief overlap when a
    previous connector instance is still shutting down.
    """
    for attempt in range(1, _BIND_RETRIES + 1):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            sock.close()
            if exc.errno == errno.EADDRINUSE and attempt < _BIND_RETRIES:
                logger.warning(
                    "Port %d in use, retrying in %.1fs (attempt %d/%d)",
                    port,
                    _BIND_RETRY_DELAY,
                    attempt,
                    _BIND_RETRIES,
                )
                time.sleep(_BIND_RETRY_DELAY)
                continue
            raise
        sock.listen(backlog)
        sock.setblocking(False)
        return sock
    # Unreachable, but keeps type checkers happy.
    raise RuntimeError("Failed to bind health socket")
