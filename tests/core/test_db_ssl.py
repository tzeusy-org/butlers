"""Unit tests for DB SSL configuration parsing and wiring — condensed."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from butlers.db import Database, schema_search_path

pytestmark = pytest.mark.unit


def test_ssl_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL sslmode parsed; POSTGRES_SSLMODE fallback; public schema_search_path correct."""
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@host:5432/postgres?sslmode=disable")
    assert Database.from_env("test_db").ssl == "disable"

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_SSLMODE", "verify-full")
    assert Database.from_env("test_db").ssl == "verify-full"

    assert schema_search_path("public") == "public"


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_ssl_forwarded_to_provision_and_connect_and_retry(
    mock_connect: AsyncMock, mock_create_pool: AsyncMock
) -> None:
    """provision() and connect() forward ssl to asyncpg; search_path set for schema;
    retry on connection_lost."""
    # ssl forwarded to provision
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)
    conn.execute = AsyncMock()
    conn.close = AsyncMock()
    mock_connect.return_value = conn
    await Database(db_name="test_db", ssl="disable").provision()
    assert mock_connect.await_args.kwargs["ssl"] == "disable"

    # ssl forwarded to connect
    pool = AsyncMock()
    mock_create_pool.return_value = pool
    out = await Database(db_name="test_db", ssl="require").connect()
    assert out is pool and mock_create_pool.await_args.kwargs["ssl"] == "require"

    # search_path set for schema-scoped topology
    mock_create_pool.reset_mock()
    await Database(db_name="butlers", schema="general").connect()
    assert mock_create_pool.await_args.kwargs["server_settings"] == {
        "search_path": "general,public"
    }

    # SSL retry on connection_lost: provision
    mock_connect.reset_mock()
    mock_connect.side_effect = [ConnectionError("unexpected connection_lost() call"), conn]
    await Database(db_name="test_db2").provision()
    assert mock_connect.await_count == 2
    assert mock_connect.await_args_list[1].kwargs["ssl"] == "disable"

    # SSL retry on connection_lost: connect
    mock_create_pool.reset_mock()
    pool2 = AsyncMock()
    mock_create_pool.side_effect = [ConnectionError("unexpected connection_lost() call"), pool2]
    out2 = await Database(db_name="test_db3").connect()
    assert out2 is pool2 and mock_create_pool.await_args_list[1].kwargs["ssl"] == "disable"


@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_provision_collation_refresh(mock_connect: AsyncMock) -> None:
    """provision() refreshes template1 collation before CREATE DATABASE; continues on failure."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    conn.close = AsyncMock()
    mock_connect.return_value = conn

    await Database(db_name="test_db").provision()
    calls = [c.args[0] for c in conn.execute.await_args_list]
    assert calls[0] == "ALTER DATABASE template1 REFRESH COLLATION VERSION"
    assert "CREATE DATABASE" in calls[1]

    # Failure in collation refresh does not stop provisioning
    conn2 = AsyncMock()
    conn2.fetchval = AsyncMock(return_value=1)
    conn2.execute = AsyncMock(side_effect=[RuntimeError("permission denied"), None])
    conn2.close = AsyncMock()
    mock_connect.return_value = conn2
    await Database(db_name="test_db2").provision()  # must not raise
