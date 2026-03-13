"""Timeline-specific Pydantic models.

Provides ``TimelineEvent`` and ``TimelineResponse`` for the cross-butler
timeline endpoint that merges sessions and notifications into a unified
event stream with cursor-based pagination.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class TimelineEvent(BaseModel):
    """A single event in the cross-butler timeline.

    Events are produced from multiple sources (sessions, notifications) and
    normalized into a common envelope format.

    Attributes
    ----------
    id:
        Unique identifier for the event (source record UUID).
    type:
        Event type: ``"session"``, ``"error"``, or ``"notification"``.
    butler:
        Name of the butler that produced the event.
    timestamp:
        When the event occurred (started_at for sessions, created_at for
        notifications).
    summary:
        Human-readable one-line summary of the event.
    data:
        Source-specific payload (e.g. session fields, notification fields).
    """

    id: UUID
    type: str
    butler: str
    timestamp: datetime
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)


class TimelineMeta(BaseModel):
    """Pagination metadata for timeline responses."""

    cursor: str | None = None
    has_more: bool = False


class TimelineResponse(BaseModel):
    """Cursor-paginated timeline response.

    Uses ``before`` timestamp cursor for efficient descending pagination.
    The ``meta.cursor`` field contains the ISO timestamp to pass as the
    ``before`` parameter for the next page. ``meta.has_more`` indicates
    whether additional pages exist.
    """

    data: list[TimelineEvent]
    meta: TimelineMeta = Field(default_factory=TimelineMeta)
