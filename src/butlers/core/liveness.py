"""Shared last_seen_at + liveness_ttl_seconds staleness formula.

Two call sites independently need the identical "is this agent's heartbeat
stale" verdict:

- ``roster/switchboard/tools/registry/registry.py::_derive_eligibility_state``
  — governs butler routing eligibility (``active``/``stale``/``quarantined``).
- ``src/butlers/core/qa/sources/infra_state.py``'s heartbeat-stale QA check
  — flags a butler QA should investigate.

Before this module existed, ``infra_state.py`` re-implemented the formula by
hand rather than importing ``registry.py`` directly, because
``src/butlers/core`` cannot import ``roster/`` code (roster is loaded
dynamically at runtime; the reverse dependency would invert that layering).
This module is the single canonical implementation, placed under
``src/butlers/core`` (mirroring the existing shared-helper convention, e.g.
``core/mcp_urls.py``) so both sides can import it directly: core code needs
no roster import, and roster already imports freely from ``butlers.core``
(e.g. ``registry.py`` imports ``core.mcp_urls``).

Callers that need more than the staleness boolean (e.g. ``registry.py``
additionally maps quarantine state onto an eligibility enum) layer that
policy on top of :func:`is_liveness_stale` rather than this module growing
enum/vocabulary concerns that not every caller needs.
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
