"""Calendar module scaffolding with provider-agnostic contracts.

This module defines:
- ``CalendarConfig``: validated module config with sensible defaults
- ``CalendarProvider``: provider interface used by calendar tools
- ``CalendarModule``: module shell with provider selection at startup
"""

from __future__ import annotations

import abc
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from butlers.modules.base import Module

GOOGLE_CALENDAR_CREDENTIALS_ENV = "BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API_BASE_URL = "https://www.googleapis.com/calendar/v3"


class CalendarAuthError(RuntimeError):
    """Base error raised by Google Calendar auth/request helpers."""


class CalendarCredentialError(CalendarAuthError):
    """Raised when Google credential JSON is missing or invalid."""


class CalendarTokenRefreshError(CalendarAuthError):
    """Raised when refresh-token exchange fails."""


class CalendarRequestError(CalendarAuthError):
    """Raised when Google Calendar API request fails."""

    def __init__(self, *, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"Google Calendar API request failed ({status_code}): {message}")


class _GoogleOAuthCredentials(BaseModel):
    """OAuth client credentials required for refresh-token exchange."""

    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)

    @field_validator("client_id", "client_secret", "refresh_token")
    @classmethod
    def _normalize_non_empty(cls, value: str, info: ValidationInfo) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return normalized

    @classmethod
    def from_env(cls) -> _GoogleOAuthCredentials:
        raw_value = os.environ.get(GOOGLE_CALENDAR_CREDENTIALS_ENV, "").strip()
        if not raw_value:
            raise CalendarCredentialError(
                f"{GOOGLE_CALENDAR_CREDENTIALS_ENV} must be set to a non-empty JSON object"
            )
        return cls.from_json(raw_value)

    @classmethod
    def from_json(cls, raw_value: str) -> _GoogleOAuthCredentials:
        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise CalendarCredentialError(
                f"{GOOGLE_CALENDAR_CREDENTIALS_ENV} must be valid JSON: {exc.msg}"
            ) from exc

        if not isinstance(payload, dict):
            raise CalendarCredentialError(
                f"{GOOGLE_CALENDAR_CREDENTIALS_ENV} must decode to a JSON object"
            )

        credential_data = {
            "client_id": _extract_google_credential_value(payload, "client_id"),
            "client_secret": _extract_google_credential_value(payload, "client_secret"),
            "refresh_token": _extract_google_credential_value(payload, "refresh_token"),
        }

        missing = sorted(key for key, value in credential_data.items() if value is None)
        if missing:
            field_list = ", ".join(missing)
            raise CalendarCredentialError(
                f"{GOOGLE_CALENDAR_CREDENTIALS_ENV} is missing required field(s): {field_list}"
            )

        invalid = sorted(
            key
            for key, value in credential_data.items()
            if not isinstance(value, str) or not value.strip()
        )
        if invalid:
            field_list = ", ".join(invalid)
            raise CalendarCredentialError(
                f"{GOOGLE_CALENDAR_CREDENTIALS_ENV} must contain non-empty string "
                f"field(s): {field_list}"
            )

        return cls(
            client_id=str(credential_data["client_id"]),
            client_secret=str(credential_data["client_secret"]),
            refresh_token=str(credential_data["refresh_token"]),
        )


class _GoogleOAuthClient:
    """Refresh-token OAuth helper with lightweight access-token caching."""

    def __init__(
        self,
        credentials: _GoogleOAuthCredentials,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._credentials = credentials
        self._http_client = http_client
        self._access_token: str | None = None
        self._access_token_expires_at: datetime | None = None
        self._refresh_lock = asyncio.Lock()

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh and self._token_is_fresh():
            assert self._access_token is not None
            return self._access_token

        async with self._refresh_lock:
            if not force_refresh and self._token_is_fresh():
                assert self._access_token is not None
                return self._access_token

            await self._refresh_access_token()
            assert self._access_token is not None
            return self._access_token

    def _token_is_fresh(self) -> bool:
        if self._access_token is None or self._access_token_expires_at is None:
            return False
        return datetime.now(UTC) < self._access_token_expires_at

    async def _refresh_access_token(self) -> None:
        try:
            response = await self._http_client.post(
                GOOGLE_OAUTH_TOKEN_URL,
                data={
                    "client_id": self._credentials.client_id,
                    "client_secret": self._credentials.client_secret,
                    "refresh_token": self._credentials.refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise CalendarTokenRefreshError(
                f"Google OAuth token refresh request failed: {exc}"
            ) from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise CalendarTokenRefreshError(
                "Google OAuth token refresh failed "
                f"({response.status_code}): {_safe_google_error_message(response)}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise CalendarTokenRefreshError(
                "Google OAuth token endpoint returned invalid JSON"
            ) from exc

        access_token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(access_token, str) or not access_token.strip():
            raise CalendarTokenRefreshError(
                "Google OAuth token response is missing a non-empty access_token"
            )

        expires_in_raw = payload.get("expires_in") if isinstance(payload, dict) else None
        expires_in_seconds = _coerce_expires_in_seconds(expires_in_raw)
        # Refresh early to avoid edge-of-expiration failures.
        refresh_ttl_seconds = max(expires_in_seconds - 60, 30)

        self._access_token = access_token.strip()
        self._access_token_expires_at = datetime.now(UTC) + timedelta(seconds=refresh_ttl_seconds)


def _extract_google_credential_value(payload: dict[str, Any], key: str) -> Any:
    if key in payload:
        return payload[key]

    for nested_key in ("installed", "web"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict) and key in nested:
            return nested[key]
    return None


def _coerce_expires_in_seconds(value: Any) -> int:
    if isinstance(value, bool):
        return 3600
    if isinstance(value, int | float):
        return int(value) if value > 0 else 3600
    return 3600


def _safe_google_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            message = error_payload.get("message")
            if isinstance(message, str) and message.strip():
                return " ".join(message.split())[:200]
        if isinstance(error_payload, str) and error_payload.strip():
            return " ".join(error_payload.split())[:200]

    raw_text = response.text.strip()
    if raw_text:
        return " ".join(raw_text.split())[:200]
    return "Request failed without an error payload"


class CalendarConflictDefaults(BaseModel):
    """Default behavior for overlapping event handling."""

    model_config = ConfigDict(extra="forbid")

    policy: Literal["suggest", "allow", "reject"] = "suggest"
    require_approval_for_overlap: bool = True


class CalendarNotificationDefaults(BaseModel):
    """Default notification and color behavior for new events."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    minutes_before: int = Field(default=15, ge=0)
    color_id: str | None = None


class CalendarConfig(BaseModel):
    """Configuration for the Calendar module."""

    provider: str = Field(min_length=1)
    calendar_id: str = Field(min_length=1)
    timezone: str = "UTC"
    conflicts: CalendarConflictDefaults = Field(default_factory=CalendarConflictDefaults)
    event_defaults: CalendarNotificationDefaults = Field(
        default_factory=CalendarNotificationDefaults
    )

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("provider must be a non-empty string")
        return normalized

    @field_validator("calendar_id", "timezone")
    @classmethod
    def _normalize_non_empty(cls, value: str, info: ValidationInfo) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return normalized


class CalendarEvent(BaseModel):
    """Canonical event shape shared across provider implementations."""

    event_id: str
    title: str
    start_at: datetime
    end_at: datetime
    timezone: str
    description: str | None = None
    location: str | None = None
    attendees: list[str] = Field(default_factory=list)
    recurrence_rule: str | None = None
    color_id: str | None = None


class CalendarEventCreate(BaseModel):
    """Payload for creating a calendar event."""

    title: str
    start_at: datetime
    end_at: datetime
    timezone: str | None = None
    description: str | None = None
    location: str | None = None
    attendees: list[str] = Field(default_factory=list)
    recurrence_rule: str | None = None
    color_id: str | None = None


class CalendarEventUpdate(BaseModel):
    """Patch payload for updating a calendar event."""

    title: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str | None = None
    description: str | None = None
    location: str | None = None
    attendees: list[str] | None = None
    recurrence_rule: str | None = None
    color_id: str | None = None


class CalendarProvider(abc.ABC):
    """Provider abstraction used by calendar tools."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g., ``google``)."""
        ...

    @abc.abstractmethod
    async def list_events(
        self,
        *,
        calendar_id: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 50,
    ) -> list[CalendarEvent]:
        """Return events in a time window."""
        ...

    @abc.abstractmethod
    async def get_event(self, *, calendar_id: str, event_id: str) -> CalendarEvent | None:
        """Fetch a single event by id."""
        ...

    @abc.abstractmethod
    async def create_event(
        self,
        *,
        calendar_id: str,
        payload: CalendarEventCreate,
    ) -> CalendarEvent:
        """Create an event."""
        ...

    @abc.abstractmethod
    async def update_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        patch: CalendarEventUpdate,
    ) -> CalendarEvent:
        """Update an event."""
        ...

    @abc.abstractmethod
    async def delete_event(self, *, calendar_id: str, event_id: str) -> None:
        """Delete (or cancel) an event."""
        ...

    @abc.abstractmethod
    async def find_conflicts(
        self,
        *,
        calendar_id: str,
        candidate: CalendarEventCreate,
    ) -> list[CalendarEvent]:
        """Find overlapping events for a candidate event."""
        ...

    @abc.abstractmethod
    async def shutdown(self) -> None:
        """Release provider resources."""
        ...


class _GoogleProvider(CalendarProvider):
    """Google provider with OAuth refresh-token and authenticated request helpers."""

    def __init__(
        self,
        config: CalendarConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._owns_http_client = http_client is None
        credentials = _GoogleOAuthCredentials.from_env()
        self._http_client = http_client or httpx.AsyncClient(timeout=30.0)
        self._oauth = _GoogleOAuthClient(credentials, self._http_client)

    @property
    def name(self) -> str:
        return "google"

    async def _request_google_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._request_with_bearer(
            method=method,
            path=path,
            params=params,
            json_body=json_body,
        )

        if response.status_code < 200 or response.status_code >= 300:
            raise CalendarRequestError(
                status_code=response.status_code,
                message=_safe_google_error_message(response),
            )

        if response.status_code == 204:
            return {}

        try:
            payload = response.json()
        except ValueError as exc:
            raise CalendarAuthError(
                "Google Calendar API returned invalid JSON for a successful response"
            ) from exc

        if not isinstance(payload, dict):
            raise CalendarAuthError("Google Calendar API returned an unexpected JSON payload shape")
        return payload

    async def _request_with_bearer(
        self,
        *,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{GOOGLE_CALENDAR_API_BASE_URL}{normalized_path}"

        response = await self._request_once(
            method=method,
            url=url,
            params=params,
            json_body=json_body,
            force_refresh=False,
        )

        if response.status_code == 401:
            response = await self._request_once(
                method=method,
                url=url,
                params=params,
                json_body=json_body,
                force_refresh=True,
            )
        return response

    async def _request_once(
        self,
        *,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        force_refresh: bool,
    ) -> httpx.Response:
        access_token = await self._oauth.get_access_token(force_refresh=force_refresh)
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            return await self._http_client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise CalendarAuthError(f"Google Calendar request failed: {exc}") from exc

    async def list_events(
        self,
        *,
        calendar_id: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 50,
    ) -> list[CalendarEvent]:
        raise NotImplementedError("Google calendar provider is not implemented yet")

    async def get_event(self, *, calendar_id: str, event_id: str) -> CalendarEvent | None:
        raise NotImplementedError("Google calendar provider is not implemented yet")

    async def create_event(
        self,
        *,
        calendar_id: str,
        payload: CalendarEventCreate,
    ) -> CalendarEvent:
        raise NotImplementedError("Google calendar provider is not implemented yet")

    async def update_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        patch: CalendarEventUpdate,
    ) -> CalendarEvent:
        raise NotImplementedError("Google calendar provider is not implemented yet")

    async def delete_event(self, *, calendar_id: str, event_id: str) -> None:
        raise NotImplementedError("Google calendar provider is not implemented yet")

    async def find_conflicts(
        self,
        *,
        calendar_id: str,
        candidate: CalendarEventCreate,
    ) -> list[CalendarEvent]:
        raise NotImplementedError("Google calendar provider is not implemented yet")

    async def shutdown(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()


class CalendarModule(Module):
    """Calendar module with provider selection and validated config."""

    _PROVIDER_CLASSES: dict[str, type[CalendarProvider]] = {
        "google": _GoogleProvider,
    }

    def __init__(self) -> None:
        self._config: CalendarConfig | None = None
        self._provider: CalendarProvider | None = None

    @property
    def name(self) -> str:
        return "calendar"

    @property
    def config_schema(self) -> type[BaseModel]:
        return CalendarConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    @property
    def credentials_env(self) -> list[str]:
        return [GOOGLE_CALENDAR_CREDENTIALS_ENV]

    def migration_revisions(self) -> str | None:
        return None

    @staticmethod
    def _coerce_config(config: Any) -> CalendarConfig:
        return config if isinstance(config, CalendarConfig) else CalendarConfig(**(config or {}))

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        # Calendar tools are introduced in later tasks; we still keep the
        # validated config available for parity with other modules.
        self._config = self._coerce_config(config)

    async def on_startup(self, config: Any, db: Any) -> None:
        self._config = self._coerce_config(config)

        provider_cls = self._PROVIDER_CLASSES.get(self._config.provider)
        if provider_cls is None:
            supported = ", ".join(sorted(self._PROVIDER_CLASSES))
            raise RuntimeError(
                f"Unsupported calendar provider '{self._config.provider}'. "
                f"Supported providers: {supported}"
            )

        self._provider = provider_cls(self._config)

    async def on_shutdown(self) -> None:
        if self._provider is not None:
            await self._provider.shutdown()
        self._provider = None
