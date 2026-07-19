"""Approvals-policy quiet-hours enforcement for owner-page notification dispatch.

Provides pure functions and a thin async DB accessor for the
``public.approvals_policy`` singleton that controls when owner-page
notifications may be sent.

Schema (core_095 migration):
    public.approvals_policy (
        id               INT  PRIMARY KEY DEFAULT 1,
        quiet_start_hour INT,           -- 0-23; NULL = disabled
        quiet_end_hour   INT,           -- 0-23; NULL = disabled
        timezone         TEXT NOT NULL DEFAULT 'UTC',
    )

Semantics:
- If ``quiet_start_hour`` or ``quiet_end_hour`` is NULL (or the row is
  missing), quiet-hours suppression is **disabled** and the function
  returns False (always send).
- Overnight ranges are handled: quiet_start_hour=22, quiet_end_hour=7
  means [22:00, 07:00) is quiet; 07:00–21:59 is active.
- Same-day ranges: quiet_start_hour=8, quiet_end_hour=12 means
  [08:00, 12:00) is quiet. Equal endpoints describe an empty window.
- Timezone-aware callers use :func:`is_policy_quiet_now` and
  :func:`policy_quiet_hours_deliver_at`, which own conversion through the
  stored IANA timezone. Invalid/incomplete persisted data fails open.

Design:
- This module deliberately avoids importing the delivery_preferences
  temporal system. They are sibling subsystems: delivery_preferences
  governs per-butler notification batching/deferral; approvals_policy
  governs global owner-page suppression.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure logic helpers
# ---------------------------------------------------------------------------


def is_in_policy_quiet_hours(
    *,
    current_hour: int,
    quiet_start: int,
    quiet_end: int,
) -> bool:
    """Return True if *current_hour* falls within the quiet window.

    Handles end-exclusive overnight windows (quiet_start > quiet_end) and
    same-day windows. Equal endpoints represent an empty window.

    Args:
        current_hour: 0-23 integer representing the current hour in the
            configured timezone.
        quiet_start: Quiet period start hour (0-23).
        quiet_end: Quiet period end hour (0-23).

    Returns:
        True if the current hour is within the quiet window.
    """
    if quiet_start == quiet_end:
        return False
    if quiet_start < quiet_end:
        # Same-day [08, 12): hours 8,9,10,11 are quiet.
        return quiet_start <= current_hour < quiet_end
    # Overnight [22, 07): hours 22,23,0,1,...,6 are quiet.
    return current_hour >= quiet_start or current_hour < quiet_end


def _policy_hours(policy: dict[str, Any] | None) -> tuple[int, int] | None:
    """Return a complete, valid policy hour pair or fail open."""
    if policy is None:
        return None

    quiet_start = policy.get("quiet_start_hour")
    quiet_end = policy.get("quiet_end_hour")
    if quiet_start is None or quiet_end is None:
        return None

    try:
        start = int(quiet_start)
        end = int(quiet_end)
    except (TypeError, ValueError):
        logger.warning("approvals_policy has non-integer quiet hours; suppression disabled")
        return None
    if not 0 <= start <= 23 or not 0 <= end <= 23:
        logger.warning("approvals_policy has out-of-range quiet hours; suppression disabled")
        return None
    return start, end


def _policy_local_now(
    policy: dict[str, Any] | None,
    *,
    now: datetime,
) -> tuple[datetime, int, int] | None:
    """Resolve valid policy data and the corresponding local time once.

    A stored policy is durable user data, so an invalid timezone is not silently
    reinterpreted as UTC. Every caller fails open through this shared boundary.
    """
    hours = _policy_hours(policy)
    if hours is None:
        return None
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("owner attention policy helpers require a timezone-aware now value")

    assert policy is not None  # narrowed by _policy_hours above
    tz_name = policy.get("timezone")
    if not isinstance(tz_name, str) or not tz_name.strip():
        logger.warning("approvals_policy has no timezone; suppression disabled")
        return None
    try:
        timezone = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        logger.warning("approvals_policy has invalid timezone %r; suppression disabled", tz_name)
        return None

    return now.astimezone(timezone), *hours


def should_suppress_by_policy(
    policy: dict[str, Any] | None,
    *,
    current_hour: int,
) -> bool:
    """Decide whether to suppress an owner-page notification.

    Args:
        policy: Dict with keys ``quiet_start_hour``, ``quiet_end_hour``,
            ``timezone`` (as returned by ``get_approvals_policy_quiet_hours``).
            Pass ``None`` to disable suppression (always send).
        current_hour: Current hour (0-23) in the policy's configured timezone.
            The caller is responsible for the timezone conversion.

    Returns:
        True if the notification should be suppressed (dropped silently).
    """
    if policy is None:
        return False

    hours = _policy_hours(policy)
    if hours is None:
        return False

    return is_in_policy_quiet_hours(
        current_hour=current_hour,
        quiet_start=hours[0],
        quiet_end=hours[1],
    )


def is_policy_quiet_now(
    policy: dict[str, Any] | None,
    *,
    now: datetime,
) -> bool:
    """Return whether an aware instant is inside the Owner Attention Policy.

    This is the sole timezone-aware boolean reader for direct owner-attention
    callers. It deliberately returns ``False`` for unavailable/incomplete/
    invalid persisted policy data so an attention decision fails open.
    """
    state = _policy_local_now(policy, now=now)
    if state is None:
        return False
    local_now, quiet_start, quiet_end = state
    return is_in_policy_quiet_hours(
        current_hour=local_now.hour,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
    )


def policy_quiet_hours_deliver_at(
    policy: dict[str, Any] | None,
    *,
    now: datetime,
) -> datetime | None:
    """Return the first post-quiet delivery instant for a policy-held message.

    The caller owns persistence and any domain-specific expiry semantics.  This
    helper only turns the end-exclusive configured quiet window into a UTC delivery
    instant, so routine owner-default notifications and approval requests keep
    the same timing boundary.

    A window ending at 07:00 first permits delivery at 07:00 local time, so the
    anchor is the exact configured end.
    """
    state = _policy_local_now(policy, now=now)
    if state is None:
        return None
    local_now, quiet_start, quiet_end = state
    if not is_in_policy_quiet_hours(
        current_hour=local_now.hour,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
    ):
        return None

    candidate = local_now.replace(hour=quiet_end, minute=0, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def approval_push_deliver_at(
    policy: dict[str, Any] | None,
    *,
    now: datetime,
) -> datetime | None:
    """Return the first post-quiet delivery instant for an approval push.

    Approval requests remain control-plane notifications whose persistence and
    pending-action expiry are owned by their caller.  This compatibility wrapper
    deliberately delegates only the shared end-exclusive quiet-hours calculation.
    """
    return policy_quiet_hours_deliver_at(policy, now=now)


# ---------------------------------------------------------------------------
# Async DB accessor
# ---------------------------------------------------------------------------


async def get_approvals_policy_quiet_hours(pool: Any) -> dict[str, Any] | None:
    """Fetch the ``public.approvals_policy`` singleton quiet-hours fields.

    Returns a dict with keys ``quiet_start_hour``, ``quiet_end_hour``,
    ``timezone`` on success, or ``None`` if the row is missing or the
    table does not exist yet (schema predates core_095).

    The default row has ``quiet_start_hour = NULL`` and
    ``quiet_end_hour = NULL``, which ``should_suppress_by_policy`` treats
    as "always send".
    """
    try:
        row = await pool.fetchrow(
            "SELECT quiet_start_hour, quiet_end_hour, timezone "
            "FROM public.approvals_policy WHERE id = 1"
        )
    except Exception:
        # Table absent (older schema) or DB error — treat as no policy
        logger.debug(
            "approvals_policy unavailable; quiet-hours suppression disabled",
            exc_info=True,
        )
        return None

    if row is None:
        return None

    return {
        "quiet_start_hour": row["quiet_start_hour"],
        "quiet_end_hour": row["quiet_end_hour"],
        "timezone": row["timezone"],
    }
