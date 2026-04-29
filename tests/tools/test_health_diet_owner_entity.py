"""Regression tests for _get_owner_entity_id() in health/tools/diet.py.

Verifies the post-core_016 owner-entity resolution path:
- queries public.entities.roles (not public.contacts.roles)
- returns None gracefully when the table does not exist (pre-migration)
- meal_log succeeds in all three cases (with entity_id, None, and DB-error fallback)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.tools.health.diet import _get_owner_entity_id, meal_log

_EATEN_AT = datetime(2026, 3, 20, 12, 0, 0, tzinfo=UTC)
_OWNER_ID = uuid.UUID("aaaabbbb-cccc-dddd-eeee-000000000001")

pytestmark = pytest.mark.unit


def _make_pool(fetchrow_result=None, fetchrow_side_effect=None) -> MagicMock:
    pool = MagicMock()
    if fetchrow_side_effect is not None:
        pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    return pool


class _AsyncCM:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


def _make_full_pool(fetchrow_result=None, fetchrow_side_effect=None) -> MagicMock:
    pool = _make_pool(fetchrow_result=fetchrow_result, fetchrow_side_effect=fetchrow_side_effect)
    # pool.execute is called directly by _write_to_health_meals (dual-write to health.meals)
    pool.execute = AsyncMock(return_value=None)
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool


async def test_get_owner_entity_id_queries_public_entities_and_fallbacks():
    """Must query public.entities with roles; returns None for missing entity or DB errors."""
    owner_id = uuid.uuid4()
    pool = _make_pool(fetchrow_result={"id": owner_id})
    result = await _get_owner_entity_id(pool)
    assert result == owner_id
    sql: str = pool.fetchrow.call_args.args[0]
    assert "public.entities" in sql
    assert "'owner' = ANY(roles)" in sql
    assert "public.contacts" not in sql

    # Graceful fallbacks: no owner, table missing, DB error
    for side_effect in (
        None,
        asyncpg.exceptions.UndefinedTableError("no table"),
        asyncpg.exceptions.PostgresConnectionError("refused"),
    ):
        p = _make_pool(fetchrow_result=None, fetchrow_side_effect=side_effect)
        assert await _get_owner_entity_id(p) is None


async def test_meal_log_entity_fallback():
    """meal_log succeeds with entity_id, None, or DB-error fallback."""
    cases = [
        ({"id": _OWNER_ID}, None, str(_OWNER_ID)),
        (None, None, None),
        (None, asyncpg.exceptions.UndefinedTableError("no table"), None),
    ]
    for fetchrow_result, fetchrow_side_effect, expected_entity_id in cases:
        fact_id = uuid.uuid4()
        pool = _make_full_pool(
            fetchrow_result=fetchrow_result, fetchrow_side_effect=fetchrow_side_effect
        )

        with (
            patch(
                "butlers.modules.memory.storage.store_fact",
                new=AsyncMock(return_value={"id": fact_id, "supersedes_id": None}),
            ) as mock_store,
            patch("butlers.tools.health.diet._get_embedding_engine", return_value=MagicMock()),
        ):
            result = await meal_log(pool, type="breakfast", description="Eggs", eaten_at=_EATEN_AT)

        assert result["id"] == str(fact_id)
        actual = mock_store.call_args.kwargs.get("entity_id")
        if expected_entity_id is None:
            assert actual is None
        else:
            assert str(actual) == expected_entity_id


async def test_meal_log_invalid_type_raises():
    """meal_log raises ValueError for invalid meal types."""
    pool = _make_full_pool()
    with pytest.raises(ValueError, match="Invalid meal type"):
        await meal_log(pool, type="brunch", description="French toast", eaten_at=_EATEN_AT)
