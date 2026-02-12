"""Calendar module scaffolding with provider-agnostic contracts.

This module defines:
- ``CalendarConfig``: validated module config with sensible defaults
- ``CalendarProvider``: provider interface used by calendar tools
- ``CalendarModule``: module shell with provider selection at startup
"""

from __future__ import annotations

import abc
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from butlers.modules.base import Module


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


class _GoogleProviderStub(CalendarProvider):
    """Placeholder provider until Google backend tasks are implemented."""

    def __init__(self, config: CalendarConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "google"

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
        return None


class CalendarModule(Module):
    """Calendar module with provider selection and validated config."""

    _PROVIDER_CLASSES: dict[str, type[CalendarProvider]] = {
        "google": _GoogleProviderStub,
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
