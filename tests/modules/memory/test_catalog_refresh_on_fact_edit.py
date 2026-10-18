"""Real-DB regression: an in-place Health edit refreshes the fact's catalog row (bu-1lh59i).

``measurement_update`` and ``meal_update`` rewrite the fact row with a plain UPDATE, which
bypasses ``store_fact``'s catalog write-behind. The catalog row used to keep the pre-edit
summary and embedding, so cross-butler search returned the superseded value. The edit now
refreshes the row; a fact excluded by sensitivity never gets a row refreshed or created.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
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

_SELECT = (
    "SELECT summary, embedding::text AS embedding, invalid_at, confidence"
    " FROM public.memory_catalog"
    " WHERE source_schema = 'public' AND source_table = 'facts' AND source_id = $1"
)


_DECOY = (
    "SELECT summary, embedding::text AS embedding, invalid_at, confidence"
    " FROM public.memory_catalog"
    " WHERE source_schema = 'other_butler' AND source_table = 'facts' AND source_id = $1"
)


def _engine() -> MagicMock:
    """Deterministic embeddings: the vector encodes the text length, so an edit changes it."""
    engine = MagicMock()
    engine.embed.side_effect = lambda text: [len(text) / 1000.0] + [0.0] * 383
    engine.model_name = "test-model"
    return engine


async def _scenario(db_url: str) -> dict:
    from butlers.modules.memory.storage import store_fact
    from butlers.tools.health import diet, measurements

    engine = _engine()
    measurements._get_embedding_engine = lambda: engine  # type: ignore[assignment]
    diet._get_embedding_engine = lambda: engine  # type: ignore[assignment]
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3, init=register_jsonb_codec)
    try:

        async def store(predicate: str, content: str, *, cataloged: bool = True, **kwargs):
            fact = await store_fact(
                pool,
                subject="owner",
                predicate=predicate,
                content=content,
                embedding_engine=engine,
                scope="health",
                valid_at=datetime.now(UTC),
                tenant_id="shared",
                source_butler="health",
                enable_shared_catalog=cataloged,
                source_schema="public",
                **kwargs,
            )
            return fact["id"]

        async def row(fact_id) -> dict | None:
            record = await pool.fetchrow(_SELECT, fact_id)
            return dict(record) if record else None

        out: dict = {}

        measurement = await store(
            "measurement_weight", "weight: 70 kg", metadata={"value": 70, "source": "owner_log"}
        )
        out["measurement_before"] = await row(measurement)
        await measurements.measurement_update(pool, str(measurement), value=100.5)
        out["measurement_after"] = await row(measurement)

        meal = await store("meal_lunch", "chicken salad", metadata={})
        out["meal_before"] = await row(meal)
        await diet.meal_update(pool, str(meal), description="tuna salad with rice")
        out["meal_after"] = await row(meal)

        # A fact classified confidential never has a catalog row: the edit must not make one.
        secret = await store(
            "measurement_weight",
            "weight: 70 kg",
            metadata={"value": 70},
            sensitivity="confidential",
        )
        out["secret_before"] = await row(secret)
        await measurements.measurement_update(pool, str(secret), value=71)
        out["secret_after"] = await row(secret)

        # A fact reclassified after cataloging: the edit stales the row, never refreshes it.
        reclassified = await store("measurement_weight", "weight: 70 kg", metadata={"value": 70})
        await pool.execute(
            "UPDATE facts SET sensitivity = 'confidential' WHERE id = $1", reclassified
        )
        out["reclassified_before"] = await row(reclassified)
        await measurements.measurement_update(pool, str(reclassified), value=73)
        out["reclassified_after"] = await row(reclassified)

        # Another butler's catalog row that happens to share the fact's source_id is not ours:
        # neither a refresh nor a stale-marking edit may touch it.
        async def decoy_of(fact_id) -> dict:
            await pool.execute(
                "INSERT INTO public.memory_catalog (source_schema, source_table, source_id,"
                " source_butler, tenant_id, summary, embedding, search_vector, memory_type)"
                " SELECT 'other_butler', source_table, source_id, source_butler, tenant_id,"
                " summary, embedding, search_vector, memory_type FROM public.memory_catalog"
                " WHERE source_schema = 'public' AND source_table = 'facts' AND source_id = $1",
                fact_id,
            )
            return dict(await pool.fetchrow(_DECOY, fact_id))

        shared = await store("measurement_weight", "weight: 70 kg", metadata={"value": 70})
        out["decoy_live_before"] = await decoy_of(shared)
        await measurements.measurement_update(pool, str(shared), value=75)
        out["decoy_live_after"] = dict(await pool.fetchrow(_DECOY, shared))
        out["decoy_live_own"] = await row(shared)

        shared_stale = await store("measurement_weight", "weight: 70 kg", metadata={"value": 70})
        await pool.execute(
            "UPDATE facts SET sensitivity = 'confidential' WHERE id = $1", shared_stale
        )
        out["decoy_stale_before"] = await decoy_of(shared_stale)
        await measurements.measurement_update(pool, str(shared_stale), value=76)
        out["decoy_stale_after"] = dict(await pool.fetchrow(_DECOY, shared_stale))
        out["decoy_stale_own"] = await row(shared_stale)

        # A fact with no catalog row yet is left to the backfill: the edit creates none.
        uncataloged = await store(
            "measurement_weight", "weight: 70 kg", cataloged=False, metadata={"value": 70}
        )
        await measurements.measurement_update(pool, str(uncataloged), value=74)
        out["uncataloged_after"] = await row(uncataloged)

        # A stale row of an edited fact carries the new content, so reviving it is not stale.
        stale = await store("meal_dinner", "soup", metadata={})
        await pool.execute(
            "UPDATE public.memory_catalog SET confidence = 0, invalid_at = now()"
            " WHERE source_table = 'facts' AND source_id = $1",
            stale,
        )
        await diet.meal_update(pool, str(stale), description="stew")
        out["stale_after"] = await row(stale)
        return out
    finally:
        await pool.close()


@pytest.fixture(scope="module")
def outcome(postgres_container) -> dict:
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="memory"))
    return asyncio.run(_scenario(db_url))


def test_a_measurement_edit_updates_the_catalog_summary(outcome: dict) -> None:
    assert outcome["measurement_before"]["summary"] == "owner measurement_weight: weight: 70 kg"
    assert outcome["measurement_after"]["summary"] == "owner measurement_weight: weight: 100.5 kg"


def test_a_measurement_edit_refreshes_the_catalog_embedding(outcome: dict) -> None:
    assert outcome["measurement_after"]["embedding"] != outcome["measurement_before"]["embedding"]


def test_a_meal_edit_updates_the_catalog_summary_and_embedding(outcome: dict) -> None:
    assert outcome["meal_before"]["summary"] == "owner meal_lunch: chicken salad"
    assert outcome["meal_after"]["summary"] == "owner meal_lunch: tuna salad with rice"
    assert outcome["meal_after"]["embedding"] != outcome["meal_before"]["embedding"]


def test_editing_a_confidential_fact_writes_no_catalog_row(outcome: dict) -> None:
    assert outcome["secret_before"] is None
    assert outcome["secret_after"] is None


def test_editing_a_reclassified_fact_stales_its_row_without_refreshing_it(outcome: dict) -> None:
    before, after = outcome["reclassified_before"], outcome["reclassified_after"]
    assert before["invalid_at"] is None
    assert after["invalid_at"] is not None and after["confidence"] == 0
    assert after["summary"] == before["summary"]


def test_editing_an_uncataloged_fact_creates_no_catalog_row(outcome: dict) -> None:
    assert outcome["uncataloged_after"] is None


def test_a_stale_row_gets_the_edited_content_and_stays_stale(outcome: dict) -> None:
    after = outcome["stale_after"]
    assert after["summary"] == "owner meal_dinner: stew"
    assert after["invalid_at"] is not None


def test_a_refresh_never_touches_another_schemas_row_with_the_same_source_id(outcome: dict) -> None:
    assert outcome["decoy_live_after"] == outcome["decoy_live_before"]
    assert outcome["decoy_live_own"]["summary"].endswith("weight: 75 kg")


def test_a_stale_marking_never_touches_another_schemas_row_with_the_same_source_id(
    outcome: dict,
) -> None:
    assert outcome["decoy_stale_after"] == outcome["decoy_stale_before"]
    assert outcome["decoy_stale_after"]["invalid_at"] is None
    assert outcome["decoy_stale_own"]["invalid_at"] is not None
