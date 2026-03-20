"""Unit tests for auto-registration of novel predicates in store_fact().

Covers tasks 4.1–4.3 from openspec/changes/predicate-registry-enforcement/tasks.md.

Scenarios tested:

  Auto-registration after novel write (4.1, 4.2):
    - Novel predicate with object_entity_id → is_edge=true, is_temporal=false
    - Novel predicate with valid_at → is_temporal=true, is_edge=false
    - Novel predicate with entity_id → expected_subject_type inferred from entity_type
    - Novel predicate with no entity extras → is_edge=false, is_temporal=false, subject_type=None

  Concurrent safety (ON CONFLICT DO NOTHING):
    - Auto-registration INSERT uses ON CONFLICT DO NOTHING

  Skip-if-registered (4.1):
    - Registered predicate does NOT get re-inserted
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.modules.memory._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load storage module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------
_STORAGE_PATH = MEMORY_MODULE_PATH / "storage.py"


def _load_storage_module():
    """Load storage.py from disk."""
    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_storage_module()
store_fact = _mod.store_fact

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncCM:
    """Simple async context manager wrapper returning a fixed value."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


@pytest.fixture()
def embedding_engine():
    """Return a mock EmbeddingEngine that produces a deterministic vector."""
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


def _make_pool(conn):
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool


def _make_conn(
    *,
    fetchval_side_effect=None,
    fetchval_return=None,
    fetchrow_side_effect=None,
    fetchrow_return=None,
):
    """Build a mock asyncpg connection.

    fetchval is used for: idempotency dedup check only.

    fetchrow is used for: entity validation (SELECT id, entity_type),
    object entity validation, predicate_registry lookup, supersession check.

    Pass fetchrow_side_effect as a list of return values in the order the
    calls will be made (entity validation rows first, then registry, then
    supersession).
    """
    conn = AsyncMock()
    if fetchval_side_effect is not None:
        conn.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    else:
        conn.fetchval = AsyncMock(return_value=fetchval_return)
    if fetchrow_side_effect is not None:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    conn.fetch = AsyncMock(return_value=[])
    return conn


def _get_auto_registration_execute_call(conn):
    """Return the conn.execute call that inserts into predicate_registry, or None."""
    for c in conn.execute.call_args_list:
        sql = c.args[0] if c.args else ""
        if "predicate_registry" in sql and "INSERT" in sql:
            return c
    return None


# ---------------------------------------------------------------------------
# Auto-registration: novel predicate without entity extras
# ---------------------------------------------------------------------------


class TestAutoRegistrationBasic:
    """Novel predicate is auto-inserted with inferred flags after successful write."""

    async def test_novel_predicate_without_extras_is_auto_registered(self, embedding_engine):
        """Novel predicate with no entity or temporal extras is registered with defaults.

        WHEN store_fact() succeeds with a predicate not in the registry and
        object_entity_id is None and valid_at is None,
        THEN predicate_registry INSERT is issued with is_edge=False, is_temporal=False,
        expected_subject_type=None.
        """
        # fetchrow: [alias=None, registry_row=None, supersession=None]
        conn = _make_conn(fetchrow_side_effect=[None, None, None])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "user",
            "custom_novel_predicate",
            "some value",
            embedding_engine,
        )

        reg_call = _get_auto_registration_execute_call(conn)
        assert reg_call is not None, "Expected auto-registration INSERT to be called"
        sql = reg_call.args[0]
        assert "ON CONFLICT (name) DO NOTHING" in sql
        # Verify parameters: predicate name, is_edge=False, is_temporal=False, subject_type=None
        args = reg_call.args
        assert args[1] == "custom_novel_predicate"
        assert args[2] is False  # is_edge
        assert args[3] is False  # is_temporal
        assert args[4] is None  # expected_subject_type

    async def test_novel_predicate_auto_registration_sql_targets_predicate_registry(
        self, embedding_engine
    ):
        """The auto-registration INSERT targets predicate_registry."""
        conn = _make_conn(fetchrow_side_effect=[None, None, None])
        pool = _make_pool(conn)

        await store_fact(pool, "user", "brand_new_predicate", "value", embedding_engine)

        reg_call = _get_auto_registration_execute_call(conn)
        assert reg_call is not None
        sql = reg_call.args[0]
        assert "predicate_registry" in sql
        assert "INSERT" in sql


# ---------------------------------------------------------------------------
# Auto-registration: inferred is_edge from object_entity_id
# ---------------------------------------------------------------------------


class TestAutoRegistrationIsEdge:
    """is_edge is inferred from object_entity_id presence."""

    async def test_novel_predicate_with_object_entity_id_registers_as_edge(self, embedding_engine):
        """Novel predicate + object_entity_id → auto-registered with is_edge=True.

        WHEN store_fact() succeeds with a predicate not in the registry and
        object_entity_id is set,
        THEN the auto-registered row MUST have is_edge=True.
        """
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()

        # fetchrow call order:
        #   1. entity_id existence+type check → entity row with entity_type='person'
        #   2. object_entity_id existence+type check → entity row
        #   3. alias lookup → None (no alias match)
        #   4. registry lookup → None (novel)
        #   5. supersession check → None (no prior fact)
        _entity_row = {"id": entity_id, "entity_type": "person"}
        _obj_entity_row = {"id": object_entity_id, "entity_type": "person"}
        conn = _make_conn(
            fetchrow_side_effect=[_entity_row, _obj_entity_row, None, None, None],
        )
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "Alice",
            "novel_edge_predicate",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        reg_call = _get_auto_registration_execute_call(conn)
        assert reg_call is not None
        args = reg_call.args
        assert args[1] == "novel_edge_predicate"
        assert args[2] is True  # is_edge
        assert args[3] is False  # is_temporal (valid_at not set)

    async def test_novel_predicate_without_object_entity_id_registers_as_non_edge(
        self, embedding_engine
    ):
        """Novel predicate without object_entity_id → auto-registered with is_edge=False."""
        entity_id = uuid.uuid4()

        # fetchrow call order:
        #   1. entity_id existence+type check → entity row
        #   2. alias lookup → None (no alias match)
        #   3. registry lookup → None (novel)
        #   4. supersession check → None
        _entity_row = {"id": entity_id, "entity_type": "person"}
        conn = _make_conn(
            fetchrow_side_effect=[_entity_row, None, None, None],
        )
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "Alice",
            "novel_property_predicate",
            "some value",
            embedding_engine,
            entity_id=entity_id,
        )

        reg_call = _get_auto_registration_execute_call(conn)
        assert reg_call is not None
        args = reg_call.args
        assert args[2] is False  # is_edge


# ---------------------------------------------------------------------------
# Auto-registration: inferred is_temporal from valid_at
# ---------------------------------------------------------------------------


class TestAutoRegistrationIsTemporal:
    """is_temporal is inferred from valid_at presence."""

    async def test_novel_predicate_with_valid_at_registers_as_temporal(self, embedding_engine):
        """Novel predicate + valid_at → auto-registered with is_temporal=True.

        WHEN store_fact() succeeds with a predicate not in the registry and
        valid_at is set,
        THEN the auto-registered row MUST have is_temporal=True.
        """
        ts = datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC)
        # fetchval: [idempotency_check=None] (no entity_id so no entity validation)
        # fetchrow: [alias=None (no match), registry=None] (no supersession for temporal facts)
        conn = _make_conn(
            fetchval_return=None,  # idempotency check: no duplicate
            fetchrow_side_effect=[None, None],
        )
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "user",
            "novel_temporal_predicate",
            "some event",
            embedding_engine,
            valid_at=ts,
        )

        reg_call = _get_auto_registration_execute_call(conn)
        assert reg_call is not None
        args = reg_call.args
        assert args[1] == "novel_temporal_predicate"
        assert args[2] is False  # is_edge (no object_entity_id)
        assert args[3] is True  # is_temporal

    async def test_novel_predicate_without_valid_at_registers_as_non_temporal(
        self, embedding_engine
    ):
        """Novel predicate without valid_at → auto-registered with is_temporal=False."""
        conn = _make_conn(fetchrow_side_effect=[None, None, None])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "user",
            "novel_property_x",
            "some value",
            embedding_engine,
        )

        reg_call = _get_auto_registration_execute_call(conn)
        assert reg_call is not None
        assert reg_call.args[3] is False  # is_temporal


# ---------------------------------------------------------------------------
# Auto-registration: inferred expected_subject_type from entity_type
# ---------------------------------------------------------------------------


class TestAutoRegistrationExpectedSubjectType:
    """expected_subject_type is inferred from entity's entity_type."""

    async def test_entity_type_inferred_for_expected_subject_type(self, embedding_engine):
        """When entity_id is provided, expected_subject_type is looked up from entity_type.

        WHEN store_fact() succeeds with a predicate not in the registry and
        entity_id resolves to an entity with entity_type='person',
        THEN the auto-registered row MUST have expected_subject_type='person'.
        The entity_type is now fetched in the same query as the existence check
        (no separate fetchval round-trip).
        """
        entity_id = uuid.uuid4()

        # fetchrow call order:
        #   1. entity existence+type check → entity row with entity_type='person'
        #   2. alias lookup → None (no alias match)
        #   3. registry lookup → None (novel predicate)
        #   4. supersession check → None (no prior fact)
        # fetchval: not used (no idempotency check for property facts)
        _entity_row = {"id": entity_id, "entity_type": "person"}
        conn = _make_conn(
            fetchrow_side_effect=[_entity_row, None, None, None],
        )
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "Alice",
            "novel_person_predicate",
            "some value",
            embedding_engine,
            entity_id=entity_id,
        )

        reg_call = _get_auto_registration_execute_call(conn)
        assert reg_call is not None
        args = reg_call.args
        assert args[4] == "person"  # expected_subject_type

    async def test_no_entity_id_means_no_subject_type_inference(self, embedding_engine):
        """Without entity_id, expected_subject_type is None (no lookup possible)."""
        conn = _make_conn(fetchrow_side_effect=[None, None, None])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "user",
            "novel_unanchored_predicate",
            "some value",
            embedding_engine,
        )

        reg_call = _get_auto_registration_execute_call(conn)
        assert reg_call is not None
        assert reg_call.args[4] is None  # expected_subject_type


# ---------------------------------------------------------------------------
# Concurrent safety: ON CONFLICT DO NOTHING
# ---------------------------------------------------------------------------


class TestAutoRegistrationConcurrentSafety:
    """ON CONFLICT DO NOTHING ensures concurrent writes do not raise."""

    async def test_auto_registration_uses_on_conflict_do_nothing(self, embedding_engine):
        """The auto-registration INSERT uses ON CONFLICT (name) DO NOTHING.

        WHEN two concurrent store_fact() calls both use the same novel predicate,
        THEN the ON CONFLICT DO NOTHING clause ensures the second writer does not
        raise an error (the conflict is handled at the SQL level).
        """
        conn = _make_conn(fetchrow_side_effect=[None, None, None])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "user",
            "concurrent_novel_pred",
            "value",
            embedding_engine,
        )

        reg_call = _get_auto_registration_execute_call(conn)
        assert reg_call is not None
        sql = reg_call.args[0]
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql


# ---------------------------------------------------------------------------
# Skip-if-registered: registered predicates must not be re-inserted
# ---------------------------------------------------------------------------


class TestAutoRegistrationSkipIfRegistered:
    """Registered predicates are NOT re-inserted into predicate_registry."""

    async def test_registered_predicate_skips_auto_registration(self, embedding_engine):
        """When the predicate is found in the registry, no auto-registration INSERT is issued.

        WHEN store_fact() is called with a predicate that IS in the registry,
        THEN predicate_registry MUST NOT receive a second INSERT.
        """
        # Registry lookup returns a row (predicate is registered, non-edge, non-temporal)
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "expected_subject_type": None,
            "expected_object_type": None,
        }
        # No entity_id: fetchrow order is alias lookup → registry lookup → supersession check.
        conn = _make_conn(fetchrow_side_effect=[None, registry_row, None])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "Alice",
            "birthday",
            "1990-01-01",
            embedding_engine,
        )

        # Auto-registration INSERT must NOT be called
        reg_call = _get_auto_registration_execute_call(conn)
        assert reg_call is None, (
            "predicate_registry INSERT must not be called for already-registered predicates"
        )

    async def test_registered_edge_predicate_with_entities_skips_auto_registration(
        self, embedding_engine
    ):
        """Registered edge predicate (is_edge=True) with both entities: no re-insert."""
        registry_row = {
            "is_edge": True,
            "is_temporal": False,
            "expected_subject_type": None,
            "expected_object_type": None,
        }
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()

        # fetchrow call order:
        #   1. entity_id existence+type check → entity row
        #   2. object_entity_id existence+type check → entity row
        #   3. alias lookup → None (no alias match)
        #   4. registry lookup → registered_row
        #   5. supersession check → None
        _entity_row = {"id": entity_id, "entity_type": "person"}
        _obj_entity_row = {"id": object_entity_id, "entity_type": "person"}
        conn = _make_conn(
            fetchrow_side_effect=[_entity_row, _obj_entity_row, None, registry_row, None],
        )
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "Alice",
            "parent_of",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        reg_call = _get_auto_registration_execute_call(conn)
        assert reg_call is None, (
            "predicate_registry INSERT must not be called for already-registered predicates"
        )


# ---------------------------------------------------------------------------
# Usage tracking: usage_count / last_used_at incremented after successful write
# Task 7.8 (usage tracking aspect from spec requirement "Usage tracking per predicate")
# ---------------------------------------------------------------------------


def _get_usage_tracking_execute_call(conn):
    """Return the conn.execute call that updates usage_count/last_used_at, or None."""
    for c in conn.execute.call_args_list:
        sql = c.args[0] if c.args else ""
        if "usage_count" in sql and "UPDATE" in sql and "predicate_registry" in sql:
            return c
    return None


class TestUsageTracking:
    """usage_count is incremented and last_used_at updated after each successful store_fact."""

    async def test_usage_count_incremented_for_registered_predicate(self, embedding_engine):
        """store_fact() issues UPDATE usage_count+1 for a registered predicate.

        WHEN store_fact() successfully stores a fact with a registered predicate
        THEN usage_count MUST be incremented by 1 and last_used_at set to now().
        """
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "expected_subject_type": None,
            "expected_object_type": None,
            "status": "active",
            "superseded_by": None,
        }
        conn = _make_conn(fetchrow_side_effect=[None, registry_row, None])
        pool = _make_pool(conn)

        await store_fact(pool, "Alice", "birthday", "1990-01-01", embedding_engine)

        usage_call = _get_usage_tracking_execute_call(conn)
        assert usage_call is not None, "Expected usage_count UPDATE to be called"
        sql = usage_call.args[0]
        assert "usage_count" in sql
        assert "last_used_at" in sql
        # The predicate name should be passed as a parameter.
        assert usage_call.args[1] == "birthday"

    async def test_usage_count_incremented_for_novel_predicate(self, embedding_engine):
        """store_fact() also increments usage_count for a novel (auto-registered) predicate.

        WHEN store_fact() succeeds with a predicate not in the registry,
        THEN usage_count MUST still be incremented (after auto-registration).
        """
        conn = _make_conn(fetchrow_side_effect=[None, None, None])
        pool = _make_pool(conn)

        await store_fact(pool, "user", "custom_novel_pred_x", "value", embedding_engine)

        usage_call = _get_usage_tracking_execute_call(conn)
        assert usage_call is not None, "Expected usage_count UPDATE for novel predicate"
        assert usage_call.args[1] == "custom_novel_pred_x"

    async def test_usage_tracking_update_targets_correct_predicate(self, embedding_engine):
        """The UPDATE is issued with the exact predicate name as parameter."""
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "expected_subject_type": None,
            "expected_object_type": None,
            "status": "active",
            "superseded_by": None,
        }
        conn = _make_conn(fetchrow_side_effect=[None, registry_row, None])
        pool = _make_pool(conn)

        await store_fact(pool, "Alice", "occupation", "engineer", embedding_engine)

        usage_call = _get_usage_tracking_execute_call(conn)
        assert usage_call is not None
        assert usage_call.args[1] == "occupation"
