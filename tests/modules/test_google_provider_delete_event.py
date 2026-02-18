"""Unit tests for _GoogleProvider.delete_event() and calendar_delete_event MCP tool.

Covers:
- Correct DELETE sent to Google Calendar API
- URL encoding of calendar_id and event_id with special characters
- 404 handled gracefully (already deleted — treated as success)
- send_updates="all" passed as query parameter
- send_updates=None sends no sendUpdates query parameter
- Non-2xx (other than 404) raises CalendarRequestError
- Bearer token sent in Authorization header
- Empty event_id raises ValueError
- MCP tool: status="deleted" on success
- MCP tool: status="not_found" when event does not exist
- MCP tool: send_updates forwarded to provider
- MCP tool: empty event_id raises ValueError
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.modules.calendar import (
    GOOGLE_CALENDAR_API_BASE_URL,
    GOOGLE_CALENDAR_CREDENTIALS_ENV,
    CalendarConfig,
    CalendarEvent,
    CalendarRequestError,
    _GoogleProvider,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_credentials_json() -> str:
    return json.dumps(
        {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
        }
    )


def _mock_response(
    *,
    status_code: int,
    url: str,
    method: str = "GET",
    json_body: dict | None = None,
    text: str = "",
) -> httpx.Response:
    request = httpx.Request(method, url)
    if json_body is not None:
        return httpx.Response(status_code=status_code, json=json_body, request=request)
    return httpx.Response(status_code=status_code, text=text, request=request)


def _make_mock_http_client() -> MagicMock:
    """Return a mock httpx.AsyncClient pre-wired with a valid OAuth token response."""
    mock_client = MagicMock(spec=httpx.AsyncClient)
    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {"access_token": "access-token", "expires_in": 3600}
    mock_client.post = AsyncMock(return_value=token_response)
    return mock_client


def _make_provider(
    monkeypatch: pytest.MonkeyPatch,
    mock_client: MagicMock,
    *,
    config: CalendarConfig | None = None,
) -> _GoogleProvider:
    monkeypatch.setenv(GOOGLE_CALENDAR_CREDENTIALS_ENV, _make_credentials_json())
    cfg = config or CalendarConfig(
        provider="google",
        calendar_id="primary",
        timezone="UTC",
    )
    return _GoogleProvider(config=cfg, http_client=mock_client)


def _make_calendar_event(
    event_id: str = "evt-1",
    title: str = "Test Event",
) -> CalendarEvent:
    return CalendarEvent(
        event_id=event_id,
        title=title,
        start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
        timezone="UTC",
    )


@pytest.fixture
def google_provider(monkeypatch):
    monkeypatch.setenv(GOOGLE_CALENDAR_CREDENTIALS_ENV, _make_credentials_json())
    config = CalendarConfig(
        provider="google",
        calendar_id="test@example.com",
        timezone="UTC",
    )
    return _GoogleProvider(config=config, http_client=_make_mock_http_client())


# ---------------------------------------------------------------------------
# _GoogleProvider.delete_event unit tests (mocked _request_with_bearer)
# ---------------------------------------------------------------------------


class TestGoogleProviderDeleteEvent:
    """Tests for _GoogleProvider.delete_event() with mocked HTTP."""

    async def test_delete_sends_correct_delete_request(self, monkeypatch: pytest.MonkeyPatch):
        """delete_event issues a DELETE to the correct Google Calendar endpoint."""
        mock_client = _make_mock_http_client()
        mock_client.request = AsyncMock(
            return_value=_mock_response(
                status_code=204,
                url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events/evt-1",
                method="DELETE",
            )
        )
        provider = _make_provider(monkeypatch, mock_client)

        await provider.delete_event(calendar_id="primary", event_id="evt-1")

        call_args = mock_client.request.call_args
        assert call_args.args[0] == "DELETE"
        assert "/calendars/primary/events/evt-1" in call_args.args[1]

    async def test_delete_200_response_succeeds(self, monkeypatch: pytest.MonkeyPatch):
        """A 200 OK response from DELETE is also treated as success."""
        mock_client = _make_mock_http_client()
        mock_client.request = AsyncMock(
            return_value=_mock_response(
                status_code=200,
                url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events/evt-1",
                method="DELETE",
                text="",
            )
        )
        provider = _make_provider(monkeypatch, mock_client)

        # Should not raise
        await provider.delete_event(calendar_id="primary", event_id="evt-1")

    async def test_delete_404_treated_as_success(self, monkeypatch: pytest.MonkeyPatch):
        """A 404 response means the event was already deleted — not an error."""
        mock_client = _make_mock_http_client()
        mock_client.request = AsyncMock(
            return_value=_mock_response(
                status_code=404,
                url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events/missing",
                method="DELETE",
                json_body={"error": {"message": "Not Found"}},
            )
        )
        provider = _make_provider(monkeypatch, mock_client)

        # Should not raise — 404 is success
        await provider.delete_event(calendar_id="primary", event_id="missing")

    async def test_delete_non_404_error_raises_calendar_request_error(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Non-2xx, non-404 responses raise CalendarRequestError."""
        mock_client = _make_mock_http_client()
        mock_client.request = AsyncMock(
            return_value=_mock_response(
                status_code=403,
                url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events/evt-1",
                method="DELETE",
                json_body={"error": {"message": "Insufficient permissions"}},
            )
        )
        provider = _make_provider(monkeypatch, mock_client)

        with pytest.raises(CalendarRequestError) as exc_info:
            await provider.delete_event(calendar_id="primary", event_id="evt-1")

        assert exc_info.value.status_code == 403
        assert "Insufficient permissions" in exc_info.value.message

    async def test_delete_500_raises_calendar_request_error(self, monkeypatch: pytest.MonkeyPatch):
        """A 500 server error raises CalendarRequestError."""
        mock_client = _make_mock_http_client()
        mock_client.request = AsyncMock(
            return_value=_mock_response(
                status_code=500,
                url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events/evt-1",
                method="DELETE",
                json_body={"error": {"message": "Backend error"}},
            )
        )
        provider = _make_provider(monkeypatch, mock_client)

        with pytest.raises(CalendarRequestError) as exc_info:
            await provider.delete_event(calendar_id="primary", event_id="evt-1")

        assert exc_info.value.status_code == 500

    async def test_delete_with_send_updates_all_passes_query_param(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """send_updates='all' is forwarded as sendUpdates query parameter."""
        mock_client = _make_mock_http_client()
        mock_client.request = AsyncMock(
            return_value=_mock_response(
                status_code=204,
                url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events/evt-1",
                method="DELETE",
            )
        )
        provider = _make_provider(monkeypatch, mock_client)

        await provider.delete_event(
            calendar_id="primary",
            event_id="evt-1",
            send_updates="all",
        )

        call_kwargs = mock_client.request.call_args.kwargs
        params = call_kwargs.get("params")
        assert params is not None
        assert params.get("sendUpdates") == "all"

    async def test_delete_with_send_updates_none_omits_query_param(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """send_updates=None does not include sendUpdates query parameter."""
        mock_client = _make_mock_http_client()
        mock_client.request = AsyncMock(
            return_value=_mock_response(
                status_code=204,
                url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events/evt-1",
                method="DELETE",
            )
        )
        provider = _make_provider(monkeypatch, mock_client)

        await provider.delete_event(
            calendar_id="primary",
            event_id="evt-1",
            send_updates=None,
        )

        call_kwargs = mock_client.request.call_args.kwargs
        params = call_kwargs.get("params")
        # Either no params at all, or sendUpdates not in params
        assert params is None or "sendUpdates" not in params

    async def test_delete_with_send_updates_external_only(self, monkeypatch: pytest.MonkeyPatch):
        """send_updates='externalOnly' is forwarded as sendUpdates query parameter."""
        mock_client = _make_mock_http_client()
        mock_client.request = AsyncMock(
            return_value=_mock_response(
                status_code=204,
                url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events/evt-1",
                method="DELETE",
            )
        )
        provider = _make_provider(monkeypatch, mock_client)

        await provider.delete_event(
            calendar_id="primary",
            event_id="evt-1",
            send_updates="externalOnly",
        )

        call_kwargs = mock_client.request.call_args.kwargs
        params = call_kwargs.get("params")
        assert params is not None
        assert params.get("sendUpdates") == "externalOnly"

    async def test_delete_sends_bearer_token_in_authorization_header(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The DELETE request includes a valid Bearer token in the Authorization header."""
        mock_client = _make_mock_http_client()
        mock_client.request = AsyncMock(
            return_value=_mock_response(
                status_code=204,
                url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events/evt-1",
                method="DELETE",
            )
        )
        provider = _make_provider(monkeypatch, mock_client)

        await provider.delete_event(calendar_id="primary", event_id="evt-1")

        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer access-token"

    async def test_delete_url_encodes_calendar_id_with_at_sign(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Calendar IDs containing @ are percent-encoded in the URL path."""
        mock_client = _make_mock_http_client()
        mock_client.request = AsyncMock(
            return_value=_mock_response(
                status_code=204,
                url=(
                    f"{GOOGLE_CALENDAR_API_BASE_URL}"
                    "/calendars/butler%40group.calendar.google.com/events/evt-1"
                ),
                method="DELETE",
            )
        )
        provider = _make_provider(monkeypatch, mock_client)

        await provider.delete_event(
            calendar_id="butler@group.calendar.google.com",
            event_id="evt-1",
        )

        call_args = mock_client.request.call_args
        url = call_args.args[1]
        assert "butler%40group.calendar.google.com" in url

    async def test_delete_url_encodes_event_id_with_special_chars(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Event IDs with special characters are percent-encoded in the URL path."""
        mock_client = _make_mock_http_client()
        mock_client.request = AsyncMock(
            return_value=_mock_response(
                status_code=204,
                url=(
                    f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events/evt%2Fwith%2Fslashes"
                ),
                method="DELETE",
            )
        )
        provider = _make_provider(monkeypatch, mock_client)

        await provider.delete_event(
            calendar_id="primary",
            event_id="evt/with/slashes",
        )

        call_args = mock_client.request.call_args
        url = call_args.args[1]
        assert "evt%2Fwith%2Fslashes" in url

    async def test_delete_empty_event_id_raises_value_error(self, monkeypatch: pytest.MonkeyPatch):
        """Empty or whitespace-only event_id raises ValueError immediately."""
        mock_client = _make_mock_http_client()
        provider = _make_provider(monkeypatch, mock_client)

        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            await provider.delete_event(calendar_id="primary", event_id="   ")

    async def test_delete_default_send_updates_is_none(self, monkeypatch: pytest.MonkeyPatch):
        """Default send_updates=None means no sendUpdates query param is sent."""
        mock_client = _make_mock_http_client()
        mock_client.request = AsyncMock(
            return_value=_mock_response(
                status_code=204,
                url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events/evt-1",
                method="DELETE",
            )
        )
        provider = _make_provider(monkeypatch, mock_client)

        # Call without send_updates — default is None
        await provider.delete_event(calendar_id="primary", event_id="evt-1")

        call_kwargs = mock_client.request.call_args.kwargs
        params = call_kwargs.get("params")
        assert params is None or "sendUpdates" not in params


# ---------------------------------------------------------------------------
# CalendarModule.calendar_delete_event MCP tool tests
# ---------------------------------------------------------------------------


class TestCalendarDeleteEventTool:
    """Tests for the calendar_delete_event MCP tool registered by CalendarModule."""

    async def test_tool_returns_deleted_status_on_success(self, google_provider):
        """Tool returns status=deleted when event exists and delete succeeds."""
        existing = _make_calendar_event("evt-del", "Meeting to delete")
        google_provider.get_event = AsyncMock(return_value=existing)
        google_provider.delete_event = AsyncMock(return_value=None)

        from butlers.modules.calendar import CalendarModule

        module = CalendarModule()
        module._provider = google_provider
        module._config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            timezone="UTC",
        )

        tools: dict = {}

        class FakeMcp:
            def tool(self):
                def decorator(fn):
                    tools[fn.__name__] = fn
                    return fn

                return decorator

        await module.register_tools(FakeMcp(), module._config, None)
        result = await tools["calendar_delete_event"](event_id="evt-del")

        assert result["status"] == "deleted"
        assert result["event_id"] == "evt-del"
        assert result["provider"] == "google"
        google_provider.delete_event.assert_awaited_once()

    async def test_tool_returns_not_found_when_event_does_not_exist(self, google_provider):
        """Tool returns status=not_found when get_event returns None."""
        google_provider.get_event = AsyncMock(return_value=None)

        from butlers.modules.calendar import CalendarModule

        module = CalendarModule()
        module._provider = google_provider
        module._config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            timezone="UTC",
        )

        tools: dict = {}

        class FakeMcp:
            def tool(self):
                def decorator(fn):
                    tools[fn.__name__] = fn
                    return fn

                return decorator

        await module.register_tools(FakeMcp(), module._config, None)
        result = await tools["calendar_delete_event"](event_id="missing-event")

        assert result["status"] == "not_found"
        assert result["event_id"] == "missing-event"

    async def test_tool_forwards_send_updates_to_provider(self, google_provider):
        """Tool passes send_updates value through to provider.delete_event."""
        existing = _make_calendar_event("evt-attendees", "Event with attendees")
        google_provider.get_event = AsyncMock(return_value=existing)
        google_provider.delete_event = AsyncMock(return_value=None)

        from butlers.modules.calendar import CalendarModule

        module = CalendarModule()
        module._provider = google_provider
        module._config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            timezone="UTC",
        )

        tools: dict = {}

        class FakeMcp:
            def tool(self):
                def decorator(fn):
                    tools[fn.__name__] = fn
                    return fn

                return decorator

        await module.register_tools(FakeMcp(), module._config, None)
        await tools["calendar_delete_event"](
            event_id="evt-attendees",
            send_updates="all",
        )

        call_kwargs = google_provider.delete_event.call_args.kwargs
        assert call_kwargs.get("send_updates") == "all"

    async def test_tool_forwards_none_send_updates_to_provider(self, google_provider):
        """Tool passes send_updates=None to provider (default — no notifications)."""
        existing = _make_calendar_event("evt-quiet", "Quiet delete")
        google_provider.get_event = AsyncMock(return_value=existing)
        google_provider.delete_event = AsyncMock(return_value=None)

        from butlers.modules.calendar import CalendarModule

        module = CalendarModule()
        module._provider = google_provider
        module._config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            timezone="UTC",
        )

        tools: dict = {}

        class FakeMcp:
            def tool(self):
                def decorator(fn):
                    tools[fn.__name__] = fn
                    return fn

                return decorator

        await module.register_tools(FakeMcp(), module._config, None)
        await tools["calendar_delete_event"](event_id="evt-quiet", send_updates=None)

        call_kwargs = google_provider.delete_event.call_args.kwargs
        assert call_kwargs.get("send_updates") is None

    async def test_tool_empty_event_id_raises_value_error(self, google_provider):
        """Tool raises ValueError when event_id is empty or whitespace."""
        from butlers.modules.calendar import CalendarModule

        module = CalendarModule()
        module._provider = google_provider
        module._config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            timezone="UTC",
        )

        tools: dict = {}

        class FakeMcp:
            def tool(self):
                def decorator(fn):
                    tools[fn.__name__] = fn
                    return fn

                return decorator

        await module.register_tools(FakeMcp(), module._config, None)

        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            await tools["calendar_delete_event"](event_id="   ")

    async def test_tool_uses_default_calendar_id_from_config(self, google_provider):
        """When calendar_id is None, tool uses the configured default calendar_id."""
        existing = _make_calendar_event("evt-1")
        google_provider.get_event = AsyncMock(return_value=existing)
        google_provider.delete_event = AsyncMock(return_value=None)

        from butlers.modules.calendar import CalendarModule

        module = CalendarModule()
        module._provider = google_provider
        module._config = CalendarConfig(
            provider="google",
            calendar_id="configured@example.com",
            timezone="UTC",
        )

        tools: dict = {}

        class FakeMcp:
            def tool(self):
                def decorator(fn):
                    tools[fn.__name__] = fn
                    return fn

                return decorator

        await module.register_tools(FakeMcp(), module._config, None)
        result = await tools["calendar_delete_event"](event_id="evt-1", calendar_id=None)

        assert result["calendar_id"] == "configured@example.com"
        # get_event called with the configured calendar_id
        google_provider.get_event.assert_awaited_once_with(
            calendar_id="configured@example.com",
            event_id="evt-1",
        )

    async def test_tool_uses_explicit_calendar_id_override(self, google_provider):
        """When calendar_id is provided, it overrides the configured default."""
        existing = _make_calendar_event("evt-2")
        google_provider.get_event = AsyncMock(return_value=existing)
        google_provider.delete_event = AsyncMock(return_value=None)

        from butlers.modules.calendar import CalendarModule

        module = CalendarModule()
        module._provider = google_provider
        module._config = CalendarConfig(
            provider="google",
            calendar_id="default@example.com",
            timezone="UTC",
        )

        tools: dict = {}

        class FakeMcp:
            def tool(self):
                def decorator(fn):
                    tools[fn.__name__] = fn
                    return fn

                return decorator

        await module.register_tools(FakeMcp(), module._config, None)
        result = await tools["calendar_delete_event"](
            event_id="evt-2",
            calendar_id="override@example.com",
        )

        assert result["calendar_id"] == "override@example.com"

    async def test_tool_provider_error_propagates(self, google_provider):
        """CalendarRequestError from provider propagates through the MCP tool."""
        existing = _make_calendar_event("evt-err")
        google_provider.get_event = AsyncMock(return_value=existing)
        google_provider.delete_event = AsyncMock(
            side_effect=CalendarRequestError(status_code=403, message="Forbidden")
        )

        from butlers.modules.calendar import CalendarModule

        module = CalendarModule()
        module._provider = google_provider
        module._config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            timezone="UTC",
        )

        tools: dict = {}

        class FakeMcp:
            def tool(self):
                def decorator(fn):
                    tools[fn.__name__] = fn
                    return fn

                return decorator

        await module.register_tools(FakeMcp(), module._config, None)

        with pytest.raises(CalendarRequestError) as exc_info:
            await tools["calendar_delete_event"](event_id="evt-err")

        assert exc_info.value.status_code == 403

    async def test_tool_includes_provider_name_in_response(self, google_provider):
        """Tool response includes provider name for traceability."""
        existing = _make_calendar_event("evt-3")
        google_provider.get_event = AsyncMock(return_value=existing)
        google_provider.delete_event = AsyncMock(return_value=None)

        from butlers.modules.calendar import CalendarModule

        module = CalendarModule()
        module._provider = google_provider
        module._config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            timezone="UTC",
        )

        tools: dict = {}

        class FakeMcp:
            def tool(self):
                def decorator(fn):
                    tools[fn.__name__] = fn
                    return fn

                return decorator

        await module.register_tools(FakeMcp(), module._config, None)
        result = await tools["calendar_delete_event"](event_id="evt-3")

        assert result["provider"] == "google"
