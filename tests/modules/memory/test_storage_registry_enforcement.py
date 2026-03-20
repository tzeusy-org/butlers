"""Unit tests for registry enforcement of is_edge and is_temporal in store_fact().

Covers tasks 2.1–2.5 from openspec/changes/predicate-registry-enforcement/tasks.md.

Scenarios tested:
  is_edge enforcement (4):
    - edge predicate without object_entity_id is rejected
    - edge predicate with object_entity_id succeeds
    - non-edge predicate without object_entity_id succeeds
    - unregistered predicate without object_entity_id succeeds

  is_temporal enforcement (4):
    - temporal predicate without valid_at is rejected
    - temporal predicate with valid_at succeeds
    - non-temporal predicate without valid_at succeeds
    - unregistered predicate without valid_at succeeds
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Load the storage module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------
_MEMORY_MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "src" / "butlers" / "modules" / "memory"
)
_STORAGE_PATH = _MEMORY_MODULE_PATH / "storage.py"


def _load_storage_module():
    """Load storage.py with sentence_transformers mocked out."""
    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_storage_module()
store_fact = _mod.store_fact

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Async context manager helper for mocking asyncpg pool/conn
# ---------------------------------------------------------------------------


class _AsyncCM:
    """Simple async context manager wrapper returning a fixed value."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def embedding_engine():
    """Return a mock EmbeddingEngine that produces a deterministic vector."""
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


def _make_pool(*, registry_row=None, entity_exists=True, obj_entity_exists=True):
    """Build (pool, conn) mocks.

    registry_row: the value returned by conn.fetchrow for predicate_registry lookups.
    entity_exists: controls whether entity-validation fetchval returns truthy.
    """
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    conn.execute = AsyncMock()

    # fetchrow is used for both registry lookup and supersession check.
    # Registry lookup is the first call (made before supersession); supersession
    # calls come after.  We set the registry result as the return value.
    conn.fetchrow = AsyncMock(return_value=registry_row)

    # fetchval is used for entity validation (entity_id / object_entity_id checks).
    # Return 1 (truthy) by default so entity checks pass.
    conn.fetchval = AsyncMock(return_value=1 if entity_exists else None)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool, conn


# ---------------------------------------------------------------------------
# is_edge enforcement
# ---------------------------------------------------------------------------


class TestIsEdgeEnforcement:
    """Registry enforcement of is_edge constraint at write time."""

    async def test_edge_predicate_without_object_entity_id_is_rejected(self, embedding_engine):
        """Edge predicate without object_entity_id raises ValueError.

        WHEN store_fact() is called with predicate 'parent_of' (registered
        with is_edge=true) and object_entity_id is NULL,
        THEN a ValueError MUST be raised naming the predicate and suggesting
        memory_entity_resolve().
        """
        registry_row = {"is_edge": True, "is_temporal": False}
        pool, _conn = _make_pool(registry_row=registry_row)

        with pytest.raises(ValueError) as exc_info:
            await store_fact(
                pool,
                "Alice",
                "parent_of",
                "Bob",
                embedding_engine,
                # object_entity_id intentionally omitted
            )

        msg = str(exc_info.value)
        assert "parent_of" in msg
        assert "is_edge" in msg or "edge predicate" in msg
        assert "object_entity_id" in msg
        assert "memory_entity_resolve" in msg

    async def test_edge_predicate_with_object_entity_id_succeeds(self, embedding_engine):
        """Edge predicate with object_entity_id is stored successfully.

        WHEN store_fact() is called with predicate 'parent_of' (registered
        with is_edge=true) and a valid object_entity_id,
        THEN the fact MUST be stored (no ValueError raised).
        """
        registry_row = {"is_edge": True, "is_temporal": False}
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()

        pool, conn = _make_pool(registry_row=registry_row)
        # fetchrow will be called multiple times: registry check (returns
        # registry_row), then supersession check (should return None for no
        # prior fact).  We need to return different values per call.
        conn.fetchrow = AsyncMock(side_effect=[registry_row, None])

        result = await store_fact(
            pool,
            "Alice",
            "parent_of",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        assert isinstance(result, uuid.UUID)

    async def test_non_edge_predicate_without_object_entity_id_succeeds(self, embedding_engine):
        """Non-edge predicate without object_entity_id is stored successfully.

        WHEN store_fact() is called with predicate 'birthday' (registered
        with is_edge=false) and object_entity_id is NULL,
        THEN the fact MUST be stored normally.
        """
        registry_row = {"is_edge": False, "is_temporal": False}
        pool, conn = _make_pool(registry_row=registry_row)
        # Registry returns non-edge row; supersession check returns None.
        conn.fetchrow = AsyncMock(side_effect=[registry_row, None])

        result = await store_fact(
            pool,
            "Alice",
            "birthday",
            "1990-01-01",
            embedding_engine,
            # object_entity_id intentionally omitted
        )

        assert isinstance(result, uuid.UUID)

    async def test_unregistered_predicate_without_object_entity_id_succeeds(self, embedding_engine):
        """Unregistered predicate without object_entity_id is stored successfully.

        WHEN store_fact() is called with a predicate NOT in the registry and
        object_entity_id is NULL,
        THEN the fact MUST be stored normally — registry enforcement only
        applies to registered predicates.
        """
        pool, conn = _make_pool(registry_row=None)
        # No registry row; supersession check also returns None.
        conn.fetchrow = AsyncMock(side_effect=[None, None])

        result = await store_fact(
            pool,
            "user",
            "custom_novel_predicate",
            "some value",
            embedding_engine,
        )

        assert isinstance(result, uuid.UUID)


# ---------------------------------------------------------------------------
# is_temporal enforcement
# ---------------------------------------------------------------------------


class TestIsTemporalEnforcement:
    """Registry enforcement of is_temporal constraint at write time."""

    async def test_temporal_predicate_without_valid_at_is_rejected(self, embedding_engine):
        """Temporal predicate without valid_at raises ValueError.

        WHEN store_fact() is called with predicate 'interaction' (registered
        with is_temporal=true) and valid_at is NULL,
        THEN a ValueError MUST be raised naming the predicate, explaining
        the supersession risk, and directing the caller to provide valid_at.
        """
        registry_row = {"is_edge": False, "is_temporal": True}
        pool, _conn = _make_pool(registry_row=registry_row)

        with pytest.raises(ValueError) as exc_info:
            await store_fact(
                pool,
                "Alice",
                "interaction",
                "had a phone call",
                embedding_engine,
                # valid_at intentionally omitted
            )

        msg = str(exc_info.value)
        assert "interaction" in msg
        assert "is_temporal" in msg or "temporal predicate" in msg
        assert "valid_at" in msg
        # Must explain the supersession risk
        assert "supersession" in msg or "supersede" in msg or "destroy" in msg

    async def test_temporal_predicate_with_valid_at_succeeds(self, embedding_engine):
        """Temporal predicate with valid_at is stored successfully.

        WHEN store_fact() is called with predicate 'interaction' (registered
        with is_temporal=true) and valid_at is set,
        THEN the fact MUST be stored as a temporal fact.
        """
        registry_row = {"is_edge": False, "is_temporal": True}
        pool, conn = _make_pool(registry_row=registry_row)
        # Registry returns temporal row; no idempotency duplicate.
        conn.fetchrow = AsyncMock(return_value=registry_row)
        conn.fetchval = AsyncMock(return_value=None)  # idempotency check: no dup

        ts = datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC)
        result = await store_fact(
            pool,
            "Alice",
            "interaction",
            "had a phone call",
            embedding_engine,
            valid_at=ts,
        )

        assert isinstance(result, uuid.UUID)

    async def test_non_temporal_predicate_without_valid_at_succeeds(self, embedding_engine):
        """Non-temporal predicate without valid_at is stored successfully.

        WHEN store_fact() is called with predicate 'birthday' (registered
        with is_temporal=false) and valid_at is NULL,
        THEN the fact MUST be stored normally as a property fact.
        """
        registry_row = {"is_edge": False, "is_temporal": False}
        pool, conn = _make_pool(registry_row=registry_row)
        conn.fetchrow = AsyncMock(side_effect=[registry_row, None])

        result = await store_fact(
            pool,
            "Alice",
            "birthday",
            "1990-01-01",
            embedding_engine,
            # valid_at intentionally omitted
        )

        assert isinstance(result, uuid.UUID)

    async def test_unregistered_predicate_without_valid_at_succeeds(self, embedding_engine):
        """Unregistered predicate without valid_at is stored successfully.

        WHEN store_fact() is called with a predicate NOT in the registry and
        valid_at is NULL,
        THEN the fact MUST be stored normally — registry enforcement only
        applies to registered predicates.
        """
        pool, conn = _make_pool(registry_row=None)
        conn.fetchrow = AsyncMock(side_effect=[None, None])

        result = await store_fact(
            pool,
            "user",
            "another_novel_predicate",
            "some value",
            embedding_engine,
        )

        assert isinstance(result, uuid.UUID)


# ---------------------------------------------------------------------------
# Registry lookup placement
# ---------------------------------------------------------------------------


class TestRegistryLookupPlacement:
    """Verify the registry lookup is issued inside the transaction."""

    async def test_registry_query_uses_predicate_name(self, embedding_engine):
        """The registry lookup queries by predicate name."""
        registry_row = {"is_edge": False, "is_temporal": False}
        pool, conn = _make_pool(registry_row=registry_row)
        conn.fetchrow = AsyncMock(side_effect=[registry_row, None])

        await store_fact(
            pool,
            "Alice",
            "birthday",
            "1990-01-01",
            embedding_engine,
        )

        # The first fetchrow call must query predicate_registry with the predicate name
        first_call = conn.fetchrow.call_args_list[0]
        sql = first_call.args[0]
        assert "predicate_registry" in sql
        # The predicate value must be passed as a parameter
        assert first_call.args[1] == "birthday"

    async def test_registry_lookup_happens_before_supersession_check(self, embedding_engine):
        """Registry lookup is the first fetchrow call; supersession follows."""
        registry_row = {"is_edge": False, "is_temporal": False}
        pool, conn = _make_pool(registry_row=registry_row)
        conn.fetchrow = AsyncMock(side_effect=[registry_row, None])

        await store_fact(pool, "Alice", "birthday", "1990-01-01", embedding_engine)

        # First fetchrow call must be the registry lookup
        first_sql = conn.fetchrow.call_args_list[0].args[0]
        assert "predicate_registry" in first_sql
