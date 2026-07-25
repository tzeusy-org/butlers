"""Integration: ``run_episode_cleanup`` is bounded and consolidation-aware.

These exercise the real DELETE SQL against a live PostgreSQL instance — the
mocked-pool unit tests elsewhere cannot verify the reap predicate or the
batching loop. The contract under test:

* an expired-but-*pending* episode within the grace window is retained, so a
  lagging consolidator never loses an un-extracted observation;
* a pending episode past the grace window IS reaped, so the table cannot grow
  without bound behind a broken consolidator;
* expired non-pending episodes (consolidated / failed / dead_letter) are reaped
  as soon as they expire;
* deletion drains a backlog larger than one batch (re-enabling the sweep on a
  large accumulated backlog never issues one unbounded delete);
* capacity trimming only ever removes *consolidated* episodes, in batches.
"""

from __future__ import annotations

import shutil

import pytest

from butlers.modules.memory.consolidation import (
    EPISODE_PENDING_GRACE_DAYS,
    run_episode_cleanup,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

# Minimal subset of the memory ``episodes`` schema that run_episode_cleanup
# touches: the reap predicate reads expires_at + consolidation_status, the
# capacity step reads consolidated + created_at.
_EPISODES_SQL = """
DROP TABLE IF EXISTS episodes;
CREATE TABLE episodes (
    id                   BIGSERIAL PRIMARY KEY,
    consolidated         BOOLEAN NOT NULL DEFAULT false,
    consolidation_status TEXT    NOT NULL DEFAULT 'pending',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at           TIMESTAMPTZ
);
"""


async def _insert(pool, *, status: str, consolidated: bool, expires_sql: str) -> int:
    """Insert one episode with an ``expires_at`` given as a raw SQL expression."""
    return await pool.fetchval(
        "INSERT INTO episodes (consolidation_status, consolidated, expires_at) "
        f"VALUES ($1, $2, {expires_sql}) RETURNING id",
        status,
        consolidated,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_cleanup_is_consolidation_aware(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_EPISODES_SQL)

        # Expired, pending, within grace → RETAINED (give consolidation a chance).
        keep_pending_grace = await _insert(
            pool, status="pending", consolidated=False, expires_sql="now() - interval '1 day'"
        )
        # Expired, pending, PAST grace → reaped (no unbounded growth behind a
        # stuck consolidator).
        reap_pending_stale = await _insert(
            pool,
            status="pending",
            consolidated=False,
            expires_sql=f"now() - interval '{EPISODE_PENDING_GRACE_DAYS + 1} days'",
        )
        # Expired, non-pending → reaped as soon as they expire.
        reap_consolidated = await _insert(
            pool, status="consolidated", consolidated=True, expires_sql="now() - interval '1 day'"
        )
        reap_failed = await _insert(
            pool, status="failed", consolidated=False, expires_sql="now() - interval '1 day'"
        )
        reap_dead = await _insert(
            pool, status="dead_letter", consolidated=False, expires_sql="now() - interval '1 day'"
        )
        # Not expired → retained regardless of status.
        keep_future = await _insert(
            pool, status="consolidated", consolidated=True, expires_sql="now() + interval '3 days'"
        )
        # No TTL → retained.
        keep_no_expiry = await _insert(
            pool, status="pending", consolidated=False, expires_sql="NULL"
        )

        result = await run_episode_cleanup(pool=pool)

        # stale-pending + consolidated + failed + dead_letter.
        assert result["expired_deleted"] == 4
        assert result["capacity_deleted"] == 0

        surviving = {r["id"] for r in await pool.fetch("SELECT id FROM episodes")}
        assert surviving == {keep_pending_grace, keep_future, keep_no_expiry}
        # Sanity: the reaped ids really are gone.
        for reaped in (reap_pending_stale, reap_consolidated, reap_failed, reap_dead):
            assert reaped not in surviving


@pytest.mark.asyncio(loop_scope="session")
async def test_cleanup_drains_backlog_larger_than_one_batch(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_EPISODES_SQL)

        # 2 300 expired, consolidated episodes — reapable and well over one
        # batch, so the loop must iterate to drain them all.
        await pool.execute(
            "INSERT INTO episodes (consolidation_status, consolidated, expires_at) "
            "SELECT 'consolidated', true, now() - interval '1 day' "
            "FROM generate_series(1, 2300)"
        )

        result = await run_episode_cleanup(pool=pool, batch_size=1000)

        assert result["expired_deleted"] == 2300
        assert result["remaining"] == 0
        assert await pool.fetchval("SELECT count(*) FROM episodes") == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_capacity_trims_only_consolidated_in_batches(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_EPISODES_SQL)

        # 50 consolidated + 10 pending, none expired. With max_entries=20 the
        # capacity step must delete 40 of the oldest *consolidated* rows and
        # never touch a pending (unconsolidated) episode.
        await pool.execute(
            "INSERT INTO episodes (consolidation_status, consolidated, expires_at, created_at) "
            "SELECT 'consolidated', true, now() + interval '5 days', "
            "       now() - (g || ' minutes')::interval "
            "FROM generate_series(1, 50) AS g"
        )
        await pool.execute(
            "INSERT INTO episodes (consolidation_status, consolidated, expires_at) "
            "SELECT 'pending', false, now() + interval '5 days' FROM generate_series(1, 10)"
        )

        result = await run_episode_cleanup(pool=pool, max_entries=20, batch_size=8)

        assert result["expired_deleted"] == 0
        assert result["capacity_deleted"] == 40
        assert result["remaining"] == 20
        assert await pool.fetchval("SELECT count(*) FROM episodes") == 20
        # The unconsolidated episodes are never deleted for capacity.
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM episodes WHERE consolidation_status = 'pending'"
            )
            == 10
        )
