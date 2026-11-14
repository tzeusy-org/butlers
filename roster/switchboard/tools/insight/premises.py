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
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import asyncpg

from butlers.core.insight_premise import PREMISE_KIND_OWNER_CONDITION, PREMISE_KIND_PROBE
from butlers.jobs.home_prepared_status import STATUSES

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
    window; None when the definer answers unknown (not found, or no supply quantity).
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
    due = await pool.fetchval(
        "SELECT public.resolve_health_medication_refill("
        "$1, $2::float8::numeric, $3::float8::numeric)",
        subject,
        doses_per_day,
        warning_days,
    )
    return None if due is None else bool(due)


async def _home_departure_still_open(pool: asyncpg.Pool, args: Mapping[str, Any]) -> bool | None:
    """True while the departure card is open and unchanged; False once closed or changed.

    None when the edge id is malformed or the definer answers unknown (no card, or no recheck
    within three minutes).  bu-jv1moa.8: the timed lane holds an unknown departure card.
    """
    try:
        edge_event_id = uuid.UUID(str(args.get("edge_event_id")))
    except ValueError:
        return None
    still_open = await pool.fetchval("SELECT public.resolve_home_departure($1)", edge_event_id)
    return None if still_open is None else bool(still_open)


async def _home_fix_status_unchanged(pool: asyncpg.Pool, args: Mapping[str, Any]) -> bool | None:
    """True while a parked Home action still has the status a card showed; False once changed.

    None when the ids or the status are malformed, or the definer answers ``unknown`` (no row, or
    a state nothing classifies): the timed lane holds an unknown card.  bu-jv1moa.114.
    """
    try:
        action_id = uuid.UUID(str(args.get("action_id")))
    except ValueError:
        return None
    shown = args.get("status")
    if not isinstance(shown, str) or shown not in STATUSES or shown == "unknown":
        return None
    current = await pool.fetchval("SELECT public.resolve_home_action_status($1)", action_id)
    if current is None or current == "unknown":
        return None
    return current == shown


#: ``(butler, probe)`` -> probe. A butler adopts premises by registering here.
PREMISE_PROBES: dict[tuple[str, str], ProbeFn] = {
    ("finance", "bill_still_pending"): _finance_bill_still_pending,
    ("health", "medication_refill_still_due"): _health_medication_refill_still_due,
    ("home", "departure_still_open"): _home_departure_still_open,
    ("home", "fix_status_unchanged"): _home_fix_status_unchanged,
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


async def check_premise(
    pool: asyncpg.Pool, premise: Mapping[str, Any] | None, *, finance_warning_reader=None
) -> bool | None:
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
            if (premise.get("butler"), premise.get("probe")) == (
                "finance",
                "subscription_warning_still_relevant",
            ):
                if finance_warning_reader is None:
                    return None
                result = await finance_warning_reader(dict(premise.get("args") or {}))
                return result if type(result) is bool else None
            probe = PREMISE_PROBES.get((premise.get("butler"), premise.get("probe")))
            if probe is None:
                return None
            return await probe(pool, premise.get("args") or {})
    except Exception:
        logger.warning("insight premise check failed: %r", premise, exc_info=True)
    return None
