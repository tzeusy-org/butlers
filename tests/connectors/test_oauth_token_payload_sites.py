"""State-mutating OAuth token-payload extraction sites validate before mutation.

This file covers seven Google connector/module refresh paths with one
parametrized test per site. Three persistence-capable callback paths are covered
separately: ``oauth_google_callback`` and ``_google_callback_from_state`` in
``tests/api/test_google_callback_token_payload.py``, and
``oauth_provider_callback`` in ``tests/api/test_oauth_provider.py``. Together,
the tests assert the same three properties:

* a valid payload is accepted and lands in runtime state,
* a malformed payload is rejected, and
* rejection leaves **no** partial mutation — the pre-existing token and expiry
  are byte-for-byte what they were before the refresh attempt.

This inventory is deliberately limited to consumers that cache or persist
token-endpoint values. Read-only live probes in ``secrets_v2.py`` and Gmail's
endpoint-identity resolver use an access token transiently and are outside this
no-partial-mutation matrix.

All token material here is synthetic and generated in-test.

[bu-n8gvq]
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.connectors.gmail import GmailConnectorConfig, GmailConnectorRuntime
from butlers.connectors.google_calendar import CalendarAccountConfig, CalendarConnectorRuntime
from butlers.connectors.google_drive import GDriveAccountConfig, GDriveAccountLoop
from butlers.connectors.google_health import (
    RESOURCE_BUNDLES,
    GoogleHealthConnector,
    GoogleHealthConnectorConfig,
    GoogleHealthCredentialError,
    OwnerContext,
    ResourceState,
    _endpoint_identity_for_user,
)
from butlers.google_credentials import GoogleOAuthClient
from butlers.modules import calendar as calendar_module
from butlers.modules.contacts import sync as contacts_sync
from butlers.modules.google_drive import _DriveTokenCache
from butlers.oauth_token_payload import OAuthTokenValidationError

pytestmark = pytest.mark.asyncio

_FRESH_ACCESS_TOKEN = "synthetic-fresh-access-token-not-real"
_STALE_ACCESS_TOKEN = "synthetic-stale-access-token-not-real"
_EMAIL = "token-payload@example.invalid"

_VALID_PAYLOAD: dict[str, Any] = {
    "access_token": _FRESH_ACCESS_TOKEN,
    "expires_in": 1800,
    "scope": "scope-a",
    "token_type": "Bearer",
}

# The four rejection classes named in the bead's acceptance criteria.
_REJECTED_PAYLOADS: list[tuple[str, dict[str, Any]]] = [
    ("missing_access_token", {"expires_in": 1800}),
    ("non_string_access_token", {"access_token": 12345, "expires_in": 1800}),
    ("blank_access_token", {"access_token": "   ", "expires_in": 1800}),
    ("string_expires_in", {"access_token": _FRESH_ACCESS_TOKEN, "expires_in": "1800"}),
    ("negative_expires_in", {"access_token": _FRESH_ACCESS_TOKEN, "expires_in": -1}),
    ("bool_expires_in", {"access_token": _FRESH_ACCESS_TOKEN, "expires_in": True}),
    ("float_expires_in", {"access_token": _FRESH_ACCESS_TOKEN, "expires_in": 1800.5}),
    ("absurd_expires_in", {"access_token": _FRESH_ACCESS_TOKEN, "expires_in": 10**12}),
]

_REJECTED_IDS = [case for case, _ in _REJECTED_PAYLOADS]
_REJECTED_ONLY = [payload for _, payload in _REJECTED_PAYLOADS]


def _token_client(payload: dict[str, Any]) -> MagicMock:
    """An HTTP client whose token endpoint returns a 200 carrying *payload*."""
    client = MagicMock()
    response = httpx.Response(
        200,
        json=payload,
        request=httpx.Request("POST", "https://oauth2.googleapis.invalid/token"),
    )
    client.post = AsyncMock(return_value=response)
    return client


# ---------------------------------------------------------------------------
# Site 1: connectors/google_calendar.py — CalendarConnectorRuntime._get_access_token
# ---------------------------------------------------------------------------


def _calendar_runtime() -> CalendarConnectorRuntime:
    config = CalendarAccountConfig(
        email=_EMAIL,
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="synthetic-refresh-token-not-real",
        switchboard_mcp_url="http://localhost:41100/sse",
    )
    runtime = CalendarConnectorRuntime(config)
    runtime._access_token = _STALE_ACCESS_TOKEN
    runtime._token_expires_at = datetime.now(UTC) - timedelta(hours=1)
    return runtime


async def test_calendar_connector_accepts_a_valid_token_payload() -> None:
    runtime = _calendar_runtime()
    runtime._http_client = _token_client(_VALID_PAYLOAD)

    assert await runtime._get_access_token() == _FRESH_ACCESS_TOKEN
    assert runtime._token_expires_at is not None
    assert runtime._token_expires_at > datetime.now(UTC)


@pytest.mark.parametrize("payload", _REJECTED_ONLY, ids=_REJECTED_IDS)
async def test_calendar_connector_rejects_without_partial_mutation(
    payload: dict[str, Any],
) -> None:
    runtime = _calendar_runtime()
    stale_expiry = runtime._token_expires_at
    runtime._http_client = _token_client(payload)

    with pytest.raises(OAuthTokenValidationError):
        await runtime._get_access_token()

    assert runtime._access_token == _STALE_ACCESS_TOKEN
    assert runtime._token_expires_at == stale_expiry


# ---------------------------------------------------------------------------
# Site 2: connectors/google_drive.py — GDriveAccountLoop._get_access_token
# ---------------------------------------------------------------------------


def _drive_loop() -> GDriveAccountLoop:
    config = GDriveAccountConfig(
        email=_EMAIL,
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="synthetic-refresh-token-not-real",
        switchboard_mcp_url="http://switchboard.test/mcp",
    )
    loop = GDriveAccountLoop(_EMAIL, config)
    loop._access_token = _STALE_ACCESS_TOKEN
    loop._token_expires_at = datetime.now(UTC) - timedelta(hours=1)
    return loop


async def test_drive_connector_accepts_a_valid_token_payload() -> None:
    loop = _drive_loop()
    loop._http_client = _token_client(_VALID_PAYLOAD)

    assert await loop._get_access_token() == _FRESH_ACCESS_TOKEN
    assert loop._token_expires_at is not None
    assert loop._token_expires_at > datetime.now(UTC)


@pytest.mark.parametrize("payload", _REJECTED_ONLY, ids=_REJECTED_IDS)
async def test_drive_connector_rejects_without_partial_mutation(payload: dict[str, Any]) -> None:
    loop = _drive_loop()
    stale_expiry = loop._token_expires_at
    loop._http_client = _token_client(payload)

    with pytest.raises(OAuthTokenValidationError):
        await loop._get_access_token()

    assert loop._access_token == _STALE_ACCESS_TOKEN
    assert loop._token_expires_at == stale_expiry


# ---------------------------------------------------------------------------
# Site 3: connectors/google_health.py — GoogleHealthConnector._mint_access_token
# ---------------------------------------------------------------------------


def _health_connector() -> tuple[GoogleHealthConnector, OwnerContext]:
    config = GoogleHealthConnectorConfig(
        switchboard_mcp_url="http://localhost:41999/mcp",
        poll_intervals={b.resource: b.default_interval_s for b in RESOURCE_BUNDLES},
    )
    connector = GoogleHealthConnector(config=config, shared_pool=None, cursor_pool=None)
    account_id = uuid.uuid4()
    ctx = OwnerContext(
        account_id=account_id,
        email=_EMAIL,
        entity_id=uuid.uuid4(),
        refresh_token_present=True,
        endpoint_identity=_endpoint_identity_for_user(_EMAIL),
    )
    ctx.cached_access_token = _STALE_ACCESS_TOKEN
    ctx.token_expires_at = datetime.now(UTC) - timedelta(hours=1)
    connector._accounts[account_id] = ctx
    for bundle in RESOURCE_BUNDLES:
        connector._resources[(account_id, bundle.resource)] = ResourceState(bundle=bundle)
    connector._shared_pool = MagicMock()
    connector._client_id = "client-id"
    connector._client_secret = "client-secret"
    return connector, ctx


@pytest.fixture
def _health_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "butlers.google_credentials._resolve_entity_refresh_token",
        AsyncMock(return_value="synthetic-refresh-token-not-real"),
    )


@pytest.mark.usefixtures("_health_refresh_token")
async def test_health_connector_accepts_a_valid_token_payload() -> None:
    connector, ctx = _health_connector()
    connector._http_client = _token_client(_VALID_PAYLOAD)

    minted = await connector._mint_access_token(ctx.account_id)

    assert minted == _FRESH_ACCESS_TOKEN
    assert ctx.cached_access_token == _FRESH_ACCESS_TOKEN
    assert ctx.token_expires_at is not None
    assert ctx.token_expires_at > datetime.now(UTC)


@pytest.mark.usefixtures("_health_refresh_token")
@pytest.mark.parametrize("payload", _REJECTED_ONLY, ids=_REJECTED_IDS)
async def test_health_connector_rejects_without_partial_mutation(payload: dict[str, Any]) -> None:
    connector, ctx = _health_connector()
    stale_expiry = ctx.token_expires_at
    connector._http_client = _token_client(payload)

    with pytest.raises(GoogleHealthCredentialError) as exc_info:
        await connector._mint_access_token(ctx.account_id)

    # Fixed local text only: no value from the payload reaches the message.
    assert str(exc_info.value) == (
        f"Google token refresh returned an invalid token payload for {_EMAIL}"
    )
    assert ctx.cached_access_token == _STALE_ACCESS_TOKEN
    assert ctx.token_expires_at == stale_expiry


# ---------------------------------------------------------------------------
# Site 4: modules/google_drive/__init__.py — _DriveTokenCache._refresh
# ---------------------------------------------------------------------------


async def _drive_cache_refresh(cache: _DriveTokenCache, payload: dict[str, Any]) -> None:
    await cache._refresh(
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="synthetic-refresh-token-not-real",
        http_client=_token_client(payload),
        on_refreshed=None,
    )


def _drive_cache() -> _DriveTokenCache:
    cache = _DriveTokenCache()
    cache._access_token = _STALE_ACCESS_TOKEN
    cache._expires_at = 0.0
    return cache


async def test_drive_module_accepts_a_valid_token_payload() -> None:
    cache = _drive_cache()

    await _drive_cache_refresh(cache, _VALID_PAYLOAD)

    assert cache._access_token == _FRESH_ACCESS_TOKEN
    assert cache._expires_at > time.monotonic()


@pytest.mark.parametrize("payload", _REJECTED_ONLY, ids=_REJECTED_IDS)
async def test_drive_module_rejects_without_partial_mutation(payload: dict[str, Any]) -> None:
    cache = _drive_cache()

    with pytest.raises(RuntimeError) as exc_info:
        await _drive_cache_refresh(cache, payload)

    # Fixed local text only: no value from the payload reaches the message.
    assert str(exc_info.value) == "Google Drive token refresh returned an invalid token payload"
    assert cache._access_token == _STALE_ACCESS_TOKEN
    assert cache._expires_at == 0.0


# ---------------------------------------------------------------------------
# Persistence-capable callback coverage lives in:
# - oauth_google_callback and _google_callback_from_state:
#   tests/api/test_google_callback_token_payload.py
# - oauth_provider_callback: tests/api/test_oauth_provider.py
# Site 5: connectors/gmail.py — GmailConnectorRuntime._get_access_token
# ---------------------------------------------------------------------------


def _gmail_runtime() -> GmailConnectorRuntime:
    config = GmailConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        connector_endpoint_identity=f"gmail:user:{_EMAIL}",
        gmail_client_id="client-id",
        gmail_client_secret="client-secret",
        gmail_refresh_token="synthetic-refresh-token-not-real",
    )
    runtime = GmailConnectorRuntime(config, cursor_pool=MagicMock())
    runtime._access_token = _STALE_ACCESS_TOKEN
    runtime._token_expires_at = datetime.now(UTC) - timedelta(hours=1)
    return runtime


async def test_gmail_connector_accepts_a_valid_token_payload() -> None:
    runtime = _gmail_runtime()
    runtime._http_client = _token_client(_VALID_PAYLOAD)

    assert await runtime._get_access_token() == _FRESH_ACCESS_TOKEN
    assert runtime._token_expires_at is not None
    assert runtime._token_expires_at > datetime.now(UTC)


@pytest.mark.parametrize("payload", _REJECTED_ONLY, ids=_REJECTED_IDS)
async def test_gmail_connector_rejects_without_partial_mutation(payload: dict[str, Any]) -> None:
    runtime = _gmail_runtime()
    stale_expiry = runtime._token_expires_at
    runtime._http_client = _token_client(payload)

    with pytest.raises(OAuthTokenValidationError):
        await runtime._get_access_token()

    assert runtime._access_token == _STALE_ACCESS_TOKEN
    assert runtime._token_expires_at == stale_expiry


# ---------------------------------------------------------------------------
# Site 6: modules/calendar.py — GoogleOAuthClient._refresh_access_token
# ---------------------------------------------------------------------------


def _calendar_module_oauth(
    payload: dict[str, Any],
    *,
    on_token_refreshed: AsyncMock | None = None,
) -> GoogleOAuthClient:
    oauth = GoogleOAuthClient(
        calendar_module._GoogleOAuthCredentials(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="synthetic-refresh-token-not-real",
        ),
        _token_client(payload),
        on_token_refreshed=on_token_refreshed,
        error_type=calendar_module.CalendarTokenRefreshError,
    )
    oauth._access_token = _STALE_ACCESS_TOKEN
    oauth._access_token_expires_at = datetime.now(UTC) - timedelta(hours=1)
    return oauth


async def test_calendar_module_accepts_a_valid_token_payload() -> None:
    on_token_refreshed = AsyncMock()
    oauth = _calendar_module_oauth(
        _VALID_PAYLOAD,
        on_token_refreshed=on_token_refreshed,
    )

    assert await oauth.get_access_token(force_refresh=True) == _FRESH_ACCESS_TOKEN
    assert oauth._access_token_expires_at is not None
    assert oauth._access_token_expires_at > datetime.now(UTC)
    on_token_refreshed.assert_awaited_once_with()


@pytest.mark.parametrize("payload", _REJECTED_ONLY, ids=_REJECTED_IDS)
async def test_calendar_module_rejects_without_partial_mutation(
    payload: dict[str, Any],
) -> None:
    on_token_refreshed = AsyncMock()
    oauth = _calendar_module_oauth(payload, on_token_refreshed=on_token_refreshed)
    stale_expiry = oauth._access_token_expires_at

    with pytest.raises(calendar_module.CalendarTokenRefreshError) as exc_info:
        await oauth.get_access_token(force_refresh=True)

    assert str(exc_info.value) == "Google OAuth token endpoint returned an invalid token payload"
    assert oauth._access_token == _STALE_ACCESS_TOKEN
    assert oauth._access_token_expires_at == stale_expiry
    on_token_refreshed.assert_not_awaited()


# ---------------------------------------------------------------------------
# Site 7: modules/contacts/sync.py — GoogleOAuthClient._refresh_access_token
# ---------------------------------------------------------------------------


def _contacts_module_oauth(payload: dict[str, Any]) -> GoogleOAuthClient:
    oauth = GoogleOAuthClient(
        contacts_sync._GoogleOAuthCredentials(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="synthetic-refresh-token-not-real",
        ),
        _token_client(payload),
        error_type=contacts_sync.ContactsTokenRefreshError,
    )
    oauth._access_token = _STALE_ACCESS_TOKEN
    oauth._access_token_expires_at = datetime.now(UTC) - timedelta(hours=1)
    return oauth


async def test_contacts_module_accepts_a_valid_token_payload() -> None:
    oauth = _contacts_module_oauth(_VALID_PAYLOAD)

    assert await oauth.get_access_token(force_refresh=True) == _FRESH_ACCESS_TOKEN
    assert oauth._access_token_expires_at is not None
    assert oauth._access_token_expires_at > datetime.now(UTC)


@pytest.mark.parametrize("payload", _REJECTED_ONLY, ids=_REJECTED_IDS)
async def test_contacts_module_rejects_without_partial_mutation(
    payload: dict[str, Any],
) -> None:
    oauth = _contacts_module_oauth(payload)
    stale_expiry = oauth._access_token_expires_at

    with pytest.raises(contacts_sync.ContactsTokenRefreshError) as exc_info:
        await oauth.get_access_token(force_refresh=True)

    assert str(exc_info.value) == "Google OAuth token endpoint returned an invalid token payload"
    assert oauth._access_token == _STALE_ACCESS_TOKEN
    assert oauth._access_token_expires_at == stale_expiry


async def test_gmail_refresh_stampede_has_one_endpoint_attempt_and_rotation_is_not_cached() -> None:
    import asyncio

    runtime = _gmail_runtime()
    calls = []

    async def endpoint(request):
        calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json=_VALID_PAYLOAD)

    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        runtime._http_client = client
        assert (
            await asyncio.gather(*(runtime._get_access_token() for _ in range(8)))
            == [_FRESH_ACCESS_TOKEN] * 8
        )
        assert len(calls) == 1
        assert str(calls[0].url) == "https://oauth2.googleapis.com/token"
        assert calls[0].method == "POST"
        runtime._config = runtime._config.model_copy(
            update={"gmail_refresh_token": "synthetic-rotated-token"}
        )
        assert await runtime._get_access_token() == _FRESH_ACCESS_TOKEN
        assert len(calls) == 2
        assert b"synthetic-rotated-token" in calls[-1].content
        credentials = calendar_module._GoogleOAuthCredentials(
            client_id="shared-module-id",
            client_secret="shared-module-secret",
            refresh_token="shared-account-refresh",
        )
        first = GoogleOAuthClient(credentials, client)
        second = GoogleOAuthClient(credentials, client)
        assert await asyncio.gather(first.get_access_token(), second.get_access_token()) == [
            _FRESH_ACCESS_TOKEN,
            _FRESH_ACCESS_TOKEN,
        ]
        assert len(calls) == 3  # distinct wrappers share one account/scope record
        second._access_token = None  # explicit data-API invalidation must mint again
        assert (
            await asyncio.gather(*(second.get_access_token() for _ in range(8)))
            == [_FRESH_ACCESS_TOKEN] * 8
        )
        assert len(calls) == 4


async def test_shared_cache_scope_expiry_force_and_failed_mint_are_isolated() -> None:
    from types import SimpleNamespace
    from urllib.parse import parse_qs

    from butlers.google_credentials import GoogleTokenCache

    now = [1000.0]
    state = SimpleNamespace(_access_token=None, _token_expires_at=0.0)

    def clock():
        return now[0]

    cache = GoogleTokenCache(state, clock=clock)
    calls = []
    payload = dict(_VALID_PAYLOAD)

    async def endpoint(request):
        calls.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        args = dict(client_id="id", client_secret="secret", refresh_token="refresh")
        assert await cache.get_token(client, **args) == _FRESH_ACCESS_TOKEN
        now[0] = state._token_expires_at - 61
        await cache.get_token(client, **args)
        assert len(calls) == 1
        now[0] += 1
        await cache.get_token(client, **args)
        assert len(calls) == 2  # exactly sixty seconds is early-expired
        await cache.get_token(client, **args, scopes=frozenset({"health-b", "health-a"}))
        assert calls[-1]["scope"] == ["health-a health-b"]
        assert len(calls) == 3
        await cache.get_token(client, **args, force_refresh=True)
        assert len(calls) == 4 and "scope" not in calls[-1]
        before = (state._access_token, state._token_expires_at)
        payload["expires_in"] = True
        with pytest.raises(OAuthTokenValidationError):
            await cache.get_token(client, **args, force_refresh=True)
        assert (state._access_token, state._token_expires_at) == before
        sibling = SimpleNamespace(_access_token=None, _token_expires_at=0.0)
        payload["expires_in"] = 30
        await GoogleTokenCache(sibling, clock=clock).get_token(
            client, **{**args, "refresh_token": "different-account-refresh"}
        )
        assert sibling._token_expires_at == now[0] + 30
        assert (state._access_token, state._token_expires_at) == before


@pytest.mark.parametrize(
    "status,code,attempts",
    [
        (400, "invalid_grant", 1),
        (400, "unauthorized_client", 3),
        (401, None, 3),
        (503, None, 3),
        (0, None, 1),
    ],
)
async def test_shared_module_refresh_keeps_retry_budget_and_revocation_boundary(
    monkeypatch,
    status,
    code,
    attempts,
) -> None:
    import asyncio

    revoked = AsyncMock()
    refreshed = AsyncMock()
    calls = []
    private = "synthetic-provider-private-message"

    async def endpoint(request):
        calls.append(request)
        if not status:
            raise httpx.ConnectError(private, request=request)
        return httpx.Response(status, json={"error": code, "error_description": private})

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        oauth = GoogleOAuthClient(
            calendar_module._GoogleOAuthCredentials(
                client_id="id", client_secret="secret", refresh_token="refresh"
            ),
            client,
            on_token_revoked=revoked,
            on_token_refreshed=refreshed,
            error_type=calendar_module.CalendarTokenRefreshError,
        )
        with pytest.raises(calendar_module.CalendarTokenRefreshError) as caught:
            await oauth.get_access_token()
        assert private not in str(caught.value)
        assert len(calls) == attempts
        assert oauth._access_token is None and oauth._access_token_expires_at is None
        assert revoked.await_count == (1 if code == "invalid_grant" else 0)
        refreshed.assert_not_awaited()
        assert all(str(request.url) == "https://oauth2.googleapis.com/token" for request in calls)


@pytest.mark.parametrize(
    "site",
    [
        "gmail",
        "calendar",
        "drive",
        "health",
        "calendar_module",
        "contacts_module",
        "drive_module",
        "oauth_probe",
        "credential_probe",
        "capability_probe",
        "health_probe",
    ],
)
async def test_every_refresh_adapter_uses_one_fake_endpoint_and_keeps_error_authority(
    monkeypatch,
    site,
):
    import asyncio
    from dataclasses import replace
    from urllib.parse import parse_qs

    from butlers.api.routers import oauth, secrets_v2
    from butlers.google_credentials import GOOGLE_TOKEN_URL

    client_type = httpx.AsyncClient
    responses = [(status, False) for status in (200, 400, 401, 503)]
    if site in {"credential_probe", "capability_probe", "health_probe"}:
        responses.append((200, True))
    for status, malformed in responses:
        calls = []
        api_calls = []
        malformed_token = "synthetic-malformed-token-not-real"
        refresh = f"synthetic-{site}-{status}"
        revoked = AsyncMock()
        pool = MagicMock()
        pool.execute = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)

        async def endpoint(
            request,
            *,
            calls=calls,
            status=status,
            api_calls=api_calls,
            malformed=malformed,
            malformed_token=malformed_token,
        ):
            if request.method != "POST":
                api_calls.append(request)
                return httpx.Response(200, json={})
            data = parse_qs(request.content.decode())
            calls.append((request, data))
            # Reach the separate down-scope exchange even when its response fails.
            result_status = 200 if site == "health_probe" and "scope" not in data else status
            invalid_mint = malformed and (site != "health_probe" or "scope" in data)
            if invalid_mint:
                return httpx.Response(
                    200,
                    json={
                        "access_token": malformed_token,
                        "expires_in": True,
                        "scope": "https://www.googleapis.com/auth/googlehealth.activity.read",
                    },
                )
            return httpx.Response(
                result_status,
                json=(
                    {"access_token": "matrix-token", "expires_in": 3600}
                    if result_status == 200
                    else {"error": "invalid_grant" if result_status == 400 else "server_error"}
                ),
            )

        with monkeypatch.context() as patcher:
            patcher.setattr(asyncio, "sleep", AsyncMock())
            patcher.setattr(
                "butlers.google_credentials._resolve_entity_refresh_token",
                AsyncMock(return_value=refresh),
            )
            store = MagicMock()
            store.load = AsyncMock(side_effect=lambda key: "id" if key.endswith("ID") else "secret")
            patcher.setattr(secrets_v2, "CredentialStore", lambda *args, store=store: store)
            patcher.setattr(
                httpx,
                "AsyncClient",
                lambda *args, **kwargs: client_type(transport=httpx.MockTransport(endpoint)),
            )
            async with client_type(transport=httpx.MockTransport(endpoint)) as client:
                if site in {"gmail", "calendar", "drive"}:
                    runtime = {
                        "gmail": _gmail_runtime,
                        "calendar": _calendar_runtime,
                        "drive": _drive_loop,
                    }[site]()
                    field = "gmail_refresh_token" if site == "gmail" else "refresh_token"
                    if site == "gmail":
                        runtime._config = runtime._config.model_copy(update={field: refresh})
                    else:
                        runtime._config = replace(runtime._config, **{field: refresh})
                    runtime._http_client = client
                    action = runtime._get_access_token
                elif site == "health":
                    runtime, context = _health_connector()
                    runtime._http_client = client

                    async def action(runtime=runtime, context=context):
                        return await runtime._mint_access_token(context.account_id)
                elif site in {"calendar_module", "contacts_module"}:
                    credentials = calendar_module._GoogleOAuthCredentials(
                        client_id="id", client_secret="secret", refresh_token=refresh
                    )
                    runtime = GoogleOAuthClient(credentials, client, on_token_revoked=revoked)
                    action = runtime.get_access_token
                elif site == "drive_module":
                    runtime = _DriveTokenCache()

                    async def action(runtime=runtime, refresh=refresh):
                        return await runtime.get_token(
                            client_id="id",
                            client_secret="secret",
                            refresh_token=refresh,
                            http_client=client,
                        )
                elif site == "oauth_probe":
                    result = await oauth._probe_google_token(
                        client_id="id", client_secret="secret", refresh_token=refresh
                    )
                    assert result.connected is (status == 200)
                elif site == "credential_probe":
                    result = await secrets_v2._verify_oauth_credential(
                        "google", "google_oauth_refresh", refresh, pool
                    )
                    if malformed:
                        assert result == (
                            "live_failed:invalid_token_payload",
                            None,
                            "token_exchange: invalid token payload",
                        )
                    else:
                        assert result[0] == (
                            "live_ok" if status == 200 else f"live_failed:{status}"
                        )
                else:
                    result = await secrets_v2._verify_google_capabilities(
                        refresh, pool, entity_id=None
                    )
                    affected = (
                        result
                        if site == "capability_probe"
                        else [row for row in result if row.capability == "health"]
                    )
                    if malformed:
                        assert affected
                        assert all(
                            row.ok is False
                            and row.code is None
                            and row.probe_status == "live_failed:invalid_token_payload"
                            and row.message
                            == (
                                "health token down-scope exchange: invalid token payload"
                                if site == "health_probe"
                                else "token_exchange: invalid token payload"
                            )
                            for row in affected
                        )
                        if site == "health_probe":
                            assert all(
                                row.ok is True for row in result if row.capability != "health"
                            )
                    else:
                        assert all(row.ok is (status == 200) for row in affected)
                if site in {
                    "gmail",
                    "calendar",
                    "drive",
                    "health",
                    "calendar_module",
                    "contacts_module",
                    "drive_module",
                }:
                    if status == 200:
                        assert await action() == "matrix-token"
                    else:
                        with pytest.raises(
                            (httpx.HTTPStatusError, GoogleHealthCredentialError, RuntimeError)
                        ):
                            await action()
                    if site.endswith("_module") and site != "drive_module":
                        assert revoked.await_count == (1 if status == 400 else 0)
                assert calls
                assert all(str(request.url) == GOOGLE_TOKEN_URL for request, _ in calls)
                assert all(data["grant_type"] == ["refresh_token"] for _, data in calls)
                assert all(data["refresh_token"] == [refresh] for _, data in calls)
                if site in {"health", "health_probe"}:
                    assert any("googlehealth" in data.get("scope", [""])[0] for _, data in calls)
                if malformed:
                    assert all(
                        request.headers.get("Authorization") != f"Bearer {malformed_token}"
                        for request in api_calls
                    )
                    if site != "health_probe":
                        assert not api_calls
                    else:
                        assert api_calls  # Other capabilities use the valid initial union mint.
                        assert all(
                            str(request.url) != secrets_v2._GOOGLE_CAPABILITY_ENDPOINTS["health"]
                            for request in api_calls
                        )
                        assert len(calls) == 2
                    revoked.assert_not_awaited()
                    store.store.assert_not_called()
                # Scope/probe verdicts never gain persistent revocation authority.
                pool.execute.assert_not_awaited()
