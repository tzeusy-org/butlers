"""Regression tests for _get_owner_entity_id() in health/tools/diet.py.

Verifies the post-core_016 owner-entity resolution path:
- queries shared.entities.roles (not shared.contacts.roles)
- returns None gracefully when the table does not exist (pre-migration)
- returns None gracefully when no owner entity is present
- meal_log succeeds in all three cases (with entity_id, None, and DB-error fallback)
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.tools.health.diet import _get_owner_entity_id, meal_log

# butlers.tools.health.diet is auto-registered by the tools loader, which
# maps roster/health/tools/diet.py → butlers.tools.health.diet

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(fetchrow_result=None, fetchrow_side_effect=None) -> MagicMock:
    """Build a minimal asyncpg Pool mock.

    pool.fetchrow() is set up directly (not via acquire/conn) because
    _get_owner_entity_id uses pool.fetchrow() at the pool level.
    """
    pool = MagicMock()
    if fetchrow_side_effect is not None:
        pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    return pool


def _make_entity_row(entity_id: uuid.UUID) -> dict:
    """Minimal dict mimicking an asyncpg Record with a single 'id' column."""
    return {"id": entity_id}


# ---------------------------------------------------------------------------
# Tests for _get_owner_entity_id()
# ---------------------------------------------------------------------------


class TestGetOwnerEntityId:
    """Unit tests for the owner-entity lookup function."""

    async def test_queries_shared_entities_not_contacts(self) -> None:
        """Must query shared.entities, not shared.contacts.roles."""
        owner_id = uuid.uuid4()
        pool = _make_pool(fetchrow_result=_make_entity_row(owner_id))

        result = await _get_owner_entity_id(pool)

        assert result == owner_id
        # Verify the SQL sent to fetchrow targets shared.entities
        call_args = pool.fetchrow.call_args
        sql: str = call_args.args[0]
        assert "shared.entities" in sql, "Must query shared.entities"
        assert "'owner' = ANY(roles)" in sql, "Must use roles column on entities"
        # Must NOT reference shared.contacts
        assert "shared.contacts" not in sql, "Must not query shared.contacts"

    async def test_returns_entity_id_when_owner_exists(self) -> None:
        """Returns the UUID of the owner entity when found."""
        owner_id = uuid.uuid4()
        pool = _make_pool(fetchrow_result=_make_entity_row(owner_id))

        result = await _get_owner_entity_id(pool)

        assert result == owner_id

    async def test_returns_none_when_no_owner_entity(self) -> None:
        """Returns None when no entity with 'owner' role exists (migrated schema, no owner yet)."""
        pool = _make_pool(fetchrow_result=None)

        result = await _get_owner_entity_id(pool)

        assert result is None

    async def test_returns_none_when_table_missing(self) -> None:
        """Returns None gracefully when shared.entities does not exist (pre-migration DB)."""
        pool = _make_pool(
            fetchrow_side_effect=Exception('relation "shared.entities" does not exist')
        )

        result = await _get_owner_entity_id(pool)

        assert result is None

    async def test_returns_none_on_any_db_error(self) -> None:
        """Any database exception is swallowed and None is returned."""
        pool = _make_pool(fetchrow_side_effect=RuntimeError("connection refused"))

        result = await _get_owner_entity_id(pool)

        assert result is None


# ---------------------------------------------------------------------------
# Tests for meal_log() with owner entity fallback behaviour
# ---------------------------------------------------------------------------


class _AsyncCM:
    """Minimal async context manager wrapper."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


def _make_full_pool(fetchrow_result=None, fetchrow_side_effect=None) -> MagicMock:
    """Build a pool mock suitable for meal_log (pool.fetchrow + pool.acquire)."""
    pool = _make_pool(fetchrow_result=fetchrow_result, fetchrow_side_effect=fetchrow_side_effect)
    # meal_log also calls store_fact which may use pool.acquire + conn
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool


class TestMealLogOwnerEntityFallback:
    """meal_log must succeed regardless of owner-entity lookup outcome."""

    async def test_meal_log_succeeds_with_owner_entity(self) -> None:
        """meal_log works when owner entity resolves to a UUID."""
        owner_id = uuid.uuid4()
        fact_id = uuid.uuid4()
        pool = _make_full_pool(fetchrow_result=_make_entity_row(owner_id))

        # store_fact is imported inside meal_log's body, so patch at source location.
        with (
            patch(
                "butlers.modules.memory.storage.store_fact",
                new=AsyncMock(return_value=fact_id),
            ) as mock_store,
            patch(
                "butlers.tools.health.diet._get_embedding_engine",
                return_value=MagicMock(),
            ),
        ):
            result = await meal_log(pool, type="breakfast", description="Eggs and toast")

        assert result["id"] == str(fact_id)
        assert result["type"] == "breakfast"
        # store_fact must be called with the resolved entity_id
        _call_kwargs = mock_store.call_args.kwargs
        assert _call_kwargs.get("entity_id") == owner_id

    async def test_meal_log_succeeds_without_owner_entity(self) -> None:
        """meal_log works when no owner entity exists (entity_id=None fallback)."""
        fact_id = uuid.uuid4()
        pool = _make_full_pool(fetchrow_result=None)

        with (
            patch(
                "butlers.modules.memory.storage.store_fact",
                new=AsyncMock(return_value=fact_id),
            ) as mock_store,
            patch(
                "butlers.tools.health.diet._get_embedding_engine",
                return_value=MagicMock(),
            ),
        ):
            result = await meal_log(pool, type="lunch", description="Salad")

        assert result["id"] == str(fact_id)
        # store_fact must be called with entity_id=None
        _call_kwargs = mock_store.call_args.kwargs
        assert _call_kwargs.get("entity_id") is None

    async def test_meal_log_succeeds_when_entities_table_missing(self) -> None:
        """meal_log works on pre-migration databases where shared.entities doesn't exist."""
        fact_id = uuid.uuid4()
        pool = _make_full_pool(
            fetchrow_side_effect=Exception('relation "shared.entities" does not exist')
        )

        with (
            patch(
                "butlers.modules.memory.storage.store_fact",
                new=AsyncMock(return_value=fact_id),
            ) as mock_store,
            patch(
                "butlers.tools.health.diet._get_embedding_engine",
                return_value=MagicMock(),
            ),
        ):
            result = await meal_log(pool, type="dinner", description="Pasta")

        assert result["id"] == str(fact_id)
        # entity_id must be None when table is absent
        _call_kwargs = mock_store.call_args.kwargs
        assert _call_kwargs.get("entity_id") is None

    async def test_meal_log_invalid_type_raises(self) -> None:
        """meal_log raises ValueError for invalid meal types (unrelated to entity lookup)."""
        pool = _make_full_pool()
        with pytest.raises(ValueError, match="Invalid meal type"):
            await meal_log(pool, type="brunch", description="French toast")
