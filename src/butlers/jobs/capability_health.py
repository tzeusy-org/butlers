"""Periodic capability-health collection and publication (RFC 0038 S6; zero LLM).

Each tick gathers observations, reduces one verdict per declared dependency and publishes it
through ``perception_write`` as ``perception_health_writer``. That role is not a member of any
other role (perception.md), so the job opens its own small pool from ``BUTLERS_HEALTH_WRITER_DSN``.
Without that variable the job does not start, and readers see ``unknown`` (fail closed); it never
falls back to the dashboard login.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.capability_health.collectors import (
    CREDENTIAL_DEPENDENCIES,
    credential_observations,
    probe_conditions,
)
from butlers.capability_health.model import Observation, reduce
from butlers.capability_health.publish import HealthPublisher, PublishState, should_publish
from butlers.core.capability_dependencies import DEPENDENCY_IDS
from butlers.core.job_health import mark_tick_incomplete, record_tick
from butlers.perception import PerceptionStore

logger = logging.getLogger(__name__)

WRITER_DSN_ENV = "BUTLERS_HEALTH_WRITER_DSN"
NOT_CONFIGURED_MESSAGE = (
    "capability health collector not started: BUTLERS_HEALTH_WRITER_DSN is not set "
    "(dependency health reads as unknown)"
)
DEFAULT_INTERVAL_S = 60.0
_logged_not_configured = False

Gather = Callable[[datetime], Awaitable[list[Observation]]]


def writer_dsn() -> str | None:
    """The dedicated writer DSN, or ``None`` (logged once). No other variable is consulted."""
    global _logged_not_configured
    dsn = (os.environ.get(WRITER_DSN_ENV) or "").strip()
    if dsn:
        return dsn
    if not _logged_not_configured:
        _logged_not_configured = True
        logger.info(NOT_CONFIGURED_MESSAGE)
    return None


async def run_tick(
    gather: Gather,
    publisher: Any,
    state: dict[str, PublishState],
    now: datetime,
) -> None:
    """Reduce every declared dependency and publish those that are due.

    The tick is reported failed when collection failed or any publish was refused.
    """
    failed = False
    try:
        observations = await gather(now)
    except Exception:
        logger.warning("capability health: collection failed; publishing unmeasurable")
        observations = []
        failed = True
    for dependency in sorted(DEPENDENCY_IDS):
        reduction = reduce(dependency, [o for o in observations if o.dependency == dependency], now)
        if not should_publish(state.get(dependency), reduction, now):
            continue
        result = await publisher.publish(reduction, now)
        if result.published:
            state[dependency] = PublishState(reduction.state, reduction.cause, now)
        else:
            failed = True
            logger.warning(
                "capability health: publish for %s refused (%s)", dependency, result.reason
            )
    await record_tick(failed=failed)


async def run_loop(tick: Callable[[], Awaitable[None]], *, interval_s: float) -> None:
    """Run ``tick`` every ``interval_s`` until cancelled; one bad tick never ends the loop."""
    if interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number, got {interval_s!r}")
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("capability health: tick failed", exc_info=True)
            await record_tick(failed=True)
        await asyncio.sleep(interval_s)


_LATEST_ATTEMPTS = """
SELECT DISTINCT ON (credential_scope, credential_key)
       credential_scope, credential_key, recorded_at
  FROM public.secret_probe_log
 WHERE (credential_scope, credential_key) IN (SELECT * FROM unnest($1::text[], $2::text[]))
 ORDER BY credential_scope, credential_key, recorded_at DESC
"""


_USER_TYPE_COUNTS = """
SELECT type, count(*) AS holders
  FROM public.entity_info
 WHERE type = ANY($1::text[])
 GROUP BY type
"""


async def credential_probe_observations(
    pool: Any, snapshots: list[Any], now: datetime
) -> list[Observation]:
    """Credential observations stamped with each credential's latest probe attempt.

    The probe log is written on every probe, success or failure, so a credential that failed
    just now is observed now even when its last success is old. A credential with no logged
    attempt has no probe time and so reads unmeasured. One query per tick covers every
    credential that feeds a declared dependency.
    """
    feeding = [
        s
        for s in snapshots
        if s.key in CREDENTIAL_DEPENDENCIES and s.probe_scope is not None and s.probe_key
    ]
    attempts: dict[tuple[str, str], datetime] = {}
    if feeding:
        # The probe log is keyed by credential type, with no entity. A user-family credential
        # may use it only when exactly one entity holds that type; otherwise one account's
        # probe would vouch for another's. Entity-qualified probe-log keys would lift this.
        user_types = sorted({s.probe_key for s in feeding if s.probe_scope == "user"})
        sole: set[str] = set()
        if user_types:
            held = await pool.fetch(_USER_TYPE_COUNTS, user_types)
            sole = {r["type"] for r in held if r["holders"] == 1}
        feeding = [s for s in feeding if s.probe_scope != "user" or s.probe_key in sole]
    if feeding:
        rows = await pool.fetch(
            _LATEST_ATTEMPTS, [s.probe_scope for s in feeding], [s.probe_key for s in feeding]
        )
        attempts = {(r["credential_scope"], r["credential_key"]): r["recorded_at"] for r in rows}
    return credential_observations(
        [(s.key, s.state, attempts.get((s.probe_scope, s.probe_key))) for s in snapshots], now
    )


async def run_capability_health_loop(
    db: Any, dsn: str, *, interval_s: float = DEFAULT_INTERVAL_S
) -> None:
    """Own the writer pool and run the collector loop (cancel to stop)."""
    from butlers.jobs.secrets_lifecycle import _collect_snapshots

    async def gather(now: datetime) -> list[Observation]:
        observations: list[Observation] = []
        try:
            snapshots = await _collect_snapshots(db)
            observations += await credential_probe_observations(
                db.credential_shared_pool(), snapshots, now
            )
        except Exception:
            mark_tick_incomplete()
            logger.warning("capability health: credential collection failed")
        try:
            observations.append(await probe_conditions(db.pool("switchboard")))
        except Exception:
            mark_tick_incomplete()
            logger.warning("capability health: conditions probe unavailable")
        return observations

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, command_timeout=10)
    try:
        state: dict[str, PublishState] = {}

        async def tick() -> None:
            async with pool.acquire() as connection:
                publisher = HealthPublisher(PerceptionStore(connection))
                await run_tick(gather, publisher, state, datetime.now(UTC))

        await run_loop(tick, interval_s=interval_s)
    finally:
        await pool.close()
