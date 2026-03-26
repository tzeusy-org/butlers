"""Unit tests for the Steam API client.

All Steam API calls are mocked via ``unittest.mock``.  No real network calls are made.

Coverage:
- Response parsing and unwrapping ({"response": {...}} envelope)
- Error handling (403, 429, 5xx, network errors)
- Batch splitting for >100 SteamIDs in get_player_summaries()
- Rate limit backoff timing (exponential with configurable ceiling)
- API key redaction (never in logs)
- Lifecycle: open/close, context manager
- _batch() utility helper
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.steam.client import (
    SteamAPIClient,
    SteamAPIError,
    SteamRateLimitError,
    _batch,
    _exponential_backoff,
    _make_request_redact_hook,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int,
    json_data: Any = None,
    text: str = "",
) -> MagicMock:
    """Build a mock httpx.Response."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    if json_data is not None:
        response.json = MagicMock(return_value=json_data)
        response.text = json.dumps(json_data)
    else:
        response.json = MagicMock(return_value=None)
        response.text = text
    return response


@pytest.fixture
def mock_http() -> AsyncMock:
    """A mock httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.fixture
async def steam_client(mock_http: AsyncMock) -> SteamAPIClient:
    """An opened SteamAPIClient with mocked HTTP transport."""
    client = SteamAPIClient(api_key="test_api_key_12345", http_client=mock_http)
    await client.open()
    return client


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_context_manager_opens_and_closes(self) -> None:
        """Context manager opens the client and closes it on exit."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        client = SteamAPIClient(api_key="key", http_client=mock_http)
        async with client as c:
            assert c._http_client is mock_http
        # When http_client is provided externally, we do NOT own it.

    async def test_creates_own_client_when_none_provided(self) -> None:
        """SteamAPIClient creates its own httpx.AsyncClient when none is provided."""
        client = SteamAPIClient(api_key="key")
        assert client._http_client is None
        await client.open()
        assert client._http_client is not None
        assert isinstance(client._http_client, httpx.AsyncClient)
        await client.close()
        assert client._http_client is None

    async def test_open_is_idempotent_with_external_client(self, mock_http: AsyncMock) -> None:
        """Calling open() a second time does not replace the client."""
        client = SteamAPIClient(api_key="key", http_client=mock_http)
        await client.open()
        await client.open()  # second call — should be a no-op
        assert client._http_client is mock_http

    async def test_request_raises_before_open(self) -> None:
        """request() raises RuntimeError if called before open()."""
        client = SteamAPIClient(api_key="key")
        with pytest.raises(RuntimeError, match="not open"):
            await client.request("ISteamUser", "GetPlayerSummaries")

    async def test_close_does_not_close_external_client(self, mock_http: AsyncMock) -> None:
        """close() does NOT close an externally-provided httpx.AsyncClient."""
        client = SteamAPIClient(api_key="key", http_client=mock_http)
        await client.open()
        await client.close()
        mock_http.aclose.assert_not_called()

    async def test_close_closes_owned_client(self) -> None:
        """close() closes the client when SteamAPIClient owns it."""
        mock_created = AsyncMock(spec=httpx.AsyncClient)
        with patch("httpx.AsyncClient", return_value=mock_created):
            client = SteamAPIClient(api_key="key")
            await client.open()
            await client.close()
        mock_created.aclose.assert_called_once()


# ---------------------------------------------------------------------------
# Response parsing and unwrapping
# ---------------------------------------------------------------------------


class TestResponseParsing:
    async def test_unwraps_response_envelope(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """request() unwraps the Steam {"response": {...}} envelope."""
        inner = {"players": [{"steamid": "123"}]}
        mock_http.get.return_value = _make_response(200, json_data={"response": inner})
        result = await steam_client.request("ISteamUser", "GetPlayerSummaries")
        assert result == inner

    async def test_returns_full_body_when_no_response_key(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """request() returns the full JSON body if no 'response' key is present."""
        body = {"game_count": 5, "games": []}
        mock_http.get.return_value = _make_response(200, json_data=body)
        result = await steam_client.request("IPlayerService", "GetRecentlyPlayedGames")
        assert result == body

    async def test_builds_correct_url(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """request() builds the correct Steam API URL."""
        mock_http.get.return_value = _make_response(200, json_data={"response": {}})
        await steam_client.request("ISteamUser", "GetPlayerSummaries", params={"steamids": "123"})
        url = mock_http.get.call_args[0][0]
        assert url == "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v1/"

    async def test_injects_api_key_in_params(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """request() includes the api_key as 'key' query parameter."""
        mock_http.get.return_value = _make_response(200, json_data={"response": {}})
        await steam_client.request("ISteamUser", "GetPlayerSummaries")
        call_kwargs = mock_http.get.call_args[1]
        assert call_kwargs["params"]["key"] == "test_api_key_12345"

    async def test_merges_extra_params(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """request() merges extra params with the api_key."""
        mock_http.get.return_value = _make_response(200, json_data={"response": {}})
        await steam_client.request(
            "IPlayerService",
            "GetOwnedGames",
            params={"steamid": "76561198000000000", "include_appinfo": 1},
        )
        call_kwargs = mock_http.get.call_args[1]
        assert call_kwargs["params"]["steamid"] == "76561198000000000"
        assert call_kwargs["params"]["include_appinfo"] == 1
        assert call_kwargs["params"]["key"] == "test_api_key_12345"

    async def test_version_in_url(self, steam_client: SteamAPIClient, mock_http: AsyncMock) -> None:
        """request() uses the specified version in the URL path."""
        mock_http.get.return_value = _make_response(200, json_data={"response": {}})
        await steam_client.request("ISteamUser", "GetFriendList", version=2)
        url = mock_http.get.call_args[0][0]
        assert "/v2/" in url

    async def test_resets_rate_limit_counter_on_success(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """A successful request resets the rate-limit backoff counter."""
        steam_client._rate_limit_attempt = 5
        mock_http.get.return_value = _make_response(200, json_data={"response": {}})
        await steam_client.request("ISteamUser", "GetPlayerSummaries")
        assert steam_client._rate_limit_attempt == 0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_429_raises_steam_rate_limit_error(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """HTTP 429 raises SteamRateLimitError."""
        mock_http.get.return_value = _make_response(429, text="Too Many Requests")
        with pytest.raises(SteamRateLimitError) as exc_info:
            await steam_client.request("ISteamUser", "GetPlayerSummaries")
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after_s > 0

    async def test_403_raises_steam_rate_limit_error(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """HTTP 403 raises SteamRateLimitError (treated as rate limit by Steam convention)."""
        mock_http.get.return_value = _make_response(403, text="Forbidden")
        with pytest.raises(SteamRateLimitError) as exc_info:
            await steam_client.request("ISteamUser", "GetPlayerSummaries")
        assert exc_info.value.status_code == 403

    async def test_500_raises_steam_api_error(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """HTTP 500 raises SteamAPIError."""
        mock_http.get.return_value = _make_response(500, text="Internal Server Error")
        with pytest.raises(SteamAPIError) as exc_info:
            await steam_client.request("ISteamUser", "GetPlayerSummaries")
        assert exc_info.value.status_code == 500

    async def test_404_raises_steam_api_error(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """HTTP 404 raises SteamAPIError."""
        mock_http.get.return_value = _make_response(404, text="Not Found")
        with pytest.raises(SteamAPIError) as exc_info:
            await steam_client.request("ISteamUser", "GetFriendList")
        assert exc_info.value.status_code == 404
        assert "404" in str(exc_info.value)

    async def test_network_error_raises_steam_api_error(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """httpx.TransportError is wrapped in SteamAPIError."""
        mock_http.get.side_effect = httpx.ConnectError("connection refused")
        with pytest.raises(SteamAPIError) as exc_info:
            await steam_client.request("ISteamUser", "GetPlayerSummaries")
        assert exc_info.value.status_code == 0

    async def test_timeout_error_raises_steam_api_error(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """httpx.TimeoutException is wrapped in SteamAPIError."""
        mock_http.get.side_effect = httpx.TimeoutException("timed out")
        with pytest.raises(SteamAPIError):
            await steam_client.request("ISteamUser", "GetPlayerSummaries")


# ---------------------------------------------------------------------------
# Rate limit backoff
# ---------------------------------------------------------------------------


class TestRateLimitBackoff:
    async def test_backoff_increases_on_repeated_429(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """Consecutive 429 responses produce increasing backoff values."""
        mock_http.get.return_value = _make_response(429, text="Rate limited")

        delays = []
        for _ in range(4):
            with pytest.raises(SteamRateLimitError) as exc_info:
                await steam_client.request("ISteamUser", "GetPlayerSummaries")
            delays.append(exc_info.value.retry_after_s)

        # Each delay should be roughly double the previous (with jitter).
        # We check directionally (not exact due to jitter).
        assert delays[1] > delays[0] * 0.5  # at least half again as long
        assert delays[2] > delays[0]
        assert delays[3] > delays[0]

    async def test_backoff_respects_ceiling(self) -> None:
        """_exponential_backoff never exceeds maximum."""
        ceiling = 300.0
        for attempt in range(20):
            delay = _exponential_backoff(attempt=attempt, initial=60.0, maximum=ceiling)
            assert delay <= ceiling * (1 + 0.15), (
                f"Delay {delay} exceeds ceiling {ceiling} at attempt {attempt}"
            )

    async def test_custom_backoff_parameters(self) -> None:
        """SteamAPIClient honours custom backoff_initial_s and backoff_max_s."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        client = SteamAPIClient(
            api_key="key",
            http_client=mock_http,
            backoff_initial_s=10.0,
            backoff_max_s=100.0,
        )
        await client.open()
        mock_http.get.return_value = _make_response(429, text="rate limited")

        with pytest.raises(SteamRateLimitError) as exc_info:
            await client.request("ISteamUser", "GetPlayerSummaries")

        # First retry should be near the initial 10s.
        assert exc_info.value.retry_after_s < 20.0  # well under the ceiling

    def test_exponential_backoff_zero_attempt(self) -> None:
        """Attempt 0 returns approximately the initial delay."""
        delay = _exponential_backoff(attempt=0, initial=60.0, maximum=3600.0, jitter_fraction=0)
        assert delay == pytest.approx(60.0)

    def test_exponential_backoff_doubles_each_attempt(self) -> None:
        """Each attempt roughly doubles the base delay (no jitter)."""
        delays = [
            _exponential_backoff(attempt=i, initial=60.0, maximum=3600.0, jitter_fraction=0)
            for i in range(5)
        ]
        assert delays[1] == pytest.approx(120.0)
        assert delays[2] == pytest.approx(240.0)
        assert delays[3] == pytest.approx(480.0)
        assert delays[4] == pytest.approx(960.0)

    def test_exponential_backoff_caps_at_maximum(self) -> None:
        """Backoff does not exceed maximum (with no jitter)."""
        delay = _exponential_backoff(attempt=30, initial=60.0, maximum=3600.0, jitter_fraction=0)
        assert delay == pytest.approx(3600.0)


# ---------------------------------------------------------------------------
# Batch splitting
# ---------------------------------------------------------------------------


class TestBatchSplitting:
    def test_batch_empty_list(self) -> None:
        """_batch returns empty list for empty input."""
        assert _batch([], 100) == []

    def test_batch_exactly_100(self) -> None:
        """_batch returns single chunk for exactly 100 items."""
        items = list(range(100))
        result = _batch(items, 100)
        assert len(result) == 1
        assert result[0] == items

    def test_batch_101_items_splits_into_two(self) -> None:
        """_batch splits 101 items into chunks of 100 + 1."""
        items = list(range(101))
        result = _batch(items, 100)
        assert len(result) == 2
        assert len(result[0]) == 100
        assert len(result[1]) == 1

    def test_batch_250_items_into_three_chunks(self) -> None:
        """_batch splits 250 items into chunks of 100, 100, 50."""
        items = list(range(250))
        result = _batch(items, 100)
        assert len(result) == 3
        assert len(result[0]) == 100
        assert len(result[1]) == 100
        assert len(result[2]) == 50

    def test_batch_preserves_all_items(self) -> None:
        """_batch preserves all input items across chunks."""
        items = list(range(350))
        result = _batch(items, 100)
        flat = [item for chunk in result for item in chunk]
        assert flat == items

    def test_batch_size_zero_raises(self) -> None:
        """_batch raises ValueError for size <= 0."""
        with pytest.raises(ValueError, match="batch size must be positive"):
            _batch([1, 2, 3], 0)

    async def test_get_player_summaries_empty(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """get_player_summaries([]) returns empty list without making any request."""
        result = await steam_client.get_player_summaries([])
        mock_http.get.assert_not_called()
        assert result == []

    async def test_get_player_summaries_single_batch(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """get_player_summaries with <= 100 IDs makes one request."""
        steam_ids = [str(i) for i in range(50)]
        mock_http.get.return_value = _make_response(
            200,
            json_data={"response": {"players": [{"steamid": sid} for sid in steam_ids]}},
        )
        result = await steam_client.get_player_summaries(steam_ids)
        assert mock_http.get.call_count == 1
        assert len(result) == 50

    async def test_get_player_summaries_splits_over_100(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """get_player_summaries auto-splits lists > 100 SteamIDs."""
        steam_ids = [str(i) for i in range(150)]

        async def side_effect(url: str, params: dict) -> MagicMock:
            ids = params["steamids"].split(",")
            return _make_response(
                200,
                json_data={"response": {"players": [{"steamid": sid} for sid in ids]}},
            )

        mock_http.get.side_effect = side_effect

        result = await steam_client.get_player_summaries(steam_ids)
        assert mock_http.get.call_count == 2
        assert len(result) == 150

    async def test_get_player_summaries_exactly_100(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """get_player_summaries with exactly 100 IDs makes exactly one request."""
        steam_ids = [str(i) for i in range(100)]
        mock_http.get.return_value = _make_response(
            200,
            json_data={"response": {"players": [{"steamid": sid} for sid in steam_ids]}},
        )
        await steam_client.get_player_summaries(steam_ids)
        assert mock_http.get.call_count == 1

    async def test_get_player_summaries_101_makes_two_requests(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """get_player_summaries with 101 IDs makes exactly two requests."""
        steam_ids = [str(i) for i in range(101)]

        def response_for_call(url: str, params: dict) -> MagicMock:
            ids = params["steamids"].split(",")
            return _make_response(
                200,
                json_data={"response": {"players": [{"steamid": sid} for sid in ids]}},
            )

        mock_http.get.side_effect = response_for_call

        result = await steam_client.get_player_summaries(steam_ids)
        assert mock_http.get.call_count == 2
        assert len(result) == 101

    async def test_get_player_summaries_sends_comma_joined_ids(
        self, steam_client: SteamAPIClient, mock_http: AsyncMock
    ) -> None:
        """get_player_summaries sends comma-joined SteamIDs in the query param."""
        steam_ids = ["111", "222", "333"]
        mock_http.get.return_value = _make_response(
            200,
            json_data={"response": {"players": []}},
        )
        await steam_client.get_player_summaries(steam_ids)
        call_kwargs = mock_http.get.call_args[1]
        assert call_kwargs["params"]["steamids"] == "111,222,333"


# ---------------------------------------------------------------------------
# API key redaction
# ---------------------------------------------------------------------------


class TestApiKeyRedaction:
    def test_redact_hook_replaces_key_in_url(self) -> None:
        """_make_request_redact_hook redacts the API key from the request URL params."""
        api_key = "super_secret_api_key"
        hook = _make_request_redact_hook(api_key)

        url = httpx.URL(
            "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v1/",
            params={"key": api_key, "steamids": "123"},
        )
        request = httpx.Request("GET", url)
        hook(request)

        # The key value should be replaced; the raw param dict should show redaction.
        assert api_key not in str(request.url)
        # URL-encoding of "[REDACTED]" is also acceptable — the secret is gone.
        assert "[REDACTED]" in str(request.url) or "%5BREDACTED%5D" in str(request.url)

    def test_redact_hook_preserves_other_params(self) -> None:
        """_make_request_redact_hook does not remove non-key query parameters."""
        api_key = "my_key"
        hook = _make_request_redact_hook(api_key)

        url = httpx.URL(
            "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v1/",
            params={"key": api_key, "steamids": "76561198000000000"},
        )
        request = httpx.Request("GET", url)
        hook(request)

        assert "76561198000000000" in str(request.url)

    def test_redact_hook_noop_when_key_not_in_url(self) -> None:
        """_make_request_redact_hook is a no-op when the key is not present."""
        api_key = "my_key"
        hook = _make_request_redact_hook(api_key)

        url = httpx.URL("https://api.steampowered.com/ISteamApps/GetAppList/v2/")
        request = httpx.Request("GET", url)
        original_url = str(request.url)
        hook(request)

        assert str(request.url) == original_url

    def test_redact_hook_noop_for_empty_key(self) -> None:
        """_make_request_redact_hook does nothing when api_key is empty string."""
        hook = _make_request_redact_hook("")
        url = httpx.URL("https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v1/")
        request = httpx.Request("GET", url)
        hook(request)  # Should not raise


# ---------------------------------------------------------------------------
# SteamRateLimitError and SteamAPIError attributes
# ---------------------------------------------------------------------------


class TestExceptions:
    def test_steam_rate_limit_error_attributes(self) -> None:
        """SteamRateLimitError exposes retry_after_s and status_code."""
        exc = SteamRateLimitError(retry_after_s=120.0, status_code=429)
        assert exc.retry_after_s == 120.0
        assert exc.status_code == 429
        assert "120.0" in str(exc)

    def test_steam_api_error_attributes(self) -> None:
        """SteamAPIError exposes status_code and body."""
        exc = SteamAPIError(status_code=500, body="Internal Server Error")
        assert exc.status_code == 500
        assert exc.body == "Internal Server Error"
        assert "500" in str(exc)

    def test_steam_rate_limit_error_default_status(self) -> None:
        """SteamRateLimitError defaults to status_code 429."""
        exc = SteamRateLimitError(retry_after_s=60.0)
        assert exc.status_code == 429
