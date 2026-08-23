"""Every OAuth token-payload extraction site validates before mutating state.

One parametrized test per site (four Google connector/module refresh paths;
the generic provider callback is covered in
``tests/api/test_oauth_provider.py``) asserting the same three properties:

* a valid payload is accepted and lands in runtime state,
* a malformed payload is rejected, and
* rejection leaves **no** partial mutation — the pre-existing token and expiry
  are byte-for-byte what they were before the refresh attempt.

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
# Site 6 (found while auditing, same shape): connectors/gmail.py
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
