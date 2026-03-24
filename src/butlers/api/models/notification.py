"""Pydantic models for the notifications API.

Maps to the ``notifications`` database table and provides an aggregation
model for dashboard statistics.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class NotificationSummary(BaseModel):
    """Notification record matching the Switchboard ``notifications`` table schema."""

    id: UUID
    source_butler: str
    channel: str
    recipient: str | None = None
    message: str
    metadata: dict | None = None
    status: str
    effective_status: str | None = None
    error: str | None = None
    session_id: UUID | None = None
    trace_id: str | None = None
    created_at: datetime


class NotificationStats(BaseModel):
    """Aggregated notification statistics for the dashboard overview.

    Provides total counts, sent/failed breakdowns, and per-channel /
    per-butler distributions.
    """

    total: int
    sent: int
    failed: int
    by_channel: dict[str, int]
    by_butler: dict[str, int]
