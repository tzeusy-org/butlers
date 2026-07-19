"""Pydantic models for the notifications API.

Maps to the ``notifications`` database table and provides an aggregation
model for dashboard statistics.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from butlers.api.models import PaginatedResponse


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


class NotificationListResponse(PaginatedResponse[NotificationSummary]):
    """Paginated notification list, plus a source-availability flag.

    ``source_available=False`` means the Switchboard notifications source
    was unreachable when this page was computed -- an empty/short page in
    that case is never a truthful "no notifications match" result (mirrors
    the repo's ``aggregates_available`` degraded-mode convention).
    """

    source_available: bool = True


class NotificationStats(BaseModel):
    """Aggregated notification statistics for the dashboard overview.

    Provides total counts, sent/failed breakdowns, and per-channel /
    per-butler distributions.
    """

    total: int
    sent: int
    failed: int
    by_channel: dict[str, int]
    # Terminal failures ONLY, grouped by source_butler (bu-y0v0c, JARVIS
    # pursuit move 9 slice 3) -- powers the notifications verdict opener's "M
    # from <butler>" clause. It matches the ``failed`` count rather than
    # including failed attempts that later delivery retries superseded.
    by_butler: dict[str, int]
    # False when the Switchboard notifications source was unreachable --
    # all counts above are zeros in that case, never a truthful "no activity".
    source_available: bool = True


class AckFailedResult(BaseModel):
    """Result of a bulk acknowledge-failed-notifications operation."""

    acknowledged: int
    """Number of notifications that were flipped from ``failed`` to ``read``."""
