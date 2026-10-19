"""Real-Postgres: premise amendments bind to the resolved ledger (bu-q7vx1q.32).

Drives the real migration chain (core_273), the real ``resolve_condition`` facade and the
real broker pass:

- an explicit resolution enqueues exactly one amendment, and resolving again adds none;
- a runtime role calling the definer for an unresolved condition, or with a ``resolved_at``
  that differs from the ledger row's, writes nothing;
- deleting a candidate leaves its amendment, and the delivery pass skips it without error.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.core.insight_premise import owner_condition_premise
from butlers.core.owner_conditions import (
    Observation,
    compute_fingerprint,
    reconcile_snapshot,
    resolve_condition,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.switchboard.insight.broker import (
    apply_pending_amendments,
    propose_insight_candidate,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_SOURCE = "finance:bill-overdue"
_COUNT = "SELECT count(*) FROM public.insight_amendments"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=2, max_size=6)
    await p.execute("TRUNCATE public.insight_amendments")
    yield p
    await p.close()


async def _delivered_candidate(pool, name: str) -> tuple[str, object]:
    fp = compute_fingerprint(_SOURCE, 1, {"bill_id": name})
    dedup_key = f"finance:bill-due:{name}"
    proposed = await propose_insight_candidate(
        pool,
        origin_butler="finance",
        priority=50,
        category="bill-due",
        dedup_key=dedup_key,
        message="Synthetic bill remains overdue",
        expires_at=datetime.now(UTC) + timedelta(days=1),
        premise=owner_condition_premise(_SOURCE, fp),
    )
    assert proposed["status"] == "accepted"
    candidate_id = await pool.fetchval(
        "UPDATE public.insight_candidates SET status = 'delivered', "
        "delivered_at = now() - interval '1 minute' WHERE dedup_key = $1 RETURNING id",
        dedup_key,
    )
    await reconcile_snapshot(
        pool,
        source=_SOURCE,
        observations=[Observation(fingerprint=fp)],
        snapshot_complete=False,
        initial_grace_seconds=3600,
    )
    return fp, candidate_id


async def _as_runtime_role(pool, sql: str, *args):
    async with pool.acquire() as conn:
        try:
            await conn.execute("SET ROLE butler_finance_rw")
        except asyncpg.PostgresError:
            pytest.skip("runtime role butler_finance_rw unavailable in this fixture")
        try:
            return await conn.fetchval(sql, *args)
        finally:
            await conn.execute("RESET ROLE")


async def test_explicit_resolution_queues_one_amendment_and_a_repeat_adds_none(pool) -> None:
    fp, candidate_id = await _delivered_candidate(pool, "explicit")

    first = await resolve_condition(pool, source=_SOURCE, fingerprint=fp)
    second = await resolve_condition(pool, source=_SOURCE, fingerprint=fp)

    assert first is not None and first.transition == "resolved"
    assert second is None
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM public.insight_amendments WHERE candidate_id = $1", candidate_id
        )
        == 1
    )


async def test_a_forged_enqueue_for_an_unresolved_condition_writes_nothing(pool) -> None:
    fp, _candidate = await _delivered_candidate(pool, "unresolved")

    queued = await _as_runtime_role(
        pool,
        "SELECT public.enqueue_premise_amendments($1, $2, now(), 'forged')",
        _SOURCE,
        fp,
    )

    assert queued == 0
    assert await pool.fetchval(_COUNT) == 0


async def test_a_forged_resolved_at_writes_nothing_and_the_real_one_uses_the_ledger_value(
    pool,
) -> None:
    fp, candidate_id = await _delivered_candidate(pool, "wrong-time")
    transition = await resolve_condition(pool, source=_SOURCE, fingerprint=fp)
    assert transition is not None and transition.resolved_at is not None
    await pool.execute("TRUNCATE public.insight_amendments")

    forged = await _as_runtime_role(
        pool,
        "SELECT public.enqueue_premise_amendments($1, $2, $3, 'forged')",
        _SOURCE,
        fp,
        transition.resolved_at + timedelta(seconds=1),
    )
    assert forged == 0
    assert await pool.fetchval(_COUNT) == 0

    genuine = await _as_runtime_role(
        pool,
        "SELECT public.enqueue_premise_amendments($1, $2, $3, 'genuine')",
        _SOURCE,
        fp,
        transition.resolved_at,
    )
    assert genuine == 1
    key = await pool.fetchval(
        "SELECT episode_key FROM public.insight_amendments WHERE candidate_id = $1", candidate_id
    )
    ledger_resolved_at = await pool.fetchval(
        "SELECT resolved_at::text FROM public.owner_conditions "
        "WHERE source = $1 AND fingerprint = $2",
        _SOURCE,
        fp,
    )
    assert key == f"{fp}@{ledger_resolved_at}"


async def test_deleting_a_candidate_keeps_its_amendment_and_the_cycle_skips_it(pool) -> None:
    fp, candidate_id = await _delivered_candidate(pool, "deleted")
    await resolve_condition(pool, source=_SOURCE, fingerprint=fp)
    assert await pool.fetchval(_COUNT) == 1

    await pool.execute("DELETE FROM public.insight_candidates WHERE id = $1", candidate_id)

    assert await pool.fetchval(_COUNT) == 1
    amend_fn = AsyncMock()
    counts = await apply_pending_amendments(pool, amend_fn, now=datetime.now(UTC))
    assert counts == {"applied": 0, "folded": 0, "retry": 0}
    amend_fn.assert_not_awaited()
    assert await pool.fetchval(_COUNT) == 1
