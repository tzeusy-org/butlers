"""Tests for the multi-database connection manager."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from butlers.api.db import DatabaseManager


@pytest.fixture
def mgr() -> DatabaseManager:
    """Return a DatabaseManager with default settings."""
    return DatabaseManager(host="localhost", port=5432, user="pg", password="secret")


def _make_mock_pool(name: str = "pool") -> AsyncMock:
    """Create a mock asyncpg pool."""
    pool = AsyncMock()
    pool.close = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    return pool


@patch("butlers.api.db.asyncpg.create_pool", new_callable=AsyncMock)
async def test_add_butler_creates_pool(mock_create: AsyncMock, mgr: DatabaseManager) -> None:
    """Verify pool is created and accessible via pool()."""
    mock_create.return_value = _make_mock_pool()
    await mgr.add_butler("switchboard")
    assert mgr.pool("switchboard") is mock_create.return_value


@patch("butlers.api.db.asyncpg.create_pool", new_callable=AsyncMock)
async def test_add_butler_default_db_name(mock_create: AsyncMock, mgr: DatabaseManager) -> None:
    """db_name defaults to butler_name when not provided."""
    mock_create.return_value = _make_mock_pool()
    await mgr.add_butler("atlas")
    mock_create.assert_called_once_with(
        host="localhost",
        port=5432,
        user="pg",
        password="secret",
        database="atlas",
        min_size=1,
        max_size=5,
    )


@patch("butlers.api.db.asyncpg.create_pool", new_callable=AsyncMock)
async def test_add_butler_custom_db_name(mock_create: AsyncMock, mgr: DatabaseManager) -> None:
    """Explicit db_name is used instead of butler_name."""
    mock_create.return_value = _make_mock_pool()
    await mgr.add_butler("atlas", db_name="atlas_prod")
    mock_create.assert_called_once_with(
        host="localhost",
        port=5432,
        user="pg",
        password="secret",
        database="atlas_prod",
        min_size=1,
        max_size=5,
    )


@patch("butlers.api.db.asyncpg.create_pool", new_callable=AsyncMock)
async def test_add_butler_forwards_ssl_mode(mock_create: AsyncMock) -> None:
    """Configured SSL mode is forwarded to asyncpg.create_pool."""
    mock_create.return_value = _make_mock_pool()
    mgr = DatabaseManager(
        host="localhost",
        port=5432,
        user="pg",
        password="secret",
        ssl="disable",
    )

    await mgr.add_butler("atlas")

    mock_create.assert_called_once_with(
        host="localhost",
        port=5432,
        user="pg",
        password="secret",
        database="atlas",
        min_size=1,
        max_size=5,
        ssl="disable",
    )


@patch("butlers.api.db.asyncpg.create_pool", new_callable=AsyncMock)
async def test_add_butler_duplicate_skipped(
    mock_create: AsyncMock, mgr: DatabaseManager, caplog: pytest.LogCaptureFixture
) -> None:
    """Adding the same butler twice is idempotent and logs a warning."""
    mock_create.return_value = _make_mock_pool()
    await mgr.add_butler("switchboard")
    with caplog.at_level(logging.WARNING, logger="butlers.api.db"):
        await mgr.add_butler("switchboard")
    assert mock_create.call_count == 1
    assert "already has a pool" in caplog.text


async def test_pool_raises_on_unknown_butler(mgr: DatabaseManager) -> None:
    """KeyError raised for unregistered butler."""
    with pytest.raises(KeyError, match="No pool for butler: unknown"):
        mgr.pool("unknown")


@patch("butlers.api.db.asyncpg.create_pool", new_callable=AsyncMock)
async def test_butler_names_returns_registered(
    mock_create: AsyncMock, mgr: DatabaseManager
) -> None:
    """butler_names property returns correct names in insertion order."""
    mock_create.return_value = _make_mock_pool()
    await mgr.add_butler("alpha")
    await mgr.add_butler("beta")
    assert mgr.butler_names == ["alpha", "beta"]


@patch("butlers.api.db.asyncpg.create_pool", new_callable=AsyncMock)
async def test_fan_out_queries_all_butlers(mock_create: AsyncMock, mgr: DatabaseManager) -> None:
    """fan_out runs query on all pools concurrently."""
    pool_a = _make_mock_pool()
    pool_a.fetch.return_value = [{"count": 10}]
    pool_b = _make_mock_pool()
    pool_b.fetch.return_value = [{"count": 20}]

    mock_create.side_effect = [pool_a, pool_b]
    await mgr.add_butler("alpha")
    await mgr.add_butler("beta")

    results = await mgr.fan_out("SELECT count(*) FROM sessions")

    pool_a.fetch.assert_called_once_with("SELECT count(*) FROM sessions")
    pool_b.fetch.assert_called_once_with("SELECT count(*) FROM sessions")
    assert results == {"alpha": [{"count": 10}], "beta": [{"count": 20}]}


@patch("butlers.api.db.asyncpg.create_pool", new_callable=AsyncMock)
async def test_fan_out_subset(mock_create: AsyncMock, mgr: DatabaseManager) -> None:
    """butler_names filter restricts which pools are queried."""
    pool_a = _make_mock_pool()
    pool_a.fetch.return_value = [{"count": 10}]
    pool_b = _make_mock_pool()
    pool_b.fetch.return_value = [{"count": 20}]

    mock_create.side_effect = [pool_a, pool_b]
    await mgr.add_butler("alpha")
    await mgr.add_butler("beta")

    results = await mgr.fan_out("SELECT 1", butler_names=["beta"])

    pool_a.fetch.assert_not_called()
    pool_b.fetch.assert_called_once_with("SELECT 1")
    assert "alpha" not in results
    assert results["beta"] == [{"count": 20}]


@patch("butlers.api.db.asyncpg.create_pool", new_callable=AsyncMock)
async def test_fan_out_handles_query_failure(
    mock_create: AsyncMock, mgr: DatabaseManager, caplog: pytest.LogCaptureFixture
) -> None:
    """Failed query returns empty list for that butler, others unaffected."""
    pool_ok = _make_mock_pool()
    pool_ok.fetch.return_value = [{"v": 1}]
    pool_bad = _make_mock_pool()
    pool_bad.fetch.side_effect = RuntimeError("connection lost")

    mock_create.side_effect = [pool_ok, pool_bad]
    await mgr.add_butler("good")
    await mgr.add_butler("bad")

    with caplog.at_level(logging.WARNING, logger="butlers.api.db"):
        results = await mgr.fan_out("SELECT 1")

    assert results["good"] == [{"v": 1}]
    assert results["bad"] == []
    assert "fan_out query failed for butler bad" in caplog.text


@patch("butlers.api.db.asyncpg.create_pool", new_callable=AsyncMock)
async def test_close_closes_all_pools(mock_create: AsyncMock, mgr: DatabaseManager) -> None:
    """All pools closed and internal dict cleared."""
    pool_a = _make_mock_pool()
    pool_b = _make_mock_pool()

    mock_create.side_effect = [pool_a, pool_b]
    await mgr.add_butler("alpha")
    await mgr.add_butler("beta")

    await mgr.close()

    pool_a.close.assert_called_once()
    pool_b.close.assert_called_once()
    assert mgr.butler_names == []
