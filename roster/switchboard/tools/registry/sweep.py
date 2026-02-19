"""Eligibility sweep — periodic liveness-based state transitions.

Scans all butler_registry rows and applies TTL-based eligibility transitions:

- active  → stale       when now() - last_seen_at > liveness_ttl_seconds
- stale   → quarantined when now() - last_seen_at > 2 * liveness_ttl_seconds
- active within TTL     → unchanged
- last_seen_at is NULL  → skipped (never reported, newly registered)

All transitions are logged to butler_registry_eligibility_log.

Issue: butlers-976.4
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.tools.switchboard.registry.registry import (
    ELIGIBILITY_ACTIVE,
    ELIGIBILITY_QUARANTINED,
    ELIGIBILITY_STALE,
    _audit_eligibility_transition,
    _normalize_positive_int,
)

logger = logging.getLogger(__name__)

DEFAULT_LIVENESS_TTL_SECONDS = 300


def _sweep_transition_reason(previous_state: str, new_state: str) -> str:
    """Return a reason string for a sweep-initiated state transition."""
    if new_state == ELIGIBILITY_STALE:
        return "liveness_ttl_expired"
    if new_state == ELIGIBILITY_QUARANTINED:
        return "liveness_ttl_2x_expired"
    # Should not occur during a sweep, but included for completeness.
    return "sweep_transition"


async def _apply_stale_transition(
    pool: asyncpg.Pool,
    *,
    name: str,
    last_seen_at: datetime,
    now: datetime,
    ttl_seconds: int,
) -> None:
    """Persist an active→stale transition and write the audit log entry."""
    await pool.execute(
        """
        UPDATE butler_registry
        SET
            eligibility_state = $1,
            eligibility_updated_at = $2
        WHERE name = $3
        """,
        ELIGIBILITY_STALE,
        now,
        name,
    )
    await _audit_eligibility_transition(
        pool,
        name=name,
        previous_state=ELIGIBILITY_ACTIVE,
        new_state=ELIGIBILITY_STALE,
        reason=_sweep_transition_reason(ELIGIBILITY_ACTIVE, ELIGIBILITY_STALE),
        previous_last_seen_at=last_seen_at,
        new_last_seen_at=last_seen_at,
        observed_at=now,
    )
    logger.info(
        "Eligibility sweep: %s active → stale (elapsed %ds, ttl %ds)",
        name,
        int((now - last_seen_at).total_seconds()),
        ttl_seconds,
    )


async def _apply_quarantine_transition(
    pool: asyncpg.Pool,
    *,
    name: str,
    previous_state: str,
    last_seen_at: datetime,
    now: datetime,
    ttl_seconds: int,
) -> str:
    """Persist a →quarantined transition and write the audit log entry."""
    quarantine_reason = (
        f"Liveness TTL exceeded by 2x: last_seen_at={last_seen_at.isoformat()}, "
        f"liveness_ttl_seconds={ttl_seconds}"
    )
    await pool.execute(
        """
        UPDATE butler_registry
        SET
            eligibility_state = $1,
            eligibility_updated_at = $2,
            quarantined_at = $3,
            quarantine_reason = $4
        WHERE name = $5
        """,
        ELIGIBILITY_QUARANTINED,
        now,
        now,
        quarantine_reason,
        name,
    )
    await _audit_eligibility_transition(
        pool,
        name=name,
        previous_state=previous_state,
        new_state=ELIGIBILITY_QUARANTINED,
        reason=_sweep_transition_reason(previous_state, ELIGIBILITY_QUARANTINED),
        previous_last_seen_at=last_seen_at,
        new_last_seen_at=last_seen_at,
        observed_at=now,
    )
    logger.info(
        "Eligibility sweep: %s %s → quarantined (elapsed %ds, ttl %ds)",
        name,
        previous_state,
        int((now - last_seen_at).total_seconds()),
        ttl_seconds,
    )
    return quarantine_reason


async def run_eligibility_sweep(
    pool: asyncpg.Pool,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate butler liveness and apply TTL-based eligibility state transitions.

    Transitions applied:
    - active → stale:       now() - last_seen_at > liveness_ttl_seconds
    - stale → quarantined:  now() - last_seen_at > 2 * liveness_ttl_seconds
    - active within TTL:    unchanged
    - last_seen_at is NULL: skipped (newly registered, never reported)

    All state changes are persisted to butler_registry and logged to
    butler_registry_eligibility_log.

    Parameters
    ----------
    pool:
        Database connection pool (Switchboard's DB).
    now:
        Timestamp override for testing. Defaults to UTC now.

    Returns
    -------
    dict
        Summary with keys:
        - evaluated: int    — number of butlers inspected (NULL last_seen_at excluded)
        - skipped: int      — butlers skipped (last_seen_at is NULL)
        - transitioned: int — number of butlers whose state changed
        - transitions: list[dict] — details for each transition
    """
    if now is None:
        now = datetime.now(UTC)

    rows = await pool.fetch(
        """
        SELECT
            name,
            eligibility_state,
            liveness_ttl_seconds,
            last_seen_at,
            quarantined_at,
            quarantine_reason
        FROM butler_registry
        ORDER BY name
        """
    )

    evaluated = 0
    skipped = 0
    transitioned = 0
    transitions: list[dict[str, Any]] = []

    for row in rows:
        row_dict = dict(row)
        last_seen_at = row_dict.get("last_seen_at")

        # Skip butlers that have never reported (last_seen_at is NULL).
        if last_seen_at is None:
            skipped += 1
            continue

        evaluated += 1
        name = row_dict["name"]
        current_state = row_dict.get("eligibility_state") or ELIGIBILITY_ACTIVE

        # Quarantined butlers are not touched by the sweep — quarantine removal
        # is a manual operator action.
        if current_state == ELIGIBILITY_QUARANTINED:
            continue

        ttl_seconds = _normalize_positive_int(
            row_dict.get("liveness_ttl_seconds"),
            default=DEFAULT_LIVENESS_TTL_SECONDS,
        )
        elapsed = now - last_seen_at

        # Determine the target state based on TTL thresholds.
        if elapsed <= timedelta(seconds=ttl_seconds):
            # Within TTL — remain active, no transition needed.
            continue

        if elapsed <= timedelta(seconds=2 * ttl_seconds):
            # Past TTL but not past 2x → stale.
            if current_state == ELIGIBILITY_ACTIVE:
                await _apply_stale_transition(
                    pool,
                    name=name,
                    last_seen_at=last_seen_at,
                    now=now,
                    ttl_seconds=ttl_seconds,
                )
                transitioned += 1
                transitions.append(
                    {
                        "butler": name,
                        "previous_state": ELIGIBILITY_ACTIVE,
                        "new_state": ELIGIBILITY_STALE,
                        "last_seen_at": last_seen_at.isoformat(),
                        "elapsed_seconds": int(elapsed.total_seconds()),
                        "liveness_ttl_seconds": ttl_seconds,
                    }
                )
        else:
            # Past 2x TTL → quarantined.
            quarantine_reason = await _apply_quarantine_transition(
                pool,
                name=name,
                previous_state=current_state,
                last_seen_at=last_seen_at,
                now=now,
                ttl_seconds=ttl_seconds,
            )
            transitioned += 1
            transitions.append(
                {
                    "butler": name,
                    "previous_state": current_state,
                    "new_state": ELIGIBILITY_QUARANTINED,
                    "last_seen_at": last_seen_at.isoformat(),
                    "elapsed_seconds": int(elapsed.total_seconds()),
                    "liveness_ttl_seconds": ttl_seconds,
                    "quarantine_reason": quarantine_reason,
                }
            )

    logger.info(
        "Eligibility sweep complete: evaluated=%d skipped=%d transitioned=%d",
        evaluated,
        skipped,
        transitioned,
    )

    return {
        "evaluated": evaluated,
        "skipped": skipped,
        "transitioned": transitioned,
        "transitions": transitions,
    }
