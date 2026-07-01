"""Tests for Steam connector config store integration.

Covers:
- save_connector_settings — upserts into switchboard.connector_registry.settings
- SteamConnector._load_config_from_store — reads from store, applies to connector
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch


def _make_pool(conn_mock: Any) -> MagicMock:
    """Build a pool mock whose acquire() returns an async context manager."""
    pool = MagicMock()
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool


# ---------------------------------------------------------------------------
# save_connector_settings unit tests
# ---------------------------------------------------------------------------


async def test_save_connector_settings_returns_merged_dict() -> None:
    """save_connector_settings returns the merged settings dict on success."""
    from butlers.connectors.cursor_store import save_connector_settings

    merged = {"account_rescan_s": 120, "heartbeat_interval_s": 30}

    conn_mock = AsyncMock()
    conn_mock.fetchrow = AsyncMock(return_value={"settings": json.dumps(merged)})
    pool = _make_pool(conn_mock)

    result = await save_connector_settings(pool, "steam", "steam:config", {"account_rescan_s": 120})
    assert result["account_rescan_s"] == 120
    assert result["heartbeat_interval_s"] == 30


async def test_save_connector_settings_returns_empty_on_none_row() -> None:
    """save_connector_settings returns empty dict when the row returns None settings."""
    from butlers.connectors.cursor_store import save_connector_settings

    conn_mock = AsyncMock()
    conn_mock.fetchrow = AsyncMock(return_value={"settings": None})
    pool = _make_pool(conn_mock)

    result = await save_connector_settings(pool, "steam", "steam:config", {"foo": 1})
    assert result == {}


async def test_save_connector_settings_executes_upsert_sql() -> None:
    """save_connector_settings executes the upsert SQL with correct params."""
    from butlers.connectors.cursor_store import save_connector_settings

    settings_in = {"account_rescan_s": 60}
    settings_out = json.dumps(settings_in)

    conn_mock = AsyncMock()
    conn_mock.fetchrow = AsyncMock(return_value={"settings": settings_out})
    pool = _make_pool(conn_mock)

    await save_connector_settings(pool, "steam", "steam:config", settings_in)

    conn_mock.fetchrow.assert_called_once()
    call_args = conn_mock.fetchrow.call_args[0]
    # First param is SQL; second/third are connector_type/endpoint_identity
    assert call_args[1] == "steam"
    assert call_args[2] == "steam:config"
    # Fourth param is the settings JSON string
    parsed = json.loads(call_args[3])
    assert parsed == settings_in


async def test_save_connector_settings_handles_dict_settings() -> None:
    """save_connector_settings accepts a dict JSONB value (not just str)."""
    from butlers.connectors.cursor_store import save_connector_settings

    merged_dict = {"max_tracked_games": 5}

    conn_mock = AsyncMock()
    # Return dict (asyncpg may decode JSONB to dict directly)
    conn_mock.fetchrow = AsyncMock(return_value={"settings": merged_dict})
    pool = _make_pool(conn_mock)

    result = await save_connector_settings(pool, "steam", "steam:config", merged_dict)
    assert result["max_tracked_games"] == 5


# ---------------------------------------------------------------------------
# SteamConnector._load_config_from_store unit tests
# ---------------------------------------------------------------------------


def _make_steam_connector() -> Any:
    """Return a minimal SteamConnector instance with mocked deps."""
    from butlers.connectors.steam import SteamConnector

    connector = SteamConnector.__new__(SteamConnector)
    connector._db_pool = AsyncMock()
    connector._account_rescan_s = 300
    connector._heartbeat_interval_s = 60
    connector._max_tracked_games = 10
    connector._effective_poll_intervals = {"recently_played": 300, "online_status": 300}
    return connector


async def test_load_config_from_store_applies_account_rescan_s() -> None:
    """_load_config_from_store updates account_rescan_s when present in settings."""
    connector = _make_steam_connector()
    settings = {"account_rescan_s": 120, "heartbeat_interval_s": 30, "max_tracked_games": 5}

    with patch(
        "butlers.connectors.cursor_store.load_connector_settings",
        new=AsyncMock(return_value=settings),
    ):
        await connector._load_config_from_store()

    assert connector._account_rescan_s == 120
    assert connector._heartbeat_interval_s == 30
    assert connector._max_tracked_games == 5


async def test_load_config_from_store_updates_poll_intervals() -> None:
    """_load_config_from_store updates effective_poll_intervals from settings."""
    connector = _make_steam_connector()
    settings = {
        "poll_intervals": {
            "recently_played": 600,
            "online_status": 120,
        }
    }

    with patch(
        "butlers.connectors.cursor_store.load_connector_settings",
        new=AsyncMock(return_value=settings),
    ):
        await connector._load_config_from_store()

    assert connector._effective_poll_intervals["recently_played"] == 600
    assert connector._effective_poll_intervals["online_status"] == 120


async def test_load_config_from_store_is_noop_on_empty_settings() -> None:
    """_load_config_from_store does not modify defaults when settings is None."""
    connector = _make_steam_connector()

    with patch(
        "butlers.connectors.cursor_store.load_connector_settings",
        new=AsyncMock(return_value=None),
    ):
        await connector._load_config_from_store()

    assert connector._account_rescan_s == 300
    assert connector._heartbeat_interval_s == 60
    assert connector._max_tracked_games == 10


async def test_load_config_from_store_ignores_invalid_values() -> None:
    """_load_config_from_store skips values that are not positive integers."""
    connector = _make_steam_connector()
    settings = {
        "account_rescan_s": -1,  # invalid
        "heartbeat_interval_s": "not-an-int",  # invalid
        "max_tracked_games": 0,  # invalid (must be > 0)
    }

    with patch(
        "butlers.connectors.cursor_store.load_connector_settings",
        new=AsyncMock(return_value=settings),
    ):
        await connector._load_config_from_store()

    # Defaults unchanged because all values were invalid
    assert connector._account_rescan_s == 300
    assert connector._heartbeat_interval_s == 60
    assert connector._max_tracked_games == 10


async def test_load_config_from_store_is_noop_on_exception() -> None:
    """_load_config_from_store does not raise when the DB call fails."""
    connector = _make_steam_connector()

    with patch(
        "butlers.connectors.cursor_store.load_connector_settings",
        new=AsyncMock(side_effect=Exception("DB unavailable")),
    ):
        # Should not raise
        await connector._load_config_from_store()

    assert connector._account_rescan_s == 300  # unchanged
