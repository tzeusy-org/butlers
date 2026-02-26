"""Schedule-specific Pydantic models.

Provides request/response models for the schedules CRUD endpoints.
Reads come from the butler's ``scheduled_tasks`` table; writes are
proxied through MCP tool calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, model_validator

_DISPATCH_MODE_PROMPT: Literal["prompt"] = "prompt"
_DISPATCH_MODE_JOB: Literal["job"] = "job"
DispatchMode = Literal["prompt", "job"]


def _validate_projection_window(
    *,
    start_at: datetime | None,
    end_at: datetime | None,
    until_at: datetime | None,
    context: str,
) -> None:
    if start_at is not None and end_at is not None and end_at <= start_at:
        raise ValueError(f"{context}.end_at must be after start_at")
    if start_at is not None and until_at is not None and until_at < start_at:
        raise ValueError(f"{context}.until_at must be on/after start_at")


def _validate_projection_strings(
    *,
    timezone: str | None,
    display_title: str | None,
    context: str,
) -> None:
    if timezone is not None and not timezone.strip():
        raise ValueError(f"{context}.timezone must be non-empty when set")
    if display_title is not None and not display_title.strip():
        raise ValueError(f"{context}.display_title must be non-empty when set")


class Schedule(BaseModel):
    """Full scheduled task record from the ``scheduled_tasks`` table."""

    id: UUID
    name: str
    cron: str
    dispatch_mode: DispatchMode = _DISPATCH_MODE_PROMPT
    prompt: str | None = None
    job_name: str | None = None
    job_args: dict[str, Any] | None = None
    timezone: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    until_at: datetime | None = None
    display_title: str | None = None
    calendar_event_id: str | None = None
    source: str = "db"
    enabled: bool = True
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ScheduleCreate(BaseModel):
    """Request body for creating a new scheduled task."""

    name: str
    cron: str
    dispatch_mode: DispatchMode = _DISPATCH_MODE_PROMPT
    prompt: str | None = None
    job_name: str | None = None
    job_args: dict[str, Any] | None = None
    timezone: str | None = None
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    until_at: AwareDatetime | None = None
    display_title: str | None = None
    calendar_event_id: str | None = None

    @model_validator(mode="after")
    def validate_dispatch_payload(self) -> ScheduleCreate:
        """Enforce mode-specific create payload constraints."""
        if self.dispatch_mode == _DISPATCH_MODE_PROMPT:
            if self.prompt is None or not self.prompt.strip():
                raise ValueError("prompt is required when dispatch_mode='prompt'")
            if self.job_name is not None:
                raise ValueError("job_name is only valid when dispatch_mode='job'")
            if self.job_args is not None:
                raise ValueError("job_args is only valid when dispatch_mode='job'")
        else:
            if self.prompt is not None:
                raise ValueError("prompt is not allowed when dispatch_mode='job'")
            if self.job_name is None or not self.job_name.strip():
                raise ValueError("job_name is required when dispatch_mode='job'")
        _validate_projection_window(
            start_at=self.start_at,
            end_at=self.end_at,
            until_at=self.until_at,
            context="schedule_create",
        )
        _validate_projection_strings(
            timezone=self.timezone,
            display_title=self.display_title,
            context="schedule_create",
        )
        return self


class ScheduleUpdate(BaseModel):
    """Request body for updating a scheduled task.

    All fields are optional; only provided fields are applied.
    """

    name: str | None = None
    cron: str | None = None
    dispatch_mode: DispatchMode | None = None
    prompt: str | None = None
    job_name: str | None = None
    job_args: dict[str, Any] | None = None
    enabled: bool | None = None
    timezone: str | None = None
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    until_at: AwareDatetime | None = None
    display_title: str | None = None
    calendar_event_id: str | None = None

    @model_validator(mode="after")
    def validate_dispatch_payload(self) -> ScheduleUpdate:
        """Reject clearly invalid mode-specific update combinations."""
        if self.dispatch_mode == _DISPATCH_MODE_PROMPT:
            if self.job_name is not None:
                raise ValueError("job_name is only valid when dispatch_mode='job'")
            if self.job_args is not None:
                raise ValueError("job_args is only valid when dispatch_mode='job'")
        elif self.dispatch_mode == _DISPATCH_MODE_JOB and self.prompt is not None:
            raise ValueError("prompt is not allowed when dispatch_mode='job'")
        _validate_projection_window(
            start_at=self.start_at,
            end_at=self.end_at,
            until_at=self.until_at,
            context="schedule_update",
        )
        _validate_projection_strings(
            timezone=self.timezone,
            display_title=self.display_title,
            context="schedule_update",
        )
        return self
