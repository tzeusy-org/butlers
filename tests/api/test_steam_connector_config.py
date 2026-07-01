"""Tests for Steam connector configuration endpoints.

Covers:
- GET /api/steam/connector/config — returns defaults when no settings stored
- PATCH /api/steam/connector/config — merges new settings, returns effective values
- GET /api/steam/accounts/{id}/config — reads per-account overrides from metadata
- PATCH /api/steam/accounts/{id}/config — writes per-account overrides to metadata

All DB calls are mocked so no live database is required.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import butlers.api.routers.steam as steam_router
from butlers.api.models.steam import (
    SteamAccountConfigOverrides,
    SteamConnectorConfigUpdateRequest,
    SteamPollIntervals,
)

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

_ACCOUNT_ID = uuid.uuid4()
_STEAM_ID = 76561198000000001

# Defaults from the router module
_DEFAULTS = {
    "account_rescan_s": steam_router._STEAM_DEFAULT_ACCOUNT_RESCAN_S,
    "heartbeat_interval_s": steam_router._STEAM_DEFAULT_HEARTBEAT_INTERVAL_S,
    "max_tracked_games": steam_router._STEAM_DEFAULT_MAX_TRACKED_GAMES,
}


def _make_mock_account() -> Any:
    """Return a minimal SteamAccount-like mock."""
    acct = MagicMock()
    acct.id = _ACCOUNT_ID
    acct.steam_id = _STEAM_ID
    acct.display_name = "TestUser"
    acct.is_primary = True
    acct.status = "active"
    acct.connected_at = None
    acct.last_poll_at = None
    return acct


def _make_pool(conn_mock: Any) -> MagicMock:
    """Build a pool mock whose acquire() returns an async context manager."""
    pool = MagicMock()
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool


def _make_db_manager(*, switchboard_pool: Any = None, shared_pool: Any = None) -> Any:
    """Return a minimal DatabaseManager-like mock."""
    mgr = MagicMock()
    if switchboard_pool is not None:
        mgr.pool = MagicMock(return_value=switchboard_pool)
    else:
        mgr.pool = MagicMock(side_effect=KeyError("switchboard"))
    if shared_pool is not None:
        mgr.credential_shared_pool = MagicMock(return_value=shared_pool)
    else:
        mgr.credential_shared_pool = MagicMock(side_effect=Exception("no shared pool"))
    return mgr


# ---------------------------------------------------------------------------
# GET /api/steam/connector/config — defaults when no settings
# ---------------------------------------------------------------------------


async def test_get_connector_config_returns_defaults_when_no_settings() -> None:
    """When no dashboard settings are stored, effective values equal the compiled defaults."""
    conn_mock = AsyncMock()
    conn_mock.fetchrow = AsyncMock(return_value={"settings": None})
    pool = _make_pool(conn_mock)

    with patch(
        "butlers.connectors.cursor_store.load_connector_settings",
        new=AsyncMock(return_value=None),
    ):
        resp = await steam_router.get_steam_connector_config(
            db_manager=_make_db_manager(switchboard_pool=pool),
        )

    assert resp.account_rescan_s == _DEFAULTS["account_rescan_s"]
    assert resp.heartbeat_interval_s == _DEFAULTS["heartbeat_interval_s"]
    assert resp.max_tracked_games == _DEFAULTS["max_tracked_games"]
    assert resp.source == "defaults"
    assert resp.poll_intervals.recently_played is None
    assert resp.poll_intervals.online_status is None


async def test_get_connector_config_returns_stored_values() -> None:
    """When dashboard settings exist, effective values reflect those settings."""
    stored_settings = {
        "account_rescan_s": 120,
        "heartbeat_interval_s": 30,
        "max_tracked_games": 5,
        "poll_intervals": {"recently_played": 600, "online_status": 600},
    }
    conn_mock = AsyncMock()
    pool = _make_pool(conn_mock)

    with patch(
        "butlers.connectors.cursor_store.load_connector_settings",
        new=AsyncMock(return_value=stored_settings),
    ):
        resp = await steam_router.get_steam_connector_config(
            db_manager=_make_db_manager(switchboard_pool=pool),
        )

    assert resp.account_rescan_s == 120
    assert resp.heartbeat_interval_s == 30
    assert resp.max_tracked_games == 5
    assert resp.source == "dashboard"
    assert resp.poll_intervals.recently_played == 600
    assert resp.poll_intervals.online_status == 600


async def test_get_connector_config_graceful_degradation_when_no_switchboard() -> None:
    """When switchboard pool is unavailable, returns defaults without raising."""
    mgr = _make_db_manager(switchboard_pool=None)

    resp = await steam_router.get_steam_connector_config(db_manager=mgr)

    assert resp.account_rescan_s == _DEFAULTS["account_rescan_s"]
    assert resp.source == "defaults"


# ---------------------------------------------------------------------------
# PATCH /api/steam/connector/config — merges settings
# ---------------------------------------------------------------------------


async def test_patch_connector_config_persists_and_returns_merged() -> None:
    """PATCH should merge supplied fields and return the updated effective config."""
    merged_result = {
        "account_rescan_s": 120,
        "heartbeat_interval_s": 60,
        "max_tracked_games": 10,
    }
    conn_mock = AsyncMock()
    pool = _make_pool(conn_mock)

    with (
        patch(
            "butlers.connectors.cursor_store.load_connector_settings",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.connectors.cursor_store.save_connector_settings",
            new=AsyncMock(return_value=merged_result),
        ),
    ):
        body = SteamConnectorConfigUpdateRequest(account_rescan_s=120)
        resp = await steam_router.update_steam_connector_config(
            body=body,
            db_manager=_make_db_manager(switchboard_pool=pool),
        )

    assert resp.account_rescan_s == 120
    assert resp.heartbeat_interval_s == 60  # default carried through


async def test_patch_connector_config_poll_intervals() -> None:
    """PATCH with poll_intervals should persist them in the settings dict."""
    saved_settings: dict = {}

    async def _fake_save(pool: Any, ct: str, ei: str, settings: dict) -> dict:
        saved_settings.update(settings)
        return settings

    async def _fake_load(pool: Any, ct: str, ei: str) -> dict | None:
        return None

    conn_mock = AsyncMock()
    pool = _make_pool(conn_mock)

    with (
        patch("butlers.connectors.cursor_store.load_connector_settings", new=_fake_load),
        patch("butlers.connectors.cursor_store.save_connector_settings", new=_fake_save),
    ):
        body = SteamConnectorConfigUpdateRequest(
            poll_intervals=SteamPollIntervals(recently_played=600, online_status=120),
        )
        resp = await steam_router.update_steam_connector_config(
            body=body,
            db_manager=_make_db_manager(switchboard_pool=pool),
        )

    assert "poll_intervals" in saved_settings
    assert saved_settings["poll_intervals"]["recently_played"] == 600
    assert saved_settings["poll_intervals"]["online_status"] == 120
    assert resp.poll_intervals.recently_played == 600


async def test_patch_connector_config_raises_503_when_no_switchboard() -> None:
    """PATCH raises HTTP 503 when the switchboard pool is unavailable."""
    from fastapi import HTTPException

    mgr = _make_db_manager(switchboard_pool=None)
    body = SteamConnectorConfigUpdateRequest(account_rescan_s=120)

    with pytest.raises(HTTPException) as exc_info:
        await steam_router.update_steam_connector_config(body=body, db_manager=mgr)

    assert exc_info.value.status_code == 503


async def test_patch_connector_config_noop_when_no_fields() -> None:
    """PATCH with no supplied fields returns current config without calling save."""
    stored = {"account_rescan_s": 100, "heartbeat_interval_s": 45, "max_tracked_games": 8}
    conn_mock = AsyncMock()
    pool = _make_pool(conn_mock)

    with (
        patch(
            "butlers.connectors.cursor_store.load_connector_settings",
            new=AsyncMock(return_value=stored),
        ),
        patch(
            "butlers.connectors.cursor_store.save_connector_settings",
            new=AsyncMock(side_effect=AssertionError("save should not be called")),
        ),
    ):
        body = SteamConnectorConfigUpdateRequest()  # all None
        resp = await steam_router.update_steam_connector_config(
            body=body,
            db_manager=_make_db_manager(switchboard_pool=pool),
        )

    assert resp.account_rescan_s == 100


# ---------------------------------------------------------------------------
# GET /api/steam/accounts/{id}/config — per-account overrides
# ---------------------------------------------------------------------------


async def test_get_account_config_returns_empty_when_no_overrides() -> None:
    """When metadata has no config keys, returns an empty overrides object."""
    conn_mock = AsyncMock()
    conn_mock.fetchrow = AsyncMock(return_value={"metadata": {}})
    pool = _make_pool(conn_mock)

    with patch.object(
        steam_router,
        "resolve_steam_account",
        new=AsyncMock(return_value=_make_mock_account()),
    ):
        resp = await steam_router.get_steam_account_config(
            account_id=_ACCOUNT_ID,
            db_manager=_make_db_manager(shared_pool=pool),
        )

    assert resp.account_id == _ACCOUNT_ID
    assert resp.steam_id == _STEAM_ID
    assert resp.overrides.poll_intervals is None
    assert resp.overrides.max_tracked_games is None


async def test_get_account_config_returns_stored_overrides() -> None:
    """When metadata has overrides, they are returned in the response."""
    metadata = {
        "poll_intervals": {"recently_played": 600, "online_status": 120},
        "max_tracked_games": 5,
    }
    conn_mock = AsyncMock()
    conn_mock.fetchrow = AsyncMock(return_value={"metadata": metadata})
    pool = _make_pool(conn_mock)

    with patch.object(
        steam_router,
        "resolve_steam_account",
        new=AsyncMock(return_value=_make_mock_account()),
    ):
        resp = await steam_router.get_steam_account_config(
            account_id=_ACCOUNT_ID,
            db_manager=_make_db_manager(shared_pool=pool),
        )

    assert resp.overrides.poll_intervals is not None
    assert resp.overrides.poll_intervals.recently_played == 600
    assert resp.overrides.poll_intervals.online_status == 120
    assert resp.overrides.max_tracked_games == 5


# ---------------------------------------------------------------------------
# PATCH /api/steam/accounts/{id}/config — per-account write
# ---------------------------------------------------------------------------


async def test_patch_account_config_writes_metadata() -> None:
    """PATCH should execute an UPDATE on steam_accounts.metadata."""
    conn_mock = AsyncMock()
    conn_mock.execute = AsyncMock()
    pool = _make_pool(conn_mock)

    with patch.object(
        steam_router,
        "resolve_steam_account",
        new=AsyncMock(return_value=_make_mock_account()),
    ):
        body = SteamAccountConfigOverrides(
            poll_intervals=SteamPollIntervals(recently_played=600),
            max_tracked_games=5,
        )
        resp = await steam_router.update_steam_account_config(
            account_id=_ACCOUNT_ID,
            body=body,
            db_manager=_make_db_manager(shared_pool=pool),
        )

    assert resp.success is True
    assert resp.account_id == _ACCOUNT_ID
    assert resp.steam_id == _STEAM_ID
    assert resp.overrides.poll_intervals is not None
    assert resp.overrides.poll_intervals.recently_played == 600
    assert resp.overrides.max_tracked_games == 5
    # Verify the DB update was called
    conn_mock.execute.assert_called_once()
    call_args = conn_mock.execute.call_args[0]
    assert "UPDATE public.steam_accounts" in call_args[0]


async def test_patch_account_config_returns_404_for_unknown_account() -> None:
    """PATCH raises HTTP 404 when account is not found."""
    from fastapi import HTTPException

    from butlers.steam_account_registry import SteamAccountNotFoundError

    conn_mock = AsyncMock()
    pool = _make_pool(conn_mock)

    with patch.object(
        steam_router,
        "resolve_steam_account",
        new=AsyncMock(side_effect=SteamAccountNotFoundError("not found")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await steam_router.update_steam_account_config(
                account_id=_ACCOUNT_ID,
                body=SteamAccountConfigOverrides(),
                db_manager=_make_db_manager(shared_pool=pool),
            )

    assert exc_info.value.status_code == 404
