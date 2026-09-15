"""Insight broker catch-up scheduling (bu-kqnum.3 slice 3).

``delivery_cycle`` in ``broker.py`` fully suppresses a routine (non-urgent)
cycle for quiet hours or an active context-bus signal, and today the only way
a held cycle resumes is the next regularly scheduled cron tick — the daily
digest's windowed cron (``roster/switchboard/butler.toml``'s
``insight-delivery-cycle``, 06:15-11:45 UTC only) or, outside that window,
tomorrow. This module reconciles a deterministic one-shot ``scheduled_tasks``
row that re-invokes the delivery cycle at the suppression's own computed end
instant, so a hold outside (or lasting past) the windowed cron still resolves
close to when it actually ends rather than waiting on the next tick.

Boundary
--------
This module is the ONLY place that reconciles the insight broker's catch-up
task. It reads/writes the Switchboard's own ``scheduled_tasks`` table (RFC
0006) via the caller's pool. Mirrors the deterministic-name reconciliation
convention in ``butlers.core.domain_event_wake``/``delegation_wake``, adapted
for a single always-current task (there is at most one active suppression at
a time) that gets *rescheduled* in place rather than reconciled by identity:
a later suppressed cycle computing a materially different end instant moves
the same task forward instead of leaving a stale target behind.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.core.scheduler import schedule_create, schedule_update

logger = logging.getLogger(__name__)

TASK_NAME = "insight-catchup"
_JOB_NAME = "insight_delivery_cycle"
_SOURCE = "insight_catchup"
# Buffer past the exact target so the scheduler's own dispatch-loop tick
# granularity can still catch it; the task's *next* computed occurrence is a
# year later (cron day/month pin), which always clears this buffer and
# auto-disables the task after its one intended firing — mirrors
# ``domain_event_wake``'s identical one-shot-via-cron+until_at pattern.
_UNTIL_AT_BUFFER = timedelta(minutes=1)
# Two targets within this tolerance are treated as "already scheduled" so a
# suppressed cycle re-run on the next windowed-cron tick (every 30 minutes)
# does not churn the row for an unchanged suppression end.
_RESCHEDULE_TOLERANCE = timedelta(seconds=60)


async def _find_task(pool: asyncpg.Pool, name: str) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        "SELECT id, enabled, job_args FROM scheduled_tasks WHERE name = $1",
        name,
    )
    return dict(row) if row is not None else None


def _stored_deliver_at(job_args: Any) -> datetime | None:
    if not isinstance(job_args, dict):
        return None
    raw = job_args.get("deliver_at")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _one_shot_cron(deliver_at: datetime) -> str:
    at = deliver_at.astimezone(UTC)
    return f"{at.minute} {at.hour} {at.day} {at.month} *"


async def reconcile_catchup_task(
    pool: asyncpg.Pool,
    *,
    deliver_at: datetime,
    reason: str,
) -> dict[str, Any]:
    """Ensure a one-shot ``insight_delivery_cycle`` catch-up task is
    scheduled for ``deliver_at`` — the instant the current suppression
    (quiet hours or a context-bus signal's max-hold) computed as its end.

    Idempotent and best-effort: callers treat every return as informational
    and must not let a failure here abort the suppressed-cycle return it is
    attached to (see ``broker._schedule_insight_catchup``).
    """
    job_args = {"deliver_at": deliver_at.isoformat(), "reason": reason, "source": _SOURCE}
    cron = _one_shot_cron(deliver_at)
    until_at = deliver_at + _UNTIL_AT_BUFFER

    existing = await _find_task(pool, TASK_NAME)
    if existing is None:
        try:
            task_id = await schedule_create(
                pool,
                TASK_NAME,
                cron,
                dispatch_mode="job",
                job_name=_JOB_NAME,
                job_args=job_args,
                until_at=until_at,
            )
        except ValueError:
            # Raced with a concurrent suppressed cycle creating the same
            # deterministic-named task. Reconcile against whatever now
            # exists instead of erroring the suppressed-skip path.
            existing = await _find_task(pool, TASK_NAME)
            if existing is None:
                logger.warning(
                    "insight-catchup: create for %r raced but re-read found nothing", TASK_NAME
                )
                return {"status": "error", "state": "race_unresolved"}
        else:
            return {"status": "ok", "state": "task_created", "task_id": str(task_id)}

    existing_deliver_at = _stored_deliver_at(existing.get("job_args"))
    already_current = (
        existing["enabled"]
        and existing_deliver_at is not None
        and abs(existing_deliver_at - deliver_at) < _RESCHEDULE_TOLERANCE
    )
    if already_current:
        return {"status": "ok", "state": "already_scheduled", "task_id": str(existing["id"])}

    await schedule_update(
        pool,
        existing["id"],
        cron=cron,
        dispatch_mode="job",
        job_name=_JOB_NAME,
        job_args=job_args,
        until_at=until_at,
        enabled=True,
    )
    return {"status": "ok", "state": "task_rescheduled", "task_id": str(existing["id"])}
