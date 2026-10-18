"""Premise re-validation for proactive insight candidates (bu-q7vx1q.5).

``check_premise`` answers one question about a candidate's stored premise:
does the fact the insight asserts still hold? The answer is tri-state and
never guessed:

- ``True``  -- the fact still holds; deliver as normal.
- ``False`` -- the fact stopped being true; the broker withdraws the candidate
  instead of sending it.
- ``None``  -- unknown (probe error, unregistered probe, no evidence row). The
  broker delivers the candidate stamped "as of <proposed_at>" and never claims
  it was rechecked.

Every probe is deterministic SQL against a narrow SECURITY DEFINER lookup; there
is no LLM in this path.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import asyncpg

from butlers.core.insight_premise import PREMISE_KIND_OWNER_CONDITION, PREMISE_KIND_PROBE

logger = logging.getLogger(__name__)

ProbeFn = Callable[[asyncpg.Pool, Mapping[str, Any]], Awaitable[bool | None]]


async def _finance_bill_still_pending(pool: asyncpg.Pool, args: Mapping[str, Any]) -> bool | None:
    """True while the bill is unpaid, False once paid, None when it cannot be found."""
    bill_id = args.get("bill_id")
    if not isinstance(bill_id, str):
        return None
    row = await pool.fetchrow("SELECT status FROM public.resolve_finance_bill_status($1)", bill_id)
    if row is None:
        return None
    return row["status"] in ("pending", "overdue")


async def _health_medication_refill_still_due(
    pool: asyncpg.Pool, args: Mapping[str, Any]
) -> bool | None:
    """True while the medication is active and its supply is within the warning window.

    False once it is stopped or deleted, or a refill pushed depletion past the
    window; None when it cannot be found or has no supply quantity to estimate from.
    """
    subject = args.get("subject")
    doses_per_day = args.get("doses_per_day")
    warning_days = args.get("warning_days")
    if not isinstance(subject, str) or isinstance(doses_per_day, bool):
        return None
    if not isinstance(doses_per_day, int | float) or doses_per_day <= 0:
        return None
    if isinstance(warning_days, bool) or not isinstance(warning_days, int | float):
        return None
    row = await pool.fetchrow(
        "SELECT is_active, days_remaining FROM public.resolve_health_medication_refill($1, $2)",
        subject,
        doses_per_day,
    )
    if row is None:
        return None
    if not row["is_active"]:
        return False
    if row["days_remaining"] is None:
        return None
    return float(row["days_remaining"]) <= warning_days


#: ``(butler, probe)`` -> probe. A butler adopts premises by registering here.
PREMISE_PROBES: dict[tuple[str, str], ProbeFn] = {
    ("finance", "bill_still_pending"): _finance_bill_still_pending,
    ("health", "medication_refill_still_due"): _health_medication_refill_still_due,
}


async def _owner_condition_active(pool: asyncpg.Pool, premise: Mapping[str, Any]) -> bool | None:
    rows = await pool.fetch(
        """
        SELECT state FROM public.owner_conditions
        WHERE source = $1 AND fingerprint = $2
        ORDER BY episode DESC
        LIMIT 1
        """,
        premise["source"],
        premise["fingerprint"],
    )
    if not rows:
        return None
    return rows[0]["state"] in ("open", "aging")


async def check_premise(pool: asyncpg.Pool, premise: Mapping[str, Any] | None) -> bool | None:
    """Return whether ``premise`` still holds; ``True`` for a null premise.

    Never raises: any probe failure is ``None`` (unknown), so a lookup hiccup
    degrades to an honest "as of" delivery rather than a silent withdrawal.
    """
    if premise is None:
        return True
    try:
        kind = premise.get("kind")
        if kind == PREMISE_KIND_OWNER_CONDITION:
            return await _owner_condition_active(pool, premise)
        if kind == PREMISE_KIND_PROBE:
            probe = PREMISE_PROBES.get((premise.get("butler"), premise.get("probe")))
            if probe is None:
                return None
            return await probe(pool, premise.get("args") or {})
    except Exception:
        logger.warning("insight premise check failed: %r", premise, exc_info=True)
    return None
