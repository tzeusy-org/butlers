"""The location retention job: reconcile grants, then state lag and failures (bu-s11n0s.7 S4b-4c).

One run, uncalled until the owner-enable step (the dark guard pins that):

1. **Start.** ``location_retention_runs`` is set to ``running`` and committed before any work.
   A run still ``running`` inside :data:`RUN_LEASE` makes this one skip; an older one never
   finished, so it is unknown, never green: taking it over counts one failure.
2. **Work.** :func:`reconcile_ready_grants`, then :func:`read_retention_lag` and its counts
   reconciled into the owner condition ledger under :data:`LAG_SOURCE`.
3. **Finish.** The outcome is written in its own transaction: a failure keeps the exception's
   class (never its message, which could quote evidence) and extends the streak.
4. **Failures.** The streak, the grants that failed reconciliation and the ready grants still
   without a receipt after :data:`STALLED_AFTER` (a connector that keeps refusing a grant
   leaves it pending forever) are reconciled under :data:`FAILED_SOURCE`.

Every observation is count-only: a category, a count, an age in hours. The ledger is state;
nothing here proposes a candidate or sends a message. A lag snapshot is reconciled only after a
complete read, so a failed run, or a failed read, never closes a lag condition by omission.

Lag counts each raw fix whose retention clock is past the policy cutoff by the first reason
that applies:

* ``grant_failed``: granted to a grant that failed reconciliation; the connector will not
  delete it under that grant.
* ``awaiting_deletion``: granted to a ready grant the connector has not yet settled.
* ``awaiting_holders``: granted to a grant not yet ready.
* ``held_open``: an adapter holds it in an open carry.
* ``uncovered``: some covered adapter has no exact record for it at that adapter's last
  recorded revision and the row's current content (a malformed or refused fix, an
  ``existing_only`` miss, a pre-coverage row, a changed revision). Such a row never becomes
  eligible on its own, so this count is how it stays visible.
* ``awaiting_grant``: every adapter is finished with it; preparation has not run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import asyncpg

from butlers.chronicler.location_retention_reconcile import reconcile_ready_grants
from butlers.chronicler.projection_coverage import COVERED_ADAPTERS, raw_row_digest_sql
from butlers.core.condition_ledger import Observation, compute_fingerprint
from butlers.core.owner_conditions import reconcile_snapshot

logger = logging.getLogger(__name__)

LAG_SOURCE = "chronicler:location-retention-projection-lag"
FAILED_SOURCE = "chronicler:location-retention-purge-failed"
IDENTITY_VERSION = 1
#: A run still ``running`` after this long never finished.
RUN_LEASE = timedelta(minutes=30)
#: A ready grant still unsettled after this long is stalled.
STALLED_AFTER = timedelta(hours=24)
LAG_CATEGORIES = (
    "grant_failed",
    "awaiting_deletion",
    "awaiting_holders",
    "held_open",
    "uncovered",
    "awaiting_grant",
)
_GRACE_S = 6 * 3600.0

_START = """
INSERT INTO location_retention_runs (id, last_started_at, last_outcome)
VALUES (1, clock_timestamp(), 'running')
ON CONFLICT (id) DO UPDATE SET
    last_started_at = EXCLUDED.last_started_at,
    last_outcome = 'running',
    consecutive_failures = location_retention_runs.consecutive_failures
        + (location_retention_runs.last_outcome = 'running')::int,
    last_failure_at = CASE WHEN location_retention_runs.last_outcome = 'running'
        THEN EXCLUDED.last_started_at ELSE location_retention_runs.last_failure_at END,
    last_error_class = CASE WHEN location_retention_runs.last_outcome = 'running'
        THEN 'Unfinished' ELSE location_retention_runs.last_error_class END
WHERE location_retention_runs.last_outcome <> 'running'
   OR location_retention_runs.last_started_at <= clock_timestamp() - $1::interval
RETURNING last_started_at
"""
_SUCCEEDED = """
UPDATE location_retention_runs
SET last_outcome = 'succeeded', last_finished_at = clock_timestamp(),
    last_success_at = clock_timestamp(), consecutive_failures = 0
WHERE id = 1 AND last_started_at = $1
RETURNING consecutive_failures, last_error_class
"""
_FAILED = """
UPDATE location_retention_runs
SET last_outcome = 'failed', last_finished_at = clock_timestamp(),
    last_failure_at = clock_timestamp(), last_error_class = $2,
    consecutive_failures = consecutive_failures + 1
WHERE id = 1 AND last_started_at = $1
RETURNING consecutive_failures, last_error_class
"""
_FAILED_GRANTS = (
    "SELECT count(DISTINCT grant_id) FROM location_retention_grant_states WHERE state = 'failed'"
)
_STALLED_GRANTS = """
SELECT count(*) AS count,
       floor(extract(epoch FROM now() - min(r.recorded_at)) / 3600)::bigint AS oldest_age_hours
FROM location_retention_grant_states AS r
WHERE r.state = 'ready'
  AND r.recorded_at <= now() - $1::interval
  AND NOT EXISTS (
      SELECT 1 FROM location_retention_grant_states AS t
      WHERE t.grant_id = r.grant_id AND t.state IN ('reconciled', 'failed')
  )
"""
_POLICY_DAYS = "SELECT days FROM location_retention_policy WHERE id = 1"


def _lag_sql() -> str:
    return f"""
WITH overdue AS (
    SELECT p.id, p.retention_at, {raw_row_digest_sql("p")} AS content_digest
    FROM connectors.owntracks_points AS p
    WHERE p.retention_at < now() - make_interval(days => $1)
),
classified AS (
    SELECT o.retention_at,
        CASE
            WHEN st.failed THEN 'grant_failed'
            WHEN st.ready THEN 'awaiting_deletion'
            WHEN gi.grant_id IS NOT NULL THEN 'awaiting_holders'
            WHEN EXISTS (
                SELECT 1 FROM location_projection_open_members AS m
                WHERE m.raw_source_id = o.id
            ) THEN 'held_open'
            WHEN EXISTS (
                SELECT 1 FROM unnest($2::text[]) AS a(adapter_name)
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM location_projection_attempts AS r
                    JOIN location_projection_coverage AS c
                      ON c.adapter_name = r.adapter_name
                     AND c.config_revision = r.config_revision
                    WHERE r.adapter_name = a.adapter_name
                      AND c.raw_source_id = o.id
                      AND c.content_digest = o.content_digest
                )
            ) THEN 'uncovered'
            ELSE 'awaiting_grant'
        END AS category
    FROM overdue AS o
    LEFT JOIN location_retention_grant_items AS gi ON gi.raw_source_id = o.id
    LEFT JOIN LATERAL (
        SELECT bool_or(s.state = 'failed') AS failed,
               bool_or(s.state = 'ready') AS ready
        FROM location_retention_grant_states AS s
        WHERE s.grant_id = gi.grant_id
    ) AS st ON true
)
SELECT category, count(*) AS count,
       floor(extract(epoch FROM now() - min(retention_at)) / 3600)::bigint AS oldest_age_hours
FROM classified
GROUP BY category
"""


@dataclass(frozen=True)
class RetentionLag:
    """Overdue raw fixes by why each is still there; categories with none are absent."""

    counts: dict[str, int]
    oldest_age_hours: dict[str, int]


async def read_retention_lag(conn: Any) -> RetentionLag:
    """One consistent read of the overdue raw fixes (a repeatable-read snapshot).

    Raises on any read failure, including a missing policy row: a lag read is complete or it
    is not used.
    """
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        days = await conn.fetchval(_POLICY_DAYS)
        if days is None:
            raise LookupError("location retention policy row is missing")
        rows = await conn.fetch(_lag_sql(), days, list(COVERED_ADAPTERS))
    return RetentionLag(
        counts={row["category"]: row["count"] for row in rows},
        oldest_age_hours={row["category"]: row["oldest_age_hours"] for row in rows},
    )


def _fingerprint(source: str, category: str) -> str:
    return compute_fingerprint(source, IDENTITY_VERSION, {"category": category})


def _observation(source: str, category: str, summary: str, **counts: Any) -> Observation:
    return Observation(
        fingerprint=_fingerprint(source, category),
        identity_version=IDENTITY_VERSION,
        summary=summary,
        metadata={"category": category, **counts},
    )


async def _state_lag(pool: asyncpg.Pool, lag: RetentionLag) -> None:
    observations = [
        _observation(
            LAG_SOURCE,
            category,
            f"{lag.counts[category]} raw location fix(es) past retention: {category}",
            count=lag.counts[category],
            oldest_age_hours=lag.oldest_age_hours[category],
        )
        for category in LAG_CATEGORIES
        if lag.counts.get(category)
    ]
    await reconcile_snapshot(
        pool,
        source=LAG_SOURCE,
        observations=observations,
        snapshot_complete=True,
        initial_grace_seconds=_GRACE_S,
    )


async def _state_failures(pool: asyncpg.Pool, streak: int, error_class: str | None) -> None:
    try:
        failed_grants = await pool.fetchval(_FAILED_GRANTS)
        stalled = await pool.fetchrow(_STALLED_GRANTS, STALLED_AFTER)
    except Exception:
        logger.warning("Location retention: failed-grant read failed; no state change")
        return
    observations = []
    if streak:
        observations.append(
            _observation(
                FAILED_SOURCE,
                "run_failing",
                f"location retention run failed {streak} time(s) in a row",
                consecutive_failures=streak,
                error_class=error_class,
            )
        )
    if failed_grants:
        observations.append(
            _observation(
                FAILED_SOURCE,
                "failed_grants",
                f"{failed_grants} retention grant(s) failed reconciliation",
                count=failed_grants,
            )
        )
    if stalled["count"]:
        observations.append(
            _observation(
                FAILED_SOURCE,
                "stalled_grants",
                f"{stalled['count']} ready retention grant(s) unsettled for over a day",
                count=stalled["count"],
                oldest_age_hours=stalled["oldest_age_hours"],
            )
        )
    await reconcile_snapshot(
        pool,
        source=FAILED_SOURCE,
        observations=observations,
        snapshot_complete=True,
        initial_grace_seconds=_GRACE_S,
    )


async def run_location_retention_job(
    db_pool: asyncpg.Pool, job_args: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Run once. Takes no ``job_args``.

    Returns ``{"status": "skipped", "reason": "running"}`` when another run holds the lease,
    ``{"status": "ok", "reconciled": n, "lag": {...}}`` on success, and
    ``{"status": "failed", "error_class": ..., "consecutive_failures": n}`` otherwise.
    """
    if job_args:
        raise ValueError(f"location retention job takes no job_args; got: {sorted(job_args)}")
    started = await db_pool.fetchval(_START, RUN_LEASE)
    if started is None:
        return {"status": "skipped", "reason": "running"}

    error_class: str | None = None
    try:
        async with db_pool.acquire() as conn:
            settled = await reconcile_ready_grants(conn)
            lag = await read_retention_lag(conn)
        await _state_lag(db_pool, lag)
    except Exception as exc:
        error_class = type(exc).__name__
        logger.warning("Location retention run failed: %s", error_class)

    if error_class is None:
        run = await db_pool.fetchrow(_SUCCEEDED, started)
    else:
        run = await db_pool.fetchrow(_FAILED, started, error_class)
    if run is None:  # another run took the lease over; it owns the outcome
        return {"status": "skipped", "reason": "lease_lost"}
    await _state_failures(db_pool, run["consecutive_failures"], run["last_error_class"])

    if error_class is not None:
        return {
            "status": "failed",
            "error_class": error_class,
            "consecutive_failures": run["consecutive_failures"],
        }
    return {"status": "ok", "reconciled": len(settled), "lag": lag.counts}
