"""refresh_catalog_for_edited_fact and its Health call sites, without a database (bu-1lh59i)."""

from __future__ import annotations

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.memory.storage import refresh_catalog_for_edited_fact

pytestmark = pytest.mark.unit

_ID = uuid.uuid4()


def _fact(**overrides) -> dict:
    row = {
        "subject": "owner",
        "predicate": "measurement_weight",
        "content": "weight: 72 kg",
        "scope": "health",
        "valid_at": None,
        "importance": 5.0,
        "retention_class": "operational",
        "sensitivity": "normal",
        "entity_id": None,
        "object_entity_id": None,
        "validity": "active",
    }
    row.update(overrides)
    return row


def _pool(fact: dict | None, status: str = "UPDATE 1") -> MagicMock:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=fact)
    pool.execute = AsyncMock(return_value=status)
    pool.fetchval = AsyncMock(return_value="health")
    return pool


def _engine() -> MagicMock:
    engine = MagicMock()
    engine.embed.return_value = [0.5] * 384
    return engine


async def test_a_live_fact_rewrites_summary_embedding_and_search_text() -> None:
    pool, engine = _pool(_fact()), _engine()

    assert await refresh_catalog_for_edited_fact(pool, _ID, embedding_engine=engine) is True

    engine.embed.assert_called_once_with("owner measurement_weight weight: 72 kg")
    sql, fact_id, summary, embedding, search_text, title, *args = pool.execute.call_args[0]
    assert "UPDATE public.memory_catalog" in sql and "INSERT" not in sql
    assert "invalid_at" not in sql  # a live refresh never changes staleness
    for assignment in ("summary = $2", "embedding = $3", "search_vector =", "title = $5"):
        assert assignment in sql
    assert fact_id == _ID
    assert "source_schema = $14" in sql and args[-1] == "health"
    assert summary == "owner measurement_weight: weight: 72 kg"
    assert embedding == str([0.5] * 384)
    assert "72" in search_text
    assert title == "owner measurement_weight"


@pytest.mark.parametrize("sensitivity", ["confidential", "pii"])
async def test_an_excluded_sensitivity_stales_the_row_instead_of_refreshing(sensitivity) -> None:
    pool, engine = _pool(_fact(sensitivity=sensitivity)), _engine()

    assert await refresh_catalog_for_edited_fact(pool, _ID, embedding_engine=engine) is True

    engine.embed.assert_not_called()
    sql = pool.execute.call_args[0][0]
    assert "confidence = 0" in sql and "invalid_at = now()" in sql and "summary" not in sql
    assert "source_schema = $2" in sql and pool.execute.call_args[0][2] == "health"


async def test_a_fact_that_is_no_longer_live_is_staled_not_refreshed() -> None:
    pool, engine = _pool(_fact(validity="retracted")), _engine()

    await refresh_catalog_for_edited_fact(pool, _ID, embedding_engine=engine)

    engine.embed.assert_not_called()
    assert "confidence = 0" in pool.execute.call_args[0][0]


async def test_a_fact_without_a_catalog_row_reports_false() -> None:
    pool = _pool(_fact(), status="UPDATE 0")

    assert await refresh_catalog_for_edited_fact(pool, _ID, embedding_engine=_engine()) is False


async def test_a_missing_fact_touches_nothing() -> None:
    pool = _pool(None)

    assert await refresh_catalog_for_edited_fact(pool, _ID, embedding_engine=_engine()) is False
    pool.execute.assert_not_called()


async def test_a_catalog_failure_is_logged_and_never_raised(caplog) -> None:
    pool = _pool(_fact())
    pool.execute = AsyncMock(side_effect=RuntimeError("relation does not exist"))

    with caplog.at_level(logging.WARNING):
        assert await refresh_catalog_for_edited_fact(pool, _ID, embedding_engine=_engine()) is False

    assert any("failed to refresh catalog entry" in r.getMessage() for r in caplog.records)


class _EditPool:
    """A stateful fake pool: records every statement and applies the facts edit to its row."""

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
        return "UPDATE 1"


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


def _assert_catalog_refreshed_after_edit(pool: _EditPool, engine: MagicMock, text: str) -> None:
    engine.embed.assert_called_once_with(text)
    edit = next(i for i, s in enumerate(pool.sql) if s.startswith("UPDATE facts SET predicate"))
    catalog = [i for i, s in enumerate(pool.sql) if "public.memory_catalog" in s]
    assert len(catalog) == 1 and catalog[0] > edit


async def test_measurement_edit_refreshes_the_catalog_from_the_edited_text() -> None:
    from butlers.tools.health import measurements

    pool, engine = _EditPool(_edit_row()), _engine()
    with patch.object(measurements, "_get_embedding_engine", lambda: engine):
        await measurements.measurement_update(pool, str(_ID), value=72)

    _assert_catalog_refreshed_after_edit(pool, engine, "owner measurement_weight weight: 72 kg")


async def test_meal_edit_refreshes_the_catalog_from_the_edited_text() -> None:
    from butlers.tools.health import diet

    pool = _EditPool(_edit_row(predicate="meal_lunch", content="salad", metadata={}))
    engine = _engine()
    with patch.object(diet, "_get_embedding_engine", lambda: engine):
        await diet.meal_update(pool, str(_ID), description="tuna salad")

    _assert_catalog_refreshed_after_edit(pool, engine, f"owner meal_lunch {pool.row['content']}")


async def test_an_excluded_edit_never_makes_the_catalog_step_embed() -> None:
    from butlers.tools.health import measurements

    pool, engine = _EditPool(_edit_row(sensitivity="confidential")), _engine()
    with patch.object(measurements, "_get_embedding_engine", lambda: engine):
        await measurements.measurement_update(pool, str(_ID), value=72)

    catalog = [(s, a) for s, a in zip(pool.sql, pool.args) if "public.memory_catalog" in s]
    assert len(catalog) == 1
    sql, args = catalog[0]
    assert "confidence = 0" in sql and "embedding" not in sql
    assert str(engine.embed.return_value) not in args


async def test_the_engine_factory_is_not_called_when_the_row_is_staled() -> None:
    pool, factory = _pool(_fact(sensitivity="confidential")), MagicMock()

    assert await refresh_catalog_for_edited_fact(pool, _ID, engine_factory=factory) is True

    factory.assert_not_called()


async def test_the_engine_factory_is_called_once_on_the_refresh_path() -> None:
    pool, engine = _pool(_fact()), _engine()
    factory = MagicMock(return_value=engine)

    assert await refresh_catalog_for_edited_fact(pool, _ID, engine_factory=factory) is True

    factory.assert_called_once_with()
    engine.embed.assert_called_once()


async def test_a_failing_engine_factory_is_logged_and_never_raised(caplog) -> None:
    pool = _pool(_fact())
    factory = MagicMock(side_effect=RuntimeError("model failed to load"))

    with caplog.at_level(logging.WARNING):
        assert await refresh_catalog_for_edited_fact(pool, _ID, engine_factory=factory) is False

    assert any("failed to refresh catalog entry" in r.getMessage() for r in caplog.records)
    pool.execute.assert_not_called()
