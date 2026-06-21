"""Session-specific Pydantic models.

Provides ``SessionDetail`` for the full session detail endpoint, extends
the existing ``SessionSummary`` with a ``butler`` field for cross-butler
views, ``SessionKindBreakdown`` for the session-kinds analytics endpoint,
``DailyActivity`` for the daily-activity analytics endpoint,
``HourlyActivity`` for the hourly-activity analytics endpoint, and
``LatencyStats`` for the latency-stats analytics endpoint.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ProcessLog(BaseModel):
    """Process-level diagnostics from a runtime adapter invocation."""

    pid: int | None = None
    exit_code: int | None = None
    command: str | None = None
    stderr: str | None = None
    runtime_type: str | None = None
    retry_attempted: bool | None = None
    retry_succeeded: bool | None = None
    result_source: str | None = None
    attempt_count: int | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None


class SessionKindItem(BaseModel):
    """A single trigger_source bucket with its session count."""

    kind: str
    count: int


class SessionKindBreakdown(BaseModel):
    """Breakdown of sessions by trigger_source for a rolling window.

    Returned by ``GET /api/butlers/{name}/analytics/session-kinds``.

    ``kinds`` lists every distinct ``trigger_source`` value found in the
    window together with its count.  The list is empty when no sessions exist.
    """

    kinds: list[SessionKindItem] = []


class DailyActivityBucket(BaseModel):
    """Session count for a single calendar day."""

    date: date
    sessions_count: int


class DailyActivity(BaseModel):
    """Daily session counts over a rolling window."""

    buckets: list[DailyActivityBucket] = []


class HourlyActivityBucket(BaseModel):
    """Session count for a single clock hour.

    ``hour_index=0`` is the most recent (current) hour; higher values are
    further back in time.  This ordering matches the left-to-right stripe
    rendering convention on the dashboard Activity tab.
    """

    hour_start: datetime
    sessions_count: int
    hour_index: int


class HourlyActivity(BaseModel):
    """Hourly session counts over a rolling window.

    Returned by ``GET /api/butlers/{name}/analytics/hourly-activity``.

    ``buckets`` is a dense series — every hour in the window is present,
    including zero-count hours (generated via ``generate_series`` + LEFT
    JOIN in SQL).  ``hour_index=0`` is the current hour.
    """

    buckets: list[HourlyActivityBucket] = []


class LatencyStats(BaseModel):
    """Latency percentile statistics for a butler over a rolling window.

    Returned by ``GET /api/butlers/{name}/analytics/latency-stats``.

    All duration fields are in milliseconds.  When no sessions with a
    recorded ``duration_ms`` exist in the window, ``count`` is 0 and the
    percentile/mean fields are ``None``.
    """

    p50_ms: float | None = None
    p95_ms: float | None = None
    mean_ms: float | None = None
    count: int = 0
    model: str | None = None


class SessionAggregateButler(BaseModel):
    """A single butler's matching-session count for the aggregate rollup."""

    butler: str
    count: int


class SessionAggregate(BaseModel):
    """Window-scoped, filter-aware session rollup across all butlers.

    Returned by ``GET /api/sessions/aggregate``.  ``total`` counts every session
    matching the active filters across all queried butlers (window-true, not the
    fetched page).  ``running_count`` is sessions with ``success IS NULL``.

    ``success_rate`` is ``success_count / (success_count + failed_count)`` or
    ``None`` when the denominator is 0 (no completed sessions to rate).

    Cost is intentionally omitted — it is not part of the summary contract.
    """

    total: int
    success_count: int
    failed_count: int
    running_count: int
    success_rate: float | None = None
    input_tokens: int
    output_tokens: int
    by_butler: list[SessionAggregateButler] = []


class SessionDetail(BaseModel):
    """Full session record with all fields from the sessions table."""

    id: UUID
    butler: str | None = None
    prompt: str
    trigger_source: str
    result: str | None = None
    tool_calls: list[dict[str, Any]] = []
    duration_ms: int | None = None
    trace_id: str | None = None
    request_id: str | None = None
    cost: dict[str, Any] | None = None
    started_at: datetime
    completed_at: datetime | None = None
    success: bool | None = None
    error: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    parent_session_id: UUID | None = None
    process_log: ProcessLog | None = None
    complexity: str | None = None
    resolution_source: str | None = None
    correction_count: int = 0
