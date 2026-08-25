"""Close out wakes that ended without a receipt (bu-6jv4m.8).

A delivery ends at "a wake task was scheduled on the subscriber". Whether
the subscriber then acted is a domain question that only the waking session
can answer, and it answers it by calling ``report_event_reaction``. Some
sessions will not: they crash, time out, run out of context, or simply exit
without saying anything. Those wakes would otherwise sit at ``scheduled``
forever, and a ledger with no end state is a ledger nobody can audit.

This sweep closes them -- and it may write exactly one verdict:
``unreported``. It never reads a completed task, a clean exit, or an absent
error as ``acted``. The whole reason this bead exists is that "the task
finished" was being read as "the collaboration worked", so inferring success
from a task's completion here would reintroduce the defect one layer up.
``running`` is the only other status it writes, and that is an observation
of fact (``scheduled_tasks.last_run_at`` is set), not a judgement.

Scope
-----
Subscriber-local. It reads the caller's *own* ``scheduled_tasks`` (RFC 0006:
one schema per butler) joined against the shared
``public.domain_event_deliveries``/``public.domain_event_reactions`` tables,
never a sibling schema's. It is called best-effort from the per-butler
scheduler loop, so each subscriber closes out its own wakes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from butlers.core.domain_event_reactions import (
    TERMINAL_REACTION_STATUSES,
    DomainEventReactionError,
    record_reaction,
)

logger = logging.getLogger(__name__)

#: How long after a wake fired we still consider its session in flight. The
#: wake spawns an LLM session; 30 minutes is comfortably past the runtime's
#: own session timeouts, so a wake still unclosed after it is genuinely
#: silent rather than merely slow.
REACTION_GRACE = timedelta(minutes=30)

#: A delivered wake whose task cannot be found at all (deleted, or never
#: reconciled into this schema) is only closed once this much time has
#: passed since delivery -- long enough that a task still being created
#: cannot be mistaken for one that vanished.
ORPHAN_WAKE_AFTER = timedelta(hours=2)

_SWEEP_BATCH_LIMIT = 200

_CANDIDATE_SQL = """
    SELECT
        d.event_id,
        d.task_name,
        d.delivered_at,
        t.id           AS task_id,
        t.last_run_at,
        t.until_at,
        (
            SELECT r.status
            FROM public.domain_event_reactions r
            WHERE r.event_id = d.event_id
              AND r.subscriber_butler = d.subscriber_butler
            ORDER BY r.recorded_at DESC, r.id DESC
            LIMIT 1
        ) AS latest_status
    FROM public.domain_event_deliveries d
    LEFT JOIN scheduled_tasks t ON t.name = d.task_name
    WHERE d.subscriber_butler = $1
      AND d.status = 'delivered'
      AND d.task_name IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM public.domain_event_reactions r
          WHERE r.event_id = d.event_id
            AND r.subscriber_butler = d.subscriber_butler
            AND r.status = ANY($2::text[])
      )
    ORDER BY d.delivered_at ASC
    LIMIT $3
"""


def decide_reaction_verdict(row: dict[str, Any], *, now: datetime) -> str | None:
    """Return the status this wake has earned, or ``None`` to leave it alone.

    Only ``"running"`` and ``"unreported"`` are reachable. ``"acted"`` is
    deliberately not: nothing observable from outside the session
    distinguishes a session that acted from one that exited without doing
    anything, and guessing is the failure this ledger exists to end.
    """
    last_run_at = row.get("last_run_at")
    if last_run_at is not None:
        if now - last_run_at < REACTION_GRACE:
            return None if row.get("latest_status") == "running" else "running"
        return "unreported"

    if row.get("task_id") is None:
        delivered_at = row.get("delivered_at")
        if delivered_at is None or now - delivered_at < ORPHAN_WAKE_AFTER:
            return None
        return "unreported"

    until_at = row.get("until_at")
    if until_at is not None and now - until_at >= REACTION_GRACE:
        # The one-shot window lapsed and the task never fired.
        return "unreported"
    return None


def _verdict_note(status: str, row: dict[str, Any]) -> str:
    if status == "running":
        return "The wake task fired; its session has not filed a receipt yet."
    if row.get("task_id") is None:
        return (
            "No local wake task could be found for this delivery, and the orphan horizon "
            "passed. No receipt was ever filed."
        )
    if row.get("last_run_at") is None:
        return "The one-shot wake window lapsed without the task ever running."
    return (
        "The wake task ran and its session ended without filing a receipt. This records "
        "silence, not an outcome."
    )


async def reconcile_reaction_lifecycle(
    pool: Any,
    *,
    subscriber_butler: str,
    now: datetime | None = None,
    limit: int = _SWEEP_BATCH_LIMIT,
) -> dict[str, int]:
    """Close out this butler's delivered wakes that never reported an outcome.

    Returns a per-run summary ``{"examined": n, "running": n, "unreported":
    n}``. A :class:`DomainEventReactionError` from the terminal-slot guard
    means a session filed its own receipt in the meantime; the session wins
    and the sweep moves on.
    """
    now = now or datetime.now(UTC)
    rows = await pool.fetch(
        _CANDIDATE_SQL,
        subscriber_butler,
        sorted(TERMINAL_REACTION_STATUSES),
        limit,
    )

    summary = {"examined": len(rows), "running": 0, "unreported": 0}
    for row in rows:
        candidate = dict(row)
        verdict = decide_reaction_verdict(candidate, now=now)
        if verdict is None:
            continue
        try:
            await record_reaction(
                pool,
                event_id=uuid.UUID(str(candidate["event_id"])),
                subscriber_butler=subscriber_butler,
                status=verdict,
                session_id=None,
                task_name=candidate.get("task_name"),
                note=_verdict_note(verdict, candidate),
            )
        except DomainEventReactionError:
            logger.debug(
                "Reaction sweep: wake for event %s on %s was closed by its own session first",
                candidate["event_id"],
                subscriber_butler,
            )
            continue
        summary[verdict] += 1
    return summary
