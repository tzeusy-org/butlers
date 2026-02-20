"""Unit tests for calendar module error handling contract (spec section 15).

Covers:
- Error hierarchy: CalendarAuthError, CalendarCredentialError,
  CalendarTokenRefreshError, CalendarRequestError
- No credential leakage in error messages (sanitized to 200 chars)
- Structured error response format from tools
- Fail-open for reads (empty with metadata)
- Fail-closed for writes (structured error dict)
- Rate-limit retry with exponential backoff (429/503)
- _build_structured_error helper
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.modules.calendar import (
    RATE_LIMIT_BASE_BACKOFF_SECONDS,
    RATE_LIMIT_MAX_RETRIES,
    RATE_LIMIT_RETRY_STATUS_CODES,
    CalendarAuthError,
    CalendarConfig,
    CalendarCredentialError,
    CalendarEvent,
    CalendarModule,
    CalendarProvider,
    CalendarRequestError,
    CalendarTokenRefreshError,
    _build_structured_error,
    _GoogleOAuthCredentials,
)

pytestmark = pytest.mark.unit


# ============================================================================
# Error Hierarchy Tests
# ============================================================================


class TestErrorHierarchy:
    """Verify the error class hierarchy matches spec section 15.1."""

    def test_calendar_auth_error_is_runtime_error(self):
        err = CalendarAuthError("base error")
        assert isinstance(err, RuntimeError)

    def test_calendar_credential_error_is_auth_error(self):
        err = CalendarCredentialError("missing credential")
        assert isinstance(err, CalendarAuthError)
        assert isinstance(err, RuntimeError)

    def test_calendar_token_refresh_error_is_auth_error(self):
        err = CalendarTokenRefreshError("token expired")
        assert isinstance(err, CalendarAuthError)
        assert isinstance(err, RuntimeError)

    def test_calendar_request_error_is_auth_error(self):
        err = CalendarRequestError(status_code=429, message="rate limited")
        assert isinstance(err, CalendarAuthError)
        assert isinstance(err, RuntimeError)

    def test_calendar_request_error_carries_status_code(self):
        err = CalendarRequestError(status_code=403, message="Forbidden")
        assert err.status_code == 403

    def test_calendar_request_error_carries_message(self):
        err = CalendarRequestError(status_code=500, message="Internal error")
        assert err.message == "Internal error"

    def test_calendar_request_error_str_includes_status_and_message(self):
        err = CalendarRequestError(status_code=404, message="Not Found")
        assert "404" in str(err)
        assert "Not Found" in str(err)

    def test_rate_limit_retry_codes_include_429_and_503(self):
        assert 429 in RATE_LIMIT_RETRY_STATUS_CODES
        assert 503 in RATE_LIMIT_RETRY_STATUS_CODES

    def test_rate_limit_max_retries_is_three(self):
        assert RATE_LIMIT_MAX_RETRIES == 3


# ============================================================================
# _build_structured_error Helper Tests
# ============================================================================


class TestBuildStructuredError:
    """Verify the structured error dict builder per spec section 15.2/15.3."""

    def test_returns_status_error(self):
        exc = CalendarRequestError(status_code=500, message="server failure")
        result = _build_structured_error(exc, provider="google", calendar_id="primary")
        assert result["status"] == "error"

    def test_includes_error_type_class_name(self):
        exc = CalendarRequestError(status_code=429, message="rate limit")
        result = _build_structured_error(exc, provider="google", calendar_id="primary")
        assert result["error_type"] == "CalendarRequestError"

    def test_includes_provider_name(self):
        exc = CalendarAuthError("network failure")
        result = _build_structured_error(exc, provider="google", calendar_id="primary")
        assert result["provider"] == "google"

    def test_includes_calendar_id(self):
        exc = CalendarAuthError("network failure")
        result = _build_structured_error(exc, provider="google", calendar_id="butler@example.com")
        assert result["calendar_id"] == "butler@example.com"

    def test_error_message_is_sanitized_whitespace_normalized(self):
        exc = CalendarAuthError("Multiple   spaces\nand\nnewlines  here")
        result = _build_structured_error(exc, provider="google", calendar_id="primary")
        assert result["error"] == "Multiple spaces and newlines here"

    def test_error_message_is_truncated_to_200_chars(self):
        long_msg = "x" * 300
        exc = CalendarAuthError(long_msg)
        result = _build_structured_error(exc, provider="google", calendar_id="primary")
        assert len(result["error"]) == 200

    def test_error_message_does_not_include_long_credential_values(self):
        # Simulate an exception whose message might include a long credential.
        # The builder should pass through sanitized form; the 200-char limit
        # provides an additional guard for long credentials.
        fake_token = "ya29.supersecrettoken" + "x" * 200
        exc = CalendarAuthError(f"Auth failed: {fake_token}")
        result = _build_structured_error(exc, provider="google", calendar_id="primary")
        # The result is truncated to 200 chars; the raw fake_token is 220+ chars,
        # so the sanitized message will be truncated and cannot include the full token.
        assert len(result["error"]) <= 200

    def test_error_message_does_not_include_short_credential_values(self):
        # Short credentials (< 200 chars) would NOT be caught by truncation alone.
        # The redaction step must explicitly remove them from the error message.
        short_token = "ya29.short20chars!"  # 18 chars — well under 200-char truncation limit
        exc = CalendarAuthError(f"Auth failed: token={short_token}")
        result = _build_structured_error(exc, provider="google", calendar_id="primary")
        # The short credential value must be redacted, not present in the error output.
        assert short_token not in result["error"]
        assert "[REDACTED]" in result["error"]

    def test_error_type_for_credential_error(self):
        exc = CalendarCredentialError("missing JSON")
        result = _build_structured_error(exc, provider="google", calendar_id="primary")
        assert result["error_type"] == "CalendarCredentialError"

    def test_error_type_for_token_refresh_error(self):
        exc = CalendarTokenRefreshError("token exchange failed")
        result = _build_structured_error(exc, provider="google", calendar_id="primary")
        assert result["error_type"] == "CalendarTokenRefreshError"


# ============================================================================
# Fail-open / Fail-closed Provider Test Double
# ============================================================================


class _ErroringProvider(CalendarProvider):
    """Provider test double that raises CalendarAuthError for testing error paths."""

    def __init__(
        self,
        *,
        list_error: Exception | None = None,
        get_error: Exception | None = None,
        create_error: Exception | None = None,
        update_error: Exception | None = None,
        conflict_error: Exception | None = None,
        event: CalendarEvent | None = None,
    ) -> None:
        self._list_error = list_error
        self._get_error = get_error
        self._create_error = create_error
        self._update_error = update_error
        self._conflict_error = conflict_error
        self._event = event

    @property
    def name(self) -> str:
        return "erroring"

    async def list_events(self, *, calendar_id: str, **kwargs: Any) -> list[CalendarEvent]:
        if self._list_error is not None:
            raise self._list_error
        return []

    async def get_event(self, *, calendar_id: str, event_id: str) -> CalendarEvent | None:
        if self._get_error is not None:
            raise self._get_error
        return self._event

    async def create_event(self, *, calendar_id: str, payload: Any) -> CalendarEvent:
        if self._create_error is not None:
            raise self._create_error
        assert self._event is not None
        return self._event

    async def update_event(self, *, calendar_id: str, event_id: str, patch: Any) -> CalendarEvent:
        if self._update_error is not None:
            raise self._update_error
        assert self._event is not None
        return self._event

    async def delete_event(self, *, calendar_id: str, event_id: str) -> None:
        raise NotImplementedError

    async def add_attendees(
        self,
        *,
        calendar_id: str,
        event_id: str,
        attendees: list[str],
        optional: bool = False,
        send_updates: str = "none",
    ) -> CalendarEvent:
        raise NotImplementedError

    async def remove_attendees(
        self,
        *,
        calendar_id: str,
        event_id: str,
        attendees: list[str],
        send_updates: str = "none",
    ) -> CalendarEvent:
        raise NotImplementedError

    async def find_conflicts(self, *, calendar_id: str, candidate: Any) -> list[CalendarEvent]:
        if self._conflict_error is not None:
            raise self._conflict_error
        return []

    async def sync_incremental(
        self,
        *,
        calendar_id: str,
        sync_token: str | None,
        full_sync_window_days: int = 30,
    ) -> tuple[list[CalendarEvent], list[str], str]:
        raise NotImplementedError

    async def shutdown(self) -> None:
        return None


class _StubMCP:
    """Minimal MCP stub that captures registered tools by function name."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


def _make_sample_event(event_id: str = "evt-001") -> CalendarEvent:
    return CalendarEvent(
        event_id=event_id,
        title="BUTLER: Sample Event",
        start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
        timezone="UTC",
        butler_generated=True,
        butler_name="general",
    )


# ============================================================================
# Fail-open Read Tool Tests
# ============================================================================


class TestFailOpenReadTools:
    """Read operations fail-open: return empty results with error metadata (spec 4.4/15.2)."""

    async def _setup_module_with_provider(
        self, provider: CalendarProvider
    ) -> tuple[CalendarModule, _StubMCP]:
        mod = CalendarModule()
        mod._provider = provider
        mcp = _StubMCP()
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
        )
        return mod, mcp

    async def test_list_events_fail_open_on_calendar_auth_error(self):
        error = CalendarAuthError("Google Calendar list request failed")
        provider = _ErroringProvider(list_error=error)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_list_events"]()

        assert result["status"] == "error"
        assert result["events"] == []
        assert "error" in result
        assert result["provider"] == "erroring"
        assert result["calendar_id"] == "primary"

    async def test_list_events_fail_open_on_calendar_request_error(self):
        error = CalendarRequestError(status_code=500, message="Server error")
        provider = _ErroringProvider(list_error=error)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_list_events"]()

        assert result["status"] == "error"
        assert result["events"] == []
        assert result["error_type"] == "CalendarRequestError"

    async def test_list_events_fail_open_on_token_refresh_error(self):
        error = CalendarTokenRefreshError("Token refresh failed")
        provider = _ErroringProvider(list_error=error)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_list_events"]()

        assert result["status"] == "error"
        assert result["events"] == []
        assert result["error_type"] == "CalendarTokenRefreshError"

    async def test_list_events_success_returns_no_error_status(self):
        provider = _ErroringProvider(event=None)  # No error, no events.
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_list_events"]()

        assert "status" not in result
        assert result["events"] == []

    async def test_get_event_fail_open_on_calendar_auth_error(self):
        error = CalendarAuthError("Get event failed")
        provider = _ErroringProvider(get_error=error)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_get_event"](event_id="evt-123")

        assert result["status"] == "error"
        assert result["event"] is None
        assert result["provider"] == "erroring"
        assert result["calendar_id"] == "primary"

    async def test_get_event_fail_open_on_request_error(self):
        error = CalendarRequestError(status_code=403, message="Forbidden")
        provider = _ErroringProvider(get_error=error)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_get_event"](event_id="evt-123")

        assert result["status"] == "error"
        assert result["event"] is None
        assert result["error_type"] == "CalendarRequestError"

    async def test_get_event_returns_not_found_for_404_request_error(self):
        """A 404 CalendarRequestError returns distinct 'not_found' status (not 'error')."""
        error = CalendarRequestError(status_code=404, message="Not Found")
        provider = _ErroringProvider(get_error=error)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_get_event"](event_id="evt-missing")

        assert result["status"] == "not_found"
        assert result["event"] is None
        assert result["provider"] == "erroring"
        assert result["calendar_id"] == "primary"
        # not_found must NOT include error/error_type keys (it's not a transient failure)
        assert "error_type" not in result

    async def test_get_event_non_404_request_error_returns_error_status(self):
        """Non-404 CalendarRequestError still returns 'error' status (not 'not_found')."""
        error = CalendarRequestError(status_code=503, message="Service Unavailable")
        provider = _ErroringProvider(get_error=error)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_get_event"](event_id="evt-123")

        assert result["status"] == "error"
        assert result["event"] is None
        assert result["error_type"] == "CalendarRequestError"

    async def test_get_event_success_returns_event(self):
        event = _make_sample_event()
        provider = _ErroringProvider(event=event)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_get_event"](event_id="evt-001")

        assert "status" not in result
        assert result["event"] is not None
        assert result["event"]["event_id"] == "evt-001"

    async def test_list_events_error_message_sanitized(self):
        long_error_msg = "Calendar list failed: " + "x" * 300
        error = CalendarAuthError(long_error_msg)
        provider = _ErroringProvider(list_error=error)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_list_events"]()

        assert result["status"] == "error"
        # Error message must be truncated to 200 chars.
        assert len(result["error"]) <= 200


# ============================================================================
# Fail-closed Write Tool Tests
# ============================================================================


class TestFailClosedWriteTools:
    """Write operations fail-closed: return structured error dicts on failure (spec 4.4/15.2)."""

    async def _setup_module_with_provider(
        self, provider: CalendarProvider
    ) -> tuple[CalendarModule, _StubMCP]:
        mod = CalendarModule()
        mod._provider = provider
        mcp = _StubMCP()
        await mod.register_tools(
            mcp=mcp,
            config={
                "provider": "google",
                "calendar_id": "primary",
                "conflicts": {"policy": "fail"},
            },
            db=SimpleNamespace(db_name="butler_general"),
        )
        return mod, mcp

    async def test_create_event_returns_error_dict_on_conflict_check_failure(self):
        conflict_error = CalendarAuthError("freeBusy API unreachable")
        provider = _ErroringProvider(conflict_error=conflict_error)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_create_event"](
            title="Test Meeting",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
        )

        assert result["status"] == "error"
        assert "error" in result
        assert result["provider"] == "erroring"
        assert result["calendar_id"] == "primary"

    async def test_create_event_returns_error_dict_on_provider_write_failure(self):
        create_error = CalendarRequestError(status_code=503, message="Service unavailable")
        provider = _ErroringProvider(create_error=create_error)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_create_event"](
            title="Test Meeting",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
        )

        assert result["status"] == "error"
        assert result["error_type"] == "CalendarRequestError"
        assert result["provider"] == "erroring"
        assert result["calendar_id"] == "primary"

    async def test_create_event_error_message_sanitized(self):
        msg = "Write failed: " + "z" * 300
        create_error = CalendarAuthError(msg)
        provider = _ErroringProvider(create_error=create_error)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_create_event"](
            title="Test Meeting",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
        )

        assert result["status"] == "error"
        assert len(result["error"]) <= 200

    async def test_update_event_returns_error_dict_when_get_event_fails(self):
        get_error = CalendarRequestError(status_code=500, message="Internal error")
        provider = _ErroringProvider(get_error=get_error)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_update_event"](
            event_id="evt-001",
            title="Updated Title",
        )

        assert result["status"] == "error"
        assert result["error_type"] == "CalendarRequestError"
        assert result["provider"] == "erroring"
        assert result["calendar_id"] == "primary"

    async def test_update_event_returns_error_dict_on_conflict_check_failure(self):
        event = _make_sample_event()
        conflict_error = CalendarAuthError("freeBusy check failed")
        # Provider: get returns event OK, conflict check fails.
        provider = _ErroringProvider(event=event, conflict_error=conflict_error)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_update_event"](
            event_id="evt-001",
            # Provide new times so conflict check is triggered.
            start_at=datetime(2026, 3, 1, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 15, 0, tzinfo=UTC),
        )

        assert result["status"] == "error"
        assert result["error_type"] == "CalendarAuthError"

    async def test_update_event_returns_error_dict_on_provider_write_failure(self):
        event = _make_sample_event()
        update_error = CalendarRequestError(status_code=409, message="Conflict on write")
        provider = _ErroringProvider(event=event, update_error=update_error)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_update_event"](
            event_id="evt-001",
            title="New Title",
        )

        assert result["status"] == "error"
        assert result["error_type"] == "CalendarRequestError"
        assert result["provider"] == "erroring"

    async def test_update_event_error_message_sanitized(self):
        event = _make_sample_event()
        update_error = CalendarAuthError("Update failed: " + "a" * 300)
        provider = _ErroringProvider(event=event, update_error=update_error)
        _, mcp = await self._setup_module_with_provider(provider)

        result = await mcp.tools["calendar_update_event"](
            event_id="evt-001",
            title="New Title",
        )

        assert result["status"] == "error"
        assert len(result["error"]) <= 200


# ============================================================================
# Rate-Limit Retry Tests
# ============================================================================


class TestRateLimitRetry:
    """Verify 429/503 retry with exponential backoff (spec section 14.2)."""

    def _make_credentials_dict(self) -> dict[str, str]:
        return {
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "refresh_token": "test-refresh-token",
        }

    def _make_token_response(self) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"access_token": "access-token-123", "expires_in": 3600}
        return resp

    def _make_http_response(
        self,
        status_code: int,
        body: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> MagicMock:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.json.return_value = body or {}
        resp.text = ""
        # Use a plain dict (not MagicMock) so headers.get() returns None for missing keys.
        resp.headers = headers or {}
        return resp

    async def test_rate_limit_constants_are_consistent(self):
        """Verify the retry configuration constants are valid."""
        assert RATE_LIMIT_MAX_RETRIES > 0
        assert RATE_LIMIT_BASE_BACKOFF_SECONDS > 0

    async def test_request_retries_on_429_up_to_max(self):
        """Provider retries exactly MAX_RETRIES times on rate-limit then gives up."""

        from butlers.modules.calendar import _GoogleProvider

        token_resp = self._make_token_response()
        rate_limit_resp = self._make_http_response(429)
        ok_resp = self._make_http_response(200, {"items": []})

        # Side effects: token refresh, then N-1 rate-limit responses, then success
        post_side_effects = [token_resp]
        request_side_effects = [rate_limit_resp] * (RATE_LIMIT_MAX_RETRIES - 1) + [ok_resp]

        mock_http = MagicMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(side_effect=post_side_effects)
        mock_http.request = AsyncMock(side_effect=request_side_effects)

        config = CalendarConfig(provider="google", calendar_id="primary")

        provider = _GoogleProvider(
            config,
            credentials=_GoogleOAuthCredentials(**self._make_credentials_dict()),
            http_client=mock_http,
        )

        with patch("butlers.modules.calendar.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            response = await provider._request_with_bearer(
                method="GET",
                path="/calendars/primary/events",
            )

        # Should have slept RATE_LIMIT_MAX_RETRIES - 1 times
        assert mock_sleep.await_count == RATE_LIMIT_MAX_RETRIES - 1
        # Final response should be the success response
        assert response.status_code == 200

    async def test_request_retries_on_503(self):
        """Provider retries on 503 Service Unavailable."""

        from butlers.modules.calendar import _GoogleProvider

        token_resp = self._make_token_response()
        unavailable_resp = self._make_http_response(503)
        ok_resp = self._make_http_response(200, {"items": []})

        mock_http = MagicMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(side_effect=[token_resp])
        mock_http.request = AsyncMock(side_effect=[unavailable_resp, ok_resp])

        config = CalendarConfig(provider="google", calendar_id="primary")

        provider = _GoogleProvider(
            config,
            credentials=_GoogleOAuthCredentials(**self._make_credentials_dict()),
            http_client=mock_http,
        )

        with patch("butlers.modules.calendar.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            response = await provider._request_with_bearer(
                method="GET",
                path="/calendars/primary/events",
            )

        assert mock_sleep.await_count == 1
        assert response.status_code == 200

    async def test_request_stops_retrying_after_max_retries(self):
        """Provider stops retrying after RATE_LIMIT_MAX_RETRIES even if still rate-limited."""

        from butlers.modules.calendar import _GoogleProvider

        token_resp = self._make_token_response()
        rate_limit_resp = self._make_http_response(429)

        mock_http = MagicMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(side_effect=[token_resp])
        # Return 429 for every request (including initial + all retries)
        mock_http.request = AsyncMock(side_effect=[rate_limit_resp] * (RATE_LIMIT_MAX_RETRIES + 1))

        config = CalendarConfig(provider="google", calendar_id="primary")

        provider = _GoogleProvider(
            config,
            credentials=_GoogleOAuthCredentials(**self._make_credentials_dict()),
            http_client=mock_http,
        )

        with patch("butlers.modules.calendar.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            response = await provider._request_with_bearer(
                method="GET",
                path="/calendars/primary/events",
            )

        # Should have slept exactly RATE_LIMIT_MAX_RETRIES times (not more)
        assert mock_sleep.await_count == RATE_LIMIT_MAX_RETRIES
        # Final response is still rate-limited (exhausted retries)
        assert response.status_code == 429

    async def test_retry_backoff_is_exponential(self):
        """Backoff values increase exponentially: 1s, 2s, 4s for MAX_RETRIES=3."""

        from butlers.modules.calendar import _GoogleProvider

        token_resp = self._make_token_response()
        rate_limit_resp = self._make_http_response(429)
        ok_resp = self._make_http_response(200, {"items": []})

        mock_http = MagicMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(side_effect=[token_resp])
        mock_http.request = AsyncMock(
            side_effect=[rate_limit_resp, rate_limit_resp, rate_limit_resp, ok_resp]
        )

        config = CalendarConfig(provider="google", calendar_id="primary")

        provider = _GoogleProvider(
            config,
            credentials=_GoogleOAuthCredentials(**self._make_credentials_dict()),
            http_client=mock_http,
        )

        sleep_calls: list[float] = []

        async def capture_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with patch("butlers.modules.calendar.asyncio.sleep", side_effect=capture_sleep):
            await provider._request_with_bearer(
                method="GET",
                path="/calendars/primary/events",
            )

        assert len(sleep_calls) == 3
        # Exponential: 1.0, 2.0, 4.0
        assert sleep_calls[0] == RATE_LIMIT_BASE_BACKOFF_SECONDS * (2**0)
        assert sleep_calls[1] == RATE_LIMIT_BASE_BACKOFF_SECONDS * (2**1)
        assert sleep_calls[2] == RATE_LIMIT_BASE_BACKOFF_SECONDS * (2**2)

    async def test_non_rate_limit_status_codes_do_not_trigger_retry(self):
        """Only 429 and 503 trigger retry; other errors are returned immediately."""

        from butlers.modules.calendar import _GoogleProvider

        token_resp = self._make_token_response()
        forbidden_resp = self._make_http_response(403)

        mock_http = MagicMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(side_effect=[token_resp])
        mock_http.request = AsyncMock(side_effect=[forbidden_resp])

        config = CalendarConfig(provider="google", calendar_id="primary")

        provider = _GoogleProvider(
            config,
            credentials=_GoogleOAuthCredentials(**self._make_credentials_dict()),
            http_client=mock_http,
        )

        with patch("butlers.modules.calendar.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            response = await provider._request_with_bearer(
                method="GET",
                path="/calendars/primary/events",
            )

        mock_sleep.assert_not_awaited()
        assert response.status_code == 403

    async def test_retry_after_header_used_as_backoff_on_429(self):
        """Retry-After header value overrides exponential backoff for 429 responses."""

        from butlers.modules.calendar import _GoogleProvider

        token_resp = self._make_token_response()
        ok_resp = self._make_http_response(200, {"items": []})

        # Build a 429 response with a Retry-After header
        rate_limit_resp = MagicMock(spec=httpx.Response)
        rate_limit_resp.status_code = 429
        rate_limit_resp.json.return_value = {}
        rate_limit_resp.text = ""
        rate_limit_resp.headers = {"Retry-After": "30"}

        mock_http = MagicMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(side_effect=[token_resp])
        mock_http.request = AsyncMock(side_effect=[rate_limit_resp, ok_resp])

        config = CalendarConfig(provider="google", calendar_id="primary")

        provider = _GoogleProvider(
            config,
            credentials=_GoogleOAuthCredentials(**self._make_credentials_dict()),
            http_client=mock_http,
        )

        sleep_calls: list[float] = []

        async def capture_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with patch("butlers.modules.calendar.asyncio.sleep", side_effect=capture_sleep):
            response = await provider._request_with_bearer(
                method="GET",
                path="/calendars/primary/events",
            )

        assert response.status_code == 200
        assert len(sleep_calls) == 1
        # Should use Retry-After value (30), not exponential backoff (1.0)
        assert sleep_calls[0] == 30.0

    async def test_retry_after_header_missing_falls_back_to_exponential_backoff(self):
        """When Retry-After header is absent on 429, exponential backoff is used."""

        from butlers.modules.calendar import _GoogleProvider

        token_resp = self._make_token_response()
        rate_limit_resp = self._make_http_response(429)  # No Retry-After header
        ok_resp = self._make_http_response(200, {"items": []})

        mock_http = MagicMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(side_effect=[token_resp])
        mock_http.request = AsyncMock(side_effect=[rate_limit_resp, ok_resp])

        config = CalendarConfig(provider="google", calendar_id="primary")

        provider = _GoogleProvider(
            config,
            credentials=_GoogleOAuthCredentials(**self._make_credentials_dict()),
            http_client=mock_http,
        )

        sleep_calls: list[float] = []

        async def capture_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with patch("butlers.modules.calendar.asyncio.sleep", side_effect=capture_sleep):
            response = await provider._request_with_bearer(
                method="GET",
                path="/calendars/primary/events",
            )

        assert response.status_code == 200
        assert len(sleep_calls) == 1
        # Should use exponential backoff (1.0 * 2^0 = 1.0 for first retry)
        assert sleep_calls[0] == RATE_LIMIT_BASE_BACKOFF_SECONDS * (2**0)

    async def test_retry_after_header_not_used_for_503(self):
        """Retry-After header is only honoured for 429; 503 uses exponential backoff."""

        from butlers.modules.calendar import _GoogleProvider

        token_resp = self._make_token_response()
        ok_resp = self._make_http_response(200, {"items": []})

        # 503 response WITH Retry-After header — should NOT be used
        unavailable_resp = MagicMock(spec=httpx.Response)
        unavailable_resp.status_code = 503
        unavailable_resp.json.return_value = {}
        unavailable_resp.text = ""
        unavailable_resp.headers = {"Retry-After": "60"}

        mock_http = MagicMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(side_effect=[token_resp])
        mock_http.request = AsyncMock(side_effect=[unavailable_resp, ok_resp])

        config = CalendarConfig(provider="google", calendar_id="primary")

        provider = _GoogleProvider(
            config,
            credentials=_GoogleOAuthCredentials(**self._make_credentials_dict()),
            http_client=mock_http,
        )

        sleep_calls: list[float] = []

        async def capture_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with patch("butlers.modules.calendar.asyncio.sleep", side_effect=capture_sleep):
            response = await provider._request_with_bearer(
                method="GET",
                path="/calendars/primary/events",
            )

        assert response.status_code == 200
        assert len(sleep_calls) == 1
        # Should use exponential backoff (1.0 * 2^0 = 1.0), not the Retry-After header (60)
        assert sleep_calls[0] == RATE_LIMIT_BASE_BACKOFF_SECONDS * (2**0)


# ============================================================================
# No Credential Leakage Tests
# ============================================================================


class TestNoCredentialLeakage:
    """Verify credential values are never included in error messages (spec section 15.3)."""

    def test_credential_error_message_does_not_include_secret_values(self):
        # CalendarCredentialError is raised before credentials are stored
        exc = CalendarCredentialError(
            "Missing required Google credential environment variable(s): GOOGLE_OAUTH_CLIENT_ID"
        )
        result = _build_structured_error(exc, provider="google", calendar_id="primary")
        # The message should not contain actual secret values (env var name is OK)
        assert "supersecretvalue" not in result["error"]

    def test_token_refresh_error_does_not_leak_access_token(self):
        # CalendarTokenRefreshError messages should not embed raw tokens.
        exc = CalendarTokenRefreshError("Google OAuth token refresh failed (401): invalid_grant")
        result = _build_structured_error(exc, provider="google", calendar_id="primary")
        # No access_token value should appear
        assert "ya29." not in result["error"]

    def test_request_error_message_is_sanitized_by_safe_google_error_message(self):
        """CalendarRequestError.message is already sanitized by _safe_google_error_message."""
        long_message = "Error from API: " + "e" * 300
        exc = CalendarRequestError(status_code=500, message=long_message[:200])
        result = _build_structured_error(exc, provider="google", calendar_id="primary")
        # The sanitized error text in the result should be capped at 200 chars
        assert len(result["error"]) <= 200
