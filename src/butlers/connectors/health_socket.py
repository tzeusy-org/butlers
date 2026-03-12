"""Shared helper for connector health/webhook server sockets.

Creates pre-bound sockets with SO_REUSEADDR so health servers can restart
quickly without hitting EADDRINUSE from TIME_WAIT sockets left by a
previous process.  If a stale process is holding the port, it is
terminated automatically before binding.
"""

from __future__ import annotations

import errno
import logging
import os
import signal
import socket
import subprocess
import time

logger = logging.getLogger(__name__)

_BIND_RETRIES = 3
_BIND_RETRY_DELAY = 1.0  # seconds


def _kill_port_holder(port: int) -> bool:
    """Find and SIGTERM the process listening on *port*. Returns True if killed."""
    try:
        out = subprocess.check_output(
            ["ss", "-tlnp", f"sport = :{port}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False

    for line in out.splitlines():
        # ss output contains pid=<N> inside the users column
        if f":{port}" not in line:
            continue
        idx = line.find("pid=")
        if idx == -1:
            continue
        pid_str = line[idx + 4 :].split(",", 1)[0].split(")", 1)[0]
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        logger.warning("Killing stale process %d holding port %d", pid, port)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        return True
    return False


def make_health_socket(host: str, port: int, backlog: int = 128) -> socket.socket:
    """Create a TCP socket with SO_REUSEADDR, bound and listening.

    Pass the returned socket to ``uvicorn.Server.serve(sockets=[sock])``
    so uvicorn skips its own bind and inherits SO_REUSEADDR.

    On EADDRINUSE the stale port holder is killed and the bind is retried.
    """
    for attempt in range(1, _BIND_RETRIES + 1):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            sock.close()
            if exc.errno == errno.EADDRINUSE and attempt < _BIND_RETRIES:
                _kill_port_holder(port)
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
