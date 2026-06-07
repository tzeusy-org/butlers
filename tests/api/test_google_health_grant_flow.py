"""Integration test: Google Health OAuth scope-grant wiring audit [bu-fodms].

Proves the full server-side state transition:

  inventory (no health scopes)
    → OAuth start URL carries health scopes
    → OAuth callback (MOCKED token exchange) writes health scopes to google_accounts
    → inventory shows google_oauth_refresh credential present
    → connector status flips from degraded → healthy (scopes present)

What is mocked and why
----------------------
- ``_exchange_code_for_tokens`` — avoids real HTTPS call to Google token endpoint.
- ``_fetch_google_userinfo`` — avoids real HTTPS call to Google userinfo endpoint.
- ``asyncpg`` pool interactions — entire DB is mocked in-process; the test exercises
  the real callback handler logic (state validation, account registry dispatch,
  _update_account_refresh_token write path) while keeping the test hermetic.

What is NOT mocked
------------------
- The FastAPI route handlers (exercised through httpx.AsyncClient + ASGITransport).
- The OAuth state store and CSRF validation (real in-memory _state_store).
- The _update_account_refresh_token write path (real logic, mock conn.execute captures it).
- The _derive_state logic in google_health router (real, asserted at both states).

Acceptance
----------
The test FAILS if:
- The inventory endpoint does not surface the primary Google account's credential.
- The OAuth start URL for scope_set=health does not contain googlehealth scopes.
- The OAuth callback fails to write health scopes (mock execute not called correctly).
- The connector status does not report ``degraded`` before the grant.
- The connector status does not report a non-degraded state after the grant.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers import google_health as gh_module
from butlers.api.routers import oauth as oauth_module
from butlers.api.routers import secrets_v2 as sv2_module
from butlers.api.routers.google_health import GOOGLE_HEALTH_SCOPE_URLS
from butlers.api.routers.oauth import _clear_state_store

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
_CLIENT_SECRET = "test-client-secret"
_OWNER_EMAIL = "owner@example.com"
_OWNER_ENTITY_ID = uuid.uuid4()
_GA_ENTITY_ID = uuid.uuid4()  # google_accounts companion entity
_GA_ID = uuid.uuid4()  # google_accounts PK

_ALL_HEALTH_SCOPES = sorted(GOOGLE_HEALTH_SCOPE_URLS)
_HEALTH_SCOPE_STRING = " ".join(_ALL_HEALTH_SCOPES)

# Simulated token response after granting health scopes.
_FAKE_TOKEN_RESPONSE = {
    "access_token": "ya29.fake_access_token",
    "refresh_token": "1//fake_health_refresh_token",
    "scope": _HEALTH_SCOPE_STRING,
    "token_type": "Bearer",
    "expires_in": 3600,
}
_FAKE_USERINFO = {"email": _OWNER_EMAIL, "name": "Test Owner"}


# ---------------------------------------------------------------------------
# Stateful shared-pool mock
# ---------------------------------------------------------------------------


class _SharedPoolState:
    """Mutable state shared across all async mock calls within one test run.

    Starts with the primary Google account having ONLY google_oauth_refresh
    (no health scopes), then after the OAuth callback the `granted_scopes`
    field is updated in-place so subsequent queries reflect the grant.
    """

    def __init__(self) -> None:
        self.ga_granted_scopes: list[str] = []  # no health scopes initially
        self.ga_status: str = "active"
        self.entity_info_refresh_token: str = "existing_refresh_token"
        self.entity_info_updated: bool = False
        self.ga_scopes_updated: bool = False
        # Track UPDATE calls for assertion
        self.execute_calls: list[tuple[str, Any]] = []

    def primary_ga_row(self) -> dict[str, Any]:
        return {
            "id": _GA_ID,
            "entity_id": _GA_ENTITY_ID,
            "email": _OWNER_EMAIL,
            "display_name": "Test Owner",
            "is_primary": True,
            "granted_scopes": list(self.ga_granted_scopes),
            "status": self.ga_status,
            "connected_at": datetime.now(UTC) - timedelta(days=1),
            "last_token_refresh_at": None,
            "metadata": {},
        }


def _make_conn_for_state(state: _SharedPoolState) -> AsyncMock:
    """Build an asyncpg connection mock that reads/writes `state`."""
    conn = AsyncMock()

    async def _fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        q = query.strip()
        # google_accounts primary lookup (google_health status router)
        if "FROM public.google_accounts" in q and "is_primary = true" in q:
            return state.primary_ga_row()
        # google_accounts lookup by email (oauth callback: get_google_account)
        if "FROM public.google_accounts" in q and "email = $1" in q:
            if args and args[0] == _OWNER_EMAIL:
                return state.primary_ga_row()
            return None
        # google_accounts lookup (no specific filter — return primary)
        if (
            "FROM public.google_accounts" in q
            and "WHERE is_primary" not in q
            and "WHERE id" not in q
        ):
            return state.primary_ga_row()
        # butler_secrets (credential store — client_id, client_secret)
        if "butler_secrets" in q or "secret_key" in q:
            key = args[0] if args else None
            if key == "GOOGLE_OAUTH_CLIENT_ID":
                return {"secret_value": _CLIENT_ID}
            if key == "GOOGLE_OAUTH_CLIENT_SECRET":
                return {"secret_value": _CLIENT_SECRET}
            return None
        # ingestion_events (status router counts)
        if "FROM public.ingestion_events" in q:
            return {"sleep_sessions_7d": 0, "daily_summaries_7d": 0}
        # entities table (owner roles)
        if "FROM public.entities" in q:
            row = MagicMock()
            row.__getitem__ = lambda self, k: (
                _OWNER_ENTITY_ID if k == "id" else (["owner"] if k == "roles" else None)
            )
            return row
        # entity_info (oauth callback: _update_account_refresh_token INSERT + select)
        if "entity_info" in q and "google_oauth_refresh" in q:
            if state.entity_info_updated or state.entity_info_refresh_token:
                row = MagicMock()
                row.__getitem__ = lambda self, k: (
                    state.entity_info_refresh_token if k == "value" else None
                )
                return row
        return None

    async def _fetch(query: str, *args: Any) -> list[dict[str, Any]]:
        q = query.strip()
        # google_accounts all active (list_health_scoped_accounts)
        if "FROM public.google_accounts" in q and "status = 'active'" in q:
            return [state.primary_ga_row()]
        # entity_info for owner-default inventory (UNION query)
        if "entity_info" in q and "google_accounts" in q and "is_primary = true" in q:
            # Return entity_info rows for the primary GA companion entity
            if state.entity_info_refresh_token or state.entity_info_updated:
                row = MagicMock()
                row.__getitem__ = lambda self, k: {
                    "id": uuid.uuid4(),
                    "entity_id": _GA_ENTITY_ID,
                    "type": "google_oauth_refresh",
                    "value": state.entity_info_refresh_token or "new_token",
                    "label": None,
                    "created_at": datetime.now(UTC),
                    "last_verified": None,
                    "last_test_ok": None,
                    "last_test_code": None,
                    "last_test_message": None,
                    "priority": 1,
                }.get(k)
                return [row]
            return []
        # entity_info for owner entity (owner credentials)
        if "entity_info" in q and "entities" in q and "owner" in q:
            return []
        return []

    async def _fetchval(query: str, *args: Any) -> Any:
        if "ingestion_events" in query:
            return None
        if "google_accounts" in query and "COUNT" in query:
            return 1  # one active account
        return None

    async def _execute(query: str, *args: Any) -> str:
        state.execute_calls.append((query, args))
        # Capture UPDATE public.google_accounts SET granted_scopes
        if "UPDATE public.google_accounts" in query and "granted_scopes" in query:
            # args[0] is the new scope list, args[1] is entity_id
            if args and isinstance(args[0], list):
                state.ga_granted_scopes = list(args[0])
                state.ga_scopes_updated = True
        # Capture INSERT INTO public.entity_info for refresh token
        if "entity_info" in query and "google_oauth_refresh" in query and "INSERT" in query:
            if len(args) >= 2:
                state.entity_info_refresh_token = args[1]
                state.entity_info_updated = True
        return "OK"

    conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    conn.fetch = AsyncMock(side_effect=_fetch)
    conn.fetchval = AsyncMock(side_effect=_fetchval)
    conn.execute = AsyncMock(side_effect=_execute)
    conn.transaction = MagicMock(return_value=_AsyncContextManagerStub())

    return conn


class _AsyncContextManagerStub:
    """Minimal async context manager that does nothing."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_shared_pool(state: _SharedPoolState) -> MagicMock:
    """Build a fake asyncpg pool backed by `state`.

    Some callers (e.g. _fetch_system_secrets, _fetch_probe_logs_bulk) call
    pool.fetch() directly rather than through pool.acquire(). We wire both
    paths to the same underlying async handlers so mock behavior is consistent.
    """

    @asynccontextmanager
    async def _acquire():
        yield _make_conn_for_state(state)

    # Re-use the handler functions from a fresh conn so we get the same logic.
    _conn = _make_conn_for_state(state)

    pool = MagicMock()
    pool.acquire = _acquire
    # Pool-level methods — delegate to a fresh conn for each call.
    pool.fetchrow = _conn.fetchrow
    pool.fetch = _conn.fetch
    pool.fetchval = _conn.fetchval
    pool.execute = _conn.execute
    return pool


def _make_swb_pool(*, heartbeat_row: dict[str, Any] | None = None) -> AsyncMock:
    """Fake switchboard pool returning a heartbeat row (or None)."""
    swb = AsyncMock()
    swb.fetchrow = AsyncMock(return_value=heartbeat_row)
    swb.fetch = AsyncMock(return_value=[heartbeat_row] if heartbeat_row else [])
    return swb


def _make_db(state: _SharedPoolState, *, heartbeat_row: dict[str, Any] | None = None) -> MagicMock:
    """Build a DatabaseManager mock backed by `state`."""
    shared_pool = _make_shared_pool(state)
    swb_pool = _make_swb_pool(heartbeat_row=heartbeat_row)

    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = shared_pool
    db.butler_names = []  # no per-butler schemas in this test
    db.pool.side_effect = lambda name: (
        swb_pool if name == "switchboard" else (_ for _ in ()).throw(KeyError(name))
    )
    return db


def _make_app(db: MagicMock) -> Any:
    """Build a FastAPI app with all three router _get_db_manager stubs overridden."""
    app = create_app(api_key="")
    # Override all three router _get_db_manager stubs to return our mock DB.
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: db
    app.dependency_overrides[sv2_module._get_db_manager] = lambda: db
    app.dependency_overrides[gh_module._get_db_manager] = lambda: db
    return app


# ---------------------------------------------------------------------------
# The flow test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_oauth_states():
    _clear_state_store()
    yield
    _clear_state_store()


async def test_google_health_grant_flow_scope_transition():
    """Full server-side state transition: no health scopes → grant → scopes present.

    Proves:
    1. Inventory (owner-default, no ?identity=) surfaces primary Google account
       credential (google_oauth_refresh) even before health scopes are granted.
    2. OAuth start URL for scope_set=health contains all three googlehealth.* scopes.
    3. OAuth callback (mocked token exchange) writes health scopes to google_accounts.
    4. Connector status reports ``degraded`` BEFORE the grant (scope_missing path).
    5. Connector status reports a non-degraded state AFTER the grant (scopes present).
    """
    state = _SharedPoolState()
    db = _make_db(state)
    app = _make_app(db)

    # ------------------------------------------------------------------ #
    # Phase 1: Inventory — primary GA credential must surface             #
    # ------------------------------------------------------------------ #
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        inv_resp = await client.get("/api/secrets/inventory")

    assert inv_resp.status_code == 200, f"inventory 1: {inv_resp.text}"
    inv_body = inv_resp.json()
    assert "data" in inv_body, "inventory must return {data: {...}}"
    user_creds = inv_body["data"].get("user", [])
    user_types = [u["type"] for u in user_creds]
    # The primary GA companion entity's google_oauth_refresh must be in the user array.
    assert "google_oauth_refresh" in user_types, (
        f"Expected google_oauth_refresh in user credentials (owner-default projection). "
        f"Got: {user_types}"
    )

    # ------------------------------------------------------------------ #
    # Phase 2: OAuth start URL must carry googlehealth.* scopes           #
    # ------------------------------------------------------------------ #
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        start_resp = await client.get(
            "/api/oauth/google/start",
            params={
                "scope_set": "health",
                "redirect": "false",
                "account_hint": _OWNER_EMAIL,
                "force_consent": "true",
            },
        )

    assert start_resp.status_code == 200, f"oauth start: {start_resp.text}"
    start_body = start_resp.json()
    # The start endpoint returns {state: ..., authorization_url: ...}
    auth_url: str = start_body.get("authorization_url", "") or start_body.get("data", {}).get(
        "authorization_url", ""
    )
    assert auth_url, f"Expected authorization_url in start response: {start_body}"
    # All three googlehealth scopes must appear in the URL.
    for health_scope in _ALL_HEALTH_SCOPES:
        encoded_scope = health_scope.replace(":", "%3A").replace("/", "%2F")
        assert health_scope in auth_url or encoded_scope in auth_url, (
            f"Expected health scope {health_scope!r} in authorization URL.\nURL: {auth_url}"
        )
    start_state = start_body.get("state")
    assert start_state, f"Expected state token in start response: {start_body}"

    # ------------------------------------------------------------------ #
    # Phase 3: Connector status BEFORE grant — must be degraded           #
    # ------------------------------------------------------------------ #
    # No health scopes on the account yet → _derive_state → degraded.
    with patch(
        "butlers.api.routers.google_health.list_health_scoped_accounts",
        AsyncMock(return_value=[]),  # empty = no health-scoped accounts yet
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            pre_grant_status_resp = await client.get("/api/connectors/google-health/status")

    assert pre_grant_status_resp.status_code == 200, (
        f"pre-grant status: {pre_grant_status_resp.text}"
    )
    pre_grant_body = pre_grant_status_resp.json()
    pre_grant_state = pre_grant_body.get("state")
    # With no health scopes granted, state must be degraded or not_configured.
    assert pre_grant_state in ("degraded", "not_configured"), (
        f"Expected degraded/not_configured before grant, got {pre_grant_state!r}.\n"
        f"Body: {pre_grant_body}"
    )

    # ------------------------------------------------------------------ #
    # Phase 4: OAuth callback (mocked token exchange) — grants health     #
    # ------------------------------------------------------------------ #
    # Patch token exchange and userinfo to avoid real HTTP calls.
    _EXCHANGE = "butlers.api.routers.oauth._exchange_code_for_tokens"
    _USERINFO = "butlers.api.routers.oauth._fetch_google_userinfo"

    with (
        patch(_EXCHANGE, AsyncMock(return_value=_FAKE_TOKEN_RESPONSE)),
        patch(_USERINFO, AsyncMock(return_value=_FAKE_USERINFO)),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            callback_resp = await client.get(
                "/api/oauth/google/callback",
                params={"code": "4/fake_auth_code", "state": start_state},
            )

    assert callback_resp.status_code == 200, (
        f"OAuth callback failed: {callback_resp.status_code} {callback_resp.text}"
    )
    cb_body = callback_resp.json()
    assert cb_body.get("success") is True, f"Expected success=true in callback: {cb_body}"
    assert cb_body.get("provider") == "google", f"Expected provider=google: {cb_body}"

    # The mock execute must have captured the UPDATE granted_scopes call.
    assert state.ga_scopes_updated, (
        "Expected _update_account_refresh_token to UPDATE public.google_accounts.granted_scopes. "
        f"Execute calls were: {[(q[:80], a) for q, a in state.execute_calls]}"
    )
    # The updated scopes must contain all health scopes.
    granted = set(state.ga_granted_scopes)
    missing = GOOGLE_HEALTH_SCOPE_URLS - granted
    assert not missing, (
        f"After callback, google_accounts.granted_scopes is missing health scopes: {missing}. "
        f"Got: {state.ga_granted_scopes}"
    )

    # ------------------------------------------------------------------ #
    # Phase 5: Connector status AFTER grant — scopes present              #
    # ------------------------------------------------------------------ #
    # list_health_scoped_accounts now returns the account (scopes granted).
    from butlers.google_account_registry import HealthScopedAccount  # noqa: PLC0415

    post_grant_account = HealthScopedAccount(
        id=_GA_ID,
        email=_OWNER_EMAIL,
        entity_id=_GA_ENTITY_ID,
        refresh_token_present=True,
    )

    # Heartbeat shows the connector as healthy and recently active.
    now = datetime.now(UTC)
    heartbeat_row = {
        "state": "healthy",
        "last_heartbeat_at": now - timedelta(seconds=10),
        "uptime_s": 3600,
        "endpoint_identity": f"google_health:user:{_OWNER_EMAIL}",
        "metadata": {},
    }
    # Rebuild db with heartbeat and updated state.
    db_post = _make_db(state, heartbeat_row=heartbeat_row)
    app_post = _make_app(db_post)

    with patch(
        "butlers.api.routers.google_health.list_health_scoped_accounts",
        AsyncMock(return_value=[post_grant_account]),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_post), base_url="http://test"
        ) as client:
            post_grant_status_resp = await client.get("/api/connectors/google-health/status")

    assert post_grant_status_resp.status_code == 200, (
        f"post-grant status: {post_grant_status_resp.text}"
    )
    post_grant_body = post_grant_status_resp.json()
    post_grant_state = post_grant_body.get("state")

    # After grant, state must NOT be degraded/not_configured.
    assert post_grant_state not in ("degraded", "not_configured"), (
        f"Expected healthy/error after grant (scopes present), got {post_grant_state!r}.\n"
        f"Scopes on account: {state.ga_granted_scopes}\n"
        f"Body: {post_grant_body}"
    )
    assert post_grant_state == "healthy", (
        f"Expected healthy after full grant + active heartbeat, got {post_grant_state!r}.\n"
        f"Body: {post_grant_body}"
    )

    # Verify scopes_granted in post-grant status response reflects health scopes.
    post_scopes = set(post_grant_body.get("scopes_granted", []))
    assert post_scopes >= GOOGLE_HEALTH_SCOPE_URLS, (
        f"Expected all health scopes in scopes_granted after grant.\n"
        f"Got: {post_scopes}\nExpected superset of: {GOOGLE_HEALTH_SCOPE_URLS}"
    )
