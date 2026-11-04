"""Pure cron, timezone, stagger and quiet-window arithmetic for the scheduler."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from butlers.core.approvals_policy import is_policy_quiet_now

logger = logging.getLogger("butlers.core.scheduler")


_DEFAULT_MAX_STAGGER_SECONDS = 15 * 60


# Fallback timezone for cron evaluation when no per-schedule or owner timezone
# resolves.  Hour-pinned crons are interpreted in this zone.
_DEFAULT_SCHEDULE_TIMEZONE = "UTC"


# A week of firings is examined for the always-suppressed lint; a cron that fires more often
# than this inside that week is treated as irregular and never warned about.
_QUIET_LINT_HORIZON = timedelta(days=7)


_QUIET_LINT_MAX_FIRINGS = 2000


def schedule_always_in_quiet_window(
    cron: str,
    *,
    timezone: str,
    quiet_policy: dict[str, Any] | None,
    now: datetime,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
) -> bool:
    """True when every firing of *cron* in the next week lands inside the owner quiet window.

    A firing counts as inside only when both its nominal instant and the latest instant the
    schedule stagger can push it to are quiet. Uncertain cases return False so they never
    warn: no usable policy, an empty window, an invalid cron, or a cron that fires more than
    ``_QUIET_LINT_MAX_FIRINGS`` times in the horizon.
    """
    if quiet_policy is None or not croniter.is_valid(cron):
        return False
    anchor = now.astimezone(_coerce_schedule_zone(timezone))
    iterator = croniter(cron, anchor)
    horizon = now + _QUIET_LINT_HORIZON
    firings = 0
    while True:
        firing = iterator.get_next(datetime).astimezone(UTC)
        if firing > horizon:
            return firings > 0
        firings += 1
        if firings > _QUIET_LINT_MAX_FIRINGS:
            return False
        latest = firing + timedelta(seconds=max_stagger_seconds)
        if not (
            is_policy_quiet_now(quiet_policy, now=firing)
            and is_policy_quiet_now(quiet_policy, now=latest)
        ):
            return False


def _cron_interval_seconds(cron: str, *, now: datetime | None = None) -> int:
    """Return the interval between the next two occurrences for ``cron``."""
    anchor = now or datetime.now(UTC)
    it = croniter(cron, anchor)
    first = it.get_next(datetime).replace(tzinfo=UTC)
    second = it.get_next(datetime).replace(tzinfo=UTC)
    return max(1, int((second - first).total_seconds()))


def _stagger_offset_seconds(
    cron: str,
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    now: datetime | None = None,
) -> int:
    """Compute a deterministic offset that never exceeds the cron cadence."""
    if not stagger_key or max_stagger_seconds <= 0:
        return 0

    cadence_seconds = _cron_interval_seconds(cron, now=now)
    max_safe_offset = min(max_stagger_seconds, cadence_seconds - 1)
    if max_safe_offset <= 0:
        return 0

    digest = hashlib.sha256(stagger_key.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return bucket % (max_safe_offset + 1)


def _effective_schedule_timezone(stored: str | None, default_timezone: str) -> str:
    """Resolve the timezone a schedule's cron should be evaluated in.

    The ``scheduled_tasks.timezone`` column is ``NOT NULL DEFAULT 'UTC'`` (added
    in core_112), so TOML/unset schedules carry the literal ``'UTC'``.  That
    sentinel — like ``None``/empty — means "follow the owner's configured
    general timezone" (*default_timezone*).  Any other stored value (e.g.
    ``'America/New_York'``) is an explicit per-schedule override and wins.

    Consequence: a schedule cannot be pinned to literal UTC while the owner's
    general timezone is non-UTC.  This is acceptable for a single-owner system
    where the owner's timezone is the universal default; pin a non-UTC zone
    explicitly when a different local time is required.
    """
    candidate = (stored or "").strip()
    if not candidate or candidate.upper() == "UTC":
        return default_timezone
    return candidate


def _coerce_schedule_zone(timezone: str | None) -> ZoneInfo:
    """Resolve a schedule timezone string to a ``ZoneInfo``, failing open to UTC.

    ``None``/empty resolves to UTC.  An unknown IANA name is logged and treated
    as UTC so a typo'd timezone never wedges dispatch.
    """
    candidate = (timezone or "").strip()
    if not candidate or candidate.upper() == "UTC":
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown schedule timezone %r; interpreting cron in UTC", timezone)
        return ZoneInfo("UTC")


def _next_run(
    cron: str,
    *,
    timezone: str | None = None,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    now: datetime | None = None,
) -> datetime:
    """Compute the next run time for a cron expression, returned in UTC.

    The cron fields are interpreted in *timezone* (an IANA name).  When
    *timezone* is ``None``/empty/``"UTC"`` the cron is evaluated in UTC, which
    preserves historical behaviour.  Anchoring croniter to a timezone-aware
    datetime makes croniter return occurrences in that zone (DST-correct); the
    result is converted to UTC for storage in ``scheduled_tasks.next_run_at``.
    """
    zone = _coerce_schedule_zone(timezone)
    anchor = (now or datetime.now(UTC)).astimezone(zone)
    next_run = croniter(cron, anchor).get_next(datetime).astimezone(UTC)
    offset_seconds = _stagger_offset_seconds(
        cron,
        stagger_key=stagger_key,
        max_stagger_seconds=max_stagger_seconds,
        now=anchor,
    )
    if offset_seconds:
        return next_run + timedelta(seconds=offset_seconds)
    return next_run
