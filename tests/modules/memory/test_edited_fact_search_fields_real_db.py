"""Real-DB regression: in-place Health edits recompute the fact's own search fields (bu-0uso1z).

``measurement_update``, ``meal_update`` and ``symptom_update`` rewrite a fact's content with a
plain UPDATE. The fact's ``embedding`` and ``search_vector`` used to stay computed from the old
text, so memory search over facts kept matching the superseded wording. They are recomputed
from the edited content now, with a single embed call per edit.
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

_FIELDS = (
    "SELECT embedding::text AS embedding, embedding_model_version AS model,"
    " search_vector @@ plainto_tsquery('english', $2) AS hits_new,"
    " search_vector @@ plainto_tsquery('english', $3) AS hits_old"
    " FROM facts WHERE id = $1"
)


class _Engine:
    """Deterministic embeddings (the vector encodes the text length) that log every call."""

    model_name = "test-model"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return [len(text) / 1000.0] + [0.0] * 383


def _vector_text(text: str) -> str:
    return str([len(text) / 1000.0] + [0.0] * 383)


async def _scenario(db_url: str) -> dict:
    from butlers.modules.memory.storage import store_fact
    from butlers.tools.health import conditions, diet, measurements

    engine = _Engine()
    for module in (measurements, diet, conditions):
        module._get_embedding_engine = lambda: engine  # type: ignore[assignment]
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3, init=register_jsonb_codec)
    try:

        async def store(predicate: str, content: str, **kwargs):
            fact = await store_fact(
                pool,
                subject="owner",
                predicate=predicate,
                content=content,
                embedding_engine=MagicMock(embed=lambda t: [0.0] * 384, model_name="seed"),
                scope="health",
                valid_at=datetime.now(UTC),
                tenant_id="shared",
                source_butler="health",
                enable_shared_catalog=False,
                source_schema="public",
                **kwargs,
            )
            return fact["id"]

        async def fields(fact_id, new_word: str, old_word: str) -> dict:
            return dict(await pool.fetchrow(_FIELDS, fact_id, new_word, old_word))

        out: dict = {}

        meal = await store("meal_lunch", "chicken salad", metadata={})
        engine.calls.clear()
        await diet.meal_update(pool, str(meal), description="tuna pasta")
        out["meal_calls"] = list(engine.calls)
        out["meal"] = await fields(meal, "tuna", "chicken")

        measurement = await store(
            "measurement_weight", "weight: 70 kg", metadata={"value": 70, "source": "owner_log"}
        )
        engine.calls.clear()
        await measurements.measurement_update(pool, str(measurement), type="heart_rate", value=88)
        out["measurement_calls"] = list(engine.calls)
        out["measurement"] = await fields(measurement, "bpm", "weight")

        symptom = await store("symptom", "headache", metadata={"severity": 3})
        engine.calls.clear()
        await conditions.symptom_update(pool, str(symptom), name="migraine")
        out["symptom_calls"] = list(engine.calls)
        out["symptom"] = await fields(symptom, "migraine", "headache")
        return out
    finally:
        await pool.close()


@pytest.fixture(scope="module")
def outcome(postgres_container) -> dict:
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="memory"))
    return asyncio.run(_scenario(db_url))


def test_a_meal_edit_reindexes_the_fact_for_full_text_search(outcome: dict) -> None:
    assert outcome["meal"]["hits_new"] is True
    assert outcome["meal"]["hits_old"] is False


def test_a_meal_edit_re_embeds_the_new_content_once(outcome: dict) -> None:
    assert outcome["meal_calls"] == ["owner meal_lunch tuna pasta"]
    assert outcome["meal"]["embedding"] == _vector_text("owner meal_lunch tuna pasta")
    assert outcome["meal"]["model"] == "test-model"


def test_a_measurement_edit_reindexes_with_the_new_predicate(outcome: dict) -> None:
    assert outcome["measurement"]["hits_new"] is True
    assert outcome["measurement"]["hits_old"] is False


def test_a_measurement_edit_re_embeds_the_new_text_once(outcome: dict) -> None:
    assert outcome["measurement_calls"] == ["owner measurement_heart_rate heart_rate: 88 bpm"]
    assert outcome["measurement"]["embedding"] == _vector_text(
        "owner measurement_heart_rate heart_rate: 88 bpm"
    )


def test_a_symptom_rename_reindexes_and_re_embeds(outcome: dict) -> None:
    assert outcome["symptom"]["hits_new"] is True
    assert outcome["symptom"]["hits_old"] is False
    assert outcome["symptom_calls"] == ["owner symptom migraine"]
    assert outcome["symptom"]["embedding"] == _vector_text("owner symptom migraine")
