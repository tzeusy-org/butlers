"""Focused tests for the Spotify Web API client."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.connectors.spotify_client import (
    SpotifyAuthError,
    SpotifyClient,
    SpotifyTokenRefreshUnavailableError,
)

pytestmark = pytest.mark.unit


def _credential_store(*, expires_at: str | None = None) -> AsyncMock:
    store = AsyncMock()

    async def _resolve(key: str) -> str | None:
        values = {
            "SPOTIFY_ACCESS_TOKEN": "access-token",
            "SPOTIFY_REFRESH_TOKEN": "refresh-token",
            "SPOTIFY_CLIENT_ID": "client-id",
            "SPOTIFY_TOKEN_EXPIRES_AT": expires_at,
        }
        return values.get(key)

    store.resolve = AsyncMock(side_effect=_resolve)
    store.store = AsyncMock()
    return store


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
