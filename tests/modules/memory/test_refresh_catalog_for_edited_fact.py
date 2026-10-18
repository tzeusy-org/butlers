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
    return pool


def _engine() -> MagicMock:
    engine = MagicMock()
    engine.embed.return_value = [0.5] * 384
    return engine


async def test_a_live_fact_rewrites_summary_embedding_and_search_text() -> None:
    pool, engine = _pool(_fact()), _engine()

    assert await refresh_catalog_for_edited_fact(pool, _ID, embedding_engine=engine) is True

    engine.embed.assert_called_once_with("owner measurement_weight weight: 72 kg")
    sql, fact_id, summary, embedding, search_text, title, *_ = pool.execute.call_args[0]
    assert "UPDATE public.memory_catalog" in sql and "INSERT" not in sql
    assert "invalid_at" not in sql  # a live refresh never changes staleness
    for assignment in ("summary = $2", "embedding = $3", "search_vector =", "title = $5"):
        assert assignment in sql
    assert fact_id == _ID
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


async def test_measurement_update_refreshes_the_catalog_after_the_edit() -> None:
    from butlers.tools.health import measurements

    row = {
        "id": _ID,
        "predicate": "measurement_weight",
        "content": "weight: 70 kg",
        "valid_at": None,
        "created_at": None,
        "metadata": {"value": 70},
    }
    pool = _pool(row)
    engine = _engine()
    with (
        patch.object(measurements, "_get_embedding_engine", lambda: engine),
        patch(
            "butlers.modules.memory.storage.refresh_catalog_for_edited_fact", new=AsyncMock()
        ) as refresh,
    ):
        await measurements.measurement_update(pool, str(_ID), value=72)

    refresh.assert_awaited_once_with(pool, _ID, embedding_engine=engine)
    assert pool.execute.await_count == 1  # the UPDATE happened before the refresh


async def test_meal_update_refreshes_the_catalog_after_the_edit() -> None:
    from butlers.tools.health import diet

    row = {
        "id": _ID,
        "predicate": "meal_lunch",
        "content": "salad",
        "valid_at": None,
        "created_at": None,
        "metadata": {},
    }
    pool = _pool(row)
    engine = _engine()
    with (
        patch.object(diet, "_get_embedding_engine", lambda: engine),
        patch(
            "butlers.modules.memory.storage.refresh_catalog_for_edited_fact", new=AsyncMock()
        ) as refresh,
    ):
        await diet.meal_update(pool, str(_ID), description="tuna salad")

    refresh.assert_awaited_once_with(pool, _ID, embedding_engine=engine)
