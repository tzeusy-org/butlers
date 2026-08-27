"""Shared heartbeat liveness policies.

Connector surfaces and QA discovery share :func:`derive_liveness` for the
canonical ``online``/``stale``/``offline`` verdict. Butler routing eligibility
and QA discovery share :func:`is_liveness_stale` for the configurable
``last_seen_at + liveness_ttl_seconds`` verdict.

These policies live under ``src/butlers/core`` so API and roster callers depend
on a stable lower layer. Core code must not import dashboard DTOs or dynamically
loaded roster modules merely to reuse heartbeat policy.

Callers that need richer states, such as quarantine or connector-specific
presentation, layer those concerns on top of these functions rather than this
module growing vocabulary that not every caller needs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

#: Fallback TTL when a stored ``liveness_ttl_seconds`` value is missing or
#: not a positive integer.
DEFAULT_LIVENESS_TTL_SECONDS = 300

#: Tolerance for a ``last_seen_at`` timestamp reported in the future (clock
#: skew between an agent host and the DB server, or a bad writer). Beyond
#: this the timestamp is untrustworthy rather than confidently recent, so it
#: must not keep an unbounded TTL window "fresh" forever.
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)


def derive_liveness(last_heartbeat_at: datetime | None) -> str:
    """Derive liveness status from last heartbeat timestamp.

    Liveness thresholds (from docs/connectors/heartbeat.md):
    - online: heartbeat within last 5 minutes
    - stale: heartbeat between 5-15 minutes ago
    - offline: no heartbeat for 15+ minutes or never seen

    A future-dated heartbeat (more than 5 minutes ahead of server clock) is
    treated as offline rather than online to avoid false-healthy reports under
    clock skew.

    Args:
        last_heartbeat_at: Timestamp of the last received heartbeat, or None if never seen

    Returns:
        One of: "online", "stale", "offline"
    """
    if last_heartbeat_at is None:
        return "offline"

    import datetime as dt

    now = dt.datetime.now(dt.UTC)
    age = (now - last_heartbeat_at).total_seconds()

    if age < -300:  # more than 5 minutes in the future — clock skew
        return "offline"
    elif age <= 300:  # 5 minutes
        return "online"
    elif age <= 900:  # 15 minutes
        return "stale"
    else:
        return "offline"


def normalize_liveness_ttl_seconds(
    value: object,
    *,
    default: int = DEFAULT_LIVENESS_TTL_SECONDS,
) -> int:
    """Coerce a stored ``liveness_ttl_seconds`` value to a positive int.

    Falls back to *default* for anything that is not a positive integer:
    missing (``None``), non-numeric, zero, or negative.
    """
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def is_liveness_stale(
    last_seen_at: datetime | None,
    *,
    ttl_seconds: object,
    now: datetime | None = None,
    clock_skew_tolerance: timedelta = CLOCK_SKEW_TOLERANCE,
) -> bool:
    """Return ``True`` when *last_seen_at* is stale against *ttl_seconds*.

    Canonical staleness sub-computation shared by ``registry.py``'s
    ``_derive_eligibility_state`` and ``InfraStateSource``'s heartbeat-stale
    check:

    - No signal at all (``last_seen_at is None``) is stale.
    - A timestamp further in the future than *clock_skew_tolerance* is
      untrustworthy and treated as stale, rather than letting an unbounded
      TTL window keep it "fresh" forever.
    - Otherwise fresh iff ``last_seen_at + ttl_seconds >= now``.

    *ttl_seconds* is normalized via :func:`normalize_liveness_ttl_seconds`
    before use, so callers may pass a raw DB value (``int``, ``None``, or an
    otherwise malformed value) directly.
    """
    now = now or datetime.now(UTC)
    if last_seen_at is None:
        return True
    if last_seen_at > now + clock_skew_tolerance:
        return True
    ttl = normalize_liveness_ttl_seconds(ttl_seconds)
    return (last_seen_at + timedelta(seconds=ttl)) < now
