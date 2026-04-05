"""Unit tests for DB SSL configuration parsing and wiring."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from butlers.db import Database, schema_search_path

pytestmark = pytest.mark.unit


def test_from_env_sslmode(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL sslmode parsed; POSTGRES_SSLMODE used as fallback."""
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@host:5432/postgres?sslmode=disable")
    assert Database.from_env("test_db").ssl == "disable"

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_SSLMODE", "verify-full")
    assert Database.from_env("test_db").ssl == "verify-full"


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_ssl_forwarded_to_provision_and_connect(mock_connect: AsyncMock, mock_create_pool: AsyncMock) -> None:
    """provision() and connect() each forward ssl mode to asyncpg."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)
    conn.execute = AsyncMock()
    conn.close = AsyncMock()
    mock_connect.return_value = conn
    await Database(db_name="test_db", ssl="disable").provision()
    assert mock_connect.await_args.kwargs["ssl"] == "disable"

    pool = AsyncMock()
    mock_create_pool.return_value = pool
    out = await Database(db_name="test_db", ssl="require").connect()
    assert out is pool
    assert mock_create_pool.await_args.kwargs["ssl"] == "require"


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
async def test_connect_sets_search_path_when_schema_is_configured(
    mock_create_pool: AsyncMock,
) -> None:
    """connect() sets server search_path for schema-scoped topology."""
    pool = AsyncMock()
    mock_create_pool.return_value = pool

    db = Database(db_name="butlers", schema="general")
    out = await db.connect()

    assert out is pool
    assert mock_create_pool.await_args is not None
    assert mock_create_pool.await_args.kwargs["server_settings"] == {
        "search_path": "general,public"
    }


def test_schema_search_path_for_public_schema() -> None:
    """Public schema omits duplicate entries in search_path."""
    assert schema_search_path("public") == "public"


@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
async def test_ssl_retry_on_connection_lost(
    mock_create_pool: AsyncMock, mock_connect: AsyncMock
) -> None:
    """provision() and connect() each retry once with ssl=disable on SSL upgrade error."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)
    conn.execute = AsyncMock()
    conn.close = AsyncMock()
    mock_connect.side_effect = [ConnectionError("unexpected connection_lost() call"), conn]

    await Database(db_name="test_db").provision()
    assert mock_connect.await_count == 2
    assert mock_connect.await_args_list[0].kwargs.get("ssl") is None
    assert mock_connect.await_args_list[1].kwargs["ssl"] == "disable"

    pool = AsyncMock()
    mock_create_pool.side_effect = [ConnectionError("unexpected connection_lost() call"), pool]
    out = await Database(db_name="test_db2").connect()
    assert out is pool
    assert mock_create_pool.await_count == 2
    assert mock_create_pool.await_args_list[1].kwargs["ssl"] == "disable"


@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_provision_collation_refresh(mock_connect: AsyncMock) -> None:
    """provision() refreshes template1 collation before CREATE DATABASE; continues on failure."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)  # DB does not exist
    conn.execute = AsyncMock()
    conn.close = AsyncMock()
    mock_connect.return_value = conn

    await Database(db_name="test_db").provision()
    calls = [c.args[0] for c in conn.execute.await_args_list]
    assert calls[0] == "ALTER DATABASE template1 REFRESH COLLATION VERSION"
    assert "CREATE DATABASE" in calls[1]

    # Failure in collation refresh does not stop provisioning
    conn2 = AsyncMock()
    conn2.fetchval = AsyncMock(return_value=1)  # DB already exists
    conn2.execute = AsyncMock(side_effect=[RuntimeError("permission denied"), None])
    conn2.close = AsyncMock()
    mock_connect.return_value = conn2
    await Database(db_name="test_db2").provision()  # must not raise
