"""Value-free health evidence for dashboard API supervised loops only.

This records work, never schedules it. PostgreSQL serializes each job's claim
and transition. An old process may neither reclaim a newer boot nor update it.
Recorder I/O is bounded and best-effort; unavailable storage is explicit to readers.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)
JobClass = Literal["periodic", "continuous"]
REASONS = frozenset(
    {
        "started",
        "tick_ok",
        "tick_failed",
        "partial_failure",
        "connected",
        "reconnecting",
        "loop_crashed",
        "loop_returned",
        "disabled",
        "stopped",
    }
)
STATES = frozenset({"starting", "ok", "degraded", "error", "disabled", "unknown"})
RECORD_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class JobSpec:
    name: str
    job_class: JobClass
    interval_s: float

    def __post_init__(self) -> None:
        if self.job_class not in ("periodic", "continuous"):
            raise ValueError("invalid background job class")
        if not math.isfinite(self.interval_s) or self.interval_s <= 0:
            raise ValueError("invalid background job interval")
        if not self.name or len(self.name) > 64 or not self.name.replace("_", "").isalnum():
            raise ValueError("invalid background job name")

    @property
    def grace_s(self) -> float:
        return max(5.0, self.interval_s / 4)


# Inventory of supervise_lifespan_loop consumers, not ordinary scheduled jobs.
# Actual configured intervals replace these defaults at supervisor registration;
# the shadow observer supplies its operator-derived interval after each cycle.
JOB_SPECS = {
    spec.name: spec
    for spec in (
        JobSpec("fleet_shadow_observer", "periodic", 150),
        JobSpec("secrets_lifecycle", "periodic", 1800),
        JobSpec("model_verify", "periodic", 3600),
        JobSpec("fleet_events_bridge", "continuous", 5),
        JobSpec("settings_console_delta", "periodic", 5),
        JobSpec("secrets_staleness", "periodic", 1800),
        JobSpec("migration_drift", "periodic", 3600),
        JobSpec("calendar_sync_deadman", "periodic", 1800),
        JobSpec("external_deadman", "periodic", 600),
    )
}


def present_job(
    spec: JobSpec, row: Mapping[str, Any] | None, process_id: UUID, now: datetime
) -> dict[str, Any]:
    """Class-aware freshness. No process heartbeat can manufacture success."""
    result: dict[str, Any] = {
        "name": spec.name,
        "job_class": spec.job_class,
        "state": "unknown",
        "reason": None,
        "last_success_at": None,
        "last_progress_at": None,
        "error_count": 0,
        "consecutive_failures": 0,
        "transitions": [],
    }
    if row is None or row["process_id"] != process_id:
        return result
    state = row["state"] if row["state"] in STATES else "unknown"
    stamp = row["last_success_at"] if spec.job_class == "periodic" else row["last_progress_at"]
    if state == "ok":
        if stamp is None:
            state = "unknown"
        elif (now - stamp).total_seconds() > row["interval_s"] + row["grace_s"]:
            state = "stale"
    elif (
        state == "starting"
        and (now - row["updated_at"]).total_seconds() > row["interval_s"] + row["grace_s"]
    ):
        state = "stale"
    result.update(
        state=state,
        reason=row["reason"] if row["reason"] in REASONS else None,
        last_success_at=row["last_success_at"],
        last_progress_at=row["last_progress_at"],
        error_count=row["error_count"],
        consecutive_failures=row["consecutive_failures"],
    )
    return result


class JobHealthStore:
    """One boot identity shared by the supervisor and the System read surface."""

    def __init__(self, pool: Callable[[], Any]) -> None:
        self.pool = pool
        self.process_id = uuid4()
        self.started_at = datetime.now(UTC)
        self.specs = dict(JOB_SPECS)
        self.unavailable_jobs: set[str] = set()

    def recorder(self, name: str, job_class: JobClass, interval_s: float) -> JobRecorder:
        if name not in JOB_SPECS or JOB_SPECS[name].job_class != job_class:
            raise ValueError("unregistered dashboard background job")
        spec = JobSpec(name, job_class, interval_s)
        self.specs[name] = spec
        return JobRecorder(self, spec)

    async def write(
        self,
        spec: JobSpec,
        state: str,
        reason: str,
        *,
        success: bool = False,
        progress: bool = False,
        failed: bool = False,
    ) -> None:
        # Never accept exception text, a summary, a URL or arbitrary metadata.
        if state not in STATES or reason not in REASONS:
            raise ValueError("invalid background health evidence")
        try:
            async with asyncio.timeout(RECORD_TIMEOUT_S):
                async with self.pool().acquire() as conn, conn.transaction():
                    # Boot ordering is immutable across retries/restarts of a loop.
                    # A delayed old boot cannot reclaim the current row.
                    await conn.execute(
                        """INSERT INTO public.dashboard_job_health
                           (job_name,job_class,process_id,process_started_at,interval_s,grace_s)
                           VALUES ($1,$2,$3,$4,$5,$6)
                           ON CONFLICT (job_name) DO UPDATE SET
                             job_class=EXCLUDED.job_class, process_id=EXCLUDED.process_id,
                             process_started_at=EXCLUDED.process_started_at,
                             interval_s=EXCLUDED.interval_s, grace_s=EXCLUDED.grace_s,
                             state='starting',reason='started',last_success_at=NULL,
                             last_progress_at=NULL,error_count=0,consecutive_failures=0,
                             updated_at=clock_timestamp()
                           WHERE (dashboard_job_health.process_started_at,
                                  dashboard_job_health.process_id::text) <
                                 (EXCLUDED.process_started_at,EXCLUDED.process_id::text)""",
                        spec.name,
                        spec.job_class,
                        self.process_id,
                        self.started_at,
                        spec.interval_s,
                        spec.grace_s,
                    )
                    old = await conn.fetchrow(
                        "SELECT * FROM public.dashboard_job_health WHERE job_name=$1 FOR UPDATE",
                        spec.name,
                    )
                    if old["process_id"] != self.process_id:
                        # Superseded is not a successful write or permission to retry a claim.
                        return
                    changed = old["state"] != state or old["reason"] != reason
                    await conn.execute(
                        """UPDATE public.dashboard_job_health SET state=$3,reason=$4,
                           last_success_at=CASE WHEN $5 THEN clock_timestamp()
                                                ELSE last_success_at END,
                           last_progress_at=CASE WHEN $6 THEN clock_timestamp()
                                                 ELSE last_progress_at END,
                           error_count=error_count + CASE WHEN $7 THEN 1 ELSE 0 END,
                           consecutive_failures=CASE WHEN $7 THEN consecutive_failures+1
                                                     WHEN $5 THEN 0 ELSE consecutive_failures END,
                           interval_s=$8,grace_s=$9,updated_at=clock_timestamp()
                           WHERE job_name=$1 AND process_id=$2""",
                        spec.name,
                        self.process_id,
                        state,
                        reason,
                        success,
                        progress,
                        failed,
                        spec.interval_s,
                        spec.grace_s,
                    )
                    if changed or old["reason"] == "started":
                        await conn.execute(
                            """INSERT INTO public.dashboard_job_transitions
                               (job_name,process_id,state,reason) VALUES ($1,$2,$3,$4)""",
                            spec.name,
                            self.process_id,
                            state,
                            reason,
                        )
                    # The smaller of 30 days and 100 transitions per job.
                    # This runs even on an unchanged healthy tick.
                    await conn.execute(
                        """DELETE FROM public.dashboard_job_transitions WHERE
                           occurred_at < clock_timestamp()-interval '30 days' OR id IN
                           (SELECT id FROM (SELECT id,row_number() OVER
                             (PARTITION BY job_name ORDER BY occurred_at DESC,id DESC) AS position
                             FROM public.dashboard_job_transitions) ranked WHERE position>100)"""
                    )
            self.unavailable_jobs.discard(spec.name)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.unavailable_jobs.add(spec.name)
            logger.warning("Background job health storage unavailable: %s", spec.name)

    async def read(self) -> dict[str, Any]:
        unavailable = {"jobs_available": False, "jobs": []}
        try:
            async with asyncio.timeout(RECORD_TIMEOUT_S):
                async with (
                    self.pool().acquire() as conn,
                    conn.transaction(
                        isolation="repeatable_read",
                        readonly=True,
                    ),
                ):
                    rows = await conn.fetch(
                        "SELECT * FROM public.dashboard_job_health WHERE job_name=ANY($1::text[])",
                        list(self.specs),
                    )
                    transitions = await conn.fetch(
                        """SELECT * FROM (SELECT job_name,state,reason,occurred_at,
                           row_number() OVER (PARTITION BY job_name
                             ORDER BY occurred_at DESC,id DESC) AS position
                           FROM public.dashboard_job_transitions
                           WHERE occurred_at >= clock_timestamp()-interval '30 days'
                           AND job_name=ANY($1::text[])) t
                           WHERE position<=100 ORDER BY job_name,occurred_at DESC""",
                        list(self.specs),
                    )
            if self.unavailable_jobs:
                return unavailable
        except asyncio.CancelledError:
            raise
        except Exception:
            return unavailable
        by_name = {row["job_name"]: row for row in rows}
        now = datetime.now(UTC)
        jobs = [
            present_job(spec, by_name.get(name), self.process_id, now)
            for name, spec in sorted(self.specs.items())
        ]
        for job in jobs:
            job["transitions"] = [
                {key: row[key] for key in ("state", "reason", "occurred_at")}
                for row in transitions
                if row["job_name"] == job["name"]
                and row["state"] in STATES
                and row["reason"] in REASONS
            ]
        return {"jobs_available": True, "jobs": jobs}


class JobRecorder:
    def __init__(self, store: JobHealthStore, spec: JobSpec) -> None:
        self.store, self.spec = store, spec
        self.reconnect_failures = 0
        self.tick_incomplete = False

    async def starting(self) -> None:
        await self.store.write(self.spec, "starting", "started")

    async def tick_ok(self) -> None:
        self.tick_incomplete = False
        self.reconnect_failures = 0
        await self.store.write(self.spec, "ok", "tick_ok", success=True, progress=True)

    async def tick_failed(self, code: str = "tick_failed") -> None:
        self.tick_incomplete = False
        safe = code if code in REASONS else "tick_failed"
        await self.store.write(self.spec, "error", safe, failed=True)

    async def connected(self) -> None:
        self.reconnect_failures = 0
        await self.store.write(self.spec, "ok", "connected", success=True, progress=True)

    async def reconnecting(self) -> None:
        self.reconnect_failures += 1
        state = "error" if self.reconnect_failures >= 3 else "degraded"
        await self.store.write(self.spec, state, "reconnecting", failed=True)

    async def disabled(self) -> None:
        await self.store.write(self.spec, "disabled", "disabled")

    async def stopped(self) -> None:
        await self.store.write(self.spec, "unknown", "stopped")


_CURRENT: ContextVar[JobRecorder | None] = ContextVar("dashboard_job_recorder", default=None)


def mark_tick_incomplete() -> None:
    """Retain caught work failure for the current supervised tick only.

    Checks keep their public result and fault-isolation contracts. Direct
    callers have no recorder, so this adds neither persistence nor policy.
    """
    recorder = _CURRENT.get()
    if recorder is not None:
        recorder.tick_incomplete = True


async def record_tick(*, failed: bool = False, interval_s: float | None = None) -> None:
    """Report only inside supervised API work; direct callers remain unchanged."""
    recorder = _CURRENT.get()
    if recorder is None:
        return
    if interval_s is not None:
        recorder.spec = JobSpec(recorder.spec.name, recorder.spec.job_class, interval_s)
        recorder.store.specs[recorder.spec.name] = recorder.spec
    if failed or recorder.tick_incomplete:
        await recorder.tick_failed("partial_failure")
    else:
        await recorder.tick_ok()


async def record_connection(*, connected: bool) -> None:
    recorder = _CURRENT.get()
    if recorder is not None:
        if connected:
            await recorder.connected()
        else:
            await recorder.reconnecting()
