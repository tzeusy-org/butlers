"""Activity feed Pydantic models.

Provides ``ActivityEvent`` and ``ActivityFeed`` for the butler-scoped
activity feed endpoint that merges sessions, pending_actions, and memory
episodes into a single time-ordered list.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class ActivityEvent(BaseModel):
    """A single event in the butler activity feed.

    Events are produced from multiple sources and normalized into a common
    envelope format.

    Attributes
    ----------
    event_type:
        Discriminator field. One of ``"session_completed"``,
        ``"approval_raised"``, ``"memory_write"``, or ``"draft_created"``.
    ts:
        Timestamp of the event (source-table column varies by event_type).
    summary:
        Human-readable one-line summary of the event.
    entity_id:
        Optional identifier for the originating entity (session ID, action
        ID, episode ID, etc.) as a string.
    metadata:
        Source-specific payload with additional context.
    """

    event_type: Literal["session_completed", "approval_raised", "memory_write", "draft_created"]
    ts: datetime
    summary: str
    entity_id: str | None = None
    metadata: dict[str, Any] = {}


class ActivityFeed(BaseModel):
    """Response model for the activity feed endpoint.

    Attributes
    ----------
    events:
        Time-ordered list of activity events, newest first.
    """

    events: list[ActivityEvent]
