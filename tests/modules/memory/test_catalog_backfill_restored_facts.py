"""Real-DB regression: the catalog backfill revives rows whose fact was restored (bu-l5l9ln).

A catalog row staled while its fact was expired used to stay stale after the fact was restored
(health_003), because the backfill only inserts missing rows. The backfill now clears
``invalid_at`` and restores ``confidence`` for rows whose fact is live again, and leaves rows
whose fact is still terminal untouched.
"""

from __future__ import annotations

import asyncio
import shutil
from unittest.mock import MagicMock

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.migrations import run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _engine() -> MagicMock:
    engine = MagicMock()
    engine.embed.return_value = [0.0] * 384
    engine.model_name = "test-model"
    return engine


async def _scenario(db_url: str) -> dict:
    from butlers.modules.memory.storage import run_memory_catalog_backfill, store_fact

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3, init=register_jsonb_codec)
    try:

        async def store(subject: str) -> object:
            fact = await store_fact(
                pool,
                subject=subject,
                predicate="measurement",
                content="70kg",
                embedding_engine=_engine(),
                scope="global",
                tenant_id="shared",
                source_butler="health",
                enable_shared_catalog=True,
                source_schema="public",
            )
            return fact["id"]

        restored = await store("restored_fact")
        await pool.execute("UPDATE facts SET confidence = 0.8 WHERE id = $1", restored)
        still_expired = await store("still_expired_fact")
        for fact_id in (restored, still_expired):
            await pool.execute("UPDATE facts SET validity = 'expired' WHERE id = $1", fact_id)
        # A fact reclassified after cataloging, as the health symptom-edit path does: the
        # sensitivity change and the catalog row staling land in one statement, and the fact
        # stays active. Its stale row must not be revived with the pre-classification summary.
        reclassified = await store("reclassified_fact")
        first = await run_memory_catalog_backfill(pool, source_schema="public", batch_size=500)
        await pool.execute(
            "WITH updated AS (UPDATE facts SET sensitivity = 'confidential' WHERE id = $1"
            " RETURNING id)"
            " UPDATE public.memory_catalog AS mc SET confidence = 0, invalid_at = now(),"
            " updated_at = now() FROM updated WHERE mc.source_schema = 'public'"
            " AND mc.source_table = 'facts' AND mc.source_id = updated.id",
            reclassified,
        )
        # The restore: validity flips back to active while the catalog row stays stale.
        await pool.execute("UPDATE facts SET validity = 'active' WHERE id = $1", restored)

        async def row(fact_id: object) -> dict:
            record = await pool.fetchrow(
                "SELECT confidence, invalid_at, updated_at FROM public.memory_catalog"
                " WHERE source_schema = 'public' AND source_table = 'facts' AND source_id = $1",
                fact_id,
            )
            return dict(record)

        before = {
            "restored": await row(restored),
            "expired": await row(still_expired),
            "reclassified": await row(reclassified),
        }
        second = await run_memory_catalog_backfill(pool, source_schema="public", batch_size=500)
        after = {
            "restored": await row(restored),
            "expired": await row(still_expired),
            "reclassified": await row(reclassified),
        }
        third = await run_memory_catalog_backfill(pool, source_schema="public", batch_size=500)
        final = {
            "restored": await row(restored),
            "expired": await row(still_expired),
            "reclassified": await row(reclassified),
        }
        return {
            "first": first,
            "second": second,
            "third": third,
            "before": before,
            "after": after,
            "final": final,
        }
    finally:
        await pool.close()


@pytest.fixture(scope="module")
def outcome(postgres_container) -> dict:
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="memory"))
    return asyncio.run(_scenario(db_url))


def test_a_stale_row_is_stale_before_the_repair(outcome: dict) -> None:
    assert outcome["before"]["restored"]["invalid_at"] is not None
    assert outcome["before"]["restored"]["confidence"] == 0


def test_a_restored_fact_gets_a_live_catalog_row(outcome: dict) -> None:
    assert outcome["second"]["facts_refreshed"] == 1
    assert outcome["after"]["restored"]["invalid_at"] is None
    assert outcome["after"]["restored"]["confidence"] == pytest.approx(0.8)


def test_a_fact_that_is_still_expired_keeps_its_stale_row(outcome: dict) -> None:
    assert outcome["after"]["expired"] == outcome["before"]["expired"]
    assert outcome["after"]["expired"]["invalid_at"] is not None


def test_repeated_backfills_change_nothing_more(outcome: dict) -> None:
    assert outcome["third"]["facts_refreshed"] == 0
    assert outcome["third"]["facts_reconciled"] == 0
    assert outcome["final"] == outcome["after"]


def test_a_reclassified_fact_keeps_its_stale_row(outcome: dict) -> None:
    assert outcome["before"]["reclassified"]["invalid_at"] is not None
    assert outcome["after"]["reclassified"] == outcome["before"]["reclassified"]
    assert outcome["after"]["reclassified"]["confidence"] == 0
    assert outcome["second"]["facts_refreshed"] == 1
