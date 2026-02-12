"""Tests for calendar module config and provider interface scaffolding."""

from __future__ import annotations

import json
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
        await mod.register_tools(mcp=object(), config=cfg, db=None)
        assert isinstance(getattr(mod, "_config"), CalendarConfig)

    async def test_register_tools_accepts_dict_config(self):
        mod = CalendarModule()
        await mod.register_tools(
            mcp=object(),
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
        )
        stored = getattr(mod, "_config")
        assert isinstance(stored, CalendarConfig)
        assert stored.provider == "google"


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
