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
    is_heartbeat: bool = Field(
        default=False,
        description=(
            "True when this event's trigger_source is a heartbeat/tick source "
            "('tick' or 'heartbeat'), classified server-side. Replaces the old "
            "client-side substring sniff on the summary text (which matched "
            "real owner events like 'Buy concert tickets')."
        ),
    )
    data: dict[str, Any] = Field(default_factory=dict)


class TimelineHeartbeatRollup(BaseModel):
    """Aggregate counts over the heartbeat events in the current page.

    Computed server-side so the UI never has to (mis)derive rollup copy from
    raw events — e.g. the correct phrasing is ``"{ticks} ticks · {butlers}
    butlers · {failed} failed"``, not "{ticks} butlers ticked" (ticks and
    distinct butler count are different numbers whenever a butler ticks more
    than once in the page).
    """

    ticks: int = 0
    butlers: int = 0
    failed: int = 0


class TimelineMeta(BaseModel):
    """Pagination metadata for timeline responses."""

    cursor: str | None = None
    has_more: bool = False
    heartbeat_rollup: TimelineHeartbeatRollup = Field(default_factory=TimelineHeartbeatRollup)
    degraded_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Names of event sources ('sessions', 'notifications') whose "
            "query failed for this request. A non-empty list means the "
            "returned page is a partial view of that source, not a truthful "
            "empty result — mirrors the aggregates_available degraded-mode "
            "convention (see CLAUDE.md) applied per-source instead of as a "
            "single flag."
        ),
    )


class TimelineResponse(BaseModel):
    """Cursor-paginated timeline response.

    Uses an opaque composite ``(timestamp, id)`` keyset cursor for stable
    descending pagination — pass ``meta.cursor`` back as the ``before``
    parameter for the next page. ``meta.has_more`` indicates whether
    additional pages exist. A bare ISO-8601 timestamp is also still accepted
    as ``before`` for backward compatibility, without the same-timestamp
    tiebreak that the composite cursor provides.
    """

    data: list[TimelineEvent]
    meta: TimelineMeta = Field(default_factory=TimelineMeta)
