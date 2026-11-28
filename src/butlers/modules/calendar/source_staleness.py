"""One calendar source's staleness verdict, shared by the dashboard radar and the plan tool."""

from __future__ import annotations

from datetime import datetime, timedelta


def source_is_stale(
    *,
    last_success_at: datetime | None,
    last_error_at: datetime | None,
    full_sync_required: bool,
    now: datetime,
    threshold: timedelta,
) -> bool:
    """True when a source never synced, last succeeded too long ago, must resync, or failed since.

    The poll interval is butler config and is not stored per source, so the default threshold
    applies; the source's own state still counts: a source that needs a full resync, or whose
    latest attempt failed after its last success, is stale at once.
    """
    if last_success_at is None or now - last_success_at > threshold or full_sync_required:
        return True
    return last_error_at is not None and last_error_at > last_success_at
