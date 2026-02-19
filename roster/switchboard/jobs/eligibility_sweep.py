"""Eligibility sweep job — periodic liveness-based state transitions.

Evaluates butler liveness and applies TTL-based eligibility state transitions.
This job is scheduled to run every 5 minutes.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

logger = logging.getLogger(__name__)


async def run_eligibility_sweep_job(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Scheduled job to run the eligibility sweep.

    Evaluates butler liveness and applies TTL-based eligibility state transitions:
    - active  → stale       when now() - last_seen_at > liveness_ttl_seconds
    - stale   → quarantined when now() - last_seen_at > 2 * liveness_ttl_seconds
    - active within TTL     → unchanged
    - last_seen_at is NULL  → skipped (never reported, newly registered)

    All transitions are logged to butler_registry_eligibility_log.

    Args:
        db_pool: Database connection pool (Switchboard's DB)

    Returns:
        Dictionary with summary:
        - evaluated: int    — number of butlers inspected (NULL last_seen_at excluded)
        - skipped: int      — butlers skipped (last_seen_at is NULL)
        - transitioned: int — number of butlers whose state changed
        - transitions: list[dict] — details for each transition
    """
    result = await run_eligibility_sweep(db_pool)

    # Log summary
    logger.info(
        "Eligibility sweep job completed: evaluated=%d, skipped=%d, transitioned=%d",
        result["evaluated"],
        result["skipped"],
        result["transitioned"],
    )

    # Log detailed transitions
    for transition in result["transitions"]:
        logger.info(
            "  %s: %s → %s (elapsed %ds, ttl %ds)",
            transition["butler"],
            transition["previous_state"],
            transition["new_state"],
            transition["elapsed_seconds"],
            transition["liveness_ttl_seconds"],
        )

    return result
