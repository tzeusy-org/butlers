"""Focused tests for the Spotify Web API client."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.connectors.spotify_client import (
    SpotifyAuthError,
    SpotifyClient,
    SpotifyTokenRefreshUnavailableError,
)

pytestmark = pytest.mark.unit


def _credential_store(*, expires_at: str | datetime | None = None) -> AsyncMock:
    store = AsyncMock()
    store.pool = MagicMock()
    store.pool.spotify_values = {
        "spotify_oauth_access": "access-token",
        "spotify_oauth_refresh": "refresh-token",
        "spotify_oauth_expires_at": expires_at,
    }

    async def _resolve(key: str) -> str | None:
        values = {
            "SPOTIFY_CLIENT_ID": "client-id",
        }
        return values.get(key)

    store.resolve = AsyncMock(side_effect=_resolve)
    store.store = AsyncMock()
    return store


@pytest.fixture(autouse=True)
def _owner_entity_info_resolver(monkeypatch):
    async def _resolve(pool: object, info_type: str) -> str | None:
        return pool.spotify_values.get(info_type)

    monkeypatch.setattr("butlers.connectors.spotify_client.resolve_owner_entity_info", _resolve)


def _response(
    status_code: int,
    json_data: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.headers = httpx.Headers(headers or {})
    response.json = MagicMock(return_value=json_data)
    response.text = json.dumps(json_data)
    return response


async def test_token_refresh_temporarily_unavailable_is_retryable() -> None:
    expires_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store = _credential_store(expires_at=expires_at)
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post = AsyncMock(
        return_value=_response(
            503,
            {"error": "temporarily_unavailable", "error_description": ""},
            headers={"Retry-After": "42"},
        )
    )

    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    with pytest.raises(SpotifyTokenRefreshUnavailableError) as exc_info:
        await client.get_me()

    assert exc_info.value.retry_after_s == 42.0
    store.store.assert_not_awaited()


async def test_token_refresh_oauth_temporarily_unavailable_is_retryable_on_400() -> None:
    expires_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store = _credential_store(expires_at=expires_at)
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post = AsyncMock(
        return_value=_response(
            400,
            {"error": "temporarily_unavailable", "error_description": ""},
            headers={"Retry-After": "30"},
        )
    )

    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    with pytest.raises(SpotifyTokenRefreshUnavailableError) as exc_info:
        await client.get_me()

    assert exc_info.value.retry_after_s == 30.0
    store.store.assert_not_awaited()


async def test_token_refresh_invalid_grant_remains_auth_error() -> None:
    expires_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store = _credential_store(expires_at=expires_at)
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post = AsyncMock(
        return_value=_response(
            400,
            {"error": "invalid_grant", "error_description": "Refresh token revoked"},
        )
    )

    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    with pytest.raises(SpotifyAuthError):
        await client.get_me()

    store.store.assert_not_awaited()


async def test_token_refresh_provider_text_is_absent_from_exception_and_logs(caplog) -> None:
    marker = "PROVIDER_REFRESH_RESPONSE_MARKER"
    expires_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store = _credential_store(expires_at=expires_at)
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post = AsyncMock(
        return_value=_response(
            400,
            {"error": marker, "error_description": marker},
        )
    )
    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    with pytest.raises(SpotifyAuthError) as exc_info:
        await client.get_me()

    assert marker not in str(exc_info.value)
    assert marker not in caplog.text


async def test_malformed_token_refresh_body_is_absent_from_exception_and_logs(caplog) -> None:
    marker = "MALFORMED_REFRESH_RESPONSE_MARKER"
    expires_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store = _credential_store(expires_at=expires_at)
    response = _response(200, {})
    response.json.side_effect = ValueError("invalid json")
    response.text = marker
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post = AsyncMock(return_value=response)
    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    with pytest.raises(SpotifyAuthError) as exc_info:
        await client.get_me()

    assert marker not in str(exc_info.value)
    assert marker not in caplog.text


@pytest.mark.parametrize(
    ("payload", "marker"),
    [
        pytest.param(
            {
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
                "expires_in": "MALFORMED_REFRESH_EXPIRY_MARKER",
            },
            "MALFORMED_REFRESH_EXPIRY_MARKER",
            id="expiry",
        ),
        pytest.param(
            {
                "access_token": "rotated-access",
                "refresh_token": ["MALFORMED_ROTATED_REFRESH_MARKER"],
                "expires_in": 3600,
            },
            "MALFORMED_ROTATED_REFRESH_MARKER",
            id="rotated-refresh",
        ),
    ],
)
async def test_token_refresh_rejects_malformed_success_payload_before_persisting(
    payload: dict[str, Any], marker: str, caplog
) -> None:
    store = _credential_store()
    conn = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield

    @asynccontextmanager
    async def _acquire():
        yield conn

    conn.transaction = MagicMock(side_effect=_transaction)
    store.pool.acquire = _acquire
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post = AsyncMock(return_value=_response(200, payload))
    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    with patch(
        "butlers.connectors.spotify_client.upsert_owner_entity_info_on_connection",
        new_callable=AsyncMock,
        return_value=True,
    ) as upsert:
        with pytest.raises(SpotifyAuthError) as exc_info:
            await client._refresh_access_token()

    assert str(exc_info.value) == (
        "Spotify token refresh returned an unexpected response. "
        "Re-connect Spotify via dashboard settings."
    )
    assert marker not in str(exc_info.value)
    assert marker not in caplog.text
    upsert.assert_not_awaited()


async def test_owner_expiry_accepts_decoded_datetime() -> None:
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    store = _credential_store(expires_at=expires_at)
    client = SpotifyClient(credential_store=store, http_client=AsyncMock(spec=httpx.AsyncClient))

    await client.open()

    assert client._expires_at == expires_at


async def test_successful_refresh_persists_owner_rows_in_one_transaction() -> None:
    store = _credential_store()
    conn = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield

    @asynccontextmanager
    async def _acquire():
        yield conn

    conn.transaction = MagicMock(side_effect=_transaction)
    store.pool.acquire = _acquire
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post = AsyncMock(
        return_value=_response(
            200,
            {
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 3600,
            },
        )
    )
    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    with patch(
        "butlers.connectors.spotify_client.upsert_owner_entity_info_on_connection",
        new_callable=AsyncMock,
        return_value=True,
    ) as upsert:
        await client._refresh_access_token()

    assert [call.args[1] for call in upsert.await_args_list] == [
        "spotify_oauth_access",
        "spotify_oauth_refresh",
        "spotify_oauth_expires_at",
    ]
    assert all(call.args[0] is conn for call in upsert.await_args_list)


async def test_current_playback_requests_track_and_episode_types() -> None:
    store = _credential_store()
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.request = AsyncMock(
        return_value=_response(
            200,
            {"is_playing": True, "item": {"type": "episode", "id": "episode-1"}},
        )
    )
    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    await client.get_currently_playing()

    assert http_client.request.await_args.kwargs["params"] == {"additional_types": "track,episode"}


async def test_oauth_tokens_resolve_only_from_owner_entity_info() -> None:
    store = _credential_store()
    values = {
        "spotify_oauth_access": "owner-access",
        "spotify_oauth_refresh": "owner-refresh",
        "spotify_oauth_expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }

    async def _resolve(_pool: object, info_type: str) -> str | None:
        return values.get(info_type)

    with patch(
        "butlers.connectors.spotify_client.resolve_owner_entity_info",
        side_effect=_resolve,
    ) as resolve_owner:
        client = SpotifyClient(credential_store=store, http_client=AsyncMock())
        await client.open()

    assert resolve_owner.await_count == 3
    store.resolve.assert_awaited_once_with("SPOTIFY_CLIENT_ID")
