"""Messenger-specific core tools.

Registered unconditionally (no _core_tool group) but only when butler_name == 'messenger'.

Tools:
- delivery_preferences_set
- delivery_preferences_get
- deferred_notifications_list
- deferred_notification_cancel
- messenger_delivery_status  (tool name: messenger_delivery_status)
- messenger_delivery_search  (tool name: messenger_delivery_search)
- messenger_delivery_attempts (tool name: messenger_delivery_attempts)
- messenger_delivery_trace   (tool name: messenger_delivery_trace)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from butlers.core.telemetry import tool_span
from butlers.core_tools._base import ToolContext


def register_messenger_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register messenger-only tools.

    Covers delivery preferences, deferred notifications, and delivery ops.
    """
    daemon = ctx.daemon
    pool = ctx.pool
    butler_name = ctx.butler_name
    is_messenger = ctx.is_messenger

    # ----- Messenger-only: delivery preferences and deferred notifications.
    if is_messenger:

        @mcp.tool()
        async def delivery_preferences_set(
            timezone: str,
            quiet_hours_start: str | None = None,
            quiet_hours_end: str | None = None,
            batch_low_priority: bool | None = None,
            batch_delivery_time: str | None = None,
            override_channels: dict[str, Any] | None = None,
        ) -> dict:
            """Configure delivery preferences for quiet hours and batching.

            Sets the butler's delivery preferences, which control when notifications
            are delivered vs deferred. On first call, a row is created with defaults
            for any unspecified fields.

            Args:
                timezone: IANA timezone string (required). Used to evaluate quiet hours
                    in the user's local time. Example: 'America/New_York'.
                quiet_hours_start: Quiet hours start in 'HH:MM' format (default '22:00').
                quiet_hours_end: Quiet hours end in 'HH:MM' format (default '07:00').
                batch_low_priority: If True, batch medium/low notifications during quiet
                    hours and deliver at batch_delivery_time (default True).
                batch_delivery_time: Time in 'HH:MM' format to flush deferred
                    notifications (default '07:00').
                override_channels: Per-channel quiet hours overrides as a dict mapping
                    channel names to {quiet_hours_start, quiet_hours_end} dicts.
                    Example: {'email': {'quiet_hours_start': '20:00',
                    'quiet_hours_end': '09:00'}}

            Returns:
                The upserted delivery preferences row.
            """
            from butlers.core.temporal.delivery_db import upsert_delivery_preferences

            _db_pool = daemon.db.pool if daemon.db is not None else None
            if _db_pool is None:
                return {"status": "error", "error": "Database not available."}
            try:
                result = await upsert_delivery_preferences(
                    _db_pool,
                    butler_name=butler_name,
                    timezone=timezone,
                    quiet_hours_start=quiet_hours_start,
                    quiet_hours_end=quiet_hours_end,
                    batch_low_priority=batch_low_priority,
                    batch_delivery_time=batch_delivery_time,
                    override_channels=override_channels,
                )
                return {"status": "ok", "preferences": result}
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @mcp.tool()
        async def delivery_preferences_get() -> dict:
            """Get the current delivery preferences for this butler.

            Returns the current delivery preferences, or a response indicating that
            no preferences are configured (in which case no quiet hours enforcement
            applies and all notifications deliver immediately).

            Returns:
                Dict with 'preferences' key containing the current row, or
                'preferences': None if no preferences are configured.
            """
            from butlers.core.temporal.delivery_db import get_delivery_preferences

            _db_pool = daemon.db.pool if daemon.db is not None else None
            if _db_pool is None:
                return {"status": "error", "error": "Database not available."}
            prefs = await get_delivery_preferences(_db_pool, butler_name)
            if prefs is None:
                return {
                    "status": "ok",
                    "preferences": None,
                    "message": (
                        "No delivery preferences configured. "
                        "All notifications deliver immediately (no quiet hours)."
                    ),
                }
            return {"status": "ok", "preferences": prefs}

        @mcp.tool()
        async def deferred_notifications_list(
            status: str | None = None,
            limit: int = 100,
        ) -> dict:
            """List deferred notifications for this butler.

            Args:
                status: Optional filter. One of: pending | delivered | expired | cancelled.
                    If omitted, all statuses are returned.
                limit: Maximum number of rows to return (default 100).

            Returns:
                Dict with 'notifications' list ordered by deliver_at ascending.
            """
            from butlers.core.temporal.delivery_db import list_deferred_notifications

            _db_pool = daemon.db.pool if daemon.db is not None else None
            if _db_pool is None:
                return {"status": "error", "error": "Database not available."}
            try:
                notifications = await list_deferred_notifications(
                    _db_pool,
                    butler_name=butler_name,
                    status=status,
                    limit=limit,
                )
                return {
                    "status": "ok",
                    "notifications": notifications,
                    "count": len(notifications),
                }
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @mcp.tool()
        async def deferred_notification_cancel(notification_id: str) -> dict:
            """Cancel a pending deferred notification.

            Only notifications with status='pending' can be cancelled. Delivered
            or expired notifications cannot be cancelled.

            Args:
                notification_id: UUID of the deferred notification to cancel.

            Returns:
                Dict with status='cancelled' if successful, or an error if not found
                or already delivered/expired.
            """
            from butlers.core.temporal.delivery_db import cancel_deferred_notification

            _db_pool = daemon.db.pool if daemon.db is not None else None
            if _db_pool is None:
                return {"status": "error", "error": "Database not available."}
            try:
                cancelled = await cancel_deferred_notification(
                    _db_pool,
                    notification_id,
                    butler_name=butler_name,
                )
                if cancelled:
                    return {"status": "cancelled", "notification_id": notification_id}
                return {
                    "status": "error",
                    "error": (
                        f"Notification {notification_id!r} not found, "
                        "not owned by this butler, "
                        "or already delivered/expired."
                    ),
                }
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @mcp.tool()
        async def scheduling_preferences_set(
            timezone: str,
            earliest_meeting_time: str | None = None,
            latest_meeting_time: str | None = None,
            meeting_days: list[str] | None = None,
            no_meeting_blocks: list[dict[str, str]] | None = None,
        ) -> dict:
            """Configure the owner's meeting-availability (life) hours.

            These are the owner's LIFE no-meeting blocks ("don't schedule meetings
            before 09:00 or on weekends") and are DISTINCT from notification quiet
            hours (delivery_preferences). They are owner-scoped — a single record,
            not per-butler — and feed calendar slot ranking so suggestions never
            land outside the allowed hours/days or inside a no-meeting block.
            Setting them does NOT change notification quiet-hours behavior.

            Args:
                timezone: IANA timezone string (required) used to interpret the
                    times below in the owner's local time. Example: 'America/New_York'.
                earliest_meeting_time: Earliest a meeting may start, 'HH:MM' (owner tz).
                latest_meeting_time: Latest a meeting may end, 'HH:MM' (owner tz).
                meeting_days: Allowed weekdays as iCal codes, e.g.
                    ['MO','TU','WE','TH','FR']. Omit for any day.
                no_meeting_blocks: Recurring daily blocks to keep free, e.g.
                    [{'start': '12:00', 'end': '13:00'}] for a lunch break.

            Returns:
                The upserted owner scheduling-availability record.
            """
            from butlers.core.temporal.scheduling import upsert_scheduling_preferences

            _db_pool = daemon.db.pool if daemon.db is not None else None
            if _db_pool is None:
                return {"status": "error", "error": "Database not available."}
            try:
                result = await upsert_scheduling_preferences(
                    _db_pool,
                    timezone=timezone,
                    earliest_meeting_time=earliest_meeting_time,
                    latest_meeting_time=latest_meeting_time,
                    meeting_days=meeting_days,
                    no_meeting_blocks=no_meeting_blocks,
                )
                return {"status": "ok", "preferences": result}
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @mcp.tool()
        async def scheduling_preferences_get() -> dict:
            """Get the owner's meeting-availability (life) hours.

            Returns the owner's scheduling-availability preferences (earliest/latest
            meeting time, allowed weekdays, owner timezone, no-meeting blocks), or a
            response indicating none are configured (in which case slot ranking
            applies no life-availability filtering).

            Returns:
                Dict with 'preferences' key, or 'preferences': None if unconfigured.
            """
            from butlers.core.temporal.scheduling import get_scheduling_preferences

            _db_pool = daemon.db.pool if daemon.db is not None else None
            if _db_pool is None:
                return {"status": "error", "error": "Database not available."}
            prefs = await get_scheduling_preferences(_db_pool)
            if prefs is None:
                return {
                    "status": "ok",
                    "preferences": None,
                    "message": (
                        "No scheduling preferences configured. "
                        "Slot ranking applies no life-availability filtering."
                    ),
                }
            return {"status": "ok", "preferences": prefs}

    # Messenger-specific operational domain tools
    if butler_name == "messenger":
        from butlers.tools.messenger import (
            messenger_delivery_attempts,
            messenger_delivery_search,
            messenger_delivery_status,
            messenger_delivery_trace,
        )

        @mcp.tool()
        @tool_span("messenger_delivery_status", butler_name=butler_name)
        async def _messenger_delivery_status(delivery_id: str) -> dict:
            """Get the current status of a delivery request.

            Returns the current terminal or in-flight status of a single
            delivery, including the latest attempt outcome and provider
            delivery ID when available.
            """
            return await messenger_delivery_status(pool, delivery_id)

        @mcp.tool()
        @tool_span("messenger_delivery_search", butler_name=butler_name)
        async def _messenger_delivery_search(
            origin_butler: str | None = None,
            channel: str | None = None,
            intent: str | None = None,
            status: str | None = None,
            since: str | None = None,
            until: str | None = None,
            limit: int = 50,
        ) -> dict:
            """Search delivery history with filters.

            Returns paginated delivery summaries sorted by recency (newest
            first). Supports filtering by origin butler, channel, intent,
            status, and time range.
            """
            return await messenger_delivery_search(
                pool,
                origin_butler=origin_butler,
                channel=channel,
                intent=intent,
                status=status,
                since=since,
                until=until,
                limit=limit,
            )

        @mcp.tool()
        @tool_span("messenger_delivery_attempts", butler_name=butler_name)
        async def _messenger_delivery_attempts(delivery_id: str) -> dict:
            """Get the full attempt history for a delivery.

            Returns the full attempt log for a delivery: timestamps,
            outcomes, latencies, error classes, retryability. Essential
            for diagnosing flaky provider behavior.
            """
            return await messenger_delivery_attempts(pool, delivery_id)

        @mcp.tool()
        @tool_span("messenger_delivery_trace", butler_name=butler_name)
        async def _messenger_delivery_trace(request_id: str) -> dict:
            """Reconstruct full lineage for a request.

            Traces from the originating butler's notify.v1 envelope through
            Switchboard routing, Messenger admission, validation, target
            resolution, provider attempts, and terminal outcome.
            """
            return await messenger_delivery_trace(pool, request_id)
