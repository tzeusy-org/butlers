"""Unit tests for DB SSL configuration parsing and wiring."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from butlers.db import Database

pytestmark = pytest.mark.unit


def test_from_env_database_url_sslmode(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL sslmode is parsed and stored on Database."""
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@host:5432/postgres?sslmode=disable")

    db = Database.from_env("test_db")

    assert db.ssl == "disable"


def test_from_env_postgres_sslmode_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """POSTGRES_SSLMODE is used when DATABASE_URL is unset."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_SSLMODE", "verify-full")

    db = Database.from_env("test_db")

    assert db.ssl == "verify-full"


@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_provision_passes_ssl_to_asyncpg_connect(mock_connect: AsyncMock) -> None:
    """provision() forwards ssl mode to asyncpg.connect."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)
    conn.close = AsyncMock()
    mock_connect.return_value = conn

    db = Database(db_name="test_db", ssl="disable")
    await db.provision()

    assert mock_connect.await_args is not None
    assert mock_connect.await_args.kwargs["ssl"] == "disable"


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
async def test_connect_passes_ssl_to_asyncpg_pool(mock_create_pool: AsyncMock) -> None:
    """connect() forwards ssl mode to asyncpg.create_pool."""
    pool = AsyncMock()
    mock_create_pool.return_value = pool

    db = Database(db_name="test_db", ssl="require")
    out = await db.connect()

    assert out is pool
    assert mock_create_pool.await_args is not None
    assert mock_create_pool.await_args.kwargs["ssl"] == "require"


@patch("butlers.db.asyncpg.connect", new_callable=AsyncMock)
async def test_provision_retries_with_ssl_disable_on_ssl_upgrade_connection_lost(
    mock_connect: AsyncMock,
) -> None:
    """provision() retries once with ssl=disable on SSL upgrade connection loss."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)
    conn.close = AsyncMock()
    mock_connect.side_effect = [ConnectionError("unexpected connection_lost() call"), conn]

    db = Database(db_name="test_db")
    await db.provision()

    assert mock_connect.await_count == 2
    assert mock_connect.await_args_list[0].kwargs.get("ssl") is None
    assert mock_connect.await_args_list[1].kwargs["ssl"] == "disable"


@patch("butlers.db.asyncpg.create_pool", new_callable=AsyncMock)
async def test_connect_retries_with_ssl_disable_on_ssl_upgrade_connection_lost(
    mock_create_pool: AsyncMock,
) -> None:
    """connect() retries once with ssl=disable on SSL upgrade connection loss."""
    pool = AsyncMock()
    mock_create_pool.side_effect = [ConnectionError("unexpected connection_lost() call"), pool]

    db = Database(db_name="test_db")
    out = await db.connect()

    assert out is pool
    assert mock_create_pool.await_count == 2
    assert mock_create_pool.await_args_list[0].kwargs.get("ssl") is None
    assert mock_create_pool.await_args_list[1].kwargs["ssl"] == "disable"
