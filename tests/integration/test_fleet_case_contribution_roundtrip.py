"""Real-Postgres regression: fleet case contribution (bu-8cdl1.7 Slices 3-7, RFC 0032).

Exercises ``butlers.core.fleet_cases`` against a fully migrated Postgres
instance (testcontainers) -- not just the mocked-pool unit tests in
``tests/core/test_fleet_cases.py``. Things a mock cannot verify:

- The RLS policy from core_217 actually blocks a non-Switchboard role's
  ``open_case``/``propose_posture``/``close_case``/``write_case_link`` at the
  database, so the MCP tool layer's route-forwarding
  (``core_tools/_fleet_cases.py``) is load-bearing, not decorative.
- ``contribute_evidence``'s ``ON CONFLICT DO NOTHING`` + re-SELECT round-trips
  correctly against real Postgres: the epic's acceptance criterion ("two
  butlers contributing the same evidence ref -> one row") means one row *per
  contributor* -- ``UNIQUE(case_id, contributor, kind, ref)`` -- so two
  distinct butlers reporting the same ref get two attributed rows, while the
  same butler repeating it collapses to one.
- Slice 6's backfill: that ``chk_fleet_cases_closed_needs_outcome`` and
  ``uq_fleet_cases_active_correlation_key`` are actually satisfied by the
  backfill's writes against the live constraints, not just by inspecting its
  SQL text (see ``tests/core/test_fleet_cases.py`` for that half).
- Slice 7's three-ledger binding: ``write_case_link``'s
  ``uq_fleet_case_links_ref`` idempotence, its RLS write-authority refusal for
  a non-Switchboard role, and that a linked case surfaces its links through
  ``read_case`` -- all against the live constraints, not mocked ones.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.core.fleet_cases import (
    BACKFILL_CORRELATION_KEY_PREFIX,
    LINK_KINDS,
    FleetCaseError,
    backfill_from_owner_conditions,
    backfill_historical_case,
    case_attention_dedup_key,
    close_case,
    contribute_evidence,
    evaluate_case_attention,
    open_case,
    propose_posture,
    read_case,
    run_lapse_sweep,
    write_case_link,
)
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def _db_name() -> str:
    return migration_db_name()


@pytest.fixture(scope="module")
def db_url(postgres_container, _db_name: str) -> str:
    return create_migrated_test_db(postgres_container, _db_name, chains=["core"])


@pytest.fixture(scope="module")
def bootstrap_url(postgres_container, _db_name: str, db_url: str) -> str:
    # fleet_cases/fleet_case_links FORCE RLS with no DELETE policy at all --
    # even butler_switchboard_rw cannot delete a row (only close it, see
    # AGENTS.md's RLS note). Cleanup needs the real superuser, which bypasses
    # RLS unconditionally.
    return migration_bootstrap_db_url(postgres_container, _db_name).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


@pytest.fixture
async def pool(db_url: str) -> asyncpg.Pool:
    # Three connections: the external blocker plus the two overlapping
    # evaluate_case_attention() calls -- a larger pool proves serialization
    # comes from the DB's advisory lock, not pool exhaustion.
    p = await asyncpg.create_pool(db_url, min_size=3, max_size=6)
    yield p
    await p.close()


async def _role_conn(db_url: str, role: str | None) -> asyncpg.Connection:
    conn = await asyncpg.connect(db_url)
    if role is not None:
        await conn.execute(f"SET ROLE {role}")
    return conn


async def _delete_case(bootstrap_url: str, correlation_key: str) -> None:
    conn = await asyncpg.connect(bootstrap_url)
    try:
        await conn.execute(
            "DELETE FROM public.fleet_cases WHERE correlation_key = $1", correlation_key
        )
    finally:
        await conn.close()


async def _seed_case(
    bootstrap_url: str,
    *,
    correlation_key: str,
    updated_at: datetime,
    state: str = "open",
    posture: str = "silent",
    outcome: str | None = None,
    closed_at: datetime | None = None,
) -> str:
    """Insert a case with a backdated ``updated_at`` -- the lapse sweep's
    staleness test needs rows the ordinary API can't produce (open_case/
    propose_posture always stamp ``now()``)."""
    conn = await asyncpg.connect(bootstrap_url)
    try:
        row = await conn.fetchrow(
            "INSERT INTO public.fleet_cases "
            "(correlation_key, state, posture, outcome, updated_at, closed_at) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            correlation_key,
            state,
            posture,
            outcome,
            updated_at,
            closed_at,
        )
        return str(row["id"])
    finally:
        await conn.close()


async def _seed_evidence(bootstrap_url: str, *, case_id: str, contributed_at: datetime) -> None:
    conn = await asyncpg.connect(bootstrap_url)
    try:
        await conn.execute(
            "INSERT INTO public.fleet_case_evidence "
            "(case_id, contributor, kind, ref, contributed_at) "
            "VALUES ($1, 'butler_health_rw', 'candidate', 'ref-1', $2)",
            case_id,
            contributed_at,
        )
    finally:
        await conn.close()


async def _seed_owner_condition(
    bootstrap_url: str,
    *,
    source: str,
    fingerprint: str,
    episode: int,
    first_detected_at: datetime,
    state: str = "resolved",
    resolved_at: datetime | None = None,
    recovered_after_s: float | None = None,
    metadata: dict | None = None,
) -> str:
    """Seed one public.owner_conditions episode directly -- Slice 6's backfill
    source. Bypasses the condition_ledger engine (reconcile_snapshot/
    resolve_condition) the same way _seed_case bypasses open_case/close_case:
    the backfill test needs a specific already-resolved episode the ordinary
    API path doesn't let a test dictate directly. Returns the episode's own
    id -- Slice 7's ``owner_condition`` link ref."""
    conn = await asyncpg.connect(bootstrap_url)
    try:
        row = await conn.fetchrow(
            "INSERT INTO public.owner_conditions "
            "(source, fingerprint, episode, state, first_detected_at, last_confirmed_at, "
            " resolved_at, recovered_after_s, metadata) "
            "VALUES ($1, $2, $3, $4, $5, $5, $6, $7, $8::jsonb) RETURNING id",
            source,
            fingerprint,
            episode,
            state,
            first_detected_at,
            resolved_at,
            recovered_after_s,
            json.dumps(metadata) if metadata is not None else None,
        )
        return str(row["id"])
    finally:
        await conn.close()


async def _delete_owner_conditions(bootstrap_url: str, *, source: str) -> None:
    conn = await asyncpg.connect(bootstrap_url)
    try:
        await conn.execute("DELETE FROM public.owner_conditions WHERE source = $1", source)
    finally:
        await conn.close()


async def test_lifecycle_via_switchboard_role_with_two_contributing_butlers(
    db_url: str, bootstrap_url: str
) -> None:
    switchboard_conn = await _role_conn(db_url, "butler_switchboard_rw")
    health_conn = await _role_conn(db_url, "butler_health_rw")
    relationship_conn = await _role_conn(db_url, "butler_relationship_rw")
    try:
        case = await open_case(switchboard_conn, correlation_key="test:roundtrip-key")
        case_id = str(case["id"])
        assert case["state"] == "open"
        assert case["posture"] == "silent"

        # Two distinct butlers reporting the identical (kind, ref): each gets
        # its own attributed row -- UNIQUE is per-contributor, not per-ref.
        health_row, health_new = await contribute_evidence(
            health_conn,
            case_id=case_id,
            contributor="butler_health_rw",
            kind="candidate",
            ref="insight-42",
        )
        assert health_new is True
        relationship_row, relationship_new = await contribute_evidence(
            relationship_conn,
            case_id=case_id,
            contributor="butler_relationship_rw",
            kind="candidate",
            ref="insight-42",
        )
        assert relationship_new is True
        assert health_row["id"] != relationship_row["id"]

        # The same butler re-reporting the identical (kind, ref) collapses to
        # the existing row -- one row per contributor, not a duplicate.
        repeat_row, repeat_new = await contribute_evidence(
            health_conn,
            case_id=case_id,
            contributor="butler_health_rw",
            kind="candidate",
            ref="insight-42",
        )
        assert repeat_new is False
        assert repeat_row["id"] == health_row["id"]

        updated = await propose_posture(switchboard_conn, case_id=case_id, posture="active")
        assert updated["posture"] == "active"

        closed = await close_case(switchboard_conn, case_id=case_id, outcome="resolved")
        assert closed["state"] == "closed"
        assert closed["outcome"] == "resolved"

        full = await read_case(switchboard_conn, case_id)
        assert full is not None
        assert len(full["evidence"]) == 2
        assert {row["contributor"] for row in full["evidence"]} == {
            "butler_health_rw",
            "butler_relationship_rw",
        }

        with pytest.raises(FleetCaseError, match="already closed"):
            await close_case(switchboard_conn, case_id=case_id, outcome="resolved-again")
    finally:
        await switchboard_conn.close()
        await health_conn.close()
        await relationship_conn.close()
        await _delete_case(bootstrap_url, "test:roundtrip-key")


async def test_non_switchboard_role_cannot_write_fleet_cases_at_the_data_layer(
    db_url: str, bootstrap_url: str
) -> None:
    """Pins why the MCP tool layer must forward these calls through
    Switchboard's route(): the data layer itself refuses at the database,
    it does not silently succeed for the wrong role. INSERT (open_case) and
    UPDATE (propose_posture/close_case) fail differently under RLS -- a
    WITH CHECK violation on INSERT raises, but an UPDATE's USING clause
    just filters the row out, so those two instead surface as a
    FleetCaseError naming the mismatch rather than a raw asyncpg error."""
    switchboard_conn = await _role_conn(db_url, "butler_switchboard_rw")
    health_conn = await _role_conn(db_url, "butler_health_rw")
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await open_case(health_conn, correlation_key="test:rls-refusal-key")

        case = await open_case(switchboard_conn, correlation_key="test:rls-refusal-key-2")
        with pytest.raises(FleetCaseError, match="did not apply"):
            await propose_posture(health_conn, case_id=str(case["id"]), posture="urgent")
        with pytest.raises(FleetCaseError, match="did not apply"):
            await close_case(health_conn, case_id=str(case["id"]), outcome="resolved")
    finally:
        await switchboard_conn.close()
        await health_conn.close()
        await _delete_case(bootstrap_url, "test:rls-refusal-key-2")


async def test_write_case_link_covers_all_three_link_kinds_and_is_idempotent(
    db_url: str, bootstrap_url: str
) -> None:
    """RFC 0032 Slice 7 end-to-end: each of the three link_kinds can be
    written and read back via (case_id, link_kind, ref), a repeat write of
    the exact same (case_id, link_kind, ref) is a no-op rather than a
    duplicate row (``uq_fleet_case_links_ref``), and a linked case surfaces
    every link through ``read_case`` (Slice 2's read API)."""
    switchboard_conn = await _role_conn(db_url, "butler_switchboard_rw")
    try:
        case = await open_case(switchboard_conn, correlation_key="test:link-lifecycle-key")
        case_id = str(case["id"])

        refs = {
            "insight_candidate": "candidate-1",
            "owner_condition": "condition-1",
            "attention_record": "attention-1",
        }
        assert set(refs) == LINK_KINDS
        for link_kind, ref in refs.items():
            link_row, newly_recorded = await write_case_link(
                switchboard_conn, case_id=case_id, link_kind=link_kind, ref=ref
            )
            assert newly_recorded is True
            assert link_row["case_id"] == case["id"]
            assert link_row["link_kind"] == link_kind
            assert link_row["ref"] == ref

        # A repeat write of the exact same (case_id, link_kind, ref) is a
        # no-op -- the existing row is returned, not a duplicate.
        repeat_row, repeat_new = await write_case_link(
            switchboard_conn,
            case_id=case_id,
            link_kind="insight_candidate",
            ref=refs["insight_candidate"],
        )
        assert repeat_new is False
        assert repeat_row["ref"] == refs["insight_candidate"]

        full = await read_case(switchboard_conn, case_id)
        assert full is not None
        assert len(full["links"]) == 3
        assert {row["link_kind"] for row in full["links"]} == LINK_KINDS

        count = await switchboard_conn.fetchval(
            "SELECT count(*) FROM public.fleet_case_links WHERE case_id = $1", case["id"]
        )
        assert count == 3
    finally:
        await switchboard_conn.close()
        await _delete_case(bootstrap_url, "test:link-lifecycle-key")


async def test_non_switchboard_role_cannot_write_fleet_case_links_at_the_data_layer(
    db_url: str, bootstrap_url: str
) -> None:
    """Write authority matches fleet_cases exactly -- a non-Switchboard
    role's INSERT on fleet_case_links raises the same
    InsufficientPrivilegeError as open_case's, so the MCP tool layer's
    record_case_link route-forwarding is load-bearing here too."""
    switchboard_conn = await _role_conn(db_url, "butler_switchboard_rw")
    health_conn = await _role_conn(db_url, "butler_health_rw")
    try:
        case = await open_case(switchboard_conn, correlation_key="test:link-rls-refusal-key")
        case_id = str(case["id"])

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await write_case_link(
                health_conn, case_id=case_id, link_kind="insight_candidate", ref="candidate-1"
            )

        # Confirmed refused at the database, not silently dropped.
        count = await switchboard_conn.fetchval(
            "SELECT count(*) FROM public.fleet_case_links WHERE case_id = $1", case["id"]
        )
        assert count == 0
    finally:
        await switchboard_conn.close()
        await health_conn.close()
        await _delete_case(bootstrap_url, "test:link-rls-refusal-key")


async def test_lapse_sweep_only_closes_genuinely_stale_silent_or_routine_cases(
    db_url: str, bootstrap_url: str
) -> None:
    """RFC 0032 Slice 5. One sweep, five seeded cases, each pinning one
    eligibility rule: staleness lapses, freshness (via evidence or a
    posture/state update) spares, urgent/active postures are never touched
    regardless of age, and an already-closed case is never resurrected."""
    now = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    stale_cutoff_breach = now - timedelta(days=10)
    within_window = now - timedelta(hours=1)

    switchboard_conn = await _role_conn(db_url, "butler_switchboard_rw")
    keys = {
        "stale": "test:lapse-stale-silent",
        "fresh_evidence": "test:lapse-fresh-evidence",
        "fresh_posture": "test:lapse-fresh-posture-update",
        "urgent": "test:lapse-urgent-never-closes",
        "already_closed": "test:lapse-already-closed",
    }
    try:
        stale_id = await _seed_case(
            bootstrap_url,
            correlation_key=keys["stale"],
            posture="silent",
            updated_at=stale_cutoff_breach,
        )
        fresh_evidence_id = await _seed_case(
            bootstrap_url,
            correlation_key=keys["fresh_evidence"],
            posture="routine",
            updated_at=stale_cutoff_breach,
        )
        await _seed_evidence(bootstrap_url, case_id=fresh_evidence_id, contributed_at=within_window)
        fresh_posture_id = await _seed_case(
            bootstrap_url,
            correlation_key=keys["fresh_posture"],
            posture="routine",
            updated_at=within_window,
        )
        urgent_id = await _seed_case(
            bootstrap_url,
            correlation_key=keys["urgent"],
            posture="urgent",
            updated_at=stale_cutoff_breach,
        )
        already_closed_id = await _seed_case(
            bootstrap_url,
            correlation_key=keys["already_closed"],
            state="closed",
            posture="silent",
            outcome="resolved",
            updated_at=stale_cutoff_breach,
            closed_at=stale_cutoff_breach,
        )

        result = await run_lapse_sweep(switchboard_conn, now=now)

        assert result == {"lapsed_case_ids": [stale_id], "lapsed_count": 1}

        stale_row = await switchboard_conn.fetchrow(
            "SELECT state, outcome, closed_at FROM public.fleet_cases WHERE id = $1", stale_id
        )
        assert stale_row["state"] == "closed"
        assert stale_row["outcome"] == "lapsed"
        # chk_fleet_cases_closed_needs_outcome requires both non-NULL together
        # for state='closed' -- the write already had to satisfy this or the
        # UPDATE itself would have raised a CheckViolationError.
        assert stale_row["closed_at"] is not None

        for spared_id in (fresh_evidence_id, fresh_posture_id, urgent_id):
            spared_row = await switchboard_conn.fetchrow(
                "SELECT state, outcome FROM public.fleet_cases WHERE id = $1", spared_id
            )
            assert spared_row["state"] == "open"
            assert spared_row["outcome"] is None

        closed_row = await switchboard_conn.fetchrow(
            "SELECT state, outcome, closed_at FROM public.fleet_cases WHERE id = $1",
            already_closed_id,
        )
        assert closed_row["state"] == "closed"
        assert closed_row["outcome"] == "resolved"
        assert closed_row["closed_at"] == stale_cutoff_breach

        rerun = await run_lapse_sweep(switchboard_conn, now=now + timedelta(days=1))
        assert rerun == {"lapsed_case_ids": [], "lapsed_count": 0}
    finally:
        await switchboard_conn.close()
        for key in keys.values():
            await _delete_case(bootstrap_url, key)


async def test_backfill_historical_case_is_idempotent_against_real_postgres(
    db_url: str, bootstrap_url: str
) -> None:
    """Requirement (1)+(2)+(3): a direct call succeeds only because the write
    already satisfies chk_fleet_cases_closed_needs_outcome (Postgres would
    raise a CheckViolationError otherwise), and a second call with the exact
    same correlation_key is a no-op rather than a duplicate row."""
    key = "test:backfill-historical-case-direct"
    opened_at = datetime(2026, 1, 1, tzinfo=UTC)
    closed_at = datetime(2026, 1, 5, tzinfo=UTC)

    switchboard_conn = await _role_conn(db_url, "butler_switchboard_rw")
    try:
        first = await backfill_historical_case(
            switchboard_conn,
            correlation_key=key,
            outcome="resolved",
            opened_at=opened_at,
            closed_at=closed_at,
        )
        assert first is not None
        assert first["state"] == "closed"
        assert first["outcome"] == "resolved"
        assert first["closed_at"] is not None

        rerun = await backfill_historical_case(
            switchboard_conn,
            correlation_key=key,
            outcome="resolved",
            opened_at=opened_at,
            closed_at=closed_at,
        )
        assert rerun is None

        count = await switchboard_conn.fetchval(
            "SELECT count(*) FROM public.fleet_cases WHERE correlation_key = $1", key
        )
        assert count == 1
    finally:
        await switchboard_conn.close()
        await _delete_case(bootstrap_url, key)


async def test_closed_backfilled_row_can_share_a_correlation_key_with_an_active_case(
    bootstrap_url: str,
) -> None:
    """Requirement (3), non-collision half: uq_fleet_cases_active_correlation_key
    is a partial index scoped to ``state <> 'closed'``. Since the backfill
    only ever writes ``state='closed'``, a closed historical row must be able
    to coexist with an active case sharing the same correlation_key --
    confirmed here at the database rather than merely asserted from the
    index's WHERE clause."""
    key = "test:backfill-collision-key"
    now = datetime(2026, 9, 6, tzinfo=UTC)
    active_id = await _seed_case(bootstrap_url, correlation_key=key, updated_at=now, state="open")
    try:
        closed_id = await _seed_case(
            bootstrap_url,
            correlation_key=key,
            updated_at=now,
            state="closed",
            outcome="resolved",
            closed_at=now,
        )
        assert closed_id != active_id

        conn = await asyncpg.connect(bootstrap_url)
        try:
            rows = await conn.fetch(
                "SELECT id, state FROM public.fleet_cases WHERE correlation_key = $1", key
            )
        finally:
            await conn.close()
        assert {row["state"] for row in rows} == {"open", "closed"}
    finally:
        await _delete_case(bootstrap_url, key)


async def test_backfill_from_owner_conditions_creates_closed_cases_and_reruns_idempotently(
    db_url: str, bootstrap_url: str
) -> None:
    """RFC 0032 Slice 6 end-to-end: a resolved owner_conditions episode
    becomes one closed fleet case with the resolution_reason as outcome; an
    unresolved episode is left untouched; rerunning creates no duplicate."""
    source = "test:backfill-source"
    detected = datetime(2026, 1, 1, tzinfo=UTC)
    resolved = datetime(2026, 1, 10, tzinfo=UTC)
    resolved_key = f"{BACKFILL_CORRELATION_KEY_PREFIX}{source}:resolved-fp:1"
    open_key = f"{BACKFILL_CORRELATION_KEY_PREFIX}{source}:open-fp:1"

    switchboard_conn = await _role_conn(db_url, "butler_switchboard_rw")
    try:
        episode_id = await _seed_owner_condition(
            bootstrap_url,
            source=source,
            fingerprint="resolved-fp",
            episode=1,
            first_detected_at=detected,
            state="resolved",
            resolved_at=resolved,
            recovered_after_s=777600.0,
            metadata={"resolution_reason": "bill_paid"},
        )
        await _seed_owner_condition(
            bootstrap_url,
            source=source,
            fingerprint="open-fp",
            episode=1,
            first_detected_at=detected,
            state="open",
            resolved_at=None,
            recovered_after_s=None,
            metadata=None,
        )

        result = await backfill_from_owner_conditions(switchboard_conn)

        created_row = await switchboard_conn.fetchrow(
            "SELECT id, state, outcome, opened_at, closed_at FROM public.fleet_cases "
            "WHERE correlation_key = $1",
            resolved_key,
        )
        assert created_row is not None
        assert created_row["state"] == "closed"
        assert created_row["outcome"] == "bill_paid"
        assert created_row["opened_at"] == detected
        assert created_row["closed_at"] == resolved
        assert str(created_row["id"]) in result["created_case_ids"]

        never_created = await switchboard_conn.fetchrow(
            "SELECT 1 FROM public.fleet_cases WHERE correlation_key = $1", open_key
        )
        assert never_created is None

        # RFC 0032 Slice 7: the backfill also binds the case to the source
        # episode via fleet_case_links -- surfaced through read_case, not
        # just queryable directly.
        full_case = await read_case(switchboard_conn, str(created_row["id"]))
        assert full_case is not None
        assert len(full_case["links"]) == 1
        assert full_case["links"][0]["link_kind"] == "owner_condition"
        assert full_case["links"][0]["ref"] == episode_id

        rerun = await backfill_from_owner_conditions(switchboard_conn)
        assert str(created_row["id"]) not in rerun["created_case_ids"]

        count = await switchboard_conn.fetchval(
            "SELECT count(*) FROM public.fleet_cases WHERE correlation_key = $1", resolved_key
        )
        assert count == 1
        link_count = await switchboard_conn.fetchval(
            "SELECT count(*) FROM public.fleet_case_links WHERE case_id = $1",
            created_row["id"],
        )
        assert link_count == 1
    finally:
        await switchboard_conn.close()
        await _delete_owner_conditions(bootstrap_url, source=source)
        await _delete_case(bootstrap_url, resolved_key)


async def test_evaluate_case_attention_concurrent_same_dedup_key_records_exactly_one_bypass(
    pool: asyncpg.Pool,
) -> None:
    """bu-zss8w: evaluate_case_attention()'s check-then-insert against
    public.attention_ledger used to be a TOCTOU race -- two truly concurrent
    calls for the same correlation_key could both pass the "already
    bypassed?" check before either committed its insert, breaking quiet
    hours twice for one case. The fix wraps the check+insert in one
    transaction holding pg_advisory_xact_lock(hashtext(dedup_key)), so
    same-key calls serialize.

    Proven the same way as
    tests/integration/test_day_close_writer_concurrency.py: an external
    connection takes the exact same advisory lock first and holds it open,
    both evaluate_case_attention() calls are launched as real concurrent
    tasks, and the test waits until Postgres itself reports two blocked
    (not-granted) advisory-lock waiters before releasing the blocker -- so
    this proves genuine lock contention, not a lucky asyncio interleaving.
    """
    # 04:00 Asia/Singapore (UTC+8), inside the 23:00-08:00 default quiet-hours
    # window core_160 seeds on a fresh install (see
    # tests/integration/test_attention_ledger_roundtrip.py).
    now = datetime(2026, 9, 6, 20, 0, tzinfo=UTC)
    correlation_key = "test:concurrent-bypass-key"
    dedup_key = case_attention_dedup_key(correlation_key)
    case_id = "33333333-3333-3333-3333-333333333333"

    call_a: asyncio.Task | None = None
    call_b: asyncio.Task | None = None
    try:
        async with pool.acquire() as blocker:
            async with blocker.transaction():
                await blocker.execute("SELECT pg_advisory_xact_lock(hashtext($1))", dedup_key)

                call_a = asyncio.create_task(
                    evaluate_case_attention(
                        pool,
                        case_id=case_id,
                        correlation_key=correlation_key,
                        posture="urgent",
                        state="open",
                        origin_butler="health",
                        now=now,
                    )
                )
                call_b = asyncio.create_task(
                    evaluate_case_attention(
                        pool,
                        case_id=case_id,
                        correlation_key=correlation_key,
                        posture="urgent",
                        state="open",
                        origin_butler="finance",
                        now=now,
                    )
                )

                async with asyncio.timeout(5):
                    while (
                        await pool.fetchval(
                            """
                            SELECT count(*)
                            FROM pg_locks
                            WHERE locktype = 'advisory' AND NOT granted
                            """
                        )
                        < 2
                    ):
                        await asyncio.sleep(0.01)

                assert not call_a.done()
                assert not call_b.done()

        results = await asyncio.gather(call_a, call_b)

        bypassed = [r for r in results if r["bypass"] is True]
        already_bypassed = [r for r in results if r["reason"] == "already_bypassed_this_window"]
        assert len(bypassed) == 1
        assert len(already_bypassed) == 1
        assert bypassed[0]["attention_ledger_id"] is not None

        ledger_count = await pool.fetchval(
            "SELECT count(*) FROM public.attention_ledger WHERE dedup_key = $1", dedup_key
        )
        assert ledger_count == 1
    finally:
        for task in (call_a, call_b):
            if task is not None and not task.done():
                task.cancel()
        await pool.execute("DELETE FROM public.attention_ledger WHERE dedup_key = $1", dedup_key)


async def test_evaluate_case_attention_different_dedup_keys_do_not_contend(
    pool: asyncpg.Pool,
) -> None:
    """Different correlation_keys hash to different advisory-lock keys, so a
    held lock for one case must never delay a concurrent call for another."""
    now = datetime(2026, 9, 6, 20, 0, tzinfo=UTC)  # 04:00 SGT -- inside quiet hours
    held_key = "test:advisory-lock-holder-key"
    other_key = "test:advisory-lock-unrelated-key"
    other_dedup_key = case_attention_dedup_key(other_key)

    try:
        async with pool.acquire() as blocker:
            async with blocker.transaction():
                await blocker.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    case_attention_dedup_key(held_key),
                )

                async with asyncio.timeout(2):
                    result = await evaluate_case_attention(
                        pool,
                        case_id="44444444-4444-4444-4444-444444444444",
                        correlation_key=other_key,
                        posture="urgent",
                        state="open",
                        origin_butler="health",
                        now=now,
                    )

        assert result["bypass"] is True
    finally:
        await pool.execute(
            "DELETE FROM public.attention_ledger WHERE dedup_key = $1", other_dedup_key
        )
