"""Real PostgreSQL grant-boundary checks for Google invalid-grant handling.

The database schema, public rows, and Calendar/Health runtime roles are real.
Only Google HTTP endpoints are intercepted with httpx.MockTransport; no
provider network request is made. The ordinary migration login is limited to
fixture seeding, the explicit reauthorization race, and reading final state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio

from butlers.connectors.google_health import (
    GoogleHealthConnector,
    GoogleHealthConnectorConfig,
    OwnerContext,
    _endpoint_identity_for_user,
)
from butlers.connectors.google_health_client import GoogleHealthTokenRevokedError
from butlers.db import register_jsonb_codec
from butlers.modules.calendar.errors import CalendarRequestError, CalendarTokenRefreshError
from butlers.modules.calendar.google import _GoogleProvider
from butlers.modules.calendar.models import CalendarConfig
from butlers.modules.calendar.oauth import _GoogleOAuthCredentials
from butlers.testing.db import open_pool

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CALENDAR_URL = "https://www.googleapis.com/calendar/v3"
_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
_REFRESH_INFO_TYPE = "google_oauth_refresh"


@dataclass(frozen=True)
class _Account:
    account_id: UUID
    entity_id: UUID
    email: str
    refresh_token: str


@dataclass
class _Database:
    control: asyncpg.Connection
    calendar_pool: asyncpg.Pool
    health_pool: asyncpg.Pool
    target: _Account
    other: _Account


@pytest.fixture
def _google_db_url(fresh_db: Callable[..., str]) -> str:
    # fresh_db is intentionally called from this synchronous fixture; it runs
    # the repository's real core migration chain before cloning the DB.
    return fresh_db("core")


async def _runtime_pool(db_url: str, role: str) -> asyncpg.Pool:
    async def set_runtime_role(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET ROLE "{role}"')

    # Use the repository DB helper and the actual init-db roles. This is not a
    # hand-created role or a direct migration-login product operation.
    return await open_pool(
        db_url,
        init=set_runtime_role,
        min_size=1,
        max_size=1,
    )


async def _seed_account(
    conn: asyncpg.Connection,
    *,
    account_id: UUID,
    entity_id: UUID,
    email: str,
    token: str,
    name: str,
) -> _Account:
    await conn.execute(
        """
        INSERT INTO public.entities (id, canonical_name, entity_type, roles)
        VALUES ($1, $2, 'other', ARRAY['google_account']::text[])
        """,
        entity_id,
        name,
    )
    await conn.execute(
        """
        INSERT INTO public.google_accounts
            (id, entity_id, email, is_primary, granted_scopes, status)
        VALUES ($1, $2, $3, false, $4::text[], 'active')
        """,
        account_id,
        entity_id,
        email,
        [_CALENDAR_SCOPE],
    )
    await conn.execute(
        """
        INSERT INTO public.entity_info (entity_id, type, value, is_primary, secured)
        VALUES ($1, $2, $3, true, true)
        """,
        entity_id,
        _REFRESH_INFO_TYPE,
        token,
    )
    return _Account(account_id, entity_id, email, token)


@pytest_asyncio.fixture(loop_scope="session")
async def _accounts(_google_db_url: str) -> AsyncIterator[_Database]:
    # This fixture login has only fixture duties. Product operations below use
    # one of the normal Calendar or Health runtime roles.
    control = await asyncpg.connect(_google_db_url)
    await register_jsonb_codec(control)
    suffix = uuid4().hex
    target_id, target_entity_id = uuid4(), uuid4()
    other_id, other_entity_id = uuid4(), uuid4()
    target_email = f"google-target-{suffix}@example.invalid"
    other_email = f"google-other-{suffix}@example.invalid"
    target_token = f"synthetic-target-refresh-{suffix}"
    other_token = f"synthetic-other-refresh-{suffix}"
    try:
        async with control.transaction():
            target = await _seed_account(
                control,
                account_id=target_id,
                entity_id=target_entity_id,
                email=target_email,
                token=target_token,
                name=f"google-account-target-{suffix}",
            )
            other = await _seed_account(
                control,
                account_id=other_id,
                entity_id=other_entity_id,
                email=other_email,
                token=other_token,
                name=f"google-account-other-{suffix}",
            )
        calendar_pool = await _runtime_pool(_google_db_url, "butler_general_rw")
        health_pool = await _runtime_pool(_google_db_url, "butler_health_rw")
        try:
            yield _Database(control, calendar_pool, health_pool, target, other)
        finally:
            await health_pool.close()
            await calendar_pool.close()
    finally:
        await control.close()


def _calendar_provider(account: _Account, pool: asyncpg.Pool, client: httpx.AsyncClient):
    return _GoogleProvider(
        CalendarConfig(provider="google", account=account.email),
        _GoogleOAuthCredentials(
            client_id="native-test-client.apps.googleusercontent.com",
            client_secret="synthetic-client-secret",
            refresh_token=account.refresh_token,
        ),
        http_client=client,
        pool=pool,
    )


async def _assert_runtime_role(
    pool: asyncpg.Pool,
    *,
    expected_role: str,
) -> None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT current_user::text AS role,
                   has_table_privilege(current_user, 'public.google_accounts', 'SELECT') AS account_select,
                   has_table_privilege(current_user, 'public.google_accounts', 'UPDATE') AS account_update,
                   has_table_privilege(current_user, 'public.entity_info', 'SELECT') AS info_select
            """
        )
    assert row["role"] == expected_role
    assert row["account_select"] is True
    assert row["account_update"] is True
    assert row["info_select"] is True


async def _status(control: asyncpg.Connection, account_id: UUID) -> str:
    result = await control.fetchval(
        "SELECT status FROM public.google_accounts WHERE id = $1", account_id
    )
    assert result is not None
    return result


async def _stored_token(control: asyncpg.Connection, entity_id: UUID) -> str:
    result = await control.fetchval(
        "SELECT value FROM public.entity_info WHERE entity_id = $1 AND type = $2",
        entity_id,
        _REFRESH_INFO_TYPE,
    )
    assert result is not None
    return result


def _token_error(request: httpx.Request, error: str) -> httpx.Response:
    assert request.method == "POST"
    assert request.url == httpx.URL(_TOKEN_URL)
    return httpx.Response(400, json={"error": error}, request=request)


async def test_calendar_current_invalid_grant_revokes_only_matching_account(
    _accounts: _Database,
) -> None:
    await _assert_runtime_role(_accounts.calendar_pool, expected_role="butler_general_rw")
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _token_error(request, "invalid_grant")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = _calendar_provider(_accounts.target, _accounts.calendar_pool, client)
    try:
        with pytest.raises(CalendarTokenRefreshError, match="invalid_grant"):
            await provider._oauth.get_access_token(force_refresh=True)
    finally:
        await client.aclose()

    assert len(requests) == 1
    assert _accounts.target.refresh_token.encode() in requests[0].content
    assert await _stored_token(_accounts.control, _accounts.target.entity_id) == (
        _accounts.target.refresh_token
    )
    assert await _status(_accounts.control, _accounts.target.account_id) == "revoked"
    assert await _status(_accounts.control, _accounts.other.account_id) == "active"


async def test_calendar_stale_invalid_grant_after_reauthorization_preserves_account(
    _accounts: _Database,
) -> None:
    await _assert_runtime_role(_accounts.calendar_pool, expected_role="butler_general_rw")
    request_started = asyncio.Event()
    release_response = asyncio.Event()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST" and request.url == httpx.URL(_TOKEN_URL)
        request_started.set()
        await asyncio.wait_for(release_response.wait(), timeout=5)
        return httpx.Response(400, json={"error": "invalid_grant"}, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = _calendar_provider(_accounts.target, _accounts.calendar_pool, client)
    pending = asyncio.create_task(provider._oauth.get_access_token(force_refresh=True))
    try:
        await asyncio.wait_for(request_started.wait(), timeout=5)
        # This models the dashboard's completed reauthorization while the
        # daemon's old-token request is in flight. Only the fixture login does
        # this state transition; the subsequent revocation uses Calendar role.
        rotated_token = f"synthetic-reauthorized-refresh-{uuid4().hex}"
        async with _accounts.control.transaction():
            await _accounts.control.execute(
                "UPDATE public.entity_info SET value = $1 WHERE entity_id = $2 AND type = $3",
                rotated_token,
                _accounts.target.entity_id,
                _REFRESH_INFO_TYPE,
            )
            await _accounts.control.execute(
                "UPDATE public.google_accounts SET status = 'active' WHERE id = $1",
                _accounts.target.account_id,
            )
        release_response.set()
        with pytest.raises(CalendarTokenRefreshError, match="invalid_grant"):
            await asyncio.wait_for(pending, timeout=5)
    finally:
        release_response.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await client.aclose()

    assert len(requests) == 1
    assert _accounts.target.refresh_token.encode() in requests[0].content
    assert await _stored_token(_accounts.control, _accounts.target.entity_id) == rotated_token
    assert await _status(_accounts.control, _accounts.target.account_id) == "active"
    assert await _status(_accounts.control, _accounts.other.account_id) == "active"


async def test_calendar_unauthorized_client_does_not_revoke_grant(
    _accounts: _Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _assert_runtime_role(_accounts.calendar_pool, expected_role="butler_general_rw")
    requests: list[httpx.Request] = []

    async def no_backoff(_delay: float) -> None:
        return None

    # Preserve the production retry count while avoiding the real 1s+2s waits.
    monkeypatch.setattr("butlers.google_credentials.asyncio.sleep", no_backoff)

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _token_error(request, "unauthorized_client")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = _calendar_provider(_accounts.target, _accounts.calendar_pool, client)
    try:
        with pytest.raises(CalendarTokenRefreshError):
            await provider._oauth.get_access_token(force_refresh=True)
    finally:
        await client.aclose()

    assert len(requests) == 3
    assert await _status(_accounts.control, _accounts.target.account_id) == "active"
    assert await _status(_accounts.control, _accounts.other.account_id) == "active"
    assert await _stored_token(_accounts.control, _accounts.target.entity_id) == (
        _accounts.target.refresh_token
    )


async def test_calendar_api_invalid_grant_text_has_no_revocation_authority(
    _accounts: _Database,
) -> None:
    await _assert_runtime_role(_accounts.calendar_pool, expected_role="butler_general_rw")
    token_requests = 0
    api_requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        if request.url == httpx.URL(_TOKEN_URL):
            token_requests += 1
            return httpx.Response(
                200,
                json={"access_token": f"synthetic-access-{token_requests}", "expires_in": 3600},
                request=request,
            )
        assert request.url.host == "www.googleapis.com"
        api_requests.append(request)
        return httpx.Response(
            401,
            json={"error": {"message": "invalid_grant reported by calendar resource"}},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = _calendar_provider(_accounts.target, _accounts.calendar_pool, client)
    try:
        with pytest.raises(CalendarRequestError) as error:
            await provider.list_calendars()
    finally:
        await client.aclose()

    assert error.value.status_code == 401
    assert token_requests == 2
    assert len(api_requests) == 2
    assert all(request.url.path.endswith("/users/me/calendarList") for request in api_requests)
    assert await _status(_accounts.control, _accounts.target.account_id) == "active"
    assert await _status(_accounts.control, _accounts.other.account_id) == "active"
    assert await _stored_token(_accounts.control, _accounts.target.entity_id) == (
        _accounts.target.refresh_token
    )


async def test_health_invalid_grant_revokes_by_account_id_not_entity_id(
    _accounts: _Database,
) -> None:
    await _assert_runtime_role(_accounts.health_pool, expected_role="butler_health_rw")
    assert _accounts.target.account_id != _accounts.target.entity_id
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _token_error(request, "invalid_grant")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = GoogleHealthConnector(
        config=GoogleHealthConnectorConfig(switchboard_mcp_url="http://127.0.0.1:41100/sse"),
        shared_pool=_accounts.health_pool,
        cursor_pool=None,
    )
    connector._client_id = "native-test-client.apps.googleusercontent.com"
    connector._client_secret = "synthetic-client-secret"
    connector._http_client = client
    context = OwnerContext(
        account_id=_accounts.target.account_id,
        email=_accounts.target.email,
        entity_id=_accounts.target.entity_id,
        refresh_token_present=True,
        endpoint_identity=_endpoint_identity_for_user(_accounts.target.email),
    )
    connector._accounts[_accounts.target.account_id] = context
    try:
        with pytest.raises(GoogleHealthTokenRevokedError):
            await connector._mint_access_token(_accounts.target.account_id, force_refresh=True)
        # This is the exact production transition invoked by _main_loop after
        # _mint_access_token reports token-endpoint invalid_grant.
        await connector._mark_account_revoked(_accounts.target.account_id)
    finally:
        await client.aclose()

    assert len(requests) == 1
    assert _accounts.target.refresh_token.encode() in requests[0].content
    assert await _stored_token(_accounts.control, _accounts.target.entity_id) == (
        _accounts.target.refresh_token
    )
    assert await _status(_accounts.control, _accounts.target.account_id) == "revoked"
    assert await _status(_accounts.control, _accounts.other.account_id) == "active"
