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

import logging
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import butlers.api.routers.secrets_v2 as _secrets_v2
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


def _make_rotate_returning_row(row: MagicMock) -> MagicMock:
    """Build the deliberately non-secret row returned by rotation UPDATE."""
    return _make_row(
        id=row["id"],
        entity_id=row["entity_id"],
        type=row["type"],
        label=row["label"],
        created_at=row["created_at"],
        last_verified=row["last_verified"],
        last_test_ok=row["last_test_ok"],
        last_test_code=row["last_test_code"],
        last_test_message=row["last_test_message"],
    )


def _make_shared_pool(
    *,
    user_row: MagicMock | None = None,
    probe_row: MagicMock | None = None,
    execute_ok: bool = True,
    oauth_app_configured: bool = False,
    rotate_update_error: Exception | None = None,
) -> AsyncMock:
    """Build a mock shared-pool that supports fetchrow, execute, and transaction.

    ``oauth_app_configured`` controls the ``butler_secrets`` lookup backing
    CredentialStore.load — i.e. whether the deployment has this provider's
    ``*_OAUTH_CLIENT_ID`` / ``*_OAUTH_CLIENT_SECRET`` stored.  reauthorize
    pre-flights those before handing back a start URL.
    """
    shared_pool = AsyncMock()
    rotate_returning_row = _make_rotate_returning_row(user_row) if user_row is not None else None

    async def _fetchrow(sql, *args):
        if "secret_probe_log" in sql:
            return probe_row
        if "butler_secrets" in sql:
            return _make_row(secret_value="app-cred") if oauth_app_configured else None
        if "entity_info" in sql or "entities" in sql:
            return user_row
        return None

    shared_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    shared_pool.fetch = AsyncMock(return_value=[])

    if execute_ok:
        shared_pool.execute = AsyncMock(return_value="UPDATE 1")
    else:
        shared_pool.execute = AsyncMock(side_effect=Exception("DB error"))

    acquired_active = False
    transaction_active = False
    transaction_query_trace: list[tuple[str, bool, bool]] = []

    async def _transaction_fetchrow(sql, *args):
        if "public.entity_info" not in sql:
            return await _fetchrow(sql, *args)
        transaction_query_trace.append((sql, acquired_active, transaction_active))
        assert acquired_active and transaction_active, (
            "transaction fetchrow must run while the acquired transaction is active"
        )
        if "UPDATE public.entity_info" in sql:
            if rotate_update_error is not None:
                raise rotate_update_error
            return rotate_returning_row
        return await _fetchrow(sql, *args)

    # Keep transaction connection spies independent from the pool so tests can
    # prove locked reads and updates never fall back to pool-level calls.
    fake_conn = AsyncMock()
    fake_conn.fetchrow = AsyncMock(side_effect=_transaction_fetchrow)
    fake_conn.fetch = AsyncMock(return_value=[])
    fake_conn.execute = (
        AsyncMock(return_value="UPDATE 1")
        if execute_ok
        else AsyncMock(side_effect=Exception("DB error"))
    )
    fake_conn.fetchval = AsyncMock(return_value=1)

    # Fake transaction() context manager.
    @asynccontextmanager
    async def _transaction():
        nonlocal transaction_active
        transaction_active = True
        try:
            yield
        finally:
            transaction_active = False

    fake_conn.transaction = MagicMock(side_effect=_transaction)

    @asynccontextmanager
    async def _acquire():
        nonlocal acquired_active
        acquired_active = True
        try:
            yield fake_conn
        finally:
            acquired_active = False

    shared_pool.acquire = MagicMock(side_effect=_acquire)
    shared_pool._transaction_connection = fake_conn
    shared_pool._transaction_query_trace = transaction_query_trace
    shared_pool._rotate_returning_row = rotate_returning_row

    return shared_pool


def _make_db(
    *,
    user_row: MagicMock | None = None,
    probe_row: MagicMock | None = None,
    shared_pool_available: bool = True,
    execute_ok: bool = True,
    oauth_app_configured: bool = False,
    rotate_update_error: Exception | None = None,
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
            oauth_app_configured=oauth_app_configured,
            rotate_update_error=rotate_update_error,
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


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("get", "/api/secrets/user/spotify", None),
        ("post", "/api/secrets/user/spotify/rotate", {"value": "never-written"}),
        ("post", "/api/secrets/user/spotify/disconnect", None),
        ("post", "/api/secrets/user/spotify/probe", None),
        ("post", "/api/secrets/user/spotify/reauthorize", None),
    ],
)
def test_all_generic_spotify_routes_reject_before_database_lookup(
    method: str, path: str, json_body: dict | None
) -> None:
    mock_db = _make_db(user_row=_make_entity_info_row(info_type="spotify_oauth_access"))
    client = _build_app(mock_db)

    response = client.request(method, path, json=json_body)

    assert response.status_code == 404
    assert response.json() == {"detail": "Credential not found"}
    mock_db.credential_shared_pool.assert_not_called()


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
    # The rotate response reuses the content-blind detail payload, so the
    # persisted credential type never rides along with it.
    assert "type" not in body["data"]
    assert "google_oauth_refresh" not in resp.text

    rotated = [c for c in audit_calls if c["action"] == "rotated"]
    assert rotated, f"Expected 'rotated' audit action; got: {audit_calls}"
    assert rotated[0].get("target") == "u:google"


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/secrets/user/openai/rotate", {"value": "replacement-value"}),
        ("/api/secrets/user/openai/disconnect", None),
        ("/api/secrets/user/openai/probe", None),
    ],
)
def test_user_mutations_do_not_require_detail_audit_history(
    path: str, payload: dict[str, str] | None
) -> None:
    """Audit-history strictness is isolated to the user-detail GET route."""
    row = _make_entity_info_row(info_type="openai_api_key", value="")
    mock_db = _make_db(user_row=row)
    shared_pool = mock_db.credential_shared_pool()

    async def _fetch(sql: str, *_args: object) -> list[object]:
        if "public.audit_log" in sql:
            raise RuntimeError("audit source unavailable")
        return []

    shared_pool.fetch = AsyncMock(side_effect=_fetch)
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(path, json=payload)

    assert response.status_code == 200, response.text
    assert not any("public.audit_log" in call.args[0] for call in shared_pool.fetch.await_args_list)


def test_rotate_404_on_missing_credential():
    """rotate returns 404 when no credential exists for the provider."""
    mock_db = _make_db(user_row=None)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/spotify/rotate", json={"value": "tok"})
    assert resp.status_code == 404


def test_rotate_missing_non_spotify_credential_locks_then_returns_404_without_update(monkeypatch):
    """The ordinary 404 is decided by the locked lookup before any write."""
    _stub_revoke(monkeypatch)
    mock_db = _make_db(user_row=None)
    shared_pool = mock_db.credential_shared_pool()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/openai/rotate", json={"value": "replacement"})

    assert resp.status_code == 404
    entity_info_sql = _matching(shared_pool, _ENTITY_INFO_SQL)
    assert len(entity_info_sql) == 1
    assert "FOR UPDATE" in entity_info_sql[0]
    assert not any("UPDATE public.entity_info" in sql for sql in entity_info_sql)


def test_rotate_rejects_telegram_api_hash_via_telegram_bot_alias_before_update():
    """The generic rotate route must not bypass Telegram's guided setup.

    Passport groups the legacy ``telegram_api_hash`` row under the
    ``telegram_bot`` provider alias.  Reject after resolving that row but
    before any write, so an alias request cannot replace the hash directly.
    """
    row = _make_entity_info_row(
        info_type="telegram_api_hash",
        value="old-api-hash",
        last_test_ok=True,
    )
    mock_db = _make_db(user_row=row)
    shared_pool = mock_db.credential_shared_pool.return_value
    client = _build_app(mock_db)

    resp = client.post(
        "/api/secrets/user/telegram_bot/rotate",
        json={"value": "replacement-api-hash"},
    )

    assert resp.status_code == 422
    assert resp.json()["detail"] == (
        "telegram_api_hash can only be updated through the guided Telegram session setup."
    )
    shared_pool.execute.assert_not_awaited()
    assert not any("UPDATE public.entity_info" in sql for sql in _issued_sql(shared_pool))


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
    verified = next(call for call in audit_calls if call["action"] == "verified")
    assert verified["result"] == "success"
    assert verified["error"] is None


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
    failed = next(call for call in audit_calls if call["action"] == "failed")
    assert failed["result"] == "error"
    assert failed["error"] == "Token revoked"


def test_probe_404_on_missing_credential():
    """probe returns 404 when no credential exists for the provider."""
    mock_db = _make_db(user_row=None)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/spotify/probe")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: POST /api/secrets/user/<provider>/reauthorize
# ---------------------------------------------------------------------------


def test_reauthorize_does_not_require_detail_audit_history():
    """A detail-audit outage must not block configured-Google reauthorization."""
    row = _make_entity_info_row(info_type="google_oauth_refresh")
    mock_db = _make_db(user_row=row, oauth_app_configured=True)
    shared_pool = mock_db.credential_shared_pool()

    async def _fetch(sql: str, *_args: object) -> list[object]:
        if "public.audit_log" in sql:
            raise RuntimeError("audit source unavailable")
        return []

    shared_pool.fetch = AsyncMock(side_effect=_fetch)
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/secrets/user/google/reauthorize")

    assert response.status_code == 200, response.text
    assert not any("public.audit_log" in call.args[0] for call in shared_pool.fetch.await_args_list)


def test_reauthorize_returns_200_with_redirect_url():
    """reauthorize returns 200 with ApiResponse<{redirect_url}>; the redirect points to
    the API-relative /oauth/<provider>/start (NO "/api" prefix — the client
    prepends its own deployment-specific API base), carries page_of_origin=secrets,
    and includes an opaque account_ref when the credential has a stored account.

    The reference used to be the account email itself (``account_hint=<label>``);
    bu-nz4sn replaced it with the credential's entity UUID, which
    /oauth/google/start resolves to the stored hint server-side.  Content-blindness
    of that swap is asserted in test_secrets_v2_probe_reauthorize_content_blind.py;
    what this test pins is that a reference is still emitted at all.
    """
    entity_id = str(uuid4())
    row = _make_entity_info_row(
        info_type="google_oauth_refresh", entity_id=entity_id, label="user@example.com"
    )
    mock_db = _make_db(user_row=row, oauth_app_configured=True)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/reauthorize")
    assert resp.status_code == 200
    redirect_url = resp.json()["data"]["redirect_url"]
    assert redirect_url.startswith("/oauth/google/start"), redirect_url
    assert "page_of_origin=secrets" in redirect_url, redirect_url
    assert f"account_ref={entity_id}" in redirect_url, (
        f"Expected account_ref in redirect_url: {redirect_url!r}"
    )


def test_reauthorize_redirect_url_carries_no_api_prefix():
    """redirect_url must be API-relative, never rooted at a hardcoded "/api".

    Regression: the endpoint used to return "/api/oauth/<provider>/start", which
    the browser navigated to verbatim.  The dashboard is served behind
    deployment-specific path mounts (/butlers → API at /butlers-api/api,
    /butlers-dev → API at /butlers-dev-api/api), where no "/api" route exists —
    so clicking "connect Spotify" landed on a dead URL.  The API mount point is
    the client's knowledge, not the backend's: return the path below the API
    root and let the client prepend its own base.
    """
    mock_db = _make_db(user_row=None, oauth_app_configured=True)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/reauthorize")
    assert resp.status_code == 200, resp.text
    redirect_url = resp.json()["data"]["redirect_url"]
    assert not redirect_url.startswith("/api/"), (
        f"redirect_url must not hardcode an /api prefix: {redirect_url!r}"
    )


def test_spotify_reauthorize_is_hidden_from_generic_secrets(monkeypatch):
    """No app credentials → 503 with the actionable "configure <KEY>" detail, and
    no 'attempted' audit row.

    The caller navigates the browser to redirect_url, so letting the start
    endpoint raise this 503 would paint a raw JSON page outside the dashboard.
    Pre-flighting it here keeps the message inside the Secrets page, and keeps
    the audit trail honest: nothing was attempted.
    """
    mock_db = _make_db(user_row=None, oauth_app_configured=False)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"action": action})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/spotify/reauthorize")
    assert resp.status_code == 404, resp.text
    assert audit_calls == [], audit_calls


def test_reauthorize_writes_attempted_audit_row(monkeypatch):
    """reauthorize appends an 'attempted' audit row to public.audit_log."""
    row = _make_entity_info_row(info_type="google_oauth_refresh")
    mock_db = _make_db(user_row=row, oauth_app_configured=True)

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


def test_reauthorize_first_time_spotify_cannot_use_generic_oauth():
    """Spotify first-time connect cannot invoke the generic OAuth starter."""
    mock_db = _make_db(user_row=None, oauth_app_configured=True)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/spotify/reauthorize")
    assert resp.status_code == 404, resp.text


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
    mock_db = _make_db(user_row=None, oauth_app_configured=True)

    audit_calls: list[dict] = []

    async def _fake_append(pool, actor, action, **kwargs):
        audit_calls.append({"actor": actor, "action": action, **kwargs})
        return 1

    import butlers.api.routers.audit as _audit_mod

    monkeypatch.setattr(_audit_mod, "append", _fake_append)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/reauthorize")
    assert resp.status_code == 200, resp.text
    assert any(c["action"] == "attempted" for c in audit_calls), audit_calls


def test_reauthorize_google_first_time_connect_still_works():
    """Google's first-time connect path stays intact (start URL, not 404)."""
    mock_db = _make_db(user_row=None, oauth_app_configured=True)
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/reauthorize")
    assert resp.status_code == 200, resp.text
    redirect_url = resp.json()["data"]["redirect_url"]
    assert redirect_url.startswith("/oauth/google/start"), redirect_url


@pytest.mark.parametrize("action", ["rotate", "disconnect", "probe", "reauthorize"])
def test_spotify_lifecycle_is_not_addressable_through_generic_secrets(action):
    row = _make_entity_info_row(info_type="spotify_oauth_refresh")
    mock_db = _make_db(user_row=row, oauth_app_configured=True)
    client = _build_app(mock_db)

    kwargs = {"json": {"value": "replacement"}} if action == "rotate" else {}
    resp = client.post(f"/api/secrets/user/spotify/{action}", **kwargs)

    assert resp.status_code == 404


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

    row = _make_entity_info_row(info_type="github_api_key", value="old-api-key")
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
    resp = client.post("/api/secrets/user/github/rotate", json={"value": "new-api-key"})

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


# ---------------------------------------------------------------------------
# Tests: mutation reads skip the scope-evidence queries (bu-psw7o)
# ---------------------------------------------------------------------------
# The content-blind projection turns `scopes_required` / `scopes_granted` into
# `capabilities_required` / `capabilities_granted`, and only two callers
# publish that: `GET /user/{provider}` and the content-blind response record
# built after `/rotate`'s update. Every other user-credential mutation read
# discards the two queries behind those fields, so they do not run there.
#
# These tests spy on the SQL actually issued through the pool and acquired
# connections rather than on the response shape: the responses were already
# correct before the skip, so only the issued-SQL set can show the work stopped.
# ---------------------------------------------------------------------------

#: `_fetch_scopes_required_by_provider` — the provider→required-scopes catalogue.
_CATALOGUE_SQL = "public.provider_feature_catalogue"
#: `_fetch_google_granted_scopes` — per-account granted scopes.
_GRANTED_SCOPES_SQL = "granted_scopes"
#: `_fetch_google_test_mode_expiry` — same table, different column; NOT skipped,
#: because `state` is derived from the expiry it returns.
_TEST_MODE_EXPIRY_SQL = "last_token_refresh_at"
#: `_fetch_probe_log` — the aggregate, bare-key probe result used by detail reads.
_AGGREGATE_PROBE_LOG_SQL = "AND credential_key = $2"
_ENTITY_INFO_SQL = "public.entity_info"
_SAFE_ROTATE_RETURNING_COLUMNS = (
    "id",
    "entity_id",
    "type",
    "label",
    "created_at",
    "last_verified",
    "last_test_ok",
    "last_test_code",
    "last_test_message",
)
_RETURNING_COLUMN = re.compile(
    r'(?:[A-Za-z_][A-Za-z0-9_]*\.)?(?:"(?P<quoted>[A-Za-z_][A-Za-z0-9_]*)"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))\Z'
)
_RETURNING_PROJECTION = re.compile(r"\bRETURNING\b(?P<projection>.+)", re.IGNORECASE | re.DOTALL)


def _issued_sql(shared_pool: AsyncMock) -> list[str]:
    """Every SQL string issued through the shared-pool test double.

    Mutation routes may use ``fetchrow``/``execute`` through an acquired
    connection while read paths use ``fetch`` directly. Keeping all three here
    makes a query-count assertion describe the route's actual round trips.
    """
    transaction_connection = getattr(shared_pool, "_transaction_connection", None)
    methods = [shared_pool.fetch, shared_pool.fetchrow, shared_pool.execute]
    if transaction_connection is not None:
        methods.extend(
            [
                transaction_connection.fetch,
                transaction_connection.fetchrow,
                transaction_connection.execute,
            ]
        )
    return [
        call.args[0]
        for method in methods
        for call in method.await_args_list
        if call.args and isinstance(call.args[0], str)
    ]


def _matching(shared_pool: AsyncMock, marker: str) -> list[str]:
    return [sql for sql in _issued_sql(shared_pool) if marker in sql]


def _capability_probe_log_sql(shared_pool: AsyncMock) -> list[str]:
    """SQL fetches whose issued key list targets capability-qualified probes."""
    return [
        call.args[0]
        for call in shared_pool.fetch.await_args_list
        if len(call.args) >= 3
        and "FROM public.secret_probe_log" in call.args[0]
        and any(isinstance(key, str) and ":" in key for key in call.args[2])
    ]


def _assert_safe_rotate_returning_projection(sql: str) -> None:
    """Require the rotation UPDATE to return exactly its safe internal record."""
    match = _RETURNING_PROJECTION.search(sql)
    assert match is not None, "rotation UPDATE must return a non-secret record"
    projection = match.group("projection").strip().rstrip(";").strip()
    columns: list[str] = []
    for expression in projection.split(","):
        column = _RETURNING_COLUMN.fullmatch(expression.strip())
        assert column is not None, f"unsafe rotation RETURNING expression: {expression!r}"
        columns.append((column.group("quoted") or column.group("bare")).lower())
    assert tuple(columns) == _SAFE_ROTATE_RETURNING_COLUMNS


def _stub_revoke(monkeypatch) -> None:
    """Keep provider revocation off the network; these tests only watch SQL."""

    async def _skipped(*_args: object, **_kwargs: object) -> str:
        return "skipped"

    monkeypatch.setattr(_secrets_v2, "_revoke_oauth_token", _skipped)


@pytest.mark.parametrize(
    "unsafe_projection",
    ("value", "ei.value", '"value"', "*", "ei.*"),
)
def test_rotate_returning_projection_guard_rejects_credential_bearing_forms(unsafe_projection):
    """Every spelling that could return a credential value must fail the guard."""
    sql = """
        UPDATE public.entity_info
        SET value = $1
        WHERE id = $2
        RETURNING
            id,
            entity_id,
            type,
            label,
            created_at,
            last_verified,
            last_test_ok,
            last_test_code,
            last_test_message
    """
    _assert_safe_rotate_returning_projection(sql)
    mutated = sql.replace("id,", f"id, {unsafe_projection},", 1)

    with pytest.raises(AssertionError):
        _assert_safe_rotate_returning_projection(mutated)


def test_rotate_uses_two_entity_info_round_trips_with_returning(monkeypatch):
    """Rotation reads a locked row then updates it without a post-write re-read."""
    _stub_revoke(monkeypatch)
    mock_db = _make_db(user_row=_make_entity_info_row(info_type="google_oauth_refresh"))
    shared_pool = mock_db.credential_shared_pool()
    client = _build_app(mock_db)

    response = client.post("/api/secrets/user/google/rotate", json={"value": "replacement"})

    assert response.status_code == 200, response.text
    entity_info_sql = _matching(shared_pool, _ENTITY_INFO_SQL)
    assert len(entity_info_sql) == 2
    assert "FOR UPDATE" in entity_info_sql[0]
    assert "UPDATE" in entity_info_sql[1]
    _assert_safe_rotate_returning_projection(entity_info_sql[1])
    with pytest.raises(KeyError):
        shared_pool._rotate_returning_row["value"]
    assert "value" not in response.json()["data"]
    assert "replacement" not in response.text


def test_rotate_locks_and_updates_on_one_transactional_connection(monkeypatch):
    """The 404/type decision and write cannot be separated across pool calls."""
    _stub_revoke(monkeypatch)
    mock_db = _make_db(user_row=_make_entity_info_row(info_type="google_oauth_refresh"))
    shared_pool = mock_db.credential_shared_pool()
    client = _build_app(mock_db)

    response = client.post("/api/secrets/user/google/rotate", json={"value": "replacement"})

    assert response.status_code == 200, response.text
    shared_pool.acquire.assert_called_once()
    connection = shared_pool._transaction_connection
    connection.transaction.assert_called_once()
    entity_info_sql = [
        call.args[0]
        for call in connection.fetchrow.await_args_list
        if call.args and _ENTITY_INFO_SQL in call.args[0]
    ]
    assert len(entity_info_sql) == 2
    assert "FOR UPDATE" in entity_info_sql[0]
    assert "UPDATE" in entity_info_sql[1] and "RETURNING" in entity_info_sql[1]
    transaction_query_trace = shared_pool._transaction_query_trace
    assert [query for query, _, _ in transaction_query_trace] == entity_info_sql
    assert all(acquired and active for _, acquired, active in transaction_query_trace)
    pool_entity_info_sql = [
        call.args[0]
        for call in shared_pool.fetchrow.await_args_list
        if call.args and _ENTITY_INFO_SQL in call.args[0]
    ]
    assert pool_entity_info_sql == []


def test_rotate_update_error_is_content_blind_in_logs_and_response(caplog, monkeypatch):
    """A DB exception after binding a new credential must not expose it."""
    supplied_value = f"credential-{uuid4()}"
    driver_text = f"driver echoed bound value {supplied_value}"
    mock_db = _make_db(
        user_row=_make_entity_info_row(info_type="google_oauth_refresh", value="old-token"),
        rotate_update_error=RuntimeError(driver_text),
    )
    client = _build_app(mock_db)

    with caplog.at_level(logging.WARNING, logger="butlers.api.routers.secrets_v2"):
        response = client.post("/api/secrets/user/google/rotate", json={"value": supplied_value})

    assert response.status_code == 503
    assert response.json() == {"detail": "Credential rotation failed"}
    assert supplied_value not in response.text
    assert driver_text not in response.text
    assert supplied_value not in caplog.text
    assert driver_text not in caplog.text


def test_rotate_loads_scope_evidence_only_for_the_published_read(monkeypatch):
    """The locked mutation read skips scopes; the response record loads them.

    The lock/update unit supplies the old type and value for the in-place write
    and provider revoke — it never reaches `_content_blind_detail`. The record
    built from UPDATE RETURNING supplies the published capability evidence, so
    exactly one catalogue read and one granted-scopes read may happen.
    """
    _stub_revoke(monkeypatch)
    row = _make_entity_info_row(info_type="google_oauth_refresh", value="old-tok")
    mock_db = _make_db(user_row=row)
    shared_pool = mock_db.credential_shared_pool()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/rotate", json={"value": "new-tok"})

    assert resp.status_code == 200, resp.text
    assert len(_matching(shared_pool, _CATALOGUE_SQL)) == 1, (
        "rotate must read the scope catalogue only for the response built from UPDATE RETURNING"
    )
    assert len(_matching(shared_pool, _GRANTED_SCOPES_SQL)) == 1, (
        "rotate must read granted scopes only for the response built from UPDATE RETURNING"
    )
    # The evidence still ships: an unloaded record would have raised instead.
    assert "capabilities_required" in resp.json()["data"]


def test_disconnect_issues_no_scope_evidence_queries(monkeypatch):
    """disconnect returns a bare status; nothing it publishes needs scopes."""
    _stub_revoke(monkeypatch)
    row = _make_entity_info_row(info_type="google_oauth_refresh", value="tok")
    mock_db = _make_db(user_row=row)
    shared_pool = mock_db.credential_shared_pool()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/disconnect")

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"status": "disconnected"}
    assert _matching(shared_pool, _CATALOGUE_SQL) == []
    assert _matching(shared_pool, _GRANTED_SCOPES_SQL) == []


def test_probe_issues_no_scope_evidence_queries_but_keeps_the_expiry_read():
    """probe publishes a TestResult, which carries no capability evidence.

    The Google test-mode expiry read stays: `detail.state` is derived from it,
    and probe branches on that state.  Asserting it is still issued is what
    keeps this a scope-only skip rather than a blanket one.
    """
    row = _make_entity_info_row(info_type="google_oauth_refresh", last_test_ok=True, value="tok")
    mock_db = _make_db(user_row=row)
    shared_pool = mock_db.credential_shared_pool()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/probe")

    assert resp.status_code == 200, resp.text
    assert _matching(shared_pool, _CATALOGUE_SQL) == []
    assert _matching(shared_pool, _GRANTED_SCOPES_SQL) == []
    assert _matching(shared_pool, _TEST_MODE_EXPIRY_SQL), (
        "probe still needs the test-mode expiry read that `state` is derived from"
    )


def test_reauthorize_issues_no_scope_evidence_queries():
    """reauthorize publishes a redirect URL; it reads only presence and label."""
    entity_id = str(uuid4())
    row = _make_entity_info_row(info_type="google_oauth_refresh", entity_id=entity_id)
    mock_db = _make_db(user_row=row, oauth_app_configured=True)
    shared_pool = mock_db.credential_shared_pool()
    client = _build_app(mock_db)

    resp = client.post("/api/secrets/user/google/reauthorize")

    assert resp.status_code == 200, resp.text
    assert f"account_ref={entity_id}" in resp.json()["data"]["redirect_url"]
    assert _matching(shared_pool, _CATALOGUE_SQL) == []
    assert _matching(shared_pool, _GRANTED_SCOPES_SQL) == []


@pytest.mark.parametrize(
    ("path", "json_body", "oauth_app_configured", "expected_evidence_reads"),
    [
        pytest.param(
            "/api/secrets/user/google/rotate",
            {"value": "new-tok"},
            False,
            1,
            id="rotate-keeps-only-published-response-evidence",
        ),
        pytest.param(
            "/api/secrets/user/google/disconnect",
            None,
            False,
            0,
            id="disconnect-skips-discarded-evidence",
        ),
        pytest.param(
            "/api/secrets/user/google/probe",
            None,
            False,
            0,
            id="probe-skips-discarded-evidence",
        ),
        pytest.param(
            "/api/secrets/user/google/reauthorize",
            None,
            True,
            0,
            id="reauthorize-skips-discarded-evidence",
        ),
    ],
)
def test_mutation_reads_skip_discarded_probe_and_capability_evidence(
    monkeypatch,
    path: str,
    json_body: dict[str, str] | None,
    oauth_app_configured: bool,
    expected_evidence_reads: int,
):
    """Mutation pre-reads fetch only evidence their response can publish.

    ``rotate`` builds its content-blind response from UPDATE RETURNING, so that
    response build still needs aggregate and capability probe evidence. Its
    locked lookup — and every read for the other three mutations — discards both
    fields and must issue neither SQL query.
    """
    if path.endswith(("/rotate", "/disconnect")):
        _stub_revoke(monkeypatch)
    row = _make_entity_info_row(info_type="google_oauth_refresh", value="old-tok")
    mock_db = _make_db(user_row=row, oauth_app_configured=oauth_app_configured)
    shared_pool = mock_db.credential_shared_pool()
    client = _build_app(mock_db)

    response = client.post(path, json=json_body)

    assert response.status_code == 200, response.text
    assert {
        "aggregate": len(_matching(shared_pool, _AGGREGATE_PROBE_LOG_SQL)),
        "capability": len(_capability_probe_log_sql(shared_pool)),
    } == {"aggregate": expected_evidence_reads, "capability": expected_evidence_reads}


@pytest.mark.asyncio
async def test_single_user_secret_loads_probe_evidence_by_default():
    """Read callers retain truthful detail evidence unless they opt out."""
    row = _make_entity_info_row(info_type="google_oauth_refresh", value="tok")
    shared_pool = _make_shared_pool(user_row=row)

    record = await _secrets_v2._fetch_single_user_secret(
        shared_pool,
        provider="google",
        identity=None,
    )

    assert record is not None
    assert record.probe_log_loaded is True
    assert record.capabilities_loaded is True
    assert len(_matching(shared_pool, _AGGREGATE_PROBE_LOG_SQL)) == 1
    assert len(_capability_probe_log_sql(shared_pool)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "skipped_option",
        "loaded_field",
        "required_flag",
        "expected_aggregate_reads",
        "expected_capability_reads",
    ),
    [
        pytest.param(
            "include_probe_log",
            "probe_log_loaded",
            "include_probe_log=True",
            0,
            1,
            id="probe-log",
        ),
        pytest.param(
            "include_capabilities",
            "capabilities_loaded",
            "include_capabilities=True",
            1,
            0,
            id="capabilities",
        ),
    ],
)
async def test_single_user_secret_marks_skipped_evidence_unprojectable(
    skipped_option: str,
    loaded_field: str,
    required_flag: str,
    expected_aggregate_reads: int,
    expected_capability_reads: int,
):
    """Helper skip flags must carry through to the content-blind boundary."""
    row = _make_entity_info_row(info_type="google_oauth_refresh", value="tok")
    shared_pool = _make_shared_pool(user_row=row)

    record = await _secrets_v2._fetch_single_user_secret(
        shared_pool,
        provider="google",
        identity=None,
        **{skipped_option: False},
    )

    assert record is not None
    assert getattr(record, loaded_field) is False
    assert len(_matching(shared_pool, _AGGREGATE_PROBE_LOG_SQL)) == expected_aggregate_reads
    assert len(_capability_probe_log_sql(shared_pool)) == expected_capability_reads
    with pytest.raises(ValueError, match=required_flag):
        _secrets_v2._content_blind_detail(record)


def test_content_blind_detail_refuses_a_record_whose_scopes_were_skipped():
    """The skip must never be projected as honest-empty capability evidence.

    Empty `capabilities_granted` is documented to mean "nothing is recorded".
    A record read with `include_scopes=False` cannot support that claim, so the
    projection rejects it instead of publishing a gap as a fact.
    """
    skipped = _secrets_v2._UserCredentialRecord(
        id=str(uuid4()),
        entity_id=str(uuid4()),
        type="google_oauth_refresh",
        provider="google",
        state="ok",
        scopes_loaded=False,
    )

    with pytest.raises(ValueError, match="include_scopes=True"):
        _secrets_v2._content_blind_detail(skipped)


@pytest.mark.parametrize(
    ("skipped_field", "required_flag"),
    [
        pytest.param("probe_log_loaded", "include_probe_log=True", id="probe-log"),
        pytest.param("capabilities_loaded", "include_capabilities=True", id="capabilities"),
    ],
)
def test_content_blind_detail_refuses_a_record_with_skipped_evidence_read(
    skipped_field: str,
    required_flag: str,
):
    """A skipped read must never become an honest-empty evidence payload."""
    skipped = _secrets_v2._UserCredentialRecord(
        id=str(uuid4()),
        entity_id=str(uuid4()),
        type="google_oauth_refresh",
        provider="google",
        state="ok",
        **{skipped_field: False},
    )

    with pytest.raises(ValueError, match=required_flag):
        _secrets_v2._content_blind_detail(skipped)
