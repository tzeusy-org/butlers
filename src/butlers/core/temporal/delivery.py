"""Time-aware delivery logic for temporal intelligence.

Provides pure functions for:
  - is_in_quiet_hours             — Check if a time falls within quiet hours
  - resolve_effective_quiet_hours — Resolve per-channel overrides from delivery preferences
  - should_defer_notification     — Determine if a notification should be deferred
  - compute_deliver_at            — Compute the next batch delivery time
  - is_notification_due           — Check if a deferred notification is due for delivery
  - is_notification_expired       — Check if a notification has expired (>24h past deliver_at)
  - compute_delivery_result       — Compute status update dict after a delivery attempt
  - filter_due_notifications      — Filter deferred notifications to those due now
  - filter_expired_notifications  — Filter deferred notifications to those expired

See: openspec/specs/time-aware-delivery/spec.md
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any


def is_in_quiet_hours(
    *,
    current_time: time,
    quiet_start: time,
    quiet_end: time,
) -> bool:
    """Determine if current_time falls within quiet hours.

    Handles overnight ranges (start > end, e.g., 22:00–07:00) and
    same-day ranges (start < end, e.g., 08:00–12:00).

    Args:
        current_time: The time to check.
        quiet_start: Quiet hours start time.
        quiet_end: Quiet hours end time.

    Returns:
        True if current_time is within quiet hours.
    """
    if quiet_start <= quiet_end:
        # Same-day range (e.g., 08:00–12:00)
        return quiet_start <= current_time <= quiet_end
    else:
        # Overnight range (e.g., 22:00–07:00)
        return current_time >= quiet_start or current_time <= quiet_end


def resolve_effective_quiet_hours(
    *,
    channel: str,
    prefs: dict[str, Any],
) -> dict[str, str]:
    """Resolve the effective quiet hours for a specific channel.

    Returns the channel-specific override if one exists in prefs.override_channels,
    otherwise returns the default quiet hours from prefs.

    Returns:
        Dict with 'quiet_hours_start' and 'quiet_hours_end' keys (string time values).
    """
    override_channels = prefs.get("override_channels") or {}
    channel_override = override_channels.get(channel)

    if channel_override:
        return {
            "quiet_hours_start": channel_override["quiet_hours_start"],
            "quiet_hours_end": channel_override["quiet_hours_end"],
        }

    return {
        "quiet_hours_start": prefs["quiet_hours_start"],
        "quiet_hours_end": prefs["quiet_hours_end"],
    }


def _parse_time(value: str | time) -> time:
    """Parse a time from 'HH:MM' string or return as-is if already a time object."""
    if isinstance(value, time):
        return value
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def should_defer_notification(
    *,
    priority: str,
    current_time: time,
    prefs: dict[str, Any] | None,
    channel: str = "",
) -> bool:
    """Determine if a notification should be deferred based on quiet hours.

    High-priority notifications always deliver immediately (never deferred).
    Medium/low-priority notifications are deferred if current_time falls within quiet hours.
    If prefs is None (no delivery preferences configured), notifications always deliver.

    Args:
        priority: "high", "medium", or "low".
        current_time: The current local time.
        prefs: Delivery preferences dict, or None if not configured.
        channel: The notification channel (for override lookup).

    Returns:
        True if the notification should be deferred.
    """
    if prefs is None:
        return False

    if priority == "high":
        return False  # High priority always bypasses quiet hours

    # Resolve effective quiet hours (channel override or default)
    effective = resolve_effective_quiet_hours(channel=channel, prefs=prefs)
    quiet_start = _parse_time(effective["quiet_hours_start"])
    quiet_end = _parse_time(effective["quiet_hours_end"])

    return is_in_quiet_hours(
        current_time=current_time,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
    )


def compute_deliver_at(
    *,
    prefs: dict[str, Any],
    now: datetime,
) -> datetime:
    """Compute the next delivery time based on batch_delivery_time in prefs.

    Returns the next occurrence of batch_delivery_time on or after now.

    Args:
        prefs: Delivery preferences dict with 'batch_delivery_time' and 'timezone'.
        now: Current UTC datetime.

    Returns:
        UTC datetime of the next batch delivery time.
    """
    batch_time = _parse_time(prefs.get("batch_delivery_time", "07:00"))

    # Build today's delivery datetime in UTC (simplified: assume UTC for now)
    # Full timezone-aware implementation will use pytz/zoneinfo when integrated
    today_delivery = datetime(
        now.year,
        now.month,
        now.day,
        batch_time.hour,
        batch_time.minute,
        tzinfo=now.tzinfo,
    )

    if today_delivery > now:
        return today_delivery

    # Batch time has already passed today — schedule for tomorrow
    return today_delivery + timedelta(days=1)


def is_notification_due(notif: dict[str, Any], *, now: datetime) -> bool:
    """Check if a deferred notification is due for delivery.

    A notification is due when:
      - status == 'pending'
      - deliver_at <= now

    Returns:
        True if the notification should be delivered now.
    """
    return notif.get("status") == "pending" and notif["deliver_at"] <= now


def is_notification_expired(notif: dict[str, Any], *, now: datetime) -> bool:
    """Check if a deferred notification has expired.

    A notification expires when it has been pending for more than 24 hours
    past its deliver_at time.

    Returns:
        True if the notification should be marked expired (not delivered).
    """
    if notif.get("status") != "pending":
        return False

    deliver_at = notif["deliver_at"]
    return (now - deliver_at).total_seconds() > 24 * 3600


def compute_delivery_result(
    *,
    delivery_succeeded: bool,
    delivered_at: datetime | None = None,
) -> dict[str, Any]:
    """Compute the status update dict after a delivery attempt.

    Args:
        delivery_succeeded: True if delivery succeeded, False if it failed.
        delivered_at: Timestamp of successful delivery (required when succeeded=True).

    Returns:
        Dict with 'status' and optionally 'delivered_at'.
    """
    if delivery_succeeded:
        return {"status": "delivered", "delivered_at": delivered_at}
    else:
        return {"status": "pending"}  # keep pending for next-tick retry


def filter_due_notifications(
    notifications: list[dict[str, Any]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    """Filter deferred notifications to those due for delivery now.

    Returns only notifications with status='pending' and deliver_at <= now.
    Expired notifications are excluded (they need separate handling).
    """
    result = []
    for n in notifications:
        if n.get("status") == "pending" and n["deliver_at"] <= now:
            # Exclude already-expired ones
            if not is_notification_expired(n, now=now):
                result.append(n)
    return result


def filter_expired_notifications(
    notifications: list[dict[str, Any]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    """Filter deferred notifications to those that have expired (> 24h past deliver_at).

    Returns only pending notifications past the 24-hour expiry window.
    """
    return [n for n in notifications if is_notification_expired(n, now=now)]
