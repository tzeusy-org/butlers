"""Tests for calendar module config and provider interface scaffolding."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module
from butlers.modules.calendar import (
    GOOGLE_CALENDAR_API_BASE_URL,
    GOOGLE_CALENDAR_CREDENTIALS_ENV,
    GOOGLE_OAUTH_TOKEN_URL,
    CalendarConfig,
    CalendarCredentialError,
    CalendarEvent,
    CalendarModule,
    CalendarProvider,
    CalendarRequestError,
    _GoogleOAuthClient,
    _GoogleOAuthCredentials,
    _GoogleProvider,
)

pytestmark = pytest.mark.unit


class TestModuleABCCompliance:
    """Verify CalendarModule satisfies the shared module contract."""

    def test_is_module_subclass(self):
        assert issubclass(CalendarModule, Module)

    def test_instantiates(self):
        mod = CalendarModule()
        assert isinstance(mod, Module)

    def test_name(self):
        assert CalendarModule().name == "calendar"

    def test_config_schema(self):
        schema = CalendarModule().config_schema
        assert schema is CalendarConfig
        assert issubclass(schema, BaseModel)

    def test_dependencies_empty(self):
        assert CalendarModule().dependencies == []

    def test_migration_revisions_none(self):
        assert CalendarModule().migration_revisions() is None

    def test_credentials_env_declared(self):
        assert CalendarModule().credentials_env == [GOOGLE_CALENDAR_CREDENTIALS_ENV]


class TestCalendarConfig:
    """Verify config validation, required fields, and defaults."""

    def test_required_fields_provider_and_calendar_id(self):
        with pytest.raises(ValidationError):
            CalendarConfig(calendar_id="primary")

        with pytest.raises(ValidationError):
            CalendarConfig(provider="google")

    def test_defaults(self):
        config = CalendarConfig(provider="google", calendar_id="primary")
        assert config.provider == "google"
        assert config.calendar_id == "primary"
        assert config.timezone == "UTC"

        assert config.conflicts.policy == "suggest"
        assert config.conflicts.require_approval_for_overlap is True

        assert config.event_defaults.enabled is True
        assert config.event_defaults.minutes_before == 15
        assert config.event_defaults.color_id is None

    def test_string_normalization(self):
        config = CalendarConfig(
            provider="  GOOGLE  ",
            calendar_id="  primary  ",
            timezone="  America/New_York  ",
        )
        assert config.provider == "google"
        assert config.calendar_id == "primary"
        assert config.timezone == "America/New_York"

    def test_non_empty_errors_include_field_name(self):
        with pytest.raises(ValidationError, match="calendar_id must be a non-empty string"):
            CalendarConfig(provider="google", calendar_id="   ")

        with pytest.raises(ValidationError, match="timezone must be a non-empty string"):
            CalendarConfig(provider="google", calendar_id="primary", timezone="   ")

    def test_nested_defaults_forbid_unknown_keys(self):
        with pytest.raises(ValidationError) as conflict_error:
            CalendarConfig(
                provider="google",
                calendar_id="primary",
                conflicts={"policy": "suggest", "unexpected": True},
            )
        assert conflict_error.value.errors()[0]["loc"] == ("conflicts", "unexpected")
        assert conflict_error.value.errors()[0]["type"] == "extra_forbidden"

        with pytest.raises(ValidationError) as defaults_error:
            CalendarConfig(
                provider="google",
                calendar_id="primary",
                event_defaults={"minutes_beforee": 10},
            )
        assert defaults_error.value.errors()[0]["loc"] == ("event_defaults", "minutes_beforee")
        assert defaults_error.value.errors()[0]["type"] == "extra_forbidden"


class TestCalendarProviderInterface:
    """Verify the provider interface exposes required tool operations."""

    def test_provider_contract_operations(self):
        abstract_methods = CalendarProvider.__abstractmethods__
        expected = {
            "name",
            "list_events",
            "get_event",
            "create_event",
            "update_event",
            "delete_event",
            "find_conflicts",
            "shutdown",
        }
        assert expected.issubset(abstract_methods)


class TestModuleStartup:
    """Verify startup provider selection behavior."""

    async def test_startup_accepts_supported_provider(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            GOOGLE_CALENDAR_CREDENTIALS_ENV,
            json.dumps(
                {
                    "client_id": "test-client-id",
                    "client_secret": "test-client-secret",
                    "refresh_token": "test-refresh-token",
                }
            ),
        )
        mod = CalendarModule()
        await mod.on_startup({"provider": "google", "calendar_id": "primary"}, db=None)

        # Verify provider was selected and is usable by later tools.
        provider = getattr(mod, "_provider")
        assert provider is not None
        assert provider.name == "google"

    async def test_startup_fails_clearly_on_unsupported_provider(self):
        mod = CalendarModule()
        with pytest.raises(RuntimeError) as excinfo:
            await mod.on_startup({"provider": "outlook", "calendar_id": "primary"}, db=None)

        error_message = str(excinfo.value)
        assert "Unsupported calendar provider 'outlook'" in error_message
        assert "Supported providers: google" in error_message

    async def test_register_tools_accepts_validated_config(self):
        mod = CalendarModule()
        cfg = CalendarConfig(provider="google", calendar_id="primary")
        await mod.register_tools(mcp=_StubMCP(), config=cfg, db=None)
        assert isinstance(getattr(mod, "_config"), CalendarConfig)

    async def test_register_tools_accepts_dict_config(self):
        mod = CalendarModule()
        await mod.register_tools(
            mcp=_StubMCP(),
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
        )
        stored = getattr(mod, "_config")
        assert isinstance(stored, CalendarConfig)
        assert stored.provider == "google"


class _StubMCP:
    """Minimal MCP stub that captures registered tools by function name."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class _ProviderDouble(CalendarProvider):
    """Provider test double used to verify module-to-provider wiring."""

    def __init__(
        self,
        *,
        events: list[CalendarEvent] | None = None,
        event: CalendarEvent | None = None,
    ) -> None:
        self._events = events or []
        self._event = event
        self.list_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "double"

    async def list_events(
        self,
        *,
        calendar_id: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 50,
    ) -> list[CalendarEvent]:
        self.list_calls.append(
            {
                "calendar_id": calendar_id,
                "start_at": start_at,
                "end_at": end_at,
                "limit": limit,
            }
        )
        return list(self._events)

    async def get_event(self, *, calendar_id: str, event_id: str) -> CalendarEvent | None:
        self.get_calls.append({"calendar_id": calendar_id, "event_id": event_id})
        return self._event

    async def create_event(self, *, calendar_id: str, payload):  # pragma: no cover
        raise NotImplementedError

    async def update_event(self, *, calendar_id: str, event_id: str, patch):  # pragma: no cover
        raise NotImplementedError

    async def delete_event(self, *, calendar_id: str, event_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def find_conflicts(self, *, calendar_id: str, candidate):  # pragma: no cover
        raise NotImplementedError

    async def shutdown(self) -> None:
        return None


class TestCalendarReadTools:
    """Verify list/get tools use provider abstraction and normalize payloads."""

    async def test_register_tools_wires_list_get_via_provider(self):
        event = CalendarEvent(
            event_id="evt-123",
            title="Dentist appointment",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
            description="Bring insurance card",
            location="Main Street Clinic",
            attendees=["alex@example.com"],
            recurrence_rule="RRULE:FREQ=WEEKLY",
            color_id="7",
        )
        provider = _ProviderDouble(events=[event], event=event)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
        )

        list_result = await mcp.tools["calendar_list_events"]()
        get_result = await mcp.tools["calendar_get_event"](event_id="evt-123")

        assert provider.list_calls == [
            {"calendar_id": "primary", "start_at": None, "end_at": None, "limit": 50}
        ]
        assert provider.get_calls == [{"calendar_id": "primary", "event_id": "evt-123"}]
        assert list_result == {
            "provider": "double",
            "calendar_id": "primary",
            "events": [
                {
                    "event_id": "evt-123",
                    "title": "Dentist appointment",
                    "start_at": "2026-02-20T14:00:00+00:00",
                    "end_at": "2026-02-20T15:00:00+00:00",
                    "timezone": "UTC",
                    "description": "Bring insurance card",
                    "location": "Main Street Clinic",
                    "attendees": ["alex@example.com"],
                    "recurrence_rule": "RRULE:FREQ=WEEKLY",
                    "color_id": "7",
                }
            ],
        }
        assert get_result == {
            "provider": "double",
            "calendar_id": "primary",
            "event": {
                "event_id": "evt-123",
                "title": "Dentist appointment",
                "start_at": "2026-02-20T14:00:00+00:00",
                "end_at": "2026-02-20T15:00:00+00:00",
                "timezone": "UTC",
                "description": "Bring insurance card",
                "location": "Main Street Clinic",
                "attendees": ["alex@example.com"],
                "recurrence_rule": "RRULE:FREQ=WEEKLY",
                "color_id": "7",
            },
        }

    async def test_calendar_id_override_is_applied_without_mutating_default(self):
        provider = _ProviderDouble()
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
        )

        await mcp.tools["calendar_list_events"](calendar_id="  butler-subcalendar  ", limit=5)
        await mcp.tools["calendar_get_event"](
            event_id="evt-456",
            calendar_id="custom-calendar",
        )

        assert provider.list_calls[0]["calendar_id"] == "butler-subcalendar"
        assert provider.list_calls[0]["limit"] == 5
        assert provider.get_calls[0]["calendar_id"] == "custom-calendar"
        assert getattr(mod, "_config").calendar_id == "primary"

    async def test_calendar_id_override_rejects_blank_string(self):
        provider = _ProviderDouble()
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
        )

        with pytest.raises(ValueError, match="calendar_id must be a non-empty string"):
            await mcp.tools["calendar_list_events"](calendar_id="   ")


class TestGoogleCredentialParsing:
    """Verify credential JSON parsing and validation errors."""

    def test_missing_env_is_explicit(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(GOOGLE_CALENDAR_CREDENTIALS_ENV, raising=False)

        with pytest.raises(CalendarCredentialError) as excinfo:
            _GoogleOAuthCredentials.from_env()

        assert GOOGLE_CALENDAR_CREDENTIALS_ENV in str(excinfo.value)
        assert "must be set" in str(excinfo.value)

    def test_invalid_json_is_explicit(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(GOOGLE_CALENDAR_CREDENTIALS_ENV, "{not-valid-json")

        with pytest.raises(CalendarCredentialError) as excinfo:
            _GoogleOAuthCredentials.from_env()

        assert GOOGLE_CALENDAR_CREDENTIALS_ENV in str(excinfo.value)
        assert "must be valid JSON" in str(excinfo.value)

    def test_missing_fields_are_explicit(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            GOOGLE_CALENDAR_CREDENTIALS_ENV,
            json.dumps({"client_id": "client-id-only"}),
        )

        with pytest.raises(CalendarCredentialError) as excinfo:
            _GoogleOAuthCredentials.from_env()

        message = str(excinfo.value)
        assert "missing required field(s)" in message
        assert "client_secret" in message
        assert "refresh_token" in message

    def test_supports_installed_json_shape(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            GOOGLE_CALENDAR_CREDENTIALS_ENV,
            json.dumps(
                {
                    "installed": {
                        "client_id": "installed-client-id",
                        "client_secret": "installed-client-secret",
                    },
                    "refresh_token": "installed-refresh-token",
                }
            ),
        )

        creds = _GoogleOAuthCredentials.from_env()
        assert creds.client_id == "installed-client-id"
        assert creds.client_secret == "installed-client-secret"
        assert creds.refresh_token == "installed-refresh-token"


class TestGoogleProviderInitialization:
    """Verify provider init does not leak resources on credential failures."""

    def test_invalid_credentials_do_not_create_owned_http_client(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(GOOGLE_CALENDAR_CREDENTIALS_ENV, "{not-valid-json")
        async_client_ctor = Mock()
        monkeypatch.setattr("butlers.modules.calendar.httpx.AsyncClient", async_client_ctor)

        with pytest.raises(CalendarCredentialError):
            _GoogleProvider(config=CalendarConfig(provider="google", calendar_id="primary"))

        async_client_ctor.assert_not_called()


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


class TestGoogleOAuthClient:
    """Verify access-token refresh against Google OAuth endpoint."""

    async def test_refresh_uses_client_id_secret_and_refresh_token(self):
        credentials = _GoogleOAuthCredentials(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )

        oauth = _GoogleOAuthClient(credentials=credentials, http_client=mock_client)
        access_token = await oauth.get_access_token()
        cached_access_token = await oauth.get_access_token()

        assert access_token == "access-token"
        assert cached_access_token == "access-token"
        assert mock_client.post.call_count == 1
        mock_client.post.assert_called_once_with(
            GOOGLE_OAUTH_TOKEN_URL,
            data={
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-token",
                "grant_type": "refresh_token",
            },
            headers={"Accept": "application/json"},
        )


class TestGoogleRequestHelper:
    """Verify bearer-token request wiring and safe non-2xx errors."""

    async def test_request_helper_injects_bearer_token(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            GOOGLE_CALENDAR_CREDENTIALS_ENV,
            json.dumps(
                {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "refresh_token": "refresh-token",
                }
            ),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        mock_client.request.return_value = _mock_response(
            status_code=200,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events",
            method="GET",
            json_body={"items": []},
        )

        provider = _GoogleProvider(
            config=CalendarConfig(provider="google", calendar_id="primary"),
            http_client=mock_client,
        )
        result = await provider._request_google_json("GET", "/calendars/primary/events")

        assert result == {"items": []}
        request_kwargs = mock_client.request.call_args.kwargs
        assert request_kwargs["headers"]["Authorization"] == "Bearer access-token"

    async def test_request_helper_surfaces_non_2xx_safely(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            GOOGLE_CALENDAR_CREDENTIALS_ENV,
            json.dumps(
                {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "refresh_token": "refresh-token",
                }
            ),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        mock_client.request.return_value = _mock_response(
            status_code=403,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events",
            method="GET",
            json_body={
                "error": {
                    "code": 403,
                    "message": "Forbidden by policy",
                    "status": "PERMISSION_DENIED",
                    "details": [{"private_token": "should-not-be-surfaced"}],
                }
            },
        )

        provider = _GoogleProvider(
            config=CalendarConfig(provider="google", calendar_id="primary"),
            http_client=mock_client,
        )

        with pytest.raises(CalendarRequestError) as excinfo:
            await provider._request_google_json("GET", "/calendars/primary/events")

        message = str(excinfo.value)
        assert excinfo.value.status_code == 403
        assert "Forbidden by policy" in message
        assert "private_token" not in message


class TestGoogleReadOperations:
    """Verify Google provider list/get read behavior."""

    async def test_list_events_maps_google_payload_to_calendar_events(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(
            GOOGLE_CALENDAR_CREDENTIALS_ENV,
            json.dumps(
                {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "refresh_token": "refresh-token",
                }
            ),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        mock_client.request.return_value = _mock_response(
            status_code=200,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events",
            method="GET",
            json_body={
                "items": [
                    {
                        "id": "evt-1",
                        "summary": "Team standup",
                        "description": "Daily check-in",
                        "location": "Zoom",
                        "start": {"dateTime": "2026-02-21T09:00:00-05:00"},
                        "end": {"dateTime": "2026-02-21T09:30:00-05:00"},
                        "attendees": [{"email": "alice@example.com"}],
                        "recurrence": ["RRULE:FREQ=DAILY"],
                        "colorId": "5",
                    },
                    {
                        "id": "evt-2",
                        "summary": "Holiday",
                        "start": {"date": "2026-02-22"},
                        "end": {"date": "2026-02-23"},
                    },
                ]
            },
        )

        provider = _GoogleProvider(
            config=CalendarConfig(provider="google", calendar_id="primary", timezone="UTC"),
            http_client=mock_client,
        )

        events = await provider.list_events(calendar_id="primary", limit=25)

        assert [event.event_id for event in events] == ["evt-1", "evt-2"]
        assert events[0].attendees == ["alice@example.com"]
        assert events[0].recurrence_rule == "RRULE:FREQ=DAILY"
        assert events[0].color_id == "5"
        assert events[1].start_at.isoformat() == "2026-02-22T00:00:00+00:00"
        assert events[1].end_at.isoformat() == "2026-02-23T00:00:00+00:00"

        request_kwargs = mock_client.request.call_args.kwargs
        assert request_kwargs["params"]["maxResults"] == 25
        assert request_kwargs["params"]["singleEvents"] is True
        assert request_kwargs["params"]["showDeleted"] is False

    async def test_get_event_returns_none_on_404(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            GOOGLE_CALENDAR_CREDENTIALS_ENV,
            json.dumps(
                {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "refresh_token": "refresh-token",
                }
            ),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        mock_client.request.return_value = _mock_response(
            status_code=404,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events/missing",
            method="GET",
            json_body={"error": {"message": "Not found"}},
        )

        provider = _GoogleProvider(
            config=CalendarConfig(provider="google", calendar_id="primary", timezone="UTC"),
            http_client=mock_client,
        )

        result = await provider.get_event(calendar_id="primary", event_id="missing")
        assert result is None
