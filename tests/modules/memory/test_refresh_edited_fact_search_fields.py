"""refresh_edited_fact_search_fields / refresh_edited_fact and their Health call sites (bu-0uso1z).

A plain ``UPDATE facts`` that rewrites ``content`` leaves the fact's own ``embedding`` and
``search_vector`` computed from the old text. The helper recomputes both exactly as
``store_fact`` does, once, and hands the vector to the catalog refresh.
"""

from __future__ import annotations

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.memory import storage
from butlers.modules.memory.search_vector import preprocess_text
from butlers.modules.memory.storage import (
    refresh_catalog_for_edited_fact,
    refresh_edited_fact,
    refresh_edited_fact_search_fields,
)

pytestmark = pytest.mark.unit

_ID = uuid.uuid4()
_VECTOR = [0.25] * 384


def _row(**overrides) -> dict:
    row = {"subject": "owner", "predicate": "meal_lunch", "content": "tuna salad"}
    row.update(overrides)
    return row


def _pool(row: dict | None) -> MagicMock:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=row)
    pool.execute = AsyncMock(return_value="UPDATE 1")
    pool.fetchval = AsyncMock(return_value="health")
    return pool


def _engine() -> MagicMock:
    engine = MagicMock()
    engine.embed.return_value = _VECTOR
    engine.model_name = "test-model"
    return engine


async def test_the_edited_content_is_embedded_and_indexed_like_store_fact() -> None:
    pool, engine = _pool(_row()), _engine()

    result = await refresh_edited_fact_search_fields(pool, _ID, embedding_engine=engine)

    assert result == _VECTOR
    engine.embed.assert_called_once_with("owner meal_lunch tuna salad")
    sql, fact_id, embedding, search_text, model = pool.execute.call_args[0]
    assert "UPDATE facts SET embedding = $2" in sql and "INSERT" not in sql
    assert "search_vector = to_tsvector(" in sql and "embedding_model_version = $4" in sql
    assert sql.rstrip().endswith("WHERE id = $1")
    assert fact_id == _ID
    assert embedding == str(_VECTOR)
    assert search_text == preprocess_text("owner meal_lunch tuna salad")
    assert model == "test-model"


async def test_the_new_predicate_is_part_of_the_indexed_text() -> None:
    pool, engine = _pool(_row(predicate="meal_dinner")), _engine()
    await refresh_edited_fact_search_fields(pool, _ID, embedding_engine=engine)
    engine.embed.assert_called_once_with("owner meal_dinner tuna salad")


async def test_a_missing_fact_is_a_quiet_noop(caplog) -> None:
    pool, engine = _pool(None), _engine()
    with caplog.at_level(logging.WARNING, logger=storage.logger.name):
        assert await refresh_edited_fact_search_fields(pool, _ID, embedding_engine=engine) is None
    assert caplog.records == []
    engine.embed.assert_not_called()
    pool.execute.assert_not_awaited()


async def test_a_failure_is_swallowed_and_logged_without_the_message(caplog) -> None:
    pool, engine = _pool(_row()), _engine()
    engine.embed.side_effect = RuntimeError("tuna salad leaked in the error text")

    with caplog.at_level(logging.WARNING, logger=storage.logger.name):
        assert await refresh_edited_fact_search_fields(pool, _ID, embedding_engine=engine) is None

    assert "RuntimeError" in caplog.text
    assert "leaked" not in caplog.text and "tuna" not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)
    pool.execute.assert_not_awaited()


async def test_the_combined_refresh_embeds_the_edit_once_and_shares_the_vector() -> None:
    fact = {
        "subject": "owner",
        "predicate": "meal_lunch",
        "content": "tuna salad",
        "scope": "health",
        "valid_at": None,
        "importance": 5.0,
        "retention_class": "operational",
        "sensitivity": "normal",
        "entity_id": None,
        "object_entity_id": None,
        "validity": "active",
    }
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=fact)
    pool.execute = AsyncMock(return_value="UPDATE 1")
    pool.fetchval = AsyncMock(return_value="health")
    engine = _engine()

    assert await refresh_edited_fact(pool, _ID, embedding_engine=engine) is True

    engine.embed.assert_called_once()
    facts_sql = pool.execute.await_args_list[0][0]
    catalog_sql = pool.execute.await_args_list[1][0]
    assert "UPDATE facts SET" in facts_sql[0]
    assert "UPDATE public.memory_catalog" in catalog_sql[0]
    assert facts_sql[2] == catalog_sql[3] == str(_VECTOR)


async def test_the_catalog_refresh_embeds_itself_when_the_fact_recompute_failed() -> None:
    pool, engine = _pool(None), _engine()
    with (
        patch.object(storage, "refresh_edited_fact_search_fields", AsyncMock(return_value=None)),
        patch.object(storage, "refresh_catalog_for_edited_fact", AsyncMock(return_value=True)) as c,
    ):
        assert await refresh_edited_fact(pool, _ID, embedding_engine=engine) is True
    c.assert_awaited_once_with(
        pool, _ID, embedding_engine=engine, engine_factory=None, embedding=None
    )


async def test_the_catalog_refresh_stores_a_given_embedding_without_embedding_again() -> None:
    fact = {
        "subject": "owner",
        "predicate": "p",
        "content": "c",
        "scope": "health",
        "valid_at": None,
        "importance": 5.0,
        "retention_class": "operational",
        "sensitivity": "normal",
        "entity_id": None,
        "object_entity_id": None,
        "validity": "active",
    }
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=fact)
    pool.execute = AsyncMock(return_value="UPDATE 1")
    pool.fetchval = AsyncMock(return_value="health")
    engine = _engine()

    await refresh_catalog_for_edited_fact(pool, _ID, embedding_engine=engine, embedding=[0.5] * 3)

    engine.embed.assert_not_called()
    assert pool.execute.call_args[0][3] == str([0.5] * 3)


class _EditPool:
    """A stateful fake pool: records every statement and applies the content edit to its row."""

    def __init__(self, row: dict) -> None:
        self.row = dict(row)
        self.sql: list[str] = []
        self.args: list[tuple] = []

    async def fetchval(self, sql: str, *args):
        return "health"

    async def fetchrow(self, sql: str, *args):
        return dict(self.row)

    async def execute(self, sql: str, *args):
        self.sql.append(sql)
        self.args.append(args)
        if sql.startswith("UPDATE facts SET predicate"):
            self.row.update(predicate=args[1], content=args[2])
        if sql.startswith("WITH updated AS"):
            self.row.update(content=args[1])
        return "UPDATE 1"

    def index(self, prefix: str) -> list[int]:
        return [i for i, s in enumerate(self.sql) if s.lstrip().startswith(prefix)]


def _edit_row(**overrides) -> dict:
    row = {
        "id": _ID,
        "subject": "owner",
        "predicate": "measurement_weight",
        "content": "weight: 70 kg",
        "valid_at": None,
        "created_at": None,
        "metadata": {"value": 70},
        "scope": "health",
        "importance": 5.0,
        "retention_class": "operational",
        "sensitivity": "normal",
        "entity_id": None,
        "object_entity_id": None,
        "validity": "active",
    }
    row.update(overrides)
    return row


def _assert_fields_then_catalog_after_edit(pool: _EditPool, engine: MagicMock, text: str) -> None:
    engine.embed.assert_called_once_with(text)
    (edit,) = pool.index("UPDATE facts SET predicate")
    (fields,) = pool.index("UPDATE facts SET embedding")
    (catalog,) = pool.index("UPDATE public.memory_catalog")
    assert edit < fields < catalog
    assert pool.args[fields][1] == pool.args[catalog][2] == str(_VECTOR)


async def test_a_measurement_edit_recomputes_the_fact_fields_then_the_catalog() -> None:
    from butlers.tools.health import measurements

    pool, engine = _EditPool(_edit_row()), _engine()
    with patch.object(measurements, "_get_embedding_engine", lambda: engine):
        await measurements.measurement_update(pool, str(_ID), value=72)

    _assert_fields_then_catalog_after_edit(pool, engine, "owner measurement_weight weight: 72 kg")


async def test_a_meal_edit_recomputes_the_fact_fields_then_the_catalog() -> None:
    from butlers.tools.health import diet

    pool = _EditPool(_edit_row(predicate="meal_lunch", content="salad", metadata={}))
    engine = _engine()
    with patch.object(diet, "_get_embedding_engine", lambda: engine):
        await diet.meal_update(pool, str(_ID), description="tuna salad")

    _assert_fields_then_catalog_after_edit(pool, engine, f"owner meal_lunch {pool.row['content']}")


async def test_a_symptom_rename_recomputes_the_fact_fields_and_leaves_its_retired_row() -> None:
    from butlers.tools.health import conditions

    pool = _EditPool(_edit_row(predicate="symptom", content="headache", metadata={"severity": 3}))
    engine = _engine()
    with patch.object(conditions, "_get_embedding_engine", lambda: engine):
        await conditions.symptom_update(pool, str(_ID), name="migraine")

    engine.embed.assert_called_once_with("owner symptom migraine")
    (edit,) = pool.index("WITH updated AS")
    (fields,) = pool.index("UPDATE facts SET embedding")
    assert edit < fields
    assert pool.index("UPDATE public.memory_catalog") == []


@pytest.mark.parametrize(
    ("module_name", "function", "kwargs", "row"),
    [
        ("measurements", "measurement_update", {"value": 72}, {}),
        (
            "diet",
            "meal_update",
            {"description": "tuna salad"},
            {"predicate": "meal_lunch", "content": "salad", "metadata": {}},
        ),
        (
            "conditions",
            "symptom_update",
            {"name": "migraine"},
            {"predicate": "symptom", "content": "headache", "metadata": {"severity": 3}},
        ),
    ],
    ids=["measurement", "meal", "symptom"],
)
async def test_an_engine_that_fails_to_load_never_fails_the_edit(
    module_name, function, kwargs, row
) -> None:
    import importlib

    module = importlib.import_module(f"butlers.tools.health.{module_name}")
    pool = _EditPool(_edit_row(**row))
    getter = MagicMock(side_effect=RuntimeError("model failed to load"))
    with patch.object(module, "_get_embedding_engine", getter):
        result = await getattr(module, function)(pool, str(_ID), **kwargs)

    assert result is not None  # the edit stands although the model could not load
    assert pool.index("UPDATE facts SET embedding") == []
    assert pool.index("UPDATE public.memory_catalog") == []
