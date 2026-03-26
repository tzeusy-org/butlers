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
    json_data: Any = None,
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
        http_client.request = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))
        result = await spotify_client.get_me()
        assert result["id"] == "spotify_user_123"
        assert result["display_name"] == "Test User"

    async def test_get_me_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))
        await spotify_client.get_me()
        call_args = http_client.request.call_args
        assert "/me" in call_args[0][1]

    async def test_get_me_includes_bearer_header(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))
        await spotify_client.get_me()
        _, kwargs = http_client.request.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer test_access_token"

    async def test_get_me_raises_if_204(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        with pytest.raises(SpotifyAPIError) as exc_info:
            await spotify_client.get_me()
        assert "204" in str(exc_info.value)

    async def test_get_me_raises_on_server_error(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(
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
        http_client.request = AsyncMock(
            return_value=_make_response(200, _CURRENTLY_PLAYING_RESPONSE)
        )
        result = await spotify_client.get_currently_playing()
        assert result is not None
        assert result["is_playing"] is True
        assert result["item"]["id"] == "track_abc"

    async def test_returns_none_when_204(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        """HTTP 204 means nothing is currently playing."""
        http_client.request = AsyncMock(return_value=_make_response(204))
        result = await spotify_client.get_currently_playing()
        assert result is None

    async def test_returns_none_when_item_is_null(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        """HTTP 200 with item=null (e.g., private session or ads) should return None."""
        http_client.request = AsyncMock(
            return_value=_make_response(200, {"is_playing": False, "item": None})
        )
        result = await spotify_client.get_currently_playing()
        assert result is None

    async def test_includes_additional_types_param(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.get_currently_playing()
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["additional_types"] == "track"

    async def test_raises_on_error_status(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(
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
        http_client.request = AsyncMock(return_value=_make_response(200, _RECENTLY_PLAYED_RESPONSE))
        result = await spotify_client.get_recently_played()
        assert len(result["items"]) == 1
        assert result["items"][0]["track"]["id"] == "track_xyz"

    async def test_sends_after_cursor(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, _RECENTLY_PLAYED_RESPONSE))
        await spotify_client.get_recently_played(after=1700000000000)
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["after"] == 1700000000000

    async def test_omits_after_when_none(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, _RECENTLY_PLAYED_RESPONSE))
        await spotify_client.get_recently_played(after=None)
        _, kwargs = http_client.request.call_args
        assert "after" not in kwargs["params"]

    async def test_sends_limit_param(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, _RECENTLY_PLAYED_RESPONSE))
        await spotify_client.get_recently_played(limit=10)
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["limit"] == 10

    async def test_handles_204_gracefully(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        """204 from recently-played returns empty result."""
        http_client.request = AsyncMock(return_value=_make_response(204))
        result = await spotify_client.get_recently_played()
        assert result["items"] == []

    async def test_raises_on_error_status(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(400, {"error": "bad request"}))
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

        http_client.request = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))
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
        http_client.request = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))

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
        http_client.request = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))

        await client.get_me()
        http_client.post.assert_awaited_once()

    async def test_refresh_updates_credential_store(self, http_client: AsyncMock) -> None:
        near_expiry = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        store = _make_credential_store(expires_at=near_expiry)
        client = SpotifyClient(credential_store=store, http_client=http_client)
        await client.open()

        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        http_client.request = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))

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
        http_client.request = AsyncMock(return_value=_make_response(200, _PROFILE_RESPONSE))

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
        http_client.request = AsyncMock(
            side_effect=[
                _make_response(401),
                _make_response(200, _PROFILE_RESPONSE),
            ]
        )

        result = await spotify_client.get_me()

        assert result["id"] == "spotify_user_123"
        assert http_client.request.await_count == 2
        http_client.post.assert_awaited_once()

    async def test_double_401_raises_auth_error(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        """A 401 on both the first call and retry raises SpotifyAuthError."""
        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        http_client.request = AsyncMock(
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
        http_client.request = AsyncMock(return_value=_make_response(401))

        with pytest.raises(SpotifyAuthError) as exc_info:
            await spotify_client.get_me()
        assert "Re-connect" in str(exc_info.value)

    async def test_refresh_without_refresh_token_raises(self) -> None:
        store = _make_credential_store(refresh_token=None)
        http = AsyncMock(spec=httpx.AsyncClient)
        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()

        http.request = AsyncMock(return_value=_make_response(401))

        with pytest.raises(SpotifyAuthError) as exc_info:
            await client.get_me()
        assert "No refresh token" in str(exc_info.value)

    async def test_refresh_without_client_id_raises(self) -> None:
        store = _make_credential_store(client_id=None)
        http = AsyncMock(spec=httpx.AsyncClient)
        client = SpotifyClient(credential_store=store, http_client=http)
        await client.open()

        http.request = AsyncMock(return_value=_make_response(401))

        with pytest.raises(SpotifyAuthError) as exc_info:
            await client.get_me()
        assert "client_id" in str(exc_info.value)

    async def test_refresh_uses_correct_grant_type(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        http_client.request = AsyncMock(
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
        http_client.request = AsyncMock(
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
        http_client.request = AsyncMock(
            return_value=_make_response(429, headers={"Retry-After": "30"})
        )

        with pytest.raises(SpotifyRateLimitError) as exc_info:
            await spotify_client.get_me()
        assert exc_info.value.retry_after_s == pytest.approx(30.0)

    async def test_429_without_retry_after_uses_backoff(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(429))

        with pytest.raises(SpotifyRateLimitError) as exc_info:
            await spotify_client.get_me()
        # Should be in a reasonable backoff range (30s ± 25%)
        assert 0 < exc_info.value.retry_after_s <= 600

    async def test_retry_after_header_parsed_correctly(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(
            return_value=_make_response(429, headers={"Retry-After": "120"})
        )

        with pytest.raises(SpotifyRateLimitError) as exc_info:
            await spotify_client.get_me()
        assert exc_info.value.retry_after_s == pytest.approx(120.0)

    async def test_invalid_retry_after_header_uses_backoff(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(
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
        http_client.request = AsyncMock(return_value=_make_response(403, {"error": "forbidden"}))

        with pytest.raises(SpotifyAPIError) as exc_info:
            await spotify_client.get_me()
        assert exc_info.value.status_code == 403

    async def test_spotify_auth_error_message_guides_reauth(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        """Auth errors must guide the user to re-authorize."""
        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        http_client.request = AsyncMock(
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
        http_client.request = AsyncMock(
            return_value=_make_response(429, headers={"Retry-After": "5"})
        )

        with pytest.raises(SpotifyRateLimitError):
            await spotify_client.get_me()
        # Only one HTTP call — no internal retry loop
        assert http_client.request.await_count == 1


# ---------------------------------------------------------------------------
# Tests: _put / _post / _delete helpers (auth/retry/rate-limit parity)
# ---------------------------------------------------------------------------


class TestPutHelper:
    async def test_put_success_200(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        body = {"snapshot_id": "abc"}
        http_client.request = AsyncMock(return_value=_make_response(200, body))
        result = await spotify_client._put("/me/player/play")
        assert result == body

    async def test_put_success_204_returns_none(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        result = await spotify_client._put("/me/player/pause")
        assert result is None

    async def test_put_401_triggers_refresh_and_retry(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        http_client.request = AsyncMock(
            side_effect=[
                _make_response(401),
                _make_response(204),
            ]
        )
        result = await spotify_client._put("/me/player/play")
        assert result is None
        assert http_client.request.await_count == 2

    async def test_put_double_401_raises_auth_error(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        http_client.request = AsyncMock(side_effect=[_make_response(401), _make_response(401)])
        with pytest.raises(SpotifyAuthError):
            await spotify_client._put("/me/player/play")

    async def test_put_429_raises_rate_limit(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(
            return_value=_make_response(429, headers={"Retry-After": "10"})
        )
        with pytest.raises(SpotifyRateLimitError) as exc_info:
            await spotify_client._put("/me/player/play")
        assert exc_info.value.retry_after_s == pytest.approx(10.0)

    async def test_put_other_error_raises_api_error(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(403, {"error": "forbidden"}))
        with pytest.raises(SpotifyAPIError) as exc_info:
            await spotify_client._put("/me/player/play")
        assert exc_info.value.status_code == 403

    async def test_put_sends_json_body(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client._put("/me/player/play", json={"uris": ["spotify:track:abc"]})
        _, kwargs = http_client.request.call_args
        assert kwargs["json"] == {"uris": ["spotify:track:abc"]}

    async def test_put_sends_params(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client._put("/me/player/volume", params={"volume_percent": 50})
        _, kwargs = http_client.request.call_args
        assert kwargs["params"] == {"volume_percent": 50}


class TestPostHelper:
    async def test_post_success_201(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        body = {"id": "new_playlist_id"}
        http_client.request = AsyncMock(return_value=_make_response(201, body))
        result = await spotify_client._post("/users/user123/playlists", json={"name": "My PL"})
        assert result == body

    async def test_post_success_204_returns_none(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        result = await spotify_client._post("/me/player/next")
        assert result is None

    async def test_post_401_triggers_refresh_and_retry(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        # API calls use http_client.request; token refresh uses http_client.post.
        # Mock them independently: request raises 401 first, then succeeds.
        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        http_client.request = AsyncMock(side_effect=[_make_response(401), _make_response(204)])
        result = await spotify_client._post("/me/player/next")
        assert result is None
        assert http_client.request.await_count == 2

    async def test_post_429_raises_rate_limit(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(
            return_value=_make_response(429, headers={"Retry-After": "5"})
        )
        with pytest.raises(SpotifyRateLimitError):
            await spotify_client._post("/me/player/next")

    async def test_post_other_error_raises_api_error(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(400, {"error": "bad_request"}))
        with pytest.raises(SpotifyAPIError) as exc_info:
            await spotify_client._post("/me/player/next")
        assert exc_info.value.status_code == 400


class TestDeleteHelper:
    async def test_delete_success_200(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        body = {"snapshot_id": "snap123"}
        http_client.request = AsyncMock(return_value=_make_response(200, body))
        result = await spotify_client._delete("/playlists/pl1/tracks", json={"tracks": []})
        assert result == body

    async def test_delete_success_204_returns_none(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        result = await spotify_client._delete("/me/tracks", json={"ids": ["track1"]})
        assert result is None

    async def test_delete_401_triggers_refresh_and_retry(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.post = AsyncMock(return_value=_make_response(200, _REFRESH_TOKEN_RESPONSE))
        http_client.request = AsyncMock(side_effect=[_make_response(401), _make_response(204)])
        result = await spotify_client._delete("/me/tracks", json={"ids": ["id1"]})
        assert result is None
        assert http_client.request.await_count == 2

    async def test_delete_429_raises_rate_limit(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(
            return_value=_make_response(429, headers={"Retry-After": "15"})
        )
        with pytest.raises(SpotifyRateLimitError) as exc_info:
            await spotify_client._delete("/me/tracks")
        assert exc_info.value.retry_after_s == pytest.approx(15.0)

    async def test_delete_other_error_raises_api_error(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(403, {"error": "forbidden"}))
        with pytest.raises(SpotifyAPIError) as exc_info:
            await spotify_client._delete("/me/tracks")
        assert exc_info.value.status_code == 403

    async def test_delete_sends_json_body(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client._delete("/me/tracks", json={"ids": ["id1", "id2"]})
        _, kwargs = http_client.request.call_args
        assert kwargs["json"] == {"ids": ["id1", "id2"]}


# ---------------------------------------------------------------------------
# Tests: search
# ---------------------------------------------------------------------------


class TestSearch:
    async def test_search_returns_results(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        body = {"tracks": {"items": [{"id": "t1", "name": "Song"}], "total": 1}}
        http_client.request = AsyncMock(return_value=_make_response(200, body))
        result = await spotify_client.search("radiohead")
        assert "tracks" in result
        assert result["tracks"]["items"][0]["id"] == "t1"

    async def test_search_default_type_is_track(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {}))
        await spotify_client.search("test")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["type"] == "track"

    async def test_search_custom_types(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {}))
        await spotify_client.search("test", types=["artist", "album"])
        _, kwargs = http_client.request.call_args
        assert "artist" in kwargs["params"]["type"]
        assert "album" in kwargs["params"]["type"]

    async def test_search_sends_query(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {}))
        await spotify_client.search("kid cudi")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["q"] == "kid cudi"

    async def test_search_includes_limit_and_offset(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {}))
        await spotify_client.search("test", limit=5, offset=10)
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["limit"] == 5
        assert kwargs["params"]["offset"] == 10

    async def test_search_includes_market(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {}))
        await spotify_client.search("test", market="GB")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["market"] == "GB"

    async def test_search_204_returns_empty_dict(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        result = await spotify_client.search("test")
        assert result == {}

    async def test_search_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {}))
        await spotify_client.search("test")
        call_url = http_client.request.call_args[0][1]
        assert "/search" in call_url


# ---------------------------------------------------------------------------
# Tests: get_playback_state
# ---------------------------------------------------------------------------


class TestGetPlaybackState:
    async def test_returns_playback_state(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        body = {
            "is_playing": True,
            "shuffle_state": False,
            "repeat_state": "off",
            "device": {"id": "dev1", "name": "My Speaker"},
        }
        http_client.request = AsyncMock(return_value=_make_response(200, body))
        result = await spotify_client.get_playback_state()
        assert result is not None
        assert result["is_playing"] is True
        assert result["device"]["id"] == "dev1"

    async def test_returns_none_when_no_active_device(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        result = await spotify_client.get_playback_state()
        assert result is None

    async def test_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.get_playback_state()
        call_url = http_client.request.call_args[0][1]
        assert "/me/player" in call_url


# ---------------------------------------------------------------------------
# Tests: play
# ---------------------------------------------------------------------------


class TestPlay:
    async def test_play_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.play()
        call_url = http_client.request.call_args[0][1]
        assert "/me/player/play" in call_url

    async def test_play_with_context_uri(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.play(context_uri="spotify:album:abc")
        _, kwargs = http_client.request.call_args
        assert kwargs["json"]["context_uri"] == "spotify:album:abc"

    async def test_play_with_uris(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.play(uris=["spotify:track:t1", "spotify:track:t2"])
        _, kwargs = http_client.request.call_args
        assert kwargs["json"]["uris"] == ["spotify:track:t1", "spotify:track:t2"]

    async def test_play_with_device_id(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.play(device_id="device123")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["device_id"] == "device123"

    async def test_play_with_position_ms(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.play(position_ms=30000)
        _, kwargs = http_client.request.call_args
        assert kwargs["json"]["position_ms"] == 30000

    async def test_play_no_body_when_no_params(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.play()
        _, kwargs = http_client.request.call_args
        assert kwargs.get("json") is None


# ---------------------------------------------------------------------------
# Tests: pause
# ---------------------------------------------------------------------------


class TestPause:
    async def test_pause_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.pause()
        call_url = http_client.request.call_args[0][1]
        assert "/me/player/pause" in call_url

    async def test_pause_with_device_id(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.pause(device_id="dev1")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["device_id"] == "dev1"

    async def test_pause_no_params_when_no_device(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.pause()
        _, kwargs = http_client.request.call_args
        assert kwargs.get("params") is None


# ---------------------------------------------------------------------------
# Tests: skip_to_next / skip_to_previous
# ---------------------------------------------------------------------------


class TestSkip:
    async def test_skip_to_next_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.skip_to_next()
        call_url = http_client.request.call_args[0][1]
        assert "/me/player/next" in call_url

    async def test_skip_to_previous_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.skip_to_previous()
        call_url = http_client.request.call_args[0][1]
        assert "/me/player/previous" in call_url

    async def test_skip_to_next_with_device_id(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.skip_to_next(device_id="dev1")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["device_id"] == "dev1"

    async def test_skip_to_previous_with_device_id(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.skip_to_previous(device_id="dev1")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["device_id"] == "dev1"


# ---------------------------------------------------------------------------
# Tests: seek_to_position
# ---------------------------------------------------------------------------


class TestSeekToPosition:
    async def test_seek_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.seek_to_position(60000)
        call_url = http_client.request.call_args[0][1]
        assert "/me/player/seek" in call_url

    async def test_seek_sends_position_ms(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.seek_to_position(45000)
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["position_ms"] == 45000

    async def test_seek_with_device_id(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.seek_to_position(5000, device_id="devX")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["device_id"] == "devX"


# ---------------------------------------------------------------------------
# Tests: set_volume
# ---------------------------------------------------------------------------


class TestSetVolume:
    async def test_set_volume_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.set_volume(75)
        call_url = http_client.request.call_args[0][1]
        assert "/me/player/volume" in call_url

    async def test_set_volume_sends_correct_param(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.set_volume(50)
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["volume_percent"] == 50


# ---------------------------------------------------------------------------
# Tests: set_shuffle
# ---------------------------------------------------------------------------


class TestSetShuffle:
    async def test_set_shuffle_on(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.set_shuffle(True)
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["state"] == "true"

    async def test_set_shuffle_off(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.set_shuffle(False)
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["state"] == "false"

    async def test_set_shuffle_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.set_shuffle(True)
        call_url = http_client.request.call_args[0][1]
        assert "/me/player/shuffle" in call_url


# ---------------------------------------------------------------------------
# Tests: set_repeat
# ---------------------------------------------------------------------------


class TestSetRepeat:
    async def test_set_repeat_track(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.set_repeat("track")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["state"] == "track"

    async def test_set_repeat_context(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.set_repeat("context")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["state"] == "context"

    async def test_set_repeat_off(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.set_repeat("off")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["state"] == "off"

    async def test_set_repeat_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.set_repeat("off")
        call_url = http_client.request.call_args[0][1]
        assert "/me/player/repeat" in call_url


# ---------------------------------------------------------------------------
# Tests: get_queue / add_to_queue
# ---------------------------------------------------------------------------


class TestQueue:
    async def test_get_queue_returns_data(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        body = {
            "currently_playing": {"id": "t1"},
            "queue": [{"id": "t2"}, {"id": "t3"}],
        }
        http_client.request = AsyncMock(return_value=_make_response(200, body))
        result = await spotify_client.get_queue()
        assert len(result["queue"]) == 2
        assert result["currently_playing"]["id"] == "t1"

    async def test_get_queue_204_returns_empty(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        result = await spotify_client.get_queue()
        assert result == {"currently_playing": None, "queue": []}

    async def test_get_queue_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"queue": []}))
        await spotify_client.get_queue()
        call_url = http_client.request.call_args[0][1]
        assert "/me/player/queue" in call_url

    async def test_add_to_queue_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.add_to_queue("spotify:track:abc")
        call_url = http_client.request.call_args[0][1]
        assert "/me/player/queue" in call_url

    async def test_add_to_queue_sends_uri(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.add_to_queue("spotify:track:abc123")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["uri"] == "spotify:track:abc123"

    async def test_add_to_queue_with_device_id(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.add_to_queue("spotify:track:abc", device_id="dev1")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["device_id"] == "dev1"


# ---------------------------------------------------------------------------
# Tests: get_top_items
# ---------------------------------------------------------------------------


class TestGetTopItems:
    async def test_get_top_tracks(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        body = {"items": [{"id": "t1"}, {"id": "t2"}], "total": 2}
        http_client.request = AsyncMock(return_value=_make_response(200, body))
        result = await spotify_client.get_top_items("tracks")
        assert len(result["items"]) == 2

    async def test_get_top_artists_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"items": []}))
        await spotify_client.get_top_items("artists")
        call_url = http_client.request.call_args[0][1]
        assert "/me/top/artists" in call_url

    async def test_get_top_items_time_range(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"items": []}))
        await spotify_client.get_top_items("tracks", time_range="short_term")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["time_range"] == "short_term"

    async def test_get_top_items_limit_offset(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"items": []}))
        await spotify_client.get_top_items("tracks", limit=10, offset=5)
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["limit"] == 10
        assert kwargs["params"]["offset"] == 5

    async def test_get_top_items_204_returns_empty(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        result = await spotify_client.get_top_items("tracks")
        assert result["items"] == []


# ---------------------------------------------------------------------------
# Tests: get_recommendations
# ---------------------------------------------------------------------------


class TestGetRecommendations:
    async def test_returns_track_recommendations(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        body = {"tracks": [{"id": "r1"}, {"id": "r2"}], "seeds": []}
        http_client.request = AsyncMock(return_value=_make_response(200, body))
        result = await spotify_client.get_recommendations(seed_tracks=["t1", "t2"])
        assert len(result["tracks"]) == 2

    async def test_sends_seed_tracks(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"tracks": []}))
        await spotify_client.get_recommendations(seed_tracks=["id1", "id2"])
        _, kwargs = http_client.request.call_args
        assert "id1" in kwargs["params"]["seed_tracks"]
        assert "id2" in kwargs["params"]["seed_tracks"]

    async def test_sends_seed_artists(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"tracks": []}))
        await spotify_client.get_recommendations(seed_artists=["artist1"])
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["seed_artists"] == "artist1"

    async def test_sends_seed_genres(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"tracks": []}))
        await spotify_client.get_recommendations(seed_genres=["pop", "rock"])
        _, kwargs = http_client.request.call_args
        assert "pop" in kwargs["params"]["seed_genres"]

    async def test_sends_audio_feature_kwargs(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"tracks": []}))
        await spotify_client.get_recommendations(seed_tracks=["t1"], min_energy=0.5)
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["min_energy"] == 0.5

    async def test_gracefully_handles_403(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(403, {"error": "forbidden"}))
        result = await spotify_client.get_recommendations(seed_tracks=["t1"])
        assert result == {"tracks": []}

    async def test_gracefully_handles_404(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(404, {"error": "not found"}))
        result = await spotify_client.get_recommendations(seed_tracks=["t1"])
        assert result == {"tracks": []}

    async def test_raises_for_other_errors(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(500, {"error": "server error"}))
        with pytest.raises(SpotifyAPIError) as exc_info:
            await spotify_client.get_recommendations(seed_tracks=["t1"])
        assert exc_info.value.status_code == 500

    async def test_204_returns_empty_tracks(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        result = await spotify_client.get_recommendations(seed_tracks=["t1"])
        assert result == {"tracks": []}


# ---------------------------------------------------------------------------
# Tests: playlists
# ---------------------------------------------------------------------------


class TestPlaylists:
    async def test_get_user_playlists_returns_items(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        body = {"items": [{"id": "pl1", "name": "My Playlist"}], "total": 1}
        http_client.request = AsyncMock(return_value=_make_response(200, body))
        result = await spotify_client.get_user_playlists()
        assert len(result["items"]) == 1

    async def test_get_user_playlists_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"items": []}))
        await spotify_client.get_user_playlists()
        call_url = http_client.request.call_args[0][1]
        assert "/me/playlists" in call_url

    async def test_get_user_playlists_204_returns_empty(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        result = await spotify_client.get_user_playlists()
        assert result["items"] == []

    async def test_get_playlist_by_id(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        body = {"id": "pl123", "name": "Cool Playlist", "tracks": {"items": []}}
        http_client.request = AsyncMock(return_value=_make_response(200, body))
        result = await spotify_client.get_playlist("pl123")
        assert result["id"] == "pl123"

    async def test_get_playlist_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"id": "pl123"}))
        await spotify_client.get_playlist("pl123")
        call_url = http_client.request.call_args[0][1]
        assert "/playlists/pl123" in call_url

    async def test_get_playlist_with_fields(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"id": "pl123"}))
        await spotify_client.get_playlist("pl123", fields="name,tracks")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["fields"] == "name,tracks"

    async def test_get_playlist_204_raises_api_error(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        with pytest.raises(SpotifyAPIError) as exc_info:
            await spotify_client.get_playlist("pl123")
        assert exc_info.value.status_code == 204

    async def test_create_playlist_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        body = {"id": "new_pl", "name": "Test PL"}
        http_client.request = AsyncMock(return_value=_make_response(201, body))
        result = await spotify_client.create_playlist("user123", "Test PL")
        assert result["id"] == "new_pl"
        call_url = http_client.request.call_args[0][1]
        assert "/users/user123/playlists" in call_url

    async def test_create_playlist_sends_name(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(201, {"id": "p1"}))
        await spotify_client.create_playlist("user1", "My Playlist")
        _, kwargs = http_client.request.call_args
        assert kwargs["json"]["name"] == "My Playlist"

    async def test_create_playlist_default_public_true(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(201, {"id": "p1"}))
        await spotify_client.create_playlist("user1", "PL")
        _, kwargs = http_client.request.call_args
        assert kwargs["json"]["public"] is True

    async def test_create_playlist_private(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(201, {"id": "p1"}))
        await spotify_client.create_playlist("user1", "Private PL", public=False)
        _, kwargs = http_client.request.call_args
        assert kwargs["json"]["public"] is False

    async def test_add_tracks_to_playlist(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        body = {"snapshot_id": "snap123"}
        http_client.request = AsyncMock(return_value=_make_response(201, body))
        result = await spotify_client.add_tracks_to_playlist(
            "pl1", ["spotify:track:t1", "spotify:track:t2"]
        )
        assert result["snapshot_id"] == "snap123"

    async def test_add_tracks_to_playlist_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(201, {"snapshot_id": "s1"}))
        await spotify_client.add_tracks_to_playlist("pl1", ["spotify:track:t1"])
        call_url = http_client.request.call_args[0][1]
        assert "/playlists/pl1/tracks" in call_url

    async def test_add_tracks_with_position(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(201, {"snapshot_id": "s1"}))
        await spotify_client.add_tracks_to_playlist("pl1", ["spotify:track:t1"], position=3)
        _, kwargs = http_client.request.call_args
        assert kwargs["json"]["position"] == 3

    async def test_remove_tracks_from_playlist(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        body = {"snapshot_id": "snap456"}
        http_client.request = AsyncMock(return_value=_make_response(200, body))
        result = await spotify_client.remove_tracks_from_playlist("pl1", ["spotify:track:t1"])
        assert result["snapshot_id"] == "snap456"

    async def test_remove_tracks_from_playlist_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"snapshot_id": "s"}))
        await spotify_client.remove_tracks_from_playlist("pl1", ["spotify:track:t1"])
        call_url = http_client.request.call_args[0][1]
        assert "/playlists/pl1/tracks" in call_url

    async def test_remove_tracks_sends_correct_body(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"snapshot_id": "s"}))
        await spotify_client.remove_tracks_from_playlist(
            "pl1", ["spotify:track:t1", "spotify:track:t2"]
        )
        _, kwargs = http_client.request.call_args
        tracks = kwargs["json"]["tracks"]
        assert {"uri": "spotify:track:t1"} in tracks
        assert {"uri": "spotify:track:t2"} in tracks

    async def test_remove_tracks_with_snapshot_id(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"snapshot_id": "s"}))
        await spotify_client.remove_tracks_from_playlist(
            "pl1", ["spotify:track:t1"], snapshot_id="snap_old"
        )
        _, kwargs = http_client.request.call_args
        assert kwargs["json"]["snapshot_id"] == "snap_old"


# ---------------------------------------------------------------------------
# Tests: saved tracks (library)
# ---------------------------------------------------------------------------


class TestSavedTracks:
    async def test_get_saved_tracks_returns_items(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        body = {
            "items": [{"track": {"id": "t1"}, "added_at": "2024-01-01T00:00:00Z"}],
            "total": 1,
        }
        http_client.request = AsyncMock(return_value=_make_response(200, body))
        result = await spotify_client.get_saved_tracks()
        assert len(result["items"]) == 1

    async def test_get_saved_tracks_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"items": []}))
        await spotify_client.get_saved_tracks()
        call_url = http_client.request.call_args[0][1]
        assert "/me/tracks" in call_url

    async def test_get_saved_tracks_204_returns_empty(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        result = await spotify_client.get_saved_tracks()
        assert result["items"] == []

    async def test_get_saved_tracks_with_market(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, {"items": []}))
        await spotify_client.get_saved_tracks(market="US")
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["market"] == "US"

    async def test_save_tracks_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.save_tracks(["id1", "id2"])
        call_url = http_client.request.call_args[0][1]
        assert "/me/tracks" in call_url

    async def test_save_tracks_sends_ids(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.save_tracks(["id1", "id2"])
        _, kwargs = http_client.request.call_args
        assert kwargs["json"]["ids"] == ["id1", "id2"]

    async def test_remove_saved_tracks_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.remove_saved_tracks(["id1"])
        call_url = http_client.request.call_args[0][1]
        assert "/me/tracks" in call_url

    async def test_remove_saved_tracks_sends_ids(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        await spotify_client.remove_saved_tracks(["id1", "id2"])
        _, kwargs = http_client.request.call_args
        assert kwargs["json"]["ids"] == ["id1", "id2"]

    async def test_check_saved_tracks_returns_bools(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        # Spotify returns a JSON array like [true, false]
        http_client.request = AsyncMock(return_value=_make_response(200, [True, False]))
        result = await spotify_client.check_saved_tracks(["id1", "id2"])
        assert result == [True, False]

    async def test_check_saved_tracks_calls_correct_endpoint(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, [True]))
        await spotify_client.check_saved_tracks(["id1"])
        call_url = http_client.request.call_args[0][1]
        assert "/me/tracks/contains" in call_url

    async def test_check_saved_tracks_sends_ids_as_csv(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(200, [True, False]))
        await spotify_client.check_saved_tracks(["id1", "id2"])
        _, kwargs = http_client.request.call_args
        assert kwargs["params"]["ids"] == "id1,id2"

    async def test_check_saved_tracks_204_returns_false_list(
        self, spotify_client: SpotifyClient, http_client: AsyncMock
    ) -> None:
        http_client.request = AsyncMock(return_value=_make_response(204))
        result = await spotify_client.check_saved_tracks(["id1", "id2"])
        assert result == [False, False]
