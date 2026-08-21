"""DB-level regression for the durable condition ledger and the exact Audit door (bu-6jv4m.3).

Two contracts are asserted against a *real* migrated Postgres, because both of
them are properties of the schema and the SQL rather than of Python:

1. ``public.butler_reachability_conditions`` (core_199) must let one
   uninterrupted outage stay ONE row with ONE stable onset across arbitrarily
   many probes, close on recovery, and open a genuinely NEW row (new onset) on
   the next down transition.  The partial unique index
   ``(butler) WHERE resolved_at IS NULL`` is what makes the router's
   open-or-extend a single atomic upsert; a unit test with a mocked pool cannot
   observe whether the ON CONFLICT target actually infers that index -- if it
   does not, the upsert raises at runtime and the entire ack story silently
   collapses back to "every poll is a new occurrence".

2. ``build_audit_group_for_row_query`` must resolve a real ``public.audit_log``
   row id to the SAME group the Issues feed itself would compute -- including
   for a historical row outside the default window, and for rows whose raw
   ``error`` text differs but which normalize onto one group.  The unit tests
   for the router mock this query away entirely; only a real execution proves
   the CTE joins and the window bound behave.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.api.audit_grouping import (
    audit_group_key,
    build_audit_group_for_row_query,
    build_audit_group_query,
    issue_from_audit_group_row,
)
from butlers.api.reachability_ledger import open_condition_onset, record_probe
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

BUTLER = "calendar"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE public.butler_reachability_conditions")
    await p.execute("TRUNCATE TABLE public.audit_log CASCADE")
    yield p
    await p.close()


async def _episodes(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch(
        """
        SELECT butler, started_at, last_seen_at, resolved_at, observations, detail
          FROM public.butler_reachability_conditions
         ORDER BY started_at
        """
    )


# ---------------------------------------------------------------------------
# 1. Condition ledger lifecycle
# ---------------------------------------------------------------------------


async def test_continuous_outage_keeps_one_row_with_one_stable_onset(pool: asyncpg.Pool) -> None:
    """Repeated probes of ONE uninterrupted outage must not manufacture recurrence.

    The onset is the epoch an acknowledgement is held against, so if it moved on
    any poll the ack would be outrun before the user could see it stick.
    """
    first = await record_probe(pool, down={BUTLER: "connection refused"}, recovered=[])
    onset = first[BUTLER].started_at

    for _ in range(4):
        again = await record_probe(pool, down={BUTLER: "connection refused"}, recovered=[])
        assert again[BUTLER].started_at == onset, (
            "onset moved during one uninterrupted outage; the ack watermark can never hold"
        )

    rows = await _episodes(pool)
    assert len(rows) == 1, f"one outage produced {len(rows)} episodes"
    assert rows[0]["observations"] == 5
    assert rows[0]["resolved_at"] is None
    # The probe clock advances (it is descriptive), the onset does not.
    assert rows[0]["last_seen_at"] >= rows[0]["started_at"]
    assert await open_condition_onset(pool, BUTLER) == onset


async def test_recovery_closes_the_condition_and_is_terminal(pool: asyncpg.Pool) -> None:
    await record_probe(pool, down={BUTLER: "connection refused"}, recovered=[])
    await record_probe(pool, down={}, recovered=[BUTLER])

    rows = await _episodes(pool)
    assert len(rows) == 1
    assert rows[0]["resolved_at"] is not None, "recovery did not close the condition"
    assert await open_condition_onset(pool, BUTLER) is None, (
        "a recovered butler still reports an open condition"
    )

    # Recovery is idempotent: a second reachable poll must not resurrect or
    # duplicate anything.
    await record_probe(pool, down={}, recovered=[BUTLER])
    assert len(await _episodes(pool)) == 1


async def test_later_down_transition_opens_a_genuinely_new_recurrence(pool: asyncpg.Pool) -> None:
    """After recovery, the next outage must get a NEW onset -- that is the
    recurrence signal that re-opens an acknowledged condition."""
    first = await record_probe(pool, down={BUTLER: "connection refused"}, recovered=[])
    first_onset = first[BUTLER].started_at
    await record_probe(pool, down={}, recovered=[BUTLER])

    second = await record_probe(pool, down={BUTLER: "timeout"}, recovered=[])
    second_onset = second[BUTLER].started_at

    assert second_onset > first_onset, (
        "recurrence reused the resolved episode's onset; an earlier ack would "
        "silently suppress a brand-new outage"
    )
    assert second[BUTLER].observations == 1

    rows = await _episodes(pool)
    assert len(rows) == 2, "the resolved episode was overwritten instead of kept as history"
    assert rows[0]["resolved_at"] is not None
    assert rows[1]["resolved_at"] is None
    assert rows[1]["detail"] == "timeout"


async def test_partial_unique_index_permits_history_but_not_two_open_episodes(
    pool: asyncpg.Pool,
) -> None:
    """The index predicate is the whole design; assert both halves of it."""
    await record_probe(pool, down={BUTLER: "boom"}, recovered=[])
    await record_probe(pool, down={}, recovered=[BUTLER])
    await record_probe(pool, down={BUTLER: "boom"}, recovered=[])

    open_count = await pool.fetchval(
        """
        SELECT COUNT(*) FROM public.butler_reachability_conditions
         WHERE butler = $1 AND resolved_at IS NULL
        """,
        BUTLER,
    )
    assert open_count == 1

    # Resolved rows are deliberately unconstrained: two of them may coexist.
    total = await pool.fetchval(
        "SELECT COUNT(*) FROM public.butler_reachability_conditions WHERE butler = $1", BUTLER
    )
    assert total == 2

    # A second OPEN row for the same butler must be rejected by the index.
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await pool.execute(
            "INSERT INTO public.butler_reachability_conditions (butler) VALUES ($1)", BUTLER
        )


async def test_one_probe_separates_down_and_recovered_butlers(pool: asyncpg.Pool) -> None:
    await record_probe(pool, down={"calendar": "refused", "health": "refused"}, recovered=[])
    episodes = await record_probe(pool, down={"calendar": "refused"}, recovered=["health"])

    assert set(episodes) == {"calendar"}
    open_butlers = await pool.fetch(
        """
        SELECT butler FROM public.butler_reachability_conditions
         WHERE resolved_at IS NULL ORDER BY butler
        """
    )
    assert [r["butler"] for r in open_butlers] == ["calendar"]


# ---------------------------------------------------------------------------
# 2. Exact Audit -> Issues group resolution
# ---------------------------------------------------------------------------


async def _insert_error(
    pool: asyncpg.Pool,
    *,
    actor: str = BUTLER,
    action: str = "session",
    error: str,
    ts: datetime,
    metadata: dict | None = None,
) -> int:
    return await pool.fetchval(
        """
        INSERT INTO public.audit_log (actor, action, result, error, metadata, ts)
        VALUES ($1, $2, 'error', $3, $4::jsonb, $5)
        RETURNING id
        """,
        actor,
        action,
        error,
        metadata,
        ts,
    )


async def test_row_resolves_to_the_same_key_the_feed_computes(pool: asyncpg.Pool) -> None:
    now = datetime.now(UTC)
    audit_id = await _insert_error(
        pool, error="KeyError: 'access_token'", ts=now - timedelta(hours=1)
    )

    rows = await pool.fetch(build_audit_group_for_row_query(), audit_id, now - timedelta(hours=24))
    assert len(rows) == 1
    resolved = issue_from_audit_group_row(rows[0])

    feed = [issue_from_audit_group_row(r) for r in await pool.fetch(build_audit_group_query())]
    feed_keys = {i.issue_key for i in feed}
    assert resolved.issue_key in feed_keys, (
        "the door resolves to a key the Issues feed does not have; the link would 404"
    )
    assert resolved.issue_key == audit_group_key("KeyError: 'access_token'")


async def test_rows_that_normalize_together_resolve_to_one_group(pool: asyncpg.Pool) -> None:
    """The client-side ``firstErrorLine`` approximation could not do this.

    These two raw ``error`` values differ in their trailing traceback AND in a
    per-run temp directory, but the backend's normalization collapses both onto
    one ``error_summary`` -- so the door must return one group whose occurrence
    count includes both rows.
    """
    now = datetime.now(UTC)
    first = await _insert_error(
        pool,
        error="OSError: cannot open /tmp/tmpab12cd34/state.json\n  at loader.py:19",
        ts=now - timedelta(hours=3),
    )
    second = await _insert_error(
        pool,
        error="OSError: cannot open /tmp/tmpZZ99zz00/state.json\n  at writer.py:88",
        ts=now - timedelta(hours=2),
    )

    for audit_id in (first, second):
        rows = await pool.fetch(
            build_audit_group_for_row_query(), audit_id, now - timedelta(hours=24)
        )
        assert len(rows) == 1
        issue = issue_from_audit_group_row(rows[0])
        assert issue.occurrences == 2, (
            "normalized siblings did not resolve to one group; a fuzzy text match "
            f"would have split them (got {issue.occurrences})"
        )
        assert issue.issue_key == audit_group_key("OSError: cannot open /tmp/.../state.json")


async def test_historical_row_is_invisible_in_the_default_window_but_found_with_none(
    pool: asyncpg.Pool,
) -> None:
    """A 45-day-old failure has no group in a 24h window -- and that absence is
    real, not a lookup failure.  Widening the bound to NULL must find it."""
    now = datetime.now(UTC)
    audit_id = await _insert_error(pool, error="RuntimeError: ancient", ts=now - timedelta(days=45))

    narrow = await pool.fetch(
        build_audit_group_for_row_query(), audit_id, now - timedelta(hours=24)
    )
    assert narrow == [], "a 45-day-old row appeared inside a 24h window"

    all_time = await pool.fetch(build_audit_group_for_row_query(), audit_id, None)
    assert len(all_time) == 1
    assert issue_from_audit_group_row(all_time[0]).issue_key == audit_group_key(
        "RuntimeError: ancient"
    )


async def test_unrelated_errors_never_collapse_into_one_group(pool: asyncpg.Pool) -> None:
    now = datetime.now(UTC)
    mine = await _insert_error(pool, error="ValueError: alpha", ts=now - timedelta(hours=1))
    await _insert_error(pool, error="ValueError: beta", ts=now - timedelta(hours=1))

    rows = await pool.fetch(build_audit_group_for_row_query(), mine, now - timedelta(hours=24))
    assert len(rows) == 1
    issue = issue_from_audit_group_row(rows[0])
    assert issue.occurrences == 1
    assert issue.issue_key == audit_group_key("ValueError: alpha")


async def test_scheduled_failure_row_resolves_to_a_critical_group(pool: asyncpg.Pool) -> None:
    now = datetime.now(UTC)
    audit_id = await _insert_error(
        pool,
        error="TimeoutError: sync overran",
        ts=now - timedelta(hours=1),
        metadata={"trigger_source": "schedule:nightly_sync"},
    )

    rows = await pool.fetch(build_audit_group_for_row_query(), audit_id, now - timedelta(hours=24))
    issue = issue_from_audit_group_row(rows[0])
    assert issue.severity == "critical"
    assert issue.type == "scheduled_task_failure:nightly-sync"


async def test_success_row_has_no_group_at_all(pool: asyncpg.Pool) -> None:
    """``normalized_errors`` filters on ``result = 'error'``, so a success row's
    id resolves to nothing -- the router must report that explicitly rather than
    render the empty result as calm."""
    now = datetime.now(UTC)
    audit_id = await pool.fetchval(
        """
        INSERT INTO public.audit_log (actor, action, result, error, ts)
        VALUES ($1, 'session', 'success', NULL, $2)
        RETURNING id
        """,
        BUTLER,
        now - timedelta(hours=1),
    )

    rows = await pool.fetch(build_audit_group_for_row_query(), audit_id, now - timedelta(hours=24))
    assert rows == []
