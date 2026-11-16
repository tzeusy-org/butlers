"""The location retention job: reconcile grants, then state lag and failures (bu-s11n0s.7 S4b-4c).

``run_location_retention_job`` reconciles ready grants, counts the raw fixes past the retention
horizon by why they are still there, and reconciles those counts into the owner condition ledger
under ``chronicler:location-retention-projection-lag``. Failures are a separate source,
``chronicler:location-retention-purge-failed``: the job's consecutive failure streak and the
grants that failed reconciliation. The ledger is state only; nothing is delivered. The run record
(``location_retention_runs``) commits before any work and the outcome after it.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg
import pytest

from butlers.chronicler import location_retention_job as job
from butlers.chronicler.location_retention_grant import prepare_retention_grant
from tests.integration import test_owntracks_retention_grant_integration as _grant_suite

grant_url = _grant_suite.grant_url
chronicler_db = _grant_suite.chronicler_db
runtime_db = _grant_suite.runtime_db
_REVISIONS = _grant_suite._REVISIONS
_cover = _grant_suite._cover
_hold = _grant_suite._hold
_insert_point = _grant_suite._insert_point

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_STATE = "INSERT INTO location_retention_grant_states (grant_id, state) VALUES ($1, $2)"


async def _prepare(chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool, minute: int) -> UUID:
    await _cover(chronicler_db, await _insert_point(chronicler_db, minute))
    async with runtime_db.acquire() as conn:
        return (await prepare_retention_grant(conn, config_revisions=_REVISIONS)).id


async def _every_category(chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool) -> None:
    """One overdue raw fix per reason it is still there, and one inside the horizon."""
    await _prepare(chronicler_db, runtime_db, 0)  # awaiting_holders: prepared, never ready
    ready = await _prepare(chronicler_db, runtime_db, 5)  # awaiting_deletion
    await chronicler_db.execute(_STATE, ready, "ready")
    await _insert_point(chronicler_db, 10)  # uncovered
    held = await _insert_point(chronicler_db, 15)
    await _cover(chronicler_db, held, skip="owntracks.place_cluster")
    await _hold(chronicler_db, held, "owntracks.place_cluster")  # held_open
    await _cover(chronicler_db, await _insert_point(chronicler_db, 20))  # awaiting_grant
    await chronicler_db.execute(
        "INSERT INTO connectors.owntracks_points (idempotency_key, ts, lat, lon, accuracy, "
        "trigger, endpoint_identity, raw_payload, recorded_at) VALUES ('recent', now(), 1.3, "
        "103.8, 10.0, 'p', 'owntracks:alice', '{}'::jsonb, now() - interval '1 hour')"
    )


async def _active(target: asyncpg.Pool, source: str) -> dict[str, dict]:
    rows = await target.fetch(
        "SELECT metadata FROM public.owner_conditions "
        "WHERE source = $1 AND state IN ('open', 'aging')",
        source,
    )

    def loaded(raw) -> dict:
        return dict(json.loads(raw) if isinstance(raw, str) else raw)

    return {meta["category"]: meta for meta in map(loaded, (r["metadata"] for r in rows))}


async def _run_row(target: asyncpg.Pool) -> asyncpg.Record:
    return await target.fetchrow("SELECT * FROM location_retention_runs WHERE id = 1")


async def test_lag_is_counted_by_why_each_overdue_fix_is_still_there(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    await _every_category(chronicler_db, runtime_db)

    async with runtime_db.acquire() as conn:
        lag = await job.read_retention_lag(conn)

    assert lag.counts == {
        "awaiting_holders": 1,
        "awaiting_deletion": 1,
        "uncovered": 1,
        "held_open": 1,
        "awaiting_grant": 1,
    }
    assert all(hours > 24 for hours in lag.oldest_age_hours.values())


async def test_the_job_states_lag_and_resolves_it_once_the_fixes_are_gone(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    await _every_category(chronicler_db, runtime_db)

    result = await job.run_location_retention_job(runtime_db)

    assert result["status"] == "ok"
    lag = await _active(chronicler_db, job.LAG_SOURCE)
    assert {category: meta["count"] for category, meta in lag.items()} == {
        "awaiting_holders": 1,
        "awaiting_deletion": 1,
        "uncovered": 1,
        "held_open": 1,
        "awaiting_grant": 1,
    }
    # Count-only evidence; ``identity_payload`` is the ledger's own version lineage.
    assert set(lag["uncovered"]) - {"identity_payload"} == {"category", "count", "oldest_age_hours"}
    assert await _active(chronicler_db, job.FAILED_SOURCE) == {}
    run = await _run_row(chronicler_db)
    assert (run["last_outcome"], run["consecutive_failures"]) == ("succeeded", 0)

    await chronicler_db.execute(
        "DELETE FROM connectors.owntracks_points WHERE idempotency_key <> 'recent'"
    )
    await job.run_location_retention_job(runtime_db)
    assert await _active(chronicler_db, job.LAG_SOURCE) == {}


async def test_a_failing_run_is_its_own_condition_and_never_clears_lag(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _every_category(chronicler_db, runtime_db)
    await job.run_location_retention_job(runtime_db)

    async def broken(conn, **kwargs):
        raise RuntimeError("connector evidence unreadable")

    monkeypatch.setattr(job, "reconcile_ready_grants", broken)
    for streak in (1, 2):
        result = await job.run_location_retention_job(runtime_db)
        assert (result["status"], result["consecutive_failures"]) == ("failed", streak)

    run = await _run_row(chronicler_db)
    assert (run["last_outcome"], run["last_error_class"]) == ("failed", "RuntimeError")
    failing = await _active(chronicler_db, job.FAILED_SOURCE)
    assert failing["run_failing"]["consecutive_failures"] == 2
    assert failing["run_failing"]["error_class"] == "RuntimeError"
    assert len(await _active(chronicler_db, job.LAG_SOURCE)) == 5  # not closed by omission

    monkeypatch.undo()
    assert (await job.run_location_retention_job(runtime_db))["status"] == "ok"
    assert await _active(chronicler_db, job.FAILED_SOURCE) == {}
    assert (await _run_row(chronicler_db))["consecutive_failures"] == 0


async def test_a_grant_that_failed_reconciliation_stays_a_condition(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    grant = await _prepare(chronicler_db, runtime_db, 0)
    await chronicler_db.execute(_STATE, grant, "ready")
    await chronicler_db.execute(_STATE, grant, "failed")

    assert (await job.run_location_retention_job(runtime_db))["status"] == "ok"

    assert (await _active(chronicler_db, job.FAILED_SOURCE))["failed_grants"]["count"] == 1
    assert (await _active(chronicler_db, job.LAG_SOURCE))["grant_failed"]["count"] == 1


async def test_a_ready_grant_pending_past_the_threshold_is_a_stalled_condition(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    fresh = await _prepare(chronicler_db, runtime_db, 0)
    await chronicler_db.execute(_STATE, fresh, "ready")
    stalled = await _prepare(chronicler_db, runtime_db, 5)
    settled = await _prepare(chronicler_db, runtime_db, 10)
    for grant in (stalled, settled):
        await chronicler_db.execute(
            "INSERT INTO location_retention_grant_states (grant_id, state, recorded_at) "
            "VALUES ($1, 'ready', now() - $2::interval)",
            grant,
            job.STALLED_AFTER + timedelta(hours=2),
        )
    await chronicler_db.execute(_STATE, settled, "failed")

    assert (await job.run_location_retention_job(runtime_db))["status"] == "ok"

    failing = await _active(chronicler_db, job.FAILED_SOURCE)
    # Only the old ready grant with no receipt: not the fresh one, not the settled one.
    assert failing["stalled_grants"]["count"] == 1
    assert (
        failing["stalled_grants"]["oldest_age_hours"] >= job.STALLED_AFTER.total_seconds() // 3600
    )
    assert failing["failed_grants"]["count"] == 1


async def test_coverage_at_a_revision_the_adapter_no_longer_runs_is_uncovered(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    stale = await _insert_point(chronicler_db, 0)
    await _cover(chronicler_db, stale, revision={"owntracks.points": "v1:" + "c" * 64})
    other_content = await _insert_point(chronicler_db, 5)
    await _cover(chronicler_db, other_content, digest={"owntracks.ssid_presence": "v1:" + "b" * 64})
    await _cover(chronicler_db, await _insert_point(chronicler_db, 10))
    await chronicler_db.execute(
        "DELETE FROM location_projection_attempts WHERE adapter_name = 'owntracks.place_cluster'"
    )

    async with runtime_db.acquire() as conn:
        lag = await job.read_retention_lag(conn)

    # With no recorded run for one adapter, nothing can be exactly covered.
    assert lag.counts == {"uncovered": 3}


async def test_exactly_covered_rows_await_a_grant(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    stale = await _insert_point(chronicler_db, 0)
    await _cover(chronicler_db, stale, revision={"owntracks.points": "v1:" + "c" * 64})
    other_content = await _insert_point(chronicler_db, 5)
    await _cover(chronicler_db, other_content, digest={"owntracks.ssid_presence": "v1:" + "b" * 64})
    await _cover(chronicler_db, await _insert_point(chronicler_db, 10))

    async with runtime_db.acquire() as conn:
        lag = await job.read_retention_lag(conn)

    assert lag.counts == {"uncovered": 2, "awaiting_grant": 1}


async def test_a_run_whose_lease_was_taken_over_leaves_the_outcome_to_the_new_run(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def taken_over(conn, **kwargs):
        await chronicler_db.execute(
            "UPDATE location_retention_runs SET last_started_at = now() + interval '1 second'"
        )
        return []

    monkeypatch.setattr(job, "reconcile_ready_grants", taken_over)

    assert await job.run_location_retention_job(runtime_db) == {
        "status": "skipped",
        "reason": "lease_lost",
    }
    assert (await _run_row(chronicler_db))["last_outcome"] == "running"


async def test_an_unfinished_run_skips_within_its_lease_and_counts_as_a_failure_after(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = datetime.now(UTC) - timedelta(minutes=5)
    await chronicler_db.execute(
        "INSERT INTO location_retention_runs (id, last_started_at, last_outcome) "
        "VALUES (1, $1, 'running')",
        started,
    )

    assert await job.run_location_retention_job(runtime_db) == {
        "status": "skipped",
        "reason": "running",
    }
    assert (await _run_row(chronicler_db))["last_started_at"] == started

    await chronicler_db.execute(
        "UPDATE location_retention_runs SET last_started_at = now() - $1::interval",
        job.RUN_LEASE + timedelta(minutes=1),
    )

    async def broken(conn, **kwargs):
        raise RuntimeError("still broken")

    monkeypatch.setattr(job, "reconcile_ready_grants", broken)
    result = await job.run_location_retention_job(runtime_db)

    # The unfinished run is unknown, never green: it counts toward the streak.
    assert result["consecutive_failures"] == 2


async def test_a_taken_over_run_stays_failing_until_a_later_clean_run(
    chronicler_db: asyncpg.Pool, runtime_db: asyncpg.Pool
) -> None:
    await chronicler_db.execute(
        "INSERT INTO location_retention_runs (id, last_started_at, last_outcome) "
        "VALUES (1, now() - $1::interval, 'running')",
        job.RUN_LEASE + timedelta(minutes=1),
    )

    # The takeover run itself succeeds, but the run it replaced never finished.
    assert (await job.run_location_retention_job(runtime_db))["status"] == "ok"
    failing = await _active(chronicler_db, job.FAILED_SOURCE)
    assert failing["run_failing"]["error_class"] == "Unfinished"
    assert failing["run_failing"]["consecutive_failures"] == 1
    assert (await _run_row(chronicler_db))["last_outcome"] == "succeeded"

    assert (await job.run_location_retention_job(runtime_db))["status"] == "ok"
    assert await _active(chronicler_db, job.FAILED_SOURCE) == {}
    assert (await _run_row(chronicler_db))["consecutive_failures"] == 0


async def test_job_args_are_refused(runtime_db: asyncpg.Pool) -> None:
    with pytest.raises(ValueError):
        await job.run_location_retention_job(runtime_db, {"limit": 5})
