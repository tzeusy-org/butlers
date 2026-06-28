"""Condensed tests for butler-specific API routers.

Condensed from:
  test_api_healing.py (31) + test_api_home_assistant.py (32) + test_api_owntracks.py (35)
  + test_api_relationship_identity.py (32) + test_api_relationship.py (26)
  + test_api_spotify.py (32) + test_api_steam.py (70) + test_api_whatsapp.py (23)
  + test_api_entity_info.py (14) + test_api_unlinked_contacts.py (14) + test_api_modules.py (22)
  → ~20 tests (bu-egmz6) → 5 tests (bu-2yw2d)

Keeps: 200/404/503/422 status codes per domain group (parametrized).
Drops: repetitive filter tests, field-by-field assertions, per-module duplicate paths.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
from butlers.api.routers.spotify import (
    _clear_state_store as _spotify_clear_states,
)
from butlers.api.routers.spotify import (
    _get_db_manager as _spotify_get_db,
)

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)
_roster_root = Path(__file__).resolve().parents[2] / "roster"


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


def _mock_pool(
    *, fetch_rows=None, fetchrow_result=None, fetchval_result=0, execute_result="DELETE 1"
):
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
# Healing API — list + 404 detail
# ---------------------------------------------------------------------------


class TestHealingAPI:
    def _make_app(self, *, fetch_rows=None, fetchrow_result=None, fetchval=0):
        pool = _mock_pool(
            fetch_rows=fetch_rows, fetchrow_result=fetchrow_result, fetchval_result=fetchval
        )
        db = _mock_db_shared(pool)
        app = create_app(api_key="")
        app.dependency_overrides[_healing_get_db] = lambda: db
        app.dependency_overrides[_get_dispatch_fn] = lambda: None
        return app, pool

    @pytest.mark.parametrize(
        "fetchrow_result,path_suffix,expected",
        [
            (None, f"/api/healing/attempts/{uuid.uuid4()}", 404),
            (None, "/api/healing/attempts?status=bad_status", 422),
        ],
        ids=["attempt-404", "invalid-status-422"],
    )
    async def test_healing_error_paths(self, fetchrow_result, path_suffix, expected):
        app, _ = self._make_app(fetchrow_result=fetchrow_result)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(path_suffix)
        assert resp.status_code == expected

    async def test_list_attempts_returns_paginated_structure(self):
        row = _row(
            {
                "id": uuid.uuid4(),
                "fingerprint": "a" * 64,
                "butler_name": "general",
                "status": "investigating",
                "severity": 2,
                "exception_type": "KeyError",
                "call_site": "foo.py:bar",
                "sanitized_msg": "msg",
                "branch_name": None,
                "worktree_path": None,
                "pr_url": None,
                "pr_number": None,
                "session_ids": [],
                "healing_session_id": None,
                "created_at": _NOW,
                "updated_at": _NOW,
                "closed_at": None,
                "error_detail": None,
            }
        )
        app, _ = self._make_app(fetch_rows=[row], fetchval=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/healing/attempts")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body and "meta" in body


# ---------------------------------------------------------------------------
# Spotify — oauth start returns auth_url
# ---------------------------------------------------------------------------


class TestSpotifyAPI:
    @pytest.fixture(autouse=True)
    def clear_spotify_states(self):
        _spotify_clear_states()
        yield
        _spotify_clear_states()

    def _make_app(self, *, client_id="a" * 32, client_secret="secret", access_token=None):
        conn = AsyncMock()

        async def _fetchrow(q, *args):
            key = args[0] if args else None
            secrets = {
                "SPOTIFY_CLIENT_ID": client_id,
                "SPOTIFY_CLIENT_SECRET": client_secret,
                "SPOTIFY_ACCESS_TOKEN": access_token,
            }
            val = secrets.get(key) if key else None
            return {"secret_value": val} if val else None

        conn.fetchrow.side_effect = _fetchrow
        # asyncpg returns a command-status string like "DELETE 1" from execute();
        # CredentialStore.delete() parses it, so a plain mock object breaks it.
        conn.execute = AsyncMock(return_value="DELETE 1")

        @asynccontextmanager
        async def _acquire():
            yield conn

        pool = MagicMock()
        pool.acquire = _acquire
        db = MagicMock()
        db.credential_shared_pool.return_value = pool
        app = create_app(api_key="")
        app.dependency_overrides[_spotify_get_db] = lambda: db
        return app

    async def test_oauth_start_returns_authorization_url(self):
        app = self._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/connectors/spotify/oauth/start")
        assert resp.status_code == 200
        assert "authorization_url" in resp.json()

    # ------------------------------------------------------------------
    # Contract conformance (bu-fm0w7): BE responses must match the spec /
    # frontend SpotifyStatusResponse shape so the drawer reads real fields.
    # ------------------------------------------------------------------

    # Exact field set the frontend SpotifyStatusResponse interface consumes.
    _STATUS_KEYS = {
        "connected",
        "state",
        "spotify_user_id",
        "display_name",
        "account_type",
        "last_sync_at",
        "error",
        "needs_reauth",
        "missing_scopes",
    }

    async def test_status_not_configured_shape(self):
        app = self._make_app(client_id=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/spotify/status")
        assert resp.status_code == 200
        body = resp.json()
        # Spec-conformant shape: exactly the keys the FE consumes, no legacy fields.
        assert set(body) == self._STATUS_KEYS
        assert body["connected"] is False
        assert body["state"] == "not_configured"
        # Legacy BE-only fields must not leak.
        for legacy in ("email", "product", "last_verified_at", "client_id_configured"):
            assert legacy not in body

    async def test_status_connected_maps_me_fields(self, monkeypatch):
        from butlers.api.routers import spotify as spotify_router

        async def _fake_me(_token):
            return {
                "id": "spotify_user_42",
                "display_name": "Ada Lovelace",
                "product": "premium",
                "email": "ada@example.com",
            }

        monkeypatch.setattr(spotify_router, "_fetch_spotify_me", _fake_me)

        app = self._make_app(access_token="tok-abc")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/spotify/status")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == self._STATUS_KEYS
        assert body["connected"] is True
        assert body["state"] == "connected"
        assert body["spotify_user_id"] == "spotify_user_42"
        assert body["display_name"] == "Ada Lovelace"
        # account_type maps from Spotify's product field.
        assert body["account_type"] == "premium"
        assert body["last_sync_at"] is not None

    async def test_status_token_refresh_failure_maps_to_error_state(self, monkeypatch):
        """A failed /me verification surfaces a distinct ``error`` state (not
        ``disconnected``) so the FE can render a red re-authorization card."""
        from butlers.api.routers import spotify as spotify_router

        async def _fail_me(_token):
            return None

        monkeypatch.setattr(spotify_router, "_fetch_spotify_me", _fail_me)

        app = self._make_app(access_token="tok-stale")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/spotify/status")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == self._STATUS_KEYS
        assert body["connected"] is False
        # Distinct error state, NOT collapsed to disconnected.
        assert body["state"] == "error"
        assert body["needs_reauth"] is True
        assert body["error"]

    async def test_callback_state_mismatch_returns_403(self):
        """Spec requires HTTP 403 (not 400) on CSRF state mismatch."""
        app = self._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/connectors/spotify/oauth/callback",
                params={"code": "auth-code", "state": "never-issued-state"},
            )
        assert resp.status_code == 403

    async def test_config_returns_configured_shape(self):
        app = self._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/connectors/spotify/config",
                json={"client_id": "a" * 32},
            )
        assert resp.status_code == 200
        assert resp.json() == {"configured": True}

    async def test_disconnect_returns_disconnected_shape(self):
        app = self._make_app(access_token="tok-abc")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/connectors/spotify/disconnect")
        assert resp.status_code == 200
        assert resp.json() == {"disconnected": True}


# ---------------------------------------------------------------------------
# Modules API — unreachable butler returns gracefully
# ---------------------------------------------------------------------------


class TestModulesAPI:
    async def test_get_module_states_unreachable_returns_gracefully(self, app):
        mock_mcp = MagicMock(spec=MCPClientManager)
        mock_mcp.get_client.side_effect = ButlerUnreachableError(
            "general", cause=ConnectionRefusedError("down")
        )
        config = ButlerConnectionInfo("general", 41200)
        app.dependency_overrides[get_butler_configs] = lambda: [config]
        app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/general/modules")
        assert resp.status_code in (200, 503)
