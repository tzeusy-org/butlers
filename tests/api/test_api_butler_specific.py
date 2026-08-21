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
from urllib.parse import parse_qs, urlparse

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
from butlers.api.routers.spotify import _exchange_code_for_tokens, _TokenExchangeError
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

    def _make_app(
        self,
        *,
        client_id="a" * 32,
        client_secret="secret",
        access_token=None,
        expires_at="2999-01-01T00:00:00+00:00",
        observed_scopes=None,
    ):
        conn = AsyncMock()

        async def _fetchrow(q, *args):
            key = args[0] if args else None
            secrets = {
                "SPOTIFY_CLIENT_ID": client_id,
                "SPOTIFY_CLIENT_SECRET": client_secret,
                "spotify_oauth_access": access_token,
                "spotify_oauth_refresh": "owner-refresh" if access_token else None,
                "spotify_oauth_expires_at": expires_at if access_token else None,
            }
            val = secrets.get(key) if key else None
            if not val:
                return None
            return {"value": val} if "SELECT ei.value" in q else {"secret_value": val}

        conn.fetchrow.side_effect = _fetchrow
        # asyncpg returns a command-status string like "DELETE 1" from execute();
        # CredentialStore.delete() parses it, so a plain mock object breaks it.
        conn.execute = AsyncMock(return_value="DELETE 1")

        @asynccontextmanager
        async def _transaction():
            yield

        conn.transaction = MagicMock(side_effect=_transaction)

        @asynccontextmanager
        async def _acquire():
            yield conn

        pool = MagicMock()
        pool.acquire = _acquire
        pool.fetchrow = AsyncMock(
            return_value=None if observed_scopes is None else {"observed_scopes": observed_scopes}
        )
        db = MagicMock()
        db.credential_shared_pool.return_value = pool
        app = create_app(api_key="")
        app.dependency_overrides[_spotify_get_db] = lambda: db
        return app

    async def test_oauth_start_returns_response_state_in_authorization_url(self):
        app = self._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/connectors/spotify/oauth/start")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"authorization_url", "state"}
        assert parse_qs(urlparse(body["authorization_url"]).query)["state"] == [body["state"]]

    # ------------------------------------------------------------------
    # Contract conformance (bu-fm0w7): BE responses must match the spec /
    # frontend SpotifyStatusResponse shape so the drawer reads real fields.
    # ------------------------------------------------------------------

    # Exact field set the frontend SpotifyStatusResponse interface consumes.
    _STATUS_KEYS = {"connected", "state", "capability_categories"}

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
        assert body["state"] == "unconfigured"
        assert body["capability_categories"] == ["listening-history"]

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
        assert body["capability_categories"] == ["listening-history"]
        assert "spotify_user_id" not in body
        assert "display_name" not in body
        assert "account_type" not in body

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
        assert body["capability_categories"] == ["listening-history"]
        assert "error" not in body

    async def test_status_incomplete_stored_triplet_maps_to_error(self, monkeypatch):
        from butlers.api.routers import spotify as spotify_router

        async def _resolve(_pool, info_type):
            return "access-only" if info_type == "spotify_oauth_access" else None

        monkeypatch.setattr(spotify_router, "resolve_owner_entity_info", _resolve)
        app = self._make_app(access_token="access-only")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/spotify/status")

        assert resp.status_code == 200
        assert resp.json()["state"] == "error"

    async def test_status_access_missing_with_companion_rows_maps_to_error(self, monkeypatch):
        from butlers.api.routers import spotify as spotify_router

        async def _resolve(_pool, info_type):
            values = {
                "spotify_oauth_refresh": "orphan-refresh",
                "spotify_oauth_expires_at": "2999-01-01T00:00:00+00:00",
            }
            return values.get(info_type)

        monkeypatch.setattr(spotify_router, "resolve_owner_entity_info", _resolve)
        app = self._make_app(access_token=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/spotify/status")

        assert resp.status_code == 200
        assert resp.json()["state"] == "error"

    async def test_status_scope_mismatch_maps_to_needs_reauth_without_scope_material(
        self, monkeypatch
    ):
        from butlers.api.routers import spotify as spotify_router

        monkeypatch.setattr(
            spotify_router, "_fetch_spotify_me", AsyncMock(return_value={"id": "user-42"})
        )
        app = self._make_app(
            access_token="tok-under-scoped",
            observed_scopes=["user-read-playback-state"],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/spotify/status")

        assert resp.status_code == 200
        assert resp.json() == {
            "connected": False,
            "state": "needs_reauth",
            "capability_categories": ["listening-history"],
        }

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
        assert resp.json() == {"detail": "spotify_state_invalid"}

    async def test_callback_provider_error_returns_fixed_local_code(self):
        app = self._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/connectors/spotify/oauth/callback",
                params={"error": "provider-controlled-detail"},
            )

        assert resp.status_code == 400
        assert resp.json() == {"detail": "spotify_authorization_failed"}

    async def test_callback_provider_error_is_projected_as_fixed_local_state(self, monkeypatch):
        monkeypatch.setenv("OAUTH_DASHBOARD_URL", "http://dashboard.test")
        app = self._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/connectors/spotify/oauth/callback",
                params={"error": "provider-controlled-detail"},
                follow_redirects=False,
            )

        assert resp.status_code == 302
        assert "toast=connection_failed" in resp.headers["location"]
        assert "provider-controlled-detail" not in resp.headers["location"]

    async def test_token_exchange_error_does_not_expose_provider_response_text(
        self, monkeypatch, caplog
    ):
        from butlers.api.routers import spotify as spotify_router

        marker = "PROVIDER_TOKEN_RESPONSE_MARKER"
        response = MagicMock(spec=httpx.Response)
        response.status_code = 400
        response.text = marker
        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.post = AsyncMock(return_value=response)
        client_context = AsyncMock()
        client_context.__aenter__.return_value = http_client
        client_context.__aexit__.return_value = None
        monkeypatch.setattr(
            spotify_router.httpx, "AsyncClient", MagicMock(return_value=client_context)
        )

        with pytest.raises(_TokenExchangeError) as exc_info:
            await _exchange_code_for_tokens(
                code="code",
                code_verifier="verifier",
                redirect_uri="http://callback.test",
                client_id="a" * 32,
            )

        assert exc_info.value.status_code == 400
        assert marker not in str(exc_info.value)
        assert marker not in caplog.text

    async def test_callback_token_exchange_failure_logs_only_local_classification(
        self, monkeypatch, caplog
    ):
        from butlers.api.routers import spotify as spotify_router

        marker = "PROVIDER_CALLBACK_RESPONSE_MARKER"
        monkeypatch.setattr(
            spotify_router,
            "_exchange_code_for_tokens",
            AsyncMock(side_effect=_TokenExchangeError(marker, status_code=400)),
        )
        app = self._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            start = await client.post("/api/connectors/spotify/oauth/start")
            resp = await client.get(
                "/api/connectors/spotify/oauth/callback",
                params={"code": "code", "state": start.json()["state"]},
            )

        assert resp.status_code == 502
        assert marker not in resp.text
        assert marker not in caplog.text

    async def test_callback_malformed_success_payload_is_content_blind_before_profile_lookup(
        self, monkeypatch, caplog
    ):
        """A provider-controlled successful payload is validated before any use."""
        from butlers.api.routers import spotify as spotify_router

        marker = "PROVIDER_CALLBACK_SUCCESS_PAYLOAD_MARKER"
        exchange = AsyncMock(
            return_value={
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "expires_in": marker,
                "scope": "user-read-playback-state",
                "token_type": "Bearer",
            }
        )
        fetch_profile = AsyncMock(return_value={"id": "user-42"})
        upsert = AsyncMock(return_value=True)
        monkeypatch.setattr(spotify_router, "_exchange_code_for_tokens", exchange)
        monkeypatch.setattr(spotify_router, "_fetch_spotify_me", fetch_profile)
        monkeypatch.setattr(spotify_router, "upsert_owner_entity_info_on_connection", upsert)

        app = self._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            start = await client.post("/api/connectors/spotify/oauth/start")
            resp = await client.get(
                "/api/connectors/spotify/oauth/callback",
                params={"code": "code", "state": start.json()["state"]},
            )

        assert resp.status_code == 502
        assert resp.json() == {
            "detail": "Failed to exchange authorization code for tokens. "
            "Retry POST /api/connectors/spotify/oauth/start."
        }
        assert marker not in resp.text
        assert marker not in caplog.text
        fetch_profile.assert_not_awaited()
        upsert.assert_not_awaited()

    async def test_callback_writes_only_secured_owner_oauth_rows(self, monkeypatch):
        from butlers.api.routers import spotify as spotify_router

        writes: list[tuple[str, str, bool]] = []

        async def _upsert(_pool, info_type: str, value: str, *, secured: bool):
            writes.append((info_type, value, secured))
            return True

        async def _exchange(**_kwargs):
            return {
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "expires_in": 3600,
                "scope": "user-read-playback-state",
                "token_type": "Bearer",
            }

        monkeypatch.setattr(spotify_router, "upsert_owner_entity_info_on_connection", _upsert)
        monkeypatch.setattr(spotify_router, "_exchange_code_for_tokens", _exchange)
        monkeypatch.setattr(
            spotify_router, "_fetch_spotify_me", AsyncMock(return_value={"id": "user-42"})
        )
        app = self._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            start = await client.post("/api/connectors/spotify/oauth/start")
            resp = await client.get(
                "/api/connectors/spotify/oauth/callback",
                params={"code": "code", "state": start.json()["state"]},
            )

        assert resp.status_code == 200
        assert [row[0] for row in writes] == [
            "spotify_oauth_access",
            "spotify_oauth_refresh",
            "spotify_oauth_expires_at",
        ]
        assert all(row[2] is True for row in writes)
        assert resp.json() == {
            "success": True,
            "message": "Spotify authorization complete.",
        }

    async def test_callback_rejects_incomplete_token_set_before_writing(self, monkeypatch):
        from butlers.api.routers import spotify as spotify_router

        async def _exchange(**_kwargs):
            return {"access_token": "access-only", "expires_in": 3600}

        upsert = AsyncMock(return_value=True)
        monkeypatch.setattr(spotify_router, "upsert_owner_entity_info_on_connection", upsert)
        monkeypatch.setattr(spotify_router, "_exchange_code_for_tokens", _exchange)
        app = self._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            start = await client.post("/api/connectors/spotify/oauth/start")
            resp = await client.get(
                "/api/connectors/spotify/oauth/callback",
                params={"code": "code", "state": start.json()["state"]},
            )

        assert resp.status_code == 502
        upsert.assert_not_awaited()

    async def test_callback_scope_persistence_failure_rolls_back_authority_transaction(
        self, monkeypatch
    ):
        from butlers.api.routers import spotify as spotify_router

        monkeypatch.setattr(
            spotify_router,
            "_exchange_code_for_tokens",
            AsyncMock(
                return_value={
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "expires_in": 3600,
                    "scope": "user-read-playback-state",
                    "token_type": "Bearer",
                }
            ),
        )
        monkeypatch.setattr(
            spotify_router, "_fetch_spotify_me", AsyncMock(return_value={"id": "user-42"})
        )
        monkeypatch.setattr(
            spotify_router,
            "upsert_owner_entity_info_on_connection",
            AsyncMock(return_value=True),
        )
        monkeypatch.setattr(
            spotify_router,
            "_store_granted_scopes_on_connection",
            AsyncMock(side_effect=RuntimeError("scope write failed")),
        )
        app = self._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            start = await client.post("/api/connectors/spotify/oauth/start")
            resp = await client.get(
                "/api/connectors/spotify/oauth/callback",
                params={"code": "code", "state": start.json()["state"]},
            )

        assert resp.status_code == 503
        assert resp.json() == {"detail": "Owner credential authority is unavailable."}

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

    async def test_disconnect_is_idempotent_after_zero_row_authority_delete(self, monkeypatch):
        from butlers.api.routers import spotify as spotify_router

        delete = AsyncMock(return_value=0)
        monkeypatch.setattr(spotify_router, "_delete_spotify_oauth_rows", delete)
        app = self._make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/connectors/spotify/disconnect")

        assert resp.status_code == 200
        assert resp.json() == {"disconnected": True}
        delete.assert_awaited_once()

    async def test_disconnect_reports_unavailable_when_credential_authority_is_missing(self):
        app = self._make_app(access_token="tok-abc")
        app.dependency_overrides[_spotify_get_db] = lambda: None
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/connectors/spotify/disconnect")

        assert resp.status_code == 503
        assert resp.json() == {"detail": "Owner credential authority is unavailable."}

    async def test_disconnect_transaction_failure_is_content_blind(self, monkeypatch, caplog):
        from butlers.api.routers import spotify as spotify_router

        marker = "DISCONNECT_TRANSACTION_MARKER"
        monkeypatch.setattr(
            spotify_router,
            "_delete_spotify_oauth_rows",
            AsyncMock(side_effect=RuntimeError(marker)),
        )
        app = self._make_app(access_token="tok-abc")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            resp = await client.post("/api/connectors/spotify/disconnect")

        assert resp.status_code == 503
        assert resp.json() == {"detail": "Owner credential authority is unavailable."}
        assert marker not in resp.text
        assert marker not in caplog.text

    async def test_disconnect_preserves_client_id_while_clearing_local_oauth_state(
        self, monkeypatch
    ):
        """Disconnect clears local token/scope rows but leaves reconnect configuration intact."""
        from butlers.api.routers import spotify as spotify_router

        delete_calls: list[object] = []

        async def _delete(pool) -> int:
            delete_calls.append(pool)
            return 3

        monkeypatch.setattr(spotify_router, "_delete_spotify_oauth_rows", _delete)
        app = self._make_app(access_token="tok-abc")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/connectors/spotify/disconnect")

        assert resp.status_code == 200
        assert len(delete_calls) == 1


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
