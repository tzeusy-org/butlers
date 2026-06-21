"""Tests for the Spotify live credential probe in probe_user_credential.

Covers bu-xfq4r: POST /api/secrets/user/spotify/probe now makes a LIVE call
to Spotify's token endpoint and GET /v1/me instead of falling back to local
state unconditionally.

Test matrix
-----------
- live_ok: token refresh succeeds + GET /v1/me returns 200
- live_failed: token refresh returns non-200 (expired/revoked refresh token)
- live_failed: /v1/me returns 401 (bad access token)
- live_failed: /v1/me returns 403
- network error on token refresh falls back to local check (HTTP 200, NOT 503)
- network error on /v1/me call falls back to local check
- SPOTIFY_CLIENT_ID not configured → skipped_local_check (no HTTP calls)
- token refresh calls accounts.spotify.com/api/token with PKCE (no client_secret)
- /v1/me call uses Bearer access token from refresh response
- audit note includes probe_status=live_ok / live_failed / skipped_local_check

Acceptance criteria (bu-xfq4r)
-------------------------------
1. Spotify probe returns live_ok/live_failed when credentials present.
2. Network/config/refresh errors fall back to local-state (HTTP 200, never 503).
3. probe_log + cache columns updated as for other providers.
4. Tests mock BOTH the token-refresh and /v1/me HTTP calls — no real Spotify calls.

Spec anchor
-----------
bu-xfq4r (Spotify live credential probe — backend)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.secrets_v2 import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)
_SPOTIFY_REFRESH_TOKEN = "AQD_fake_spotify_refresh_token"
_SPOTIFY_ACCESS_TOKEN = "BQD_fake_spotify_access_token"
_SPOTIFY_CLIENT_ID = "fake_spotify_client_id_32chars00"


# ---------------------------------------------------------------------------
# Row factories
# ---------------------------------------------------------------------------


def _make_row(**kwargs) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda k: kwargs[k])
    return m


def _make_entity_info_row(
    *,
    info_type: str = "spotify_oauth_refresh",
    value: str = _SPOTIFY_REFRESH_TOKEN,
    label: str | None = "spotify-user",
    last_verified: datetime | None = None,
    last_test_ok: bool | None = True,
    last_test_code: int | None = None,
    last_test_message: str | None = None,
) -> MagicMock:
    row_id = uuid4()
    eid = str(uuid4())
    return _make_row(
        id=row_id,
        entity_id=eid,
        type=info_type,
        value=value,
        label=label,
        last_verified=last_verified,
        last_test_ok=last_test_ok,
        last_test_code=last_test_code,
        last_test_message=last_test_message,
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Shared pool factory
# ---------------------------------------------------------------------------


def _make_shared_pool(
    *,
    user_row: MagicMock | None = None,
    raw_token_value: str | None = _SPOTIFY_REFRESH_TOKEN,
    spotify_client_id: str | None = _SPOTIFY_CLIENT_ID,
    execute_ok: bool = True,
) -> AsyncMock:
    """Build a mock shared pool for Spotify probe tests.

    Handles:
    - entity_info fetchrow (full row for _fetch_single_user_secret)
    - entity_info fetchrow by id (raw refresh token)
    - butler_secrets fetchrow (CredentialStore.load() for SPOTIFY_CLIENT_ID)
    - secret_probe_log fetchrow (no prior probe)
    - acquire/transaction (for probe_log insert + entity_info update)
    """
    shared_pool = AsyncMock()

    async def _fetchrow(sql: str, *args):
        # Probe log lookup (no prior probe for these tests)
        if "secret_probe_log" in sql:
            return None
        # Raw token fetch by PK (used by probe endpoint to get refresh token)
        if "entity_info" in sql and "WHERE id = $1" in sql:
            if raw_token_value is not None:
                return _make_row(value=raw_token_value)
            return None
        # CredentialStore.load() — butler_secrets lookup by key
        if "butler_secrets" in sql:
            if args:
                key = args[0]
                if key == "SPOTIFY_CLIENT_ID" and spotify_client_id:
                    return _make_row(secret_key=key, secret_value=spotify_client_id)
            return None
        # Full entity_info row (used by _fetch_single_user_secret)
        if "entity_info" in sql or "entities" in sql:
            return user_row
        return None

    shared_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    shared_pool.fetch = AsyncMock(return_value=[])

    if execute_ok:
        shared_pool.execute = AsyncMock(return_value="UPDATE 1")
    else:
        shared_pool.execute = AsyncMock(side_effect=Exception("DB error"))

    fake_conn = AsyncMock()
    fake_conn.fetchrow = shared_pool.fetchrow
    fake_conn.fetch = shared_pool.fetch
    fake_conn.execute = shared_pool.execute
    fake_conn.fetchval = AsyncMock(return_value=1)

    @asynccontextmanager
    async def _transaction():
        yield

    fake_conn.transaction = _transaction

    @asynccontextmanager
    async def _acquire():
        yield fake_conn

    shared_pool.acquire = _acquire
    return shared_pool


def _make_db(
    *,
    user_row: MagicMock | None = None,
    raw_token_value: str | None = _SPOTIFY_REFRESH_TOKEN,
    spotify_client_id: str | None = _SPOTIFY_CLIENT_ID,
    execute_ok: bool = True,
) -> MagicMock:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=AsyncMock())

    shared_pool = _make_shared_pool(
        user_row=user_row,
        raw_token_value=raw_token_value,
        spotify_client_id=spotify_client_id,
        execute_ok=execute_ok,
    )
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)
    return mock_db


def _build_app(mock_db: MagicMock) -> TestClient:
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return TestClient(app)


def _make_fake_httpx_client(
    *,
    token_refresh_status: int = 200,
    token_refresh_body: dict | None = None,
    me_status: int = 200,
    me_body: dict | None = None,
    network_error_on: str | None = None,  # "token_refresh" or "me"
) -> tuple[AsyncMock, list[dict]]:
    """Build a fake httpx.AsyncClient for Spotify probe tests.

    Returns (fake_client, calls_list).  Both POST (token refresh) and GET (/v1/me)
    are captured in calls_list so tests can assert on exact call details.
    """
    calls: list[dict] = []

    refresh_body = token_refresh_body or {
        "access_token": _SPOTIFY_ACCESS_TOKEN,
        "expires_in": 3600,
        "token_type": "Bearer",
    }
    profile_body = me_body or {"id": "spotify-user-123", "display_name": "Test User"}

    async def _fake_post(url, **kwargs):
        if network_error_on == "token_refresh":
            raise httpx.ConnectError("connection refused")
        calls.append({"method": "POST", "url": str(url), **kwargs})
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = token_refresh_status
        fake_resp.json = MagicMock(return_value=refresh_body)
        fake_resp.text = str(refresh_body)
        return fake_resp

    async def _fake_get(url, **kwargs):
        if network_error_on == "me":
            raise httpx.ConnectError("connection refused")
        calls.append({"method": "GET", "url": str(url), **kwargs})
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = me_status
        fake_resp.json = MagicMock(return_value=profile_body)
        return fake_resp

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)
    fake_client.get = AsyncMock(side_effect=_fake_get)
    return fake_client, calls


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestSpotifyProbe:
    """POST /api/secrets/user/spotify/probe live-verify tests (bu-xfq4r)."""

    def _make_spotify_db(
        self,
        *,
        last_test_ok: bool | None = True,
        spotify_client_id: str | None = _SPOTIFY_CLIENT_ID,
        raw_token_value: str | None = _SPOTIFY_REFRESH_TOKEN,
    ) -> MagicMock:
        row = _make_entity_info_row(
            info_type="spotify_oauth_refresh",
            value=raw_token_value or _SPOTIFY_REFRESH_TOKEN,
            last_test_ok=last_test_ok,
        )
        return _make_db(
            user_row=row,
            raw_token_value=raw_token_value,
            spotify_client_id=spotify_client_id,
        )

    def test_spotify_probe_200_returns_live_ok(self, monkeypatch):
        """Spotify probe: token refresh + /v1/me 200 → probe_ok=True; note probe_status=live_ok."""
        mock_db = self._make_spotify_db()
        fake_client, _ = _make_fake_httpx_client()

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        audit_calls: list[dict] = []

        async def _fake_append(pool, actor, action, **kwargs):
            audit_calls.append({"actor": actor, "action": action, **kwargs})
            return 1

        import butlers.api.routers.audit as _audit_mod

        monkeypatch.setattr(_audit_mod, "append", _fake_append)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/spotify/probe")

        assert resp.status_code == 200
        assert resp.json()["data"]["ok"] is True
        assert audit_calls, "Expected at least one audit call"
        assert "probe_status=live_ok" in audit_calls[0].get("note", "")

    def test_spotify_probe_calls_token_refresh_then_me(self, monkeypatch):
        """Spotify probe calls POST token endpoint then GET /v1/me in order."""
        mock_db = self._make_spotify_db()
        fake_client, calls = _make_fake_httpx_client()

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/spotify/probe")

        assert resp.status_code == 200

        post_calls = [c for c in calls if c["method"] == "POST"]
        get_calls = [c for c in calls if c["method"] == "GET"]
        assert post_calls, "Expected token refresh POST call"
        assert get_calls, "Expected /v1/me GET call"

        # Token refresh must call accounts.spotify.com/api/token
        assert "accounts.spotify.com/api/token" in post_calls[0]["url"]

        # /v1/me must be called with Bearer access token
        assert "api.spotify.com/v1/me" in get_calls[0]["url"]
        auth = get_calls[0].get("headers", {}).get("Authorization", "")
        assert auth == f"Bearer {_SPOTIFY_ACCESS_TOKEN}", (
            f"Expected 'Bearer {_SPOTIFY_ACCESS_TOKEN}'; got {auth!r}"
        )

    def test_spotify_probe_token_refresh_no_client_secret(self, monkeypatch):
        """Spotify PKCE token refresh must NOT include client_secret in request body."""
        mock_db = self._make_spotify_db()
        fake_client, calls = _make_fake_httpx_client()

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/spotify/probe")

        assert resp.status_code == 200

        post_calls = [c for c in calls if c["method"] == "POST"]
        assert post_calls, "Expected token refresh POST call"

        # POST body (data=) must contain grant_type, refresh_token, client_id.
        # It must NOT contain client_secret (PKCE flow).
        form_data = post_calls[0].get("data", {})
        assert form_data.get("grant_type") == "refresh_token"
        assert form_data.get("refresh_token") == _SPOTIFY_REFRESH_TOKEN
        assert form_data.get("client_id") == _SPOTIFY_CLIENT_ID
        assert "client_secret" not in form_data, "PKCE token refresh must NOT include client_secret"

    def test_spotify_probe_token_refresh_failure_returns_live_failed(self, monkeypatch):
        """Token refresh non-200 (e.g. revoked refresh token) → probe_ok=False."""
        mock_db = self._make_spotify_db()
        fake_client, _ = _make_fake_httpx_client(
            token_refresh_status=400,
            token_refresh_body={"error": "invalid_grant"},
        )

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/spotify/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["ok"] is False
        assert data["code"] == 400

    def test_spotify_probe_me_401_returns_live_failed(self, monkeypatch):
        """/v1/me HTTP 401 → probe_ok=False, code=401; note probe_status=live_failed."""
        mock_db = self._make_spotify_db()
        fake_client, _ = _make_fake_httpx_client(me_status=401)

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        audit_calls: list[dict] = []

        async def _fake_append(pool, actor, action, **kwargs):
            audit_calls.append({"actor": actor, "action": action, **kwargs})
            return 1

        import butlers.api.routers.audit as _audit_mod

        monkeypatch.setattr(_audit_mod, "append", _fake_append)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/spotify/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["ok"] is False
        assert data["code"] == 401
        assert audit_calls, "Expected at least one audit call"
        assert "probe_status=live_failed" in audit_calls[0].get("note", "")

    def test_spotify_probe_me_403_returns_live_failed(self, monkeypatch):
        """/v1/me HTTP 403 → probe_ok=False, code=403."""
        mock_db = self._make_spotify_db()
        fake_client, _ = _make_fake_httpx_client(me_status=403)

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/spotify/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["ok"] is False
        assert data["code"] == 403

    def test_spotify_probe_network_error_on_token_refresh_falls_back(self, monkeypatch):
        """Network error during token refresh → fallback to local check, HTTP 200."""
        mock_db = self._make_spotify_db(last_test_ok=True)
        fake_client, _ = _make_fake_httpx_client(network_error_on="token_refresh")

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/spotify/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        # Network error → skipped_local_check → local state wins.
        # last_test_ok=True + value set → state='ok' → probe_ok=True.
        assert data["ok"] is True

    def test_spotify_probe_network_error_on_me_falls_back(self, monkeypatch):
        """Network error during GET /v1/me → fallback to local check, HTTP 200."""
        mock_db = self._make_spotify_db(last_test_ok=True)
        fake_client, _ = _make_fake_httpx_client(network_error_on="me")

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/spotify/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        # Network error → skipped_local_check → local state wins.
        assert data["ok"] is True

    def test_spotify_probe_missing_client_id_falls_back_to_local(self, monkeypatch):
        """SPOTIFY_CLIENT_ID not configured → skipped_local_check, no HTTP calls."""
        mock_db = self._make_spotify_db(spotify_client_id=None, last_test_ok=True)

        http_calls: list[dict] = []

        async def _fake_post(url, **kwargs):
            http_calls.append({"method": "POST", "url": str(url)})
            raise AssertionError(f"Should not call HTTP; got POST {url}")

        async def _fake_get(url, **kwargs):
            http_calls.append({"method": "GET", "url": str(url)})
            raise AssertionError(f"Should not call HTTP; got GET {url}")

        fake_client = AsyncMock()
        fake_client.post = AsyncMock(side_effect=_fake_post)
        fake_client.get = AsyncMock(side_effect=_fake_get)

        async def _fake_aenter(self):
            return fake_client

        async def _fake_aexit(self, *args):
            pass

        monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
        monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

        audit_calls: list[dict] = []

        async def _fake_append(pool, actor, action, **kwargs):
            audit_calls.append({"actor": actor, "action": action, **kwargs})
            return 1

        import butlers.api.routers.audit as _audit_mod

        monkeypatch.setattr(_audit_mod, "append", _fake_append)

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/spotify/probe")

        assert resp.status_code == 200
        data = resp.json()["data"]
        # Falls back to local state: last_test_ok=True + value set → ok → True.
        assert data["ok"] is True
        assert not http_calls, f"No HTTP calls expected; got: {http_calls}"
        # Audit note records the skipped-local-check fallback status.
        assert audit_calls, "Expected at least one audit call"
        assert "probe_status=skipped_local_check" in audit_calls[0].get("note", "")

    def test_spotify_probe_never_returns_503(self, monkeypatch):
        """Even when everything fails, Spotify probe returns HTTP 200 (never 503)."""
        # Missing client_id + no token → full fallback to local state.
        mock_db = self._make_spotify_db(
            spotify_client_id=None,
            raw_token_value=None,
            last_test_ok=None,
        )

        client = _build_app(mock_db)
        resp = client.post("/api/secrets/user/spotify/probe")

        # Must be HTTP 200, never 503.
        assert resp.status_code == 200
