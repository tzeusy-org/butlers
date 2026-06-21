"""Tests for the dual-write behaviour introduced in ``roster/health/tools/diet.py``.

Every ``meal_log()`` call must write to both:
- ``facts`` table (memory module) — via ``store_fact``
- ``health.meals`` table — via ``_write_to_health_meals``

Covers:
- Successful dual-write: both surfaces receive data.
- health.meals write uses the same meal_id UUID as the stable PK.
- Correct column mapping: type, description, nutrition JSONB, eaten_at, notes.
- Nutrition None → health.meals nutrition column is NULL.
- Nutrition dict → health.meals nutrition column is a JSON object.
- _write_to_health_meals failure is swallowed (warning only); facts write stands.
- _write_to_health_meals: ON CONFLICT DO NOTHING query shape.
- _write_to_health_meals standalone: no-raise on asyncpg error.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.tools.health.diet import _write_to_health_meals, meal_log

_EATEN_AT = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
_MEAL_ID = uuid.uuid4()

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncCM:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


def _make_pool(*, execute_side_effect=None) -> MagicMock:
    """Build a mock pool wired for both fetchrow (owner entity) and execute (health.meals)."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)  # no owner entity
    if execute_side_effect is not None:
        pool.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        pool.execute = AsyncMock(return_value=None)
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool


# ---------------------------------------------------------------------------
# _write_to_health_meals unit tests
# ---------------------------------------------------------------------------


async def test_write_to_health_meals_inserts_row_with_ordered_args() -> None:
    """_write_to_health_meals issues the ON CONFLICT INSERT with column-ordered args."""
    pool = _make_pool()
    meal_id = uuid.uuid4()

    await _write_to_health_meals(
        pool,
        meal_id=meal_id,
        type="dinner",
        description="Steak",
        nutrition=None,
        eaten_at=_EATEN_AT,
        notes="Medium rare",
    )

    pool.execute.assert_awaited_once()
    args = pool.execute.call_args.args
    sql: str = args[0]
    assert "INSERT INTO health.meals" in sql
    assert "ON CONFLICT (id) DO NOTHING" in sql
    # args[1..] are the positional bind parameters in column order.
    assert args[1] == meal_id
    assert args[2] == "dinner"
    assert args[3] == "Steak"
    assert args[4] is None  # nutrition is None
    assert args[5] == _EATEN_AT
    assert args[6] == "Medium rare"


async def test_write_to_health_meals_nutrition_jsonb() -> None:
    """When nutrition is provided, the INSERT binds a JSON string."""
    pool = _make_pool()
    nutrition = {"calories": 600, "protein_g": 40, "carbs_g": 20, "fat_g": 30}

    await _write_to_health_meals(
        pool,
        meal_id=_MEAL_ID,
        type="lunch",
        description="Beef bowl",
        nutrition=nutrition,
        eaten_at=_EATEN_AT,
        notes=None,
    )

    args = pool.execute.call_args.args
    nutrition_arg = args[4]  # $4 = nutrition
    assert nutrition_arg is not None
    parsed = json.loads(nutrition_arg)
    assert parsed["calories"] == 600
    assert parsed["macros"]["protein_g"] == 40
    assert parsed["macros"]["carbs_g"] == 20
    assert parsed["macros"]["fat_g"] == 30


async def test_write_to_health_meals_swallows_postgres_error() -> None:
    """A PostgresError from the INSERT must be caught and logged, not re-raised."""
    pool = _make_pool(execute_side_effect=asyncpg.exceptions.PostgresError("connection reset"))

    # Must not raise
    await _write_to_health_meals(
        pool,
        meal_id=_MEAL_ID,
        type="snack",
        description="Apple",
        nutrition=None,
        eaten_at=_EATEN_AT,
        notes=None,
    )


# ---------------------------------------------------------------------------
# meal_log dual-write integration tests
# ---------------------------------------------------------------------------


async def test_meal_log_health_meals_failure_does_not_raise() -> None:
    """meal_log must succeed even when the inner health.meals INSERT fails.

    The swallowing happens *inside* ``_write_to_health_meals`` (it catches
    ``asyncpg.PostgresError`` and logs a warning).  This test exercises that
    production path by injecting the failure at the ``pool.execute`` level so
    that ``_write_to_health_meals`` runs for real, catches the error, and
    ``meal_log`` returns successfully.
    """
    pool = _make_pool(execute_side_effect=asyncpg.exceptions.PostgresError("disk full"))
    fact_id = uuid.uuid4()

    with (
        patch(
            "butlers.modules.memory.storage.store_fact",
            new=AsyncMock(return_value={"id": fact_id, "supersedes_id": None}),
        ),
        patch("butlers.tools.health.diet._get_embedding_engine", return_value=MagicMock()),
    ):
        # _write_to_health_meals runs for real; pool.execute raises PostgresError,
        # which is caught and swallowed inside _write_to_health_meals.
        # meal_log must return successfully — no exception should propagate.
        result = await meal_log(pool, type="snack", description="Apple", eaten_at=_EATEN_AT)

    assert result["id"] == str(fact_id)
    assert result["type"] == "snack"


async def test_meal_log_stable_meal_id_passed_to_both_surfaces() -> None:
    """meal_log must use fact_id from store_fact as the meal_id for the health.meals write.

    Both storage surfaces must share the same stable UUID so that ON CONFLICT DO NOTHING
    provides real idempotency on retries: store_fact returns the same fact_id for
    identical content, and health.meals deduplicates on the same id.
    """
    pool = _make_pool()
    fact_id = uuid.uuid4()
    captured_meal_ids: list[uuid.UUID] = []

    async def _fake_dual_write(pool, *, meal_id, **kwargs):
        captured_meal_ids.append(meal_id)

    with (
        patch(
            "butlers.modules.memory.storage.store_fact",
            new=AsyncMock(return_value={"id": fact_id, "supersedes_id": None}),
        ),
        patch("butlers.tools.health.diet._get_embedding_engine", return_value=MagicMock()),
        patch(
            "butlers.tools.health.diet._write_to_health_meals",
            new=_fake_dual_write,
        ),
    ):
        await meal_log(pool, type="dinner", description="Pizza", eaten_at=_EATEN_AT)

    assert len(captured_meal_ids) == 1
    assert captured_meal_ids[0] == fact_id, (
        "meal_id passed to _write_to_health_meals must equal fact_id from store_fact "
        "so both surfaces share the same stable UUID for idempotent retries"
    )
