"""Tests for user-credential mutation endpoints.

Covers bu-e9gge: POST /api/secrets/user/<provider>/{rotate,disconnect,probe,reauthorize}

Test matrix per endpoint:
- Success path: 200 with correct envelope and payload shape.
- Audit row written: audit_append_spy called with correct action.
- 404 on unknown provider (no credential found).
- probe: same-transaction commit (probe_log row + entity_info update).
- reauthorize: redirect_url contains page_of_origin=secrets.
- disconnect: 404 on provider without a credential.

Spec anchor
-----------
openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
§User credential mutations
openspec/changes/redesign-secrets-passport/specs/butler-secrets/spec.md
§Cross-Page Reauth Bookkeeping
openspec/changes/redesign-secrets-passport/specs/core-credentials/spec.md
§Cache write on probe
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

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


def _make_row(**kwargs) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda k: kwargs[k])
    return m


def _make_entity_info_row(
    *,
    entity_id: str | None = None,
    info_type: str = "google_oauth_refresh",
    value: str = "tok3n",
    label: str | None = "user@example.com",
    last_verified: datetime | None = None,
    last_test_ok: bool | None = True,
    last_test_code: int | None = None,
    last_test_message: str | None = None,
) -> MagicMock:
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


def _make_shared_pool(
    *,
    user_row: MagicMock | None = None,
    probe_row: MagicMock | None = None,
    execute_ok: bool = True,
) -> AsyncMock:
    """Build a mock shared-pool that supports fetchrow, execute, and transaction."""
    shared_pool = AsyncMock()

    async def _fetchrow(sql, *args):
        if "secret_probe_log" in sql:
            return probe_row
        if "entity_info" in sql or "entities" in sql:
            return user_row
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

    # Fake transaction() context manager.
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
    probe_row: MagicMock | None = None,
    shared_pool_available: bool = True,
    execute_ok: bool = True,
) -> MagicMock:
    """Build a mock DatabaseManager for mutation endpoint tests."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=AsyncMock())

    if shared_pool_available:
        shared_pool = _make_shared_pool(
            user_row=user_row,
            probe_row=probe_row,
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


# ---------------------------------------------------------------------------
# Tests: POST /api/secrets/user/<provider>/rotate
# ---------------------------------------------------------------------------


def test_rotate_returns_200_and_writes_canonical_audit(monkeypatch):
    """rotate returns 200 with ApiResponse<UserSecretDetail> envelope and appends a
    'rotated' audit row targeting the canonical key 'u:google'."""
    row = _make_entity_info_row(info_type="google_oauth_refresh", last_test_ok=True)
    mock_db = _make_db(user_row=row)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/rotate", json={"value": "new-tok3n"})
    assert resp.status_code == 200
    body = resp.json()
    assert "meta" in body
    assert body["data"]["provider"] == "google"
    assert "type" in body["data"]

    rotated = [c for c in audit_calls if c["action"] == "rotated"]
    assert rotated, f"Expected 'rotated' audit action; got: {audit_calls}"
    assert rotated[0].get("target") == "u:google"


def test_rotate_404_on_missing_credential():
    """rotate returns 404 when no credential exists for the provider."""
    mock_db = _make_db(user_row=None)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/spotify/rotate", json={"value": "tok"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: POST /api/secrets/user/<provider>/disconnect
# ---------------------------------------------------------------------------


def test_disconnect_returns_200_and_writes_audit(monkeypatch):
    """disconnect returns 200 with {status: 'disconnected'} and appends a
    'disconnected' audit row to public.audit_log."""
    row = _make_entity_info_row(info_type="google_oauth_refresh")
    mock_db = _make_db(user_row=row)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/disconnect")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "disconnected"
    assert any(c["action"] == "disconnected" for c in audit_calls), (
        f"Expected 'disconnected' audit action; got: {audit_calls}"
    )


def test_disconnect_404_on_missing_credential():
    """disconnect returns 404 when no credential exists for the provider."""
    mock_db = _make_db(user_row=None)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/spotify/disconnect")
    assert resp.status_code == 404


def test_disconnect_google_calls_revoke_url(monkeypatch):
    """Regression (bu-hr3nt): Google disconnect revokes the token at Google.

    Previously the disconnect endpoint deleted the entity_info row only and did
    NOT revoke at the provider, leaving a live refresh token. It must now call
    _revoke_oauth_token (Google revoke URL) with the old token, matching the
    /rotate and DELETE /accounts/{id} siblings.
    """
    import httpx

    row = _make_entity_info_row(info_type="google_oauth_refresh", value="old-token-xyz")
    mock_db = _make_db(user_row=row)

    revoke_calls: list[dict] = []

    async def _fake_post(url, **kwargs):
        revoke_calls.append({"url": str(url), **kwargs})
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 200
        return fake_resp

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/disconnect")

    assert resp.status_code == 200
    assert revoke_calls, "Expected disconnect to call the Google revoke URL"
    assert "oauth2.googleapis.com/revoke" in revoke_calls[0]["url"]
    # The old token must be sent in the POST body (data=), not query params.
    data = revoke_calls[0].get("data", {})
    assert data.get("token") == "old-token-xyz", (
        f"Expected old token in revoke body data, got: {data}"
    )


def test_disconnect_invokes_revoke_helper_with_old_token(monkeypatch):
    """disconnect calls _revoke_oauth_token with the provider, type, and old token."""
    import butlers.api.routers.secrets_v2 as _sv2

    row = _make_entity_info_row(info_type="google_oauth_refresh", value="live-token")
    mock_db = _make_db(user_row=row)

    revoke_args: list[tuple] = []

    async def _spy_revoke(provider, credential_type, old_value, **kwargs):
        revoke_args.append((provider, credential_type, old_value))
        return "succeeded"

    monkeypatch.setattr(_sv2, "_revoke_oauth_token", _spy_revoke)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/disconnect")

    assert resp.status_code == 200
    assert revoke_args, "Expected disconnect to invoke _revoke_oauth_token"
    provider_arg, type_arg, token_arg = revoke_args[0]
    assert provider_arg == "google"
    assert type_arg == "google_oauth_refresh"
    assert token_arg == "live-token"


def test_disconnect_google_revoke_failure_does_not_strand_row(monkeypatch):
    """A Google-side revoke failure must NOT make disconnect return non-200."""
    import httpx

    row = _make_entity_info_row(info_type="google_oauth_refresh", value="old-token")
    mock_db = _make_db(user_row=row)

    async def _fake_post(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/disconnect")

    # Disconnect MUST succeed even when revoke fails.
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "disconnected"


def test_disconnect_non_oauth_provider_does_not_call_revoke(monkeypatch):
    """A non-OAuth credential type must not trigger a provider revoke HTTP call."""
    import httpx

    row = _make_entity_info_row(info_type="api_key", value="static-key")
    mock_db = _make_db(user_row=row)

    revoke_calls: list[dict] = []

    async def _fake_post(url, **kwargs):
        revoke_calls.append({"url": str(url)})
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 200
        return fake_resp

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/openai/disconnect")

    assert resp.status_code == 200
    assert not revoke_calls, "Non-OAuth disconnect must not call the revoke endpoint"


# ---------------------------------------------------------------------------
# Tests: POST /api/secrets/user/<provider>/probe
# ---------------------------------------------------------------------------


def test_probe_returns_200_with_test_result():
    """probe returns 200 with ApiResponse<TestResult> envelope; ok=True for a
    credential whose last_test_ok=True."""
    row = _make_entity_info_row(info_type="google_oauth_refresh", last_test_ok=True, value="tok")
    mock_db = _make_db(user_row=row)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/probe")
    assert resp.status_code == 200
    body = resp.json()
    assert "meta" in body
    data = body["data"]
    assert "at" in data
    assert data["ok"] is True


def test_probe_writes_both_probe_log_and_cache_in_transaction():
    """probe writes one probe_log row AND updates entity_info in the same transaction."""
    row = _make_entity_info_row(info_type="google_oauth_refresh", last_test_ok=True, value="tok")
    mock_db = _make_db(user_row=row)
    shared_pool = mock_db.credential_shared_pool()

    # Collect all execute calls through the transaction conn.
    # Patch the fake_conn.execute inside the acquire context.
    # We check that BOTH probe_log INSERT and entity_info UPDATE appear.
    fake_conn_calls: list[str] = []

    @asynccontextmanager
    async def _acquire_tracking():
        conn = AsyncMock()
        conn.fetchrow = shared_pool.fetchrow
        conn.fetch = shared_pool.fetch
        conn.fetchval = AsyncMock(return_value=1)

        @asynccontextmanager
        async def _transaction():
            yield

        conn.transaction = _transaction

        async def _conn_execute(sql, *args, **kwargs):
            fake_conn_calls.append(sql)
            return "OK"

        conn.execute = _conn_execute
        yield conn

    shared_pool.acquire = _acquire_tracking

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/probe")
    assert resp.status_code == 200

    # Both SQL statements must appear.
    probe_log_inserts = [s for s in fake_conn_calls if "secret_probe_log" in s]
    entity_info_updates = [s for s in fake_conn_calls if "entity_info" in s and "UPDATE" in s]
    assert probe_log_inserts, "Expected INSERT into secret_probe_log"
    assert entity_info_updates, "Expected UPDATE on entity_info"


def test_probe_verified_action_when_ok(monkeypatch):
    """probe writes 'verified' audit action when credential is ok."""
    row = _make_entity_info_row(info_type="google_oauth_refresh", last_test_ok=True, value="tok")
    mock_db = _make_db(user_row=row)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)

    client.post("/api/secrets/user/google/probe")
    assert any(c["action"] == "verified" for c in audit_calls)


def test_probe_failed_action_when_not_ok(monkeypatch):
    """probe writes 'failed' audit action when credential is in a failing state."""
    row = _make_entity_info_row(
        info_type="google_oauth_refresh",
        last_test_ok=False,
        value="tok",
        last_test_message="Token revoked",
    )
    mock_db = _make_db(user_row=row)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)

    client.post("/api/secrets/user/google/probe")
    assert any(c["action"] == "failed" for c in audit_calls)


def test_probe_404_on_missing_credential():
    """probe returns 404 when no credential exists for the provider."""
    mock_db = _make_db(user_row=None)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/spotify/probe")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: POST /api/secrets/user/<provider>/reauthorize
# ---------------------------------------------------------------------------


def test_reauthorize_returns_200_with_redirect_url():
    """reauthorize returns 200 with ApiResponse<{redirect_url}>; the redirect points to
    /api/oauth/<provider>/start, carries page_of_origin=secrets, and includes an
    account_hint when the credential has a label."""
    row = _make_entity_info_row(info_type="google_oauth_refresh", label="user@example.com")
    mock_db = _make_db(user_row=row)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/reauthorize")
    assert resp.status_code == 200
    redirect_url = resp.json()["data"]["redirect_url"]
    assert "/api/oauth/google/start" in redirect_url, redirect_url
    assert "page_of_origin=secrets" in redirect_url, redirect_url
    assert (
        "account_hint=user%40example.com" in redirect_url
        or "account_hint=user@example.com" in redirect_url
    ), f"Expected account_hint in redirect_url: {redirect_url!r}"


def test_reauthorize_writes_attempted_audit_row(monkeypatch):
    """reauthorize appends an 'attempted' audit row to public.audit_log."""
    row = _make_entity_info_row(info_type="google_oauth_refresh")
    mock_db = _make_db(user_row=row)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/reauthorize")
    assert resp.status_code == 200
    assert any(c["action"] == "attempted" for c in audit_calls), (
        f"Expected 'attempted' audit action; got: {audit_calls}"
    )


def test_reauthorize_first_time_connect_oauth_provider_returns_start_url():
    """First-time connect for a non-Google OAuth provider with NO entity_info row
    returns a start/authorize redirect (not 404).

    Regression for bu-vvez2: clicking 'connect' for a never-set non-Google OAuth
    provider (e.g. spotify) used to 404 because the reauthorize endpoint required
    a pre-existing credential row.  Google bypassed this via its own start flow;
    the other OAuth providers had no equivalent.  Now every OAuth-kind provider
    routes a first-time connect to /api/oauth/<provider>/start.
    """
    mock_db = _make_db(user_row=None)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/spotify/reauthorize")
    assert resp.status_code == 200, resp.text
    redirect_url = resp.json()["data"]["redirect_url"]
    assert "/api/oauth/spotify/start" in redirect_url, redirect_url
    assert "page_of_origin=secrets" in redirect_url, redirect_url
    # No stored account → no account_hint on a first-time connect.
    assert "account_hint" not in redirect_url, redirect_url


def test_reauthorize_unregistered_catalog_oauth_provider_returns_501():
    """First-time connect for a catalog-oauth provider with no OAuth integration
    wired into _PROVIDER_REGISTRY (e.g. whatsapp) returns an honest 501 instead
    of a redirect_url that would land the browser on a confusing JSON 404.

    Regression for bu-atcfw: whatsapp is kind='oauth' in the catalog but has no
    registered OAuth provider (no real OAuth app credentials).  Rather than
    fabricating a provider, reauthorize returns 501 so the FE can show an honest
    'not yet available' message.
    """
    from butlers.api.routers.oauth import _PROVIDER_REGISTRY
    from butlers.secrets_provider_catalog import PROVIDER_CATALOG

    # Preconditions this test depends on.
    assert PROVIDER_CATALOG["whatsapp"].kind == "oauth"
    assert "whatsapp" not in _PROVIDER_REGISTRY

    mock_db = _make_db(user_row=None)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/whatsapp/reauthorize")
    assert resp.status_code == 501, resp.text
    # FastAPI HTTPException → {"detail": "..."} so the FE surfaces an honest message.
    detail = resp.json()["detail"]
    assert "not yet available" in detail.lower()
    assert "WhatsApp" in detail


def test_reauthorize_first_time_connect_writes_attempted_audit_row(monkeypatch):
    """First-time connect (no row) still writes an 'attempted' audit row."""
    mock_db = _make_db(user_row=None)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/spotify/reauthorize")
    assert resp.status_code == 200, resp.text
    assert any(c["action"] == "attempted" for c in audit_calls), audit_calls


def test_reauthorize_google_first_time_connect_still_works():
    """Google's first-time connect path stays intact (start URL, not 404)."""
    mock_db = _make_db(user_row=None)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/reauthorize")
    assert resp.status_code == 200, resp.text
    redirect_url = resp.json()["data"]["redirect_url"]
    assert "/api/oauth/google/start" in redirect_url, redirect_url


def test_reauthorize_404_on_missing_non_oauth_credential():
    """reauthorize returns 404 when no credential exists for a NON-OAuth provider.

    Token / apikey / webhook providers have no OAuth start path — a first-time
    connect for them is established by writing a value, not by an OAuth dance, so
    the missing-credential 404 is preserved.  'github' is a token-kind provider.
    """
    mock_db = _make_db(user_row=None)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/github/reauthorize")
    assert resp.status_code == 404


def test_reauthorize_404_on_unknown_provider():
    """reauthorize returns 404 for an unknown provider with no credential row."""
    mock_db = _make_db(user_row=None)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/not_a_real_provider/reauthorize")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: OAuth token revocation during rotate (bu-ohwbh)
# ---------------------------------------------------------------------------


def test_rotate_google_calls_revoke_url(monkeypatch):
    """Google rotation calls the OAuth revoke endpoint with the old token."""
    import httpx

    row = _make_entity_info_row(info_type="google_oauth_refresh", value="old-token-xyz")
    mock_db = _make_db(user_row=row)

    revoke_calls: list[dict] = []

    async def _fake_post(url, **kwargs):
        revoke_calls.append({"url": str(url), **kwargs})
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 200
        return fake_resp

    # Patch AsyncClient.post — the revoke helper uses `async with httpx.AsyncClient() as c`.
    # We patch the class-level __aenter__ / __aexit__ via AsyncMock so the async context manager
    # yields a fake client with a mocked post().
    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/rotate", json={"value": "new-token-abc"})

    assert resp.status_code == 200
    assert revoke_calls, "Expected at least one call to the Google revoke URL"
    assert "oauth2.googleapis.com/revoke" in revoke_calls[0]["url"]
    # The old token value must be in the POST body (data=), NOT in query params.
    # Sending in query params risks token leakage via proxy/server logs.
    data = revoke_calls[0].get("data", {})
    assert data.get("token") == "old-token-xyz", (
        f"Expected old token in revoke body data, got: {data}"
    )


def test_rotate_google_revoke_failure_does_not_fail_rotation(monkeypatch):
    """Google revoke HTTP failure does NOT cause the rotate endpoint to return non-200."""
    import httpx

    row = _make_entity_info_row(info_type="google_oauth_refresh", value="old-token")
    mock_db = _make_db(user_row=row)

    async def _fake_post(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/rotate", json={"value": "new-token"})

    # Rotation MUST succeed even when revoke fails.
    assert resp.status_code == 200
    assert "data" in resp.json()


def test_rotate_google_revoke_http_non_200_does_not_fail_rotation(monkeypatch):
    """Google revoke HTTP 400 response does NOT cause the rotate endpoint to return non-200."""
    import httpx

    row = _make_entity_info_row(info_type="google_oauth_refresh", value="old-tok")
    mock_db = _make_db(user_row=row)

    async def _fake_post(url, **kwargs):
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 400
        return fake_resp

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/rotate", json={"value": "new-tok"})

    assert resp.status_code == 200


def test_rotate_non_oauth_provider_does_not_call_revoke(monkeypatch):
    """Rotation of a non-OAuth credential (e.g. a plain API key) does NOT call the revoke URL.

    Spotify type 'spotify_api_key' does not match the _OAUTH_TYPE_SUFFIXES, so
    revoke is skipped entirely.
    """
    import httpx

    row = _make_entity_info_row(info_type="spotify_api_key", value="old-api-key")
    mock_db = _make_db(user_row=row)

    revoke_calls: list[dict] = []

    async def _fake_post(url, **kwargs):
        revoke_calls.append({"url": str(url)})
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 200
        return fake_resp

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/spotify/rotate", json={"value": "new-api-key"})

    assert resp.status_code == 200
    assert not revoke_calls, (
        f"Expected no revoke calls for non-OAuth credential, got: {revoke_calls}"
    )


def test_rotate_github_skips_revoke_when_app_creds_absent(monkeypatch):
    """GitHub revocation is skipped (not HTTP-called) when app credentials are not configured.

    GitHub is now in _OAUTH_REVOKE_PROVIDERS.  When GITHUB_OAUTH_CLIENT_ID /
    GITHUB_OAUTH_CLIENT_SECRET are absent from butler_secrets, the revoke helper
    short-circuits and returns 'skipped' without making any HTTP call.
    Rotation still returns 200.
    """
    import httpx

    row = _make_entity_info_row(info_type="github_oauth_access", value="old-github-tok")
    # The default _make_shared_pool returns None for butler_secrets fetches (cred store will
    # find no rows for GITHUB_OAUTH_CLIENT_ID / GITHUB_OAUTH_CLIENT_SECRET).
    mock_db = _make_db(user_row=row)

    http_calls: list[dict] = []

    async def _fake_delete(url, **kwargs):
        http_calls.append({"url": str(url), "method": "DELETE"})
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 204
        return fake_resp

    fake_client = AsyncMock()
    fake_client.delete = AsyncMock(side_effect=_fake_delete)
    fake_client.post = AsyncMock()

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/github/rotate", json={"value": "new-github-tok"})

    assert resp.status_code == 200
    assert not http_calls, (
        f"Expected no HTTP revoke call when GitHub app creds absent, got: {http_calls}"
    )


def test_rotate_audit_note_contains_revoke_status(monkeypatch):
    """Audit note for rotate contains revoke_status= field."""
    import httpx

    row = _make_entity_info_row(info_type="google_oauth_refresh", value="old-tok")
    mock_db = _make_db(user_row=row)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)

    async def _fake_post(url, **kwargs):
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 200
        return fake_resp

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/rotate", json={"value": "new-tok"})
    assert resp.status_code == 200

    rotated = [c for c in audit_calls if c["action"] == "rotated"]
    assert rotated, "Expected 'rotated' audit row"
    note = rotated[0].get("note", "")
    assert "revoke_status=" in note, f"Expected 'revoke_status=' in audit note, got: {note!r}"


def test_rotate_no_revoke_when_new_value_equals_old(monkeypatch):
    """No-op rotation (new value == old value) must NOT call the revoke endpoint.

    Revoking the current token when value is unchanged would invalidate it.
    """
    import httpx

    old_value = "same-token"
    row = _make_entity_info_row(info_type="google_oauth_refresh", value=old_value)
    mock_db = _make_db(user_row=row)

    revoke_calls: list[dict] = []

    async def _fake_post(url, **kwargs):
        revoke_calls.append({"url": str(url)})
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 200
        return fake_resp

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    # Same value as stored — no-op rotation.
    resp = client.post("/api/secrets/user/google/rotate", json={"value": old_value})

    assert resp.status_code == 200
    assert not revoke_calls, (
        f"Expected no revoke when new value equals old value, got: {revoke_calls}"
    )


# ---------------------------------------------------------------------------
# Tests: GitHub OAuth token revocation during rotate (bu-h7b8w)
# ---------------------------------------------------------------------------


def _make_db_with_github_creds(
    *,
    user_row: MagicMock,
    client_id: str = "Iv1.abcdef123456",
    client_secret: str = "gh_cs_secret",
) -> MagicMock:
    """Build a mock DatabaseManager where butler_secrets returns GitHub app creds.

    The shared pool's acquire → conn.fetchrow is patched to return the GitHub
    app credentials when queried by GITHUB_OAUTH_CLIENT_ID/_SECRET keys.
    """
    from contextlib import asynccontextmanager

    shared_pool = AsyncMock()

    async def _fetchrow(sql, *args):
        # entity_info / entities lookup (for user credential fetch).
        if "entity_info" in sql or "entities" in sql:
            return user_row
        # secret_probe_log lookup — return None (no probe history).
        if "secret_probe_log" in sql:
            return None
        return None

    # butler_secrets lookup for CredentialStore.load()
    async def _conn_fetchrow(sql, *args):
        # CredentialStore queries: SELECT secret_value FROM butler_secrets WHERE secret_key = $1
        if "butler_secrets" in sql and args:
            key = args[0]
            if key == "GITHUB_OAUTH_CLIENT_ID":
                row_mock = MagicMock()
                row_mock.__getitem__ = MagicMock(
                    side_effect=lambda k: client_id if k == "secret_value" else None
                )
                return row_mock
            if key == "GITHUB_OAUTH_CLIENT_SECRET":
                row_mock = MagicMock()
                row_mock.__getitem__ = MagicMock(
                    side_effect=lambda k: client_secret if k == "secret_value" else None
                )
                return row_mock
        # entity_info / entities fallback
        if "entity_info" in sql or "entities" in sql:
            return user_row
        return None

    shared_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    shared_pool.fetch = AsyncMock(return_value=[])
    shared_pool.execute = AsyncMock(return_value="UPDATE 1")

    fake_conn = AsyncMock()
    fake_conn.fetchrow = AsyncMock(side_effect=_conn_fetchrow)
    fake_conn.fetch = AsyncMock(return_value=[])
    fake_conn.execute = AsyncMock(return_value="UPDATE 1")
    fake_conn.fetchval = AsyncMock(return_value=1)

    @asynccontextmanager
    async def _transaction():
        yield

    fake_conn.transaction = _transaction

    @asynccontextmanager
    async def _acquire():
        yield fake_conn

    shared_pool.acquire = _acquire

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=AsyncMock())
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)
    return mock_db


def test_rotate_github_calls_delete_revoke_endpoint(monkeypatch):
    """GitHub rotation calls DELETE /applications/{client_id}/grant with Basic auth.

    When GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET are configured in
    butler_secrets, the revoke helper sends:
    - DELETE https://api.github.com/applications/{client_id}/grant
    - HTTP Basic auth (client_id:client_secret)
    - JSON body {"access_token": old_token}
    """
    import httpx

    row = _make_entity_info_row(info_type="github_oauth_access", value="old-gh-tok")
    mock_db = _make_db_with_github_creds(
        user_row=row,
        client_id="Iv1.testclientid",
        client_secret="gh_cs_testsecret",
    )

    http_calls: list[dict] = []

    async def _fake_delete(url, **kwargs):
        http_calls.append({"url": str(url), "kwargs": kwargs})
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 204
        return fake_resp

    fake_client = AsyncMock()
    fake_client.delete = AsyncMock(side_effect=_fake_delete)
    fake_client.post = AsyncMock()

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/github/rotate", json={"value": "new-gh-tok"})

    assert resp.status_code == 200
    assert http_calls, "Expected a DELETE call to GitHub revoke endpoint"

    call = http_calls[0]
    assert "api.github.com/applications/Iv1.testclientid/grant" in call["url"], (
        f"Expected GitHub revoke URL with client_id, got: {call['url']}"
    )
    # Verify Basic auth credentials.
    auth = call["kwargs"].get("auth")
    assert auth == ("Iv1.testclientid", "gh_cs_testsecret"), (
        f"Expected Basic auth (client_id, client_secret), got: {auth}"
    )
    # Verify JSON body contains the old access token.
    json_body = call["kwargs"].get("json", {})
    assert json_body.get("access_token") == "old-gh-tok", (
        f"Expected old token in JSON body, got: {json_body}"
    )
    # Verify required GitHub API headers are present (User-Agent is strictly required
    # by GitHub to avoid 403; X-GitHub-Api-Version pins the API version).
    headers = call["kwargs"].get("headers", {})
    assert headers.get("User-Agent") == "ButlerSecretsManager/1.0", (
        f"Expected User-Agent header, got: {headers}"
    )
    assert headers.get("X-GitHub-Api-Version") == "2022-11-28", (
        f"Expected X-GitHub-Api-Version header, got: {headers}"
    )


def test_rotate_github_revoke_204_returns_succeeded(monkeypatch):
    """GitHub revoke returning HTTP 204 is treated as success ('succeeded')."""
    import httpx

    row = _make_entity_info_row(info_type="github_oauth_access", value="old-tok")
    mock_db = _make_db_with_github_creds(user_row=row)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)

    async def _fake_delete(url, **kwargs):
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 204
        return fake_resp

    fake_client = AsyncMock()
    fake_client.delete = AsyncMock(side_effect=_fake_delete)
    fake_client.post = AsyncMock()

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/github/rotate", json={"value": "new-tok"})

    assert resp.status_code == 200
    rotated = [c for c in audit_calls if c["action"] == "rotated"]
    assert rotated, "Expected 'rotated' audit row"
    note = rotated[0].get("note", "")
    assert "revoke_status=succeeded" in note, (
        f"Expected revoke_status=succeeded in audit note, got: {note!r}"
    )


def test_rotate_github_revoke_failure_does_not_fail_rotation(monkeypatch):
    """GitHub revoke HTTP failure (non-200/204) does NOT fail the rotation (returns 200)."""
    import httpx

    row = _make_entity_info_row(info_type="github_oauth_access", value="old-tok")
    mock_db = _make_db_with_github_creds(user_row=row)

    async def _fake_delete(url, **kwargs):
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 422
        return fake_resp

    fake_client = AsyncMock()
    fake_client.delete = AsyncMock(side_effect=_fake_delete)
    fake_client.post = AsyncMock()

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/github/rotate", json={"value": "new-tok"})

    # Rotation MUST succeed even when GitHub revoke returns non-200.
    assert resp.status_code == 200
    assert "data" in resp.json()


def test_rotate_github_revoke_network_error_does_not_fail_rotation(monkeypatch):
    """GitHub revoke network error does NOT fail the rotation (returns 200)."""
    import httpx

    row = _make_entity_info_row(info_type="github_oauth_access", value="old-tok")
    mock_db = _make_db_with_github_creds(user_row=row)

    async def _fake_delete(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    fake_client = AsyncMock()
    fake_client.delete = AsyncMock(side_effect=_fake_delete)
    fake_client.post = AsyncMock()

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/github/rotate", json={"value": "new-tok"})

    assert resp.status_code == 200
    assert "data" in resp.json()
