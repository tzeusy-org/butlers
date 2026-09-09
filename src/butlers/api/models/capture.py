"""Response models for GET /api/captures."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CaptureSummary(BaseModel):
    """A single row from public.captures, as returned to the dashboard."""

    capture_id: str
    channel: str
    content: str
    receipt_state: str
    routed_kind: str | None = None
    target_schema: str | None = None
    target_table: str | None = None
    target_row_id: str | None = None
    refusal_reason: str | None = None
    source_butler: str
    created_at: datetime
    updated_at: datetime
