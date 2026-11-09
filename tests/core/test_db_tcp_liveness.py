"""Pool connections detect a partitioned peer (bu-dhloi1.58).

A savepoint ROLLBACK TO and the outer COMMIT after an admission deadline carry no client
timer, by design: a second timer there recreates asyncpg's shared cancel-future bug. A
partition would then hold the connection and its row locks unbounded, so every pool
connection arms TCP keepalives and a user timeout and the kernel resets a dead peer.
"""

from __future__ import annotations

import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from butlers import db
from butlers.db import Database

pytestmark = pytest.mark.unit


def _conn(sock: socket.socket | None) -> SimpleNamespace:
    transport = SimpleNamespace(get_extra_info=lambda name: sock if name == "socket" else None)
    return SimpleNamespace(_transport=transport)


async def test_a_new_tcp_connection_gets_keepalive_and_a_user_timeout() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with patch("butlers.db.register_jsonb_codec", new=AsyncMock()):
            await Database(db_name="x")._init_connection(_conn(sock))
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE) == db.TCP_KEEPIDLE_S
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL) == db.TCP_KEEPINTVL_S
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT) == db.TCP_KEEPCNT
        assert sock.getsockopt(socket.IPPROTO_TCP, 18) == db.TCP_USER_TIMEOUT_MS  # TCP_USER_TIMEOUT
    finally:
        sock.close()


@pytest.mark.parametrize("kind", ["unix", "none", "refuses"])
async def test_connections_without_a_tcp_socket_still_initialise(kind: str) -> None:
    sock = (
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if kind == "unix"
        else None
        if kind == "none"
        else SimpleNamespace(
            family=socket.AF_INET,
            setsockopt=lambda *a: (_ for _ in ()).throw(OSError("no such option")),
        )
    )
    register = AsyncMock()
    with patch("butlers.db.register_jsonb_codec", new=register):
        await Database(db_name="x")._init_connection(_conn(sock))
    register.assert_awaited_once()
    if kind == "unix":
        sock.close()


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
async def test_the_pool_sets_no_command_timeout(create_pool: AsyncMock) -> None:
    """``timeout=None`` falls back to it and would re-arm the second client timer."""
    await Database(db_name="x").connect()
    assert "command_timeout" not in create_pool.call_args.kwargs
