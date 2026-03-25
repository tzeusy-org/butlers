"""Unit tests for the Spotify API client.

All Spotify API calls are mocked via ``respx`` (httpx-compatible mock) or
``unittest.mock``.  No real network calls are made.

Coverage:
- get_me(): success, auth error propagation
- get_currently_playing(): success (playing), no content (not playing), error
- get_recently_played(): with/without after cursor, no items, error
- Auth: proactive token refresh, 401 retry with refresh, double-401 failure
- Token refresh: success updates CredentialStore, failure raises SpotifyAuthError
- Rate limiting: Retry-After header respected, exponential backoff fallback
- Lifecycle: open/close, context manager
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.connectors.spotify_client import (
    SpotifyAPIError,
    SpotifyAuthError,
    SpotifyClient,
    SpotifyRateLimitError,
    _jittered_backoff,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_credential_store(
    *,
    access_token: str | None = "test_access_token",
    refresh_token: str | None = "test_refresh_token",
    client_id: str | None = "abcdef1234567890abcdef1234567890",
    expires_at: str | None = None,
) -> AsyncMock:
    """Build a mock CredentialStore with configurable token values."""
    store = AsyncMock()

    async def _resolve(key: str) -> str | None:
        mapping = {
            "SPOTIFY_ACCESS_TOKEN": access_token,
            "SPOTIFY_REFRESH_TOKEN": refresh_token,
            "SPOTIFY_CLIENT_ID": client_id,
            "SPOTIFY_TOKEN_EXPIRES_AT": expires_at,
        }
        return mapping.get(key)

    store.resolve = AsyncMock(side_effect=_resolve)
    store.store = AsyncMock()
    return store


def _make_response(
    status_code: int,
    json_data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Build a mock httpx.Response."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.headers = httpx.Headers(headers or {})
    if json_data is not None:
        response.json = MagicMock(return_value=json_data)
        response.text = json.dumps(json_data)
    else:
        response.json = MagicMock(return_value=None)
        response.text = ""
    return response


@pytest.fixture
def credential_store() -> AsyncMock:
    return _make_credential_store()


@pytest.fixture
def http_client() -> AsyncMock:
    """A mock httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.fixture
async def spotify_client(credential_store: AsyncMock, http_client: AsyncMock) -> SpotifyClient:
    """An opened SpotifyClient with mocked HTTP and credentials."""
    client = SpotifyClient(credential_store=credential_store, http_client=http_client)
    await client.open()
    return client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_PROFILE_RESPONSE = {
    "id": "spotify_user_123",
    "display_name": "Test User",
    "product": "premium",
    "country": "US",
}

_CURRENTLY_PLAYING_RESPONSE = {
    "is_playing": True,
    "item": {
        "id": "track_abc",
        "name": "Test Track",
        "artists": [{"name": "Test Artist"}],
        "album": {"name": "Test Album"},
        "duration_ms": 200000,
    },
    "context": {"type": "playlist", "uri": "spotify:playlist:123"},
    "progress_ms": 50000,
    "timestamp": 1700000000000,
}

_RECENTLY_PLAYED_RESPONSE = {
    "items": [
        {
            "track": {"id": "track_xyz", "name": "Old Track", "artists": [{"name": "Old Artist"}]},
            "played_at": "2024-11-14T12:00:00Z",
        }
    ],
    "cursors": {"after": "1700001000000", "before": "1699999000000"},
    "next": "https://api.spotify.com/v1/me/player/recently-played?after=1700001000000",
}

_REFRESH_TOKEN_RESPONSE = {
    "access_token": "new_access_token_xyz",
    "refresh_token": "new_refresh_token_xyz",
    "expires_in": 3600,
    "token_type": "Bearer",
}


# ---------------------------------------------------------------------------
# Tests: lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_open_loads_credentials(self) -> None:
        store = _make_credential_store()
        http = AsyncMock(spec=httpx.AsyncClient)
        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()
        assert client._access_token == "test_access_token"
        assert client._refresh_token == "test_refresh_token"
        assert client._client_id == "abcdef1234567890abcdef1234567890"

    async def test_context_manager_closes_owned_client(self) -> None:
        store = _make_credential_store()
        # No http_client supplied → SpotifyClient will create and own one
        with patch("butlers.connectors.spotify_client.httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value = mock_instance
            async with SpotifyClient(credential_store=store):
                pass
            mock_instance.aclose.assert_awaited_once()

    async def test_context_manager_does_not_close_injected_client(self) -> None:
        store = _make_credential_store()
        http = AsyncMock(spec=httpx.AsyncClient)
        async with SpotifyClient(credential_store=store, http_client=http):
            pass
        http.aclose.assert_not_awaited()

    async def test_open_parses_valid_expires_at(self) -> None:
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        store = _make_credential_store(expires_at=future)
        http = AsyncMock(spec=httpx.AsyncClient)
        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()
        assert client._expires_at is not None

    async def test_open_forces_refresh_on_invalid_expires_at(self) -> None:
        """An unparsable SPOTIFY_TOKEN_EXPIRES_AT should force proactive refresh."""
        store = _make_credential_store(expires_at="not-a-date")
        http = AsyncMock(spec=httpx.AsyncClient)
        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()
        # _expires_at is set to a past datetime to trigger proactive refresh.
        assert client._expires_at is not None
        assert client._expires_at < datetime.now(UTC)

    async def test_open_handles_naive_expires_at(self) -> None:
        # A naive ISO timestamp (no timezone) should be treated as UTC
        naive_ts = "2030-01-01T12:00:00"
        store = _make_credential_store(expires_at=naive_ts)
        http = AsyncMock(spec=httpx.AsyncClient)
        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()
        assert client._expires_at is not None
        assert client._expires_at.tzinfo is UTC


# ---------------------------------------------------------------------------
# Tests: get_me
# ---------------------------------------------------------------------------


class TestGetMe:
    async def test_get_me_success(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))
        result = await spotify_client.get_me()
        assert result["id"] == "spotify_user_123"
        assert result["display_name"] == "Test User"

    async def test_get_me_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))
        await spotify_client.get_me()
        call_args = http_client.get.call_args
        assert "/me" in call_args[0][0]

    async def test_get_me_includes_bearer_header(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))
        await spotify_client.get_me()
        _, kwargs = http_client.get.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer test_access_token"

    async def test_get_me_raises_if_204(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(return_value=_make_response(204))
        with pytest.raises(SpotifyAPIError) as exc_info:
            await spotify_client.get_me()
        assert "204" in str(exc_info.value)

    async def test_get_me_raises_on_server_error(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(
            return_value=_make_response(500, {"error": "internal server error"})
        )
        with pytest.raises(SpotifyAPIError) as exc_info:
            await spotify_client.get_me()
        assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Tests: get_currently_playing
# ---------------------------------------------------------------------------


class TestGetCurrentlyPlaying:
    async def test_returns_playing_data(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(return_value=_make_response(200, _CURRENTLY_PLAYING_RESPONSE))
        result = await spotify_client.get_currently_playing()
        assert result is not None
        assert result["is_playing"] is True
        assert result["item"]["id"] == "track_abc"

    async def test_returns_none_when_204(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        """HTTP 204 means nothing is currently playing."""
        http_client.get = AsyncMock(return_value=_make_response(204))
        result = await spotify_client.get_currently_playing()
        assert result is None

    async def test_returns_none_when_item_is_null(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        """HTTP 200 with item=null (e.g., private session or ads) should return None."""
        http_client.get = AsyncMock(
            return_value=_make_response(200, {"is_playing": False, "item": None})
        )
        result = await spotify_client.get_currently_playing()
        assert result is None

    async def test_includes_additional_types_param(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(return_value=_make_response(204))
        await spotify_client.get_currently_playing()
        _, kwargs = http_client.get.call_args
        assert kwargs["params"]["additional_types"] == "track"

    async def test_raises_on_error_status(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(
            return_value=_make_response(503, {"error": "service unavailable"})
        )
        with pytest.raises(SpotifyAPIError) as exc_info:
            await spotify_client.get_currently_playing()
        assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Tests: get_recently_played
# ---------------------------------------------------------------------------


class TestGetRecentlyPlayed:
    async def test_returns_items(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(return_value=_make_response(200, _RECENTLY_PLAYED_RESPONSE))
        result = await spotify_client.get_recently_played()
        assert len(result["items"]) == 1
        assert result["items"][0]["track"]["id"] == "track_xyz"

    async def test_sends_after_cursor(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(return_value=_make_response(200, _RECENTLY_PLAYED_RESPONSE))
        await spotify_client.get_recently_played(after=1700000000000)
        _, kwargs = http_client.get.call_args
        assert kwargs["params"]["after"] == 1700000000000

    async def test_omits_after_when_none(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(return_value=_make_response(200, _RECENTLY_PLAYED_RESPONSE))
        await spotify_client.get_recently_played(after=None)
        _, kwargs = http_client.get.call_args
        assert "after" not in kwargs["params"]

    async def test_sends_limit_param(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(return_value=_make_response(200, _RECENTLY_PLAYED_RESPONSE))
        await spotify_client.get_recently_played(limit=10)
        _, kwargs = http_client.get.call_args
        assert kwargs["params"]["limit"] == 10

    async def test_handles_204_gracefully(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        """204 from recently-played returns empty result."""
        http_client.get = AsyncMock(return_value=_make_response(204))
        result = await spotify_client.get_recently_played()
        assert result["items"] == []

    async def test_raises_on_error_status(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(return_value=_make_response(400, {"error": "bad request"}))
        with pytest.raises(SpotifyAPIError) as exc_info:
            await spotify_client.get_recently_played()
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Tests: proactive token refresh
# ---------------------------------------------------------------------------


class TestProactiveRefresh:
    async def test_no_refresh_when_token_fresh(
        self, http_client: AsyncMock, credential_store: AsyncMock
    ) -> None:
        future_expiry = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        store = _make_credential_store(expires_at=future_expiry)
        client = SpotifyClient(credential_store=store, http_client=http_client)
        await client.open()

        http_client.get = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))
        await client.get_me()

        # store() should NOT have been called for token refresh
        store.store.assert_not_awaited()

    async def test_proactive_refresh_when_within_5min(self, http_client: AsyncMock) -> None:
        # Token expiring in 3 minutes → should trigger proactive refresh
        near_expiry = (datetime.now(UTC) + timedelta(minutes=3)).isoformat()
        store = _make_credential_store(expires_at=near_expiry)
        client = SpotifyClient(credential_store=store, http_client=http_client)
        await client.open()

        # First call goes to token endpoint, second call is the actual API call
        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        http_client.get = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))

        await client.get_me()

        # Refresh endpoint was called
        http_client.post.assert_awaited_once()
        call_args = http_client.post.call_args
        assert "accounts.spotify.com" in call_args[0][0]

    async def test_proactive_refresh_when_already_expired(self, http_client: AsyncMock) -> None:
        past_expiry = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        store = _make_credential_store(expires_at=past_expiry)
        client = SpotifyClient(credential_store=store, http_client=http_client)
        await client.open()

        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        http_client.get = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))

        await client.get_me()
        http_client.post.assert_awaited_once()

    async def test_refresh_updates_credential_store(self, http_client: AsyncMock) -> None:
        near_expiry = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        store = _make_credential_store(expires_at=near_expiry)
        client = SpotifyClient(credential_store=store, http_client=http_client)
        await client.open()

        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        http_client.get = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))

        await client.get_me()

        # store() should be called for new access token, refresh token, expires_at
        stored_keys = {call.args[0] for call in store.store.call_args_list}
        assert "SPOTIFY_ACCESS_TOKEN" in stored_keys
        assert "SPOTIFY_TOKEN_EXPIRES_AT" in stored_keys

    async def test_refresh_updates_in_memory_token(self, http_client: AsyncMock) -> None:
        near_expiry = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        store = _make_credential_store(expires_at=near_expiry)
        client = SpotifyClient(credential_store=store, http_client=http_client)
        await client.open()

        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        http_client.get = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))

        await client.get_me()

        assert client._access_token == "new_access_token_xyz"
        assert client._refresh_token == "new_refresh_token_xyz"


# ---------------------------------------------------------------------------
# Tests: automatic 401 retry
# ---------------------------------------------------------------------------


class TestAutoRefreshOn401:
    async def test_401_triggers_refresh_and_retry(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        """A 401 on the first call triggers a token refresh and one retry."""
        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        # First GET → 401, second GET → 200 (after refresh)
        http_client.get = AsyncMock(
            side_effect=[
                _make_response(401),
                _make_response(200, _PROFILE_RESPONSE),
            ]
        )

        result = await spotify_client.get_me()

        assert result["id"] == "spotify_user_123"
        assert http_client.get.await_count == 2
        http_client.post.assert_awaited_once()

    async def test_double_401_raises_auth_error(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        """A 401 on both the first call and retry raises SpotifyAuthError."""
        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        http_client.get = AsyncMock(
            side_effect=[
                _make_response(401),
                _make_response(401),
            ]
        )

        with pytest.raises(SpotifyAuthError):
            await spotify_client.get_me()

    async def test_refresh_request_failure_raises_auth_error(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        """If token refresh itself returns an error, SpotifyAuthError is raised."""
        http_client.post = AsyncMock(return_value=_make_response(400, {"error": "invalid_grant"}))
        http_client.get = AsyncMock(return_value=_make_response(401))

        with pytest.raises(SpotifyAuthError) as exc_info:
            await spotify_client.get_me()
        assert "Re-connect" in str(exc_info.value)

    async def test_refresh_without_refresh_token_raises(self) -> None:
        store = _make_credential_store(refresh_token=None)
        http = AsyncMock(spec=httpx.AsyncClient)
        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()

        http.get = AsyncMock(return_value=_make_response(401))

        with pytest.raises(SpotifyAuthError) as exc_info:
            await client.get_me()
        assert "No refresh token" in str(exc_info.value)

    async def test_refresh_without_client_id_raises(self) -> None:
        store = _make_credential_store(client_id=None)
        http = AsyncMock(spec=httpx.AsyncClient)
        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()

        http.get = AsyncMock(return_value=_make_response(401))

        with pytest.raises(SpotifyAuthError) as exc_info:
            await client.get_me()
        assert "client_id" in str(exc_info.value)

    async def test_refresh_uses_correct_grant_type(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        http_client.get = AsyncMock(
            side_effect=[
                _make_response(401),
                _make_response(200, _PROFILE_RESPONSE),
            ]
        )

        await spotify_client.get_me()

        _, kwargs = http_client.post.call_args
        assert kwargs["data"]["grant_type"] == "refresh_token"
        assert kwargs["data"]["refresh_token"] == "test_refresh_token"

    async def test_refresh_keeps_old_refresh_token_when_not_rotated(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        """If the refresh response omits refresh_token, the old one is kept."""
        refresh_no_rotation = {
            "access_token": "new_access_token_xyz",
            "expires_in": 3600,
            "token_type": "Bearer",
            # refresh_token is intentionally absent
        }
        http_client.post = AsyncMock(return_value=_make_response(200, refresh_no_rotation))
        http_client.get = AsyncMock(
            side_effect=[
                _make_response(401),
                _make_response(200, _PROFILE_RESPONSE),
            ]
        )

        await spotify_client.get_me()

        assert spotify_client._refresh_token == "test_refresh_token"


# ---------------------------------------------------------------------------
# Tests: rate limit handling
# ---------------------------------------------------------------------------


class TestRateLimitHandling:
    async def test_429_raises_rate_limit_error(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(return_value=_make_response(429, headers={"Retry-After": "30"}))

        with pytest.raises(SpotifyRateLimitError) as exc_info:
            await spotify_client.get_me()
        assert exc_info.value.retry_after_s == pytest.approx(30.0)

    async def test_429_without_retry_after_uses_backoff(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(return_value=_make_response(429))

        with pytest.raises(SpotifyRateLimitError) as exc_info:
            await spotify_client.get_me()
        # Should be in a reasonable backoff range (30s ± 25%)
        assert 0 < exc_info.value.retry_after_s <= 600

    async def test_retry_after_header_parsed_correctly(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(
            return_value=_make_response(429, headers={"Retry-After": "120"})
        )

        with pytest.raises(SpotifyRateLimitError) as exc_info:
            await spotify_client.get_me()
        assert exc_info.value.retry_after_s == pytest.approx(120.0)

    async def test_invalid_retry_after_header_uses_backoff(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(
            return_value=_make_response(429, headers={"Retry-After": "not-a-number"})
        )

        with pytest.raises(SpotifyRateLimitError) as exc_info:
            await spotify_client.get_me()
        assert exc_info.value.retry_after_s > 0


# ---------------------------------------------------------------------------
# Tests: _jittered_backoff utility
# ---------------------------------------------------------------------------


class TestJitteredBackoff:
    def test_first_attempt_near_initial(self) -> None:
        delay = _jittered_backoff(0, initial=30.0, maximum=600.0)
        # 30 ± 7.5
        assert 22.5 <= delay <= 37.5

    def test_second_attempt_doubles(self) -> None:
        delay = _jittered_backoff(1, initial=30.0, maximum=600.0)
        # 60 ± 15
        assert 45.0 <= delay <= 75.0

    def test_capped_at_maximum(self) -> None:
        delay = _jittered_backoff(100, initial=30.0, maximum=600.0)
        # Max is 600 ± 150
        assert delay <= 750.0

    def test_positive_delay(self) -> None:
        for attempt in range(10):
            assert _jittered_backoff(attempt) > 0


# ---------------------------------------------------------------------------
# Tests: error propagation
# ---------------------------------------------------------------------------


class TestErrorPropagation:
    async def test_spotify_api_error_has_status_code(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.get = AsyncMock(return_value=_make_response(403, {"error": "forbidden"}))

        with pytest.raises(SpotifyAPIError) as exc_info:
            await spotify_client.get_me()
        assert exc_info.value.status_code == 403

    async def test_spotify_auth_error_message_guides_reauth(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        """Auth errors must guide the user to re-authorize."""
        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        http_client.get = AsyncMock(
            side_effect=[
                _make_response(401),
                _make_response(401),
            ]
        )

        with pytest.raises(SpotifyAuthError) as exc_info:
            await spotify_client.get_me()
        msg = str(exc_info.value)
        assert "Re-connect" in msg or "re-authorize" in msg.lower() or "dashboard" in msg

    async def test_rate_limit_error_is_not_retried_internally(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        """SpotifyClient should NOT internally sleep/retry on 429; caller decides."""
        http_client.get = AsyncMock(return_value=_make_response(429, headers={"Retry-After": "5"}))

        with pytest.raises(SpotifyRateLimitError):
            await spotify_client.get_me()
        # Only one HTTP call — no internal retry loop
        assert http_client.get.await_count == 1
