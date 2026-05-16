"""Approvals-policy quiet-hours enforcement for owner-page notification dispatch.

Provides pure functions and a thin async DB accessor for the
``public.approvals_policy`` singleton that controls when owner-page
notifications may be sent.

Schema (core_095 migration):
    public.approvals_policy (
        id               INT  PRIMARY KEY DEFAULT 1,
        quiet_start_hour INT,           -- 0-23 inclusive; NULL = disabled
        quiet_end_hour   INT,           -- 0-23 inclusive; NULL = disabled
        timezone         TEXT NOT NULL DEFAULT 'UTC',
    )

Semantics:
- If ``quiet_start_hour`` or ``quiet_end_hour`` is NULL (or the row is
  missing), quiet-hours suppression is **disabled** and the function
  returns False (always send).
- Overnight ranges are handled: quiet_start_hour=22, quiet_end_hour=7
  means 22:00–07:00 is quiet; 07:01–21:59 is active.
- Same-day ranges: quiet_start_hour=8, quiet_end_hour=12 means
  08:00–12:00 is quiet.
- The caller supplies the *current hour* in the policy's configured
  timezone (conversion is the caller's responsibility).

Design:
- This module deliberately avoids importing the delivery_preferences
  temporal system. They are sibling subsystems: delivery_preferences
  governs per-butler notification batching/deferral; approvals_policy
  governs global owner-page suppression.
"""

from __future__ import annotations

import logging
from typing import Any

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

    Handles overnight windows (quiet_start > quiet_end) and same-day
    windows (quiet_start <= quiet_end).

    Args:
        current_hour: 0-23 integer representing the current hour in the
            configured timezone.
        quiet_start: Quiet period start hour (0-23).
        quiet_end: Quiet period end hour (0-23).

    Returns:
        True if the current hour is within the quiet window.
    """
    if quiet_start <= quiet_end:
        # Same-day range, e.g. 08-12: hour 8,9,10,11,12 are quiet
        return quiet_start <= current_hour <= quiet_end
    else:
        # Overnight range, e.g. 22-07: 22,23,0,1,...,7 are quiet
        return current_hour >= quiet_start or current_hour <= quiet_end


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

    quiet_start = policy.get("quiet_start_hour")
    quiet_end = policy.get("quiet_end_hour")

    if quiet_start is None or quiet_end is None:
        # Quiet hours not configured — always send
        return False

    return is_in_policy_quiet_hours(
        current_hour=current_hour,
        quiet_start=int(quiet_start),
        quiet_end=int(quiet_end),
    )


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
        "timezone": row["timezone"] or "UTC",
    }
