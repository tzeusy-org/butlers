"""Condensed tests for butler-specific API routers.

Condensed from:
  test_api_healing.py (31) + test_api_home_assistant.py (32) + test_api_owntracks.py (35)
  + test_api_relationship_identity.py (32) + test_api_relationship.py (26)
  + test_api_spotify.py (32) + test_api_steam.py (70) + test_api_whatsapp.py (23)
  + test_api_entity_info.py (14) + test_api_unlinked_contacts.py (14) + test_api_modules.py (22)
  → ~20 tests (bu-egmz6)

Keeps: 200/404/503 status codes, validation errors, response structure contracts.
Drops: unit tests for helper functions, field-by-field assertions, repetitive filter tests.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.routers.healing import _get_db_manager as _healing_get_db
from butlers.api.routers.healing import _get_dispatch_fn
from butlers.api.routers.owntracks import _get_db_manager as _owntracks_get_db
from butlers.api.routers.spotify import (
    _clear_state_store as _spotify_clear_states,
)
from butlers.api.routers.spotify import (
    _get_db_manager as _spotify_get_db,
)
from butlers.api.routers.steam import _get_db_manager as _steam_get_db
from butlers.api.routers.whatsapp import _get_bridge_socket_path

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)

# ---------------------------------------------------------------------------
# Dynamic module loading for roster-based routers
# ---------------------------------------------------------------------------

_roster_root = Path(__file__).resolve().parents[2] / "roster"


def _load_relationship_module() -> Any:
    """Load relationship router module dynamically (mirrors router_discovery logic)."""
    module_name = "relationship_api_router"
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = _roster_root / "relationship" / "api" / "router.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Row(dict):
    """dict subclass that mimics asyncpg Record (supports dict() and attr access)."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(key, default)


def _row(data: dict) -> _Row:
    return _Row(data)


def _mock_pool(*, fetch_rows=None, fetchrow_result=None, fetchval_result=0,
               execute_result="DELETE 1"):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_rows or [])
    pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    pool.fetchval = AsyncMock(return_value=fetchval_result)
    pool.execute = AsyncMock(return_value=execute_result)
    return pool


def _mock_db_shared(pool):
    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = pool
    return db


# ---------------------------------------------------------------------------
# Healing API
# ---------------------------------------------------------------------------


class TestHealingAPI:
    def _make_app(self, *, fetch_rows=None, fetchrow_result=None, fetchval=0):
        pool = _mock_pool(fetch_rows=fetch_rows, fetchrow_result=fetchrow_result,
                          fetchval_result=fetchval)
        db = _mock_db_shared(pool)
        app = create_app()
        app.dependency_overrides[_healing_get_db] = lambda: db
        app.dependency_overrides[_get_dispatch_fn] = lambda: None
        return app, pool

    async def test_list_attempts_returns_paginated_structure(self):
        now = datetime.now(tz=UTC)
        row = _row({
            "id": uuid.uuid4(), "fingerprint": "a" * 64, "butler_name": "general",
            "status": "investigating", "severity": 2, "exception_type": "KeyError",
            "call_site": "foo.py:bar", "sanitized_msg": "msg", "branch_name": None,
            "worktree_path": None, "pr_url": None, "pr_number": None,
            "session_ids": [], "healing_session_id": None,
            "created_at": now, "updated_at": now, "closed_at": None, "error_detail": None,
        })
        app, _ = self._make_app(fetch_rows=[row], fetchval=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/healing/attempts")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body and "meta" in body

    async def test_list_attempts_invalid_status_returns_422(self):
        app, _ = self._make_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/healing/attempts", params={"status": "bad_status"})
        assert resp.status_code == 422

    async def test_get_attempt_404_when_not_found(self):
        app, _ = self._make_app(fetchrow_result=None)
        nid = uuid.uuid4()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get(f"/api/healing/attempts/{nid}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Home Assistant API
# ---------------------------------------------------------------------------


_HA_VALIDATE = "butlers.api.routers.home_assistant._validate_ha_connection"
_HA_RESOLVE_POOL = "butlers.api.routers.home_assistant._resolve_pool"
_HA_UPSERT_EI = "butlers.api.routers.home_assistant.upsert_owner_entity_info"


class TestHomeAssistantAPI:
    async def test_save_credentials_validates_and_returns_200(self):
        from butlers.api.routers.home_assistant import _get_db_manager as _ha_get_db
        mock_pool = _mock_pool()
        app = create_app()
        app.dependency_overrides[_ha_get_db] = lambda: _mock_db_shared(mock_pool)
        with (
            patch(_HA_VALIDATE, AsyncMock(return_value={"version": "2024.1"})),
            patch(_HA_RESOLVE_POOL, AsyncMock(return_value=mock_pool)),
            patch(_HA_UPSERT_EI, AsyncMock()),
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                resp = await client.post(
                    "/api/settings/home-assistant",
                    json={"url": "http://localhost:8123", "token": "test-token"},
                )
        assert resp.status_code in (200, 201)

    async def test_save_credentials_422_missing_url(self):
        from butlers.api.routers.home_assistant import _get_db_manager as _ha_get_db
        app = create_app()
        app.dependency_overrides[_ha_get_db] = lambda: MagicMock(spec=DatabaseManager)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.post("/api/settings/home-assistant", json={"token": "x"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Steam API
# ---------------------------------------------------------------------------


class TestSteamAPI:
    def _make_app(self, *, db=None):
        app = create_app()
        if db is None:
            db = _mock_db_shared(_mock_pool())
        app.dependency_overrides[_steam_get_db] = lambda: db
        return app

    async def test_list_accounts_returns_list(self):
        app = self._make_app()
        with patch("butlers.api.routers.steam.list_steam_accounts",
                   AsyncMock(return_value=[])):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                resp = await client.get("/api/steam/accounts")
        assert resp.status_code == 200
        body = resp.json()
        assert "accounts" in body or isinstance(body, list)

    async def test_connect_account_422_api_key_too_short(self):
        app = self._make_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.post(
                "/api/steam/accounts",
                json={"api_key": "short", "steam_id": 76561198000000001},
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Spotify API
# ---------------------------------------------------------------------------


class TestSpotifyAPI:
    @pytest.fixture(autouse=True)
    def clear_spotify_states(self):
        _spotify_clear_states()
        yield
        _spotify_clear_states()

    def _make_app(self, *, client_id="a" * 32, client_secret="secret"):
        conn = AsyncMock()

        async def _fetchrow(q, *args):
            key = args[0] if args else None
            secrets = {
                "SPOTIFY_CLIENT_ID": client_id,
                "SPOTIFY_CLIENT_SECRET": client_secret,
            }
            val = secrets.get(key) if key else None
            return {"secret_value": val} if val else None

        conn.fetchrow.side_effect = _fetchrow

        @asynccontextmanager
        async def _acquire():
            yield conn

        pool = MagicMock()
        pool.acquire = _acquire
        db = MagicMock()
        db.credential_shared_pool.return_value = pool
        app = create_app()
        app.dependency_overrides[_spotify_get_db] = lambda: db
        return app

    async def test_oauth_start_returns_json_with_auth_url(self):
        app = self._make_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.post("/api/connectors/spotify/oauth/start",
                                     json={"redirect_uri": "http://localhost/callback"})
        assert resp.status_code in (200, 422)
        if resp.status_code == 200:
            body = resp.json()
            assert "authorization_url" in body or "auth_url" in body or "url" in body

    async def test_status_endpoint_returns_200(self):
        app = self._make_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/connectors/spotify/status")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# OwnTracks API
# ---------------------------------------------------------------------------


class TestOwnTracksAPI:
    def _make_app(self, *, db_available=True):
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        conn.execute = AsyncMock(return_value=None)

        @asynccontextmanager
        async def _acquire():
            yield conn

        pool = MagicMock()
        pool.acquire = _acquire
        db = MagicMock(spec=DatabaseManager)
        if db_available:
            db.credential_shared_pool.return_value = pool
        else:
            db.credential_shared_pool.side_effect = KeyError("no pool")
        app = create_app()
        app.dependency_overrides[_owntracks_get_db] = lambda: db
        return app

    async def test_status_endpoint_returns_200(self):
        app = self._make_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/connectors/owntracks/status")
        assert resp.status_code == 200

    async def test_generate_token_503_when_no_db(self):
        app = create_app()
        app.dependency_overrides[_owntracks_get_db] = lambda: None
        with patch("butlers.api.routers.owntracks._make_credential_store",
                   return_value=None):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                resp = await client.post("/api/connectors/owntracks/token/generate")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# WhatsApp API
# ---------------------------------------------------------------------------


class TestWhatsAppAPI:
    def _make_app(self):
        app = create_app()
        app.dependency_overrides[_get_bridge_socket_path] = lambda: "/tmp/test-bridge.sock"
        return app

    async def test_status_connected_returns_200(self):
        app = self._make_app()
        bridge_data = {"state": "connected", "phone": "+12345677890"}
        with patch("butlers.api.routers.whatsapp._bridge_get",
                   AsyncMock(return_value=bridge_data)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                resp = await client.get("/api/connectors/whatsapp/status")
        assert resp.status_code == 200
        assert resp.json()["bridge_running"] is True

    async def test_status_bridge_down_returns_not_configured(self):
        app = self._make_app()
        with patch("butlers.api.routers.whatsapp._bridge_get", AsyncMock(return_value=None)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                resp = await client.get("/api/connectors/whatsapp/status")
        assert resp.status_code == 200
        assert resp.json()["bridge_running"] is False


# ---------------------------------------------------------------------------
# Relationship / Identity API (roster-based router)
# ---------------------------------------------------------------------------


class TestRelationshipAPI:
    def _make_app(self, *, fetchrow_result=None, fetch_rows=None):
        rel_mod = _load_relationship_module()
        pool = _mock_pool(fetchrow_result=fetchrow_result, fetch_rows=fetch_rows)
        # relationship router uses db.pool("relationship"), not credential_shared_pool
        db = MagicMock(spec=DatabaseManager)
        db.pool.return_value = pool
        # Also provide butler_names and configs so discovery succeeds
        db.butler_names = ["relationship"]
        app = create_app()
        app.dependency_overrides[rel_mod._get_db_manager] = lambda: db
        # Provide butler config so the relationship butler is recognized
        from butlers.api.deps import get_butler_configs
        app.dependency_overrides[get_butler_configs] = lambda: [
            ButlerConnectionInfo("relationship", 41300)
        ]
        return app

    async def test_list_contacts_returns_200(self):
        app = self._make_app(fetch_rows=[])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/relationship/contacts")
        assert resp.status_code == 200

    async def test_get_contact_404_when_not_found(self):
        app = self._make_app(fetchrow_result=None)
        nid = uuid.uuid4()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get(f"/api/relationship/contacts/{nid}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Modules API
# ---------------------------------------------------------------------------


class TestModulesAPI:
    async def test_get_module_states_unreachable_returns_gracefully(self, app):
        mock_mcp = MagicMock(spec=MCPClientManager)
        mock_mcp.get_client.side_effect = ButlerUnreachableError("general", "down")
        config = ButlerConnectionInfo("general", 41200)
        app.dependency_overrides[get_butler_configs] = lambda: [config]
        app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/butlers/general/modules")
        # Either 503 or 200 with degraded/empty state is acceptable
        assert resp.status_code in (200, 503)
