"""Tests for the live-provider probe implementation in probe_user_credential.

Covers bu-omyg6: POST /api/secrets/user/<provider>/probe now makes a live
provider call (Google userinfo) instead of just checking local state.

Test matrix:
- Google probe calls token exchange then userinfo with the access token.
- Google userinfo 200 → probe_ok=True, probe_status=live_ok in audit.
- Google userinfo 401 → probe_ok=False, code=401.
- Token-mint (token exchange) failure → probe_ok=False.
- Non-Google provider (no verify handler) → falls back to local check.
- Network error on token exchange → falls back to local check (NOT False).
- Network error on userinfo call → falls back to local check.
- Audit note includes probe_status.

Spec anchor
-----------
openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
§User credential mutations — probe writes probe_log + audit
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.secrets_v2 import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)

_REFRESH_TOKEN = "fake-refresh-token-xyz"
_ACCESS_TOKEN = "fake-access-token-abc"
_CLIENT_ID = "fake-client-id"
_CLIENT_SECRET = "fake-client-secret"


def _make_row(**kwargs) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda k: kwargs[k])
    return m


def _make_entity_info_row(
    *,
    entity_id: str | None = None,
    info_type: str = "google_oauth_refresh",
    value: str = _REFRESH_TOKEN,
    label: str | None = "user@example.com",
    last_verified: datetime | None = None,
    last_test_ok: bool | None = True,
    last_test_code: int | None = None,
    last_test_message: str | None = None,
) -> MagicMock:
    from uuid import uuid4

    row_id = uuid4()
    eid = entity_id or str(uuid4())
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


def _make_butler_secrets_row(
    key: str,
    value: str,
) -> MagicMock:
    return _make_row(secret_key=key, secret_value=value)


def _make_shared_pool(
    *,
    user_row: MagicMock | None = None,
    raw_token_value: str | None = _REFRESH_TOKEN,
    client_id: str | None = _CLIENT_ID,
    client_secret: str | None = _CLIENT_SECRET,
    execute_ok: bool = True,
) -> AsyncMock:
    """Build a mock shared-pool for probe live-verify tests.

    Handles:
    - entity_info fetchrow (full row for _fetch_single_user_secret)
    - entity_info fetchrow by id (raw value for token exchange)
    - butler_secrets fetchrow (for CredentialStore.load() of client_id/client_secret)
    - secret_probe_log fetchrow (returns None — no prior probe)
    - acquire/transaction (for probe_log insert + entity_info update)
    """
    shared_pool = AsyncMock()

    async def _fetchrow(sql: str, *args):
        # Probe log lookup (always no prior probe for these tests)
        if "secret_probe_log" in sql:
            return None
        # Raw token fetch by PK (used by probe to get refresh token)
        if "entity_info" in sql and "WHERE id = $1" in sql:
            if raw_token_value is not None:
                return _make_row(value=raw_token_value)
            return None
        # Full entity_info row (used by _fetch_single_user_secret)
        if "entity_info" in sql or "entities" in sql:
            return user_row
        # CredentialStore.load() queries butler_secrets by key
        if "butler_secrets" in sql:
            if args:
                key = args[0]
                from butlers.google_credentials import KEY_CLIENT_ID, KEY_CLIENT_SECRET

                if key == KEY_CLIENT_ID and client_id:
                    return _make_butler_secrets_row(key, client_id)
                if key == KEY_CLIENT_SECRET and client_secret:
                    return _make_butler_secrets_row(key, client_secret)
            return None
        return None

    shared_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    shared_pool.fetch = AsyncMock(return_value=[])

    if execute_ok:
        shared_pool.execute = AsyncMock(return_value="UPDATE 1")
    else:
        shared_pool.execute = AsyncMock(side_effect=Exception("DB error"))

    # Fake transaction context manager (used by probe endpoint).
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
    raw_token_value: str | None = _REFRESH_TOKEN,
    client_id: str | None = _CLIENT_ID,
    client_secret: str | None = _CLIENT_SECRET,
    shared_pool_available: bool = True,
    execute_ok: bool = True,
) -> MagicMock:
    """Build a mock DatabaseManager for probe live-verify tests."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=AsyncMock())

    if shared_pool_available:
        shared_pool = _make_shared_pool(
            user_row=user_row,
            raw_token_value=raw_token_value,
            client_id=client_id,
            client_secret=client_secret,
            execute_ok=execute_ok,
        )
        mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)
    else:
        mock_db.credential_shared_pool = MagicMock(side_effect=KeyError("no shared pool"))

    return mock_db


def _build_app(mock_db: MagicMock) -> TestClient:
    """Create a TestClient with the given mock DatabaseManager."""
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return TestClient(app)


def _make_fake_httpx_client(
    *,
    token_exchange_status: int = 200,
    token_exchange_body: dict | None = None,
    userinfo_status: int = 200,
    userinfo_body: dict | None = None,
    network_error_on: str | None = None,  # "token_exchange" or "userinfo"
) -> tuple[AsyncMock, list[dict]]:
    """Build a fake httpx.AsyncClient and a calls capture list.

    Returns (fake_client, calls_list) where calls_list accumulates every HTTP
    call made (url, method, headers, data).
    """
    calls: list[dict] = []

    token_body = token_exchange_body or {"access_token": _ACCESS_TOKEN, "expires_in": 3600}
    info_body = userinfo_body or {"sub": "12345", "email": "user@example.com"}

    async def _fake_post(url, **kwargs):
        if network_error_on == "token_exchange":
            raise httpx.ConnectError("connection refused")
        calls.append({"method": "POST", "url": str(url), **kwargs})
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = token_exchange_status
        fake_resp.json = MagicMock(return_value=token_body)
        fake_resp.text = str(token_body)
        return fake_resp

    async def _fake_get(url, **kwargs):
        if network_error_on == "userinfo":
            raise httpx.ConnectError("connection refused")
        calls.append({"method": "GET", "url": str(url), **kwargs})
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = userinfo_status
        fake_resp.json = MagicMock(return_value=info_body)
        return fake_resp

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)
    fake_client.get = AsyncMock(side_effect=_fake_get)
    return fake_client, calls


# ---------------------------------------------------------------------------
# Tests: live Google userinfo probe
# ---------------------------------------------------------------------------


def test_google_probe_calls_token_exchange_then_userinfo(monkeypatch):
    """Google probe calls token exchange first, then userinfo with the access token."""
    row = _make_entity_info_row(info_type="google_oauth_refresh", last_test_ok=True)
    mock_db = _make_db(user_row=row)

    fake_client, calls = _make_fake_httpx_client()

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/probe")

    assert resp.status_code == 200

    # Must have called token exchange (POST) and userinfo (GET).
    post_calls = [c for c in calls if c["method"] == "POST"]
    get_calls = [c for c in calls if c["method"] == "GET"]
    assert post_calls, "Expected token exchange POST call"
    assert get_calls, "Expected userinfo GET call"

    # Token exchange must hit the Google token URL.
    assert "oauth2.googleapis.com/token" in post_calls[0]["url"]

    # Userinfo must include the Bearer access token.
    headers = get_calls[0].get("headers", {})
    assert headers.get("Authorization") == f"Bearer {_ACCESS_TOKEN}", (
        f"Expected Bearer token in userinfo header; got: {headers}"
    )


def test_google_probe_userinfo_200_returns_probe_ok_true(monkeypatch):
    """Google probe with userinfo HTTP 200 → probe_ok=True; audit note probe_status=live_ok."""
    row = _make_entity_info_row(info_type="google_oauth_refresh", last_test_ok=True)
    mock_db = _make_db(user_row=row)

    fake_client, _ = _make_fake_httpx_client(userinfo_status=200)

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
    resp = client.post("/api/secrets/user/google/probe")

    assert resp.status_code == 200
    assert resp.json()["data"]["ok"] is True
    assert audit_calls, "Expected at least one audit call"
    assert "probe_status=live_ok" in audit_calls[0].get("note", "")


def test_google_probe_userinfo_401_returns_probe_ok_false_with_code(monkeypatch):
    """Google probe userinfo HTTP 401 → probe_ok=False, code=401; note probe_status=live_failed."""
    row = _make_entity_info_row(info_type="google_oauth_refresh", last_test_ok=True)
    mock_db = _make_db(user_row=row)

    fake_client, _ = _make_fake_httpx_client(userinfo_status=401)

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
    resp = client.post("/api/secrets/user/google/probe")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is False
    assert data["code"] == 401
    assert audit_calls, "Expected at least one audit call"
    assert "probe_status=live_failed" in audit_calls[0].get("note", "")


def test_google_probe_token_exchange_failure_returns_probe_ok_false(monkeypatch):
    """Token exchange (refresh → access) HTTP failure → probe_ok=False."""
    row = _make_entity_info_row(info_type="google_oauth_refresh", last_test_ok=True)
    mock_db = _make_db(user_row=row)

    # Token exchange returns 400 (e.g., refresh token revoked).
    fake_client, _ = _make_fake_httpx_client(
        token_exchange_status=400,
        token_exchange_body={"error": "invalid_grant"},
    )

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/probe")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is False
    assert data["code"] == 400


def test_non_google_provider_falls_back_to_local_check(monkeypatch):
    """Unlisted provider (e.g. telegram) falls back to local-state check, no HTTP calls."""
    # telegram is not in _OAUTH_VERIFY_PROVIDERS and has no custom handler
    # → always returns skipped_local_check without any HTTP calls.
    row = _make_entity_info_row(
        info_type="telegram_oauth_refresh",
        last_test_ok=True,
        value="telegram-refresh-tok",
    )
    mock_db = _make_db(
        user_row=row,
        raw_token_value="telegram-refresh-tok",
    )

    http_calls: list[dict] = []

    async def _fake_post(url, **kwargs):
        http_calls.append({"method": "POST", "url": str(url)})
        raise AssertionError("Should not call HTTP for unsupported provider")

    async def _fake_get(url, **kwargs):
        http_calls.append({"method": "GET", "url": str(url)})
        raise AssertionError("Should not call HTTP for unsupported provider")

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)
    fake_client.get = AsyncMock(side_effect=_fake_get)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/telegram/probe")

    assert resp.status_code == 200
    data = resp.json()["data"]
    # Local state is ok (last_test_ok=True, value set) → probe_ok=True
    assert data["ok"] is True
    assert not http_calls, f"Expected no HTTP calls for unsupported provider; got: {http_calls}"


def test_network_error_on_token_exchange_falls_back_to_local_check(monkeypatch):
    """Network error during token exchange → fallback to local check, NOT probe_ok=False."""
    row = _make_entity_info_row(
        info_type="google_oauth_refresh",
        last_test_ok=True,
        value="refresh-tok",
    )
    mock_db = _make_db(user_row=row)

    # Token exchange raises a network error.
    fake_client, _ = _make_fake_httpx_client(network_error_on="token_exchange")

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
    resp = client.post("/api/secrets/user/google/probe")

    assert resp.status_code == 200
    data = resp.json()["data"]
    # Network error → skipped_local_check → local state wins.
    # last_test_ok=True + value set → state='ok' → probe_ok=True.
    assert data["ok"] is True
    # Audit note records the skipped-local-check fallback status.
    assert audit_calls, "Expected at least one audit call"
    assert "probe_status=skipped_local_check" in audit_calls[0].get("note", "")


def test_network_error_on_userinfo_falls_back_to_local_check(monkeypatch):
    """Network error during userinfo call → fallback to local check, NOT probe_ok=False."""
    row = _make_entity_info_row(
        info_type="google_oauth_refresh",
        last_test_ok=True,
        value="refresh-tok",
    )
    mock_db = _make_db(user_row=row)

    # Userinfo raises a network error (token exchange succeeds).
    fake_client, _ = _make_fake_httpx_client(network_error_on="userinfo")

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/probe")

    assert resp.status_code == 200
    data = resp.json()["data"]
    # Network error on userinfo → skipped_local_check → local state wins.
    assert data["ok"] is True


def test_google_probe_missing_app_credentials_falls_back_to_local_check(monkeypatch):
    """Missing app credentials (no client_id/client_secret) → fallback to local check."""
    row = _make_entity_info_row(
        info_type="google_oauth_refresh",
        last_test_ok=True,
        value="refresh-tok",
    )
    # Supply no app credentials in butler_secrets.
    mock_db = _make_db(user_row=row, client_id=None, client_secret=None)

    http_calls: list[dict] = []

    async def _fake_post(url, **kwargs):
        http_calls.append({"url": str(url)})
        raise AssertionError("Should not call HTTP when app credentials are missing")

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/probe")

    assert resp.status_code == 200
    data = resp.json()["data"]
    # Falls back to local state: last_test_ok=True + value → ok → True.
    assert data["ok"] is True
    assert not http_calls, f"No HTTP calls expected; got: {http_calls}"


# ---------------------------------------------------------------------------
# Tests: GitHub PAT live probe (bu-ppe9v)
# ---------------------------------------------------------------------------

_GITHUB_PAT = "ghp_fakePersonalAccessToken12345"


def _make_github_pat_row(
    *,
    last_test_ok: bool | None = True,
    value: str = _GITHUB_PAT,
) -> MagicMock:
    """Build a mock entity_info row for a GitHub PAT credential."""
    return _make_entity_info_row(
        info_type="github_pat",
        value=value,
        label="tzeusy",
        last_test_ok=last_test_ok,
    )


def _make_github_db(
    *,
    last_test_ok: bool | None = True,
    raw_token_value: str | None = _GITHUB_PAT,
) -> MagicMock:
    """Build a mock DatabaseManager for a GitHub PAT probe."""
    row = _make_github_pat_row(last_test_ok=last_test_ok)
    return _make_db(
        user_row=row,
        raw_token_value=raw_token_value,
        # GitHub PAT probe does not need app credentials.
        client_id=None,
        client_secret=None,
    )


def test_github_pat_probe_calls_api_github_user_directly(monkeypatch):
    """GitHub PAT probe calls GET https://api.github.com/user with 'token <pat>' header."""
    mock_db = _make_github_db()

    calls: list[dict] = []

    async def _fake_get(url, **kwargs):
        calls.append({"method": "GET", "url": str(url), **kwargs})
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 200
        fake_resp.json = MagicMock(return_value={"login": "tzeusy"})
        return fake_resp

    async def _fake_post(url, **kwargs):
        raise AssertionError("GitHub PAT probe must NOT call token exchange")

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(side_effect=_fake_get)
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/github/probe")

    assert resp.status_code == 200

    assert len(calls) == 1, f"Expected exactly one GET call; got: {calls}"
    assert "api.github.com/user" in calls[0]["url"]
    auth = calls[0].get("headers", {}).get("Authorization", "")
    assert auth == f"token {_GITHUB_PAT}", f"Expected 'token <pat>' header; got: {auth!r}"


def test_github_pat_probe_200_returns_probe_ok_true(monkeypatch):
    """GitHub PAT probe with api.github.com/user HTTP 200 → probe_ok=True."""
    mock_db = _make_github_db()

    async def _fake_get(url, **kwargs):
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 200
        fake_resp.json = MagicMock(return_value={"login": "tzeusy"})
        return fake_resp

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(side_effect=_fake_get)
    fake_client.post = AsyncMock(side_effect=AssertionError("No POST for PAT"))

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/github/probe")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is True


def test_github_pat_probe_401_returns_probe_ok_false_with_code(monkeypatch):
    """GitHub PAT probe with api.github.com/user HTTP 401 → probe_ok=False, code=401."""
    mock_db = _make_github_db()

    async def _fake_get(url, **kwargs):
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 401
        fake_resp.json = MagicMock(return_value={"message": "Bad credentials"})
        return fake_resp

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(side_effect=_fake_get)
    fake_client.post = AsyncMock(side_effect=AssertionError("No POST for PAT"))

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/github/probe")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is False
    assert data["code"] == 401


def test_github_pat_probe_network_error_falls_back_to_local_check(monkeypatch):
    """Network error when calling api.github.com/user → fallback to local check (NOT False)."""
    mock_db = _make_github_db(last_test_ok=True)

    async def _fake_get(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(side_effect=_fake_get)
    fake_client.post = AsyncMock(side_effect=AssertionError("No POST for PAT"))

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/github/probe")

    assert resp.status_code == 200
    data = resp.json()["data"]
    # Network error → skipped_local_check → local state wins.
    # last_test_ok=True + value set → state='ok' → probe_ok=True.
    assert data["ok"] is True


def test_github_pat_type_not_accepted_for_google(monkeypatch):
    """A github_pat credential type is NOT accepted by the Google provider config."""
    # Create a row as if someone registered a 'github_pat' type but is probing under 'google'.
    # This should fall back to local check, not trigger any live verify.
    row = _make_entity_info_row(
        info_type="github_pat",
        last_test_ok=True,
        value="ghp_fake",
    )
    mock_db = _make_db(user_row=row, raw_token_value="ghp_fake")

    http_calls: list[dict] = []

    async def _fake_get(url, **kwargs):
        http_calls.append({"method": "GET", "url": str(url)})
        raise AssertionError(f"Should not call HTTP; got GET {url}")

    async def _fake_post(url, **kwargs):
        http_calls.append({"method": "POST", "url": str(url)})
        raise AssertionError(f"Should not call HTTP; got POST {url}")

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(side_effect=_fake_get)
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    # Probe under 'google' even though the entity_info type is github_pat.
    # The Google config only accepts _oauth_refresh → should skip live verify.
    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/probe")

    assert resp.status_code == 200
    data = resp.json()["data"]
    # Falls back to local state: last_test_ok=True → probe_ok=True.
    assert data["ok"] is True
    assert not http_calls, f"No HTTP calls expected; got: {http_calls}"
