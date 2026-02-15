"""Session-specific Pydantic models.

Provides ``SessionDetail`` for the full session detail endpoint and extends
the existing ``SessionSummary`` with a ``butler`` field for cross-butler views.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


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
