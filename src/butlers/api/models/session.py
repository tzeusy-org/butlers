"""Session-specific Pydantic models.

Provides ``SessionDetail`` for the full session detail endpoint, extends
the existing ``SessionSummary`` with a ``butler`` field for cross-butler
views, and ``SessionKindBreakdown`` for the session-kinds analytics endpoint.
"""

from __future__ import annotations

from datetime import datetime
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
