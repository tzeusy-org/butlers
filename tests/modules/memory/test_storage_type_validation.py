"""Unit tests for domain/range type validation (soft warnings) in store_fact().

Covers tasks 12.1–12.4 from openspec/changes/predicate-registry-enforcement/tasks.md.

Spec reference: openspec/changes/predicate-registry-enforcement/specs/predicate-enforcement/spec.md
  Requirement: Domain and range type validation (soft)
Design reference: design.md — D7

Scenarios tested (4 from spec):
  1. Subject type mismatch → warning included in response, fact is still stored.
  2. Object type mismatch → warning included in response, fact is still stored.
  3. NULL expected types → no type validation, no warning produced.
  4. Matching types → no warning produced.

Additional coverage:
  - Both subject and object mismatch simultaneously → two warnings.
  - entity_type fetched in same query as existence check (no extra round-trip).
  - Registered predicate with matching types produces no warning.
  - Unregistered predicate (novel) never triggers type warnings.
"""

from __future__ import annotations

import importlib.util
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.modules.memory._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load storage module from disk.
# ---------------------------------------------------------------------------
_STORAGE_PATH = MEMORY_MODULE_PATH / "storage.py"


def _load_storage_module():
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
    """Async context manager wrapper returning a fixed value."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


@pytest.fixture()
def embedding_engine():
    """Mock EmbeddingEngine with a deterministic vector."""
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


def _make_pool_and_conn(
    *,
    entity_type: str | None = "person",
    object_entity_type: str | None = "person",
    registry_row: dict | None = None,
    has_entity_id: bool = True,
    has_object_entity_id: bool = False,
) -> tuple:
    """Build (pool, conn) mocks with configurable entity types.

    fetchrow call order (depending on which ids are provided):
      If has_entity_id:      fetchrow 1 = entity existence+type check
      If has_object_entity_id: fetchrow 2 = object entity existence+type check
      Always:                fetchrow N = registry lookup
      If property fact:      fetchrow N+1 = supersession check → None
    """
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)  # idempotency check
    conn.fetch = AsyncMock(return_value=[])  # fuzzy matching

    side_effects: list = []
    _eid = uuid.uuid4()
    _oid = uuid.uuid4()

    if has_entity_id:
        _entity_row = {"id": _eid, "entity_type": entity_type}
        side_effects.append(_entity_row)

    if has_object_entity_id:
        _obj_row = {"id": _oid, "entity_type": object_entity_type}
        side_effects.append(_obj_row)

    # Alias resolution (always called, returns None = not an alias)
    side_effects.append(None)

    # Registry lookup
    side_effects.append(registry_row)

    # Supersession check (for property facts — not temporal)
    side_effects.append(None)

    conn.fetchrow = AsyncMock(side_effect=side_effects)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool, conn, _eid, _oid


# ---------------------------------------------------------------------------
# Scenario 1: Subject type mismatch → warning
# ---------------------------------------------------------------------------


class TestSubjectTypeMismatch:
    """Subject entity type doesn't match expected_subject_type → warning in response."""

    async def test_subject_type_mismatch_produces_warning(self, embedding_engine):
        """WHEN subject entity_type='organization' but expected_subject_type='person',
        THEN the fact is still stored AND the response includes a warning.
        """
        entity_id = uuid.uuid4()
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "expected_subject_type": "person",
            "expected_object_type": None,
        }
        pool, conn, _eid, _oid = _make_pool_and_conn(
            entity_type="organization",
            registry_row=registry_row,
            has_entity_id=True,
            has_object_entity_id=False,
        )
        # Override the entity_id to match what we pass in
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": entity_id, "entity_type": "organization"},  # entity check
                None,  # alias resolution
                registry_row,  # registry lookup
                None,  # supersession check
            ]
        )

        result = await store_fact(
            pool,
            "AcmeCorp",
            "parent_of",
            "SubsidiaryCorp",
            embedding_engine,
            entity_id=entity_id,
        )

        # Fact must still be stored (INSERT was called)
        assert any(
            "INSERT" in (c.args[0] if c.args else "") for c in conn.execute.call_args_list
        ), "Expected INSERT into facts to be called"

        # Response must contain a warning about subject type mismatch
        assert isinstance(result, dict)
        assert isinstance(result["id"], uuid.UUID)
        assert "warnings" in result, "Expected 'warnings' key in response"
        assert len(result["warnings"]) >= 1
        warning_text = " ".join(result["warnings"])
        assert "subject" in warning_text.lower() or "Subject" in warning_text
        assert "person" in warning_text
        assert "organization" in warning_text

    async def test_subject_type_mismatch_fact_is_stored_successfully(self, embedding_engine):
        """Subject type mismatch must NOT block the write — Wikidata soft-warning pattern."""
        entity_id = uuid.uuid4()
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "expected_subject_type": "person",
            "expected_object_type": None,
        }
        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": entity_id, "entity_type": "organization"},
                None,  # alias resolution
                registry_row,
                None,
            ]
        )
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        # Must not raise
        result = await store_fact(
            pool,
            "AcmeCorp",
            "parent_of",
            "SubsidiaryCorp",
            embedding_engine,
            entity_id=entity_id,
        )

        assert isinstance(result, dict)
        assert "id" in result

    async def test_subject_type_warning_names_expected_and_actual(self, embedding_engine):
        """Warning message must name both the expected and actual entity types."""
        entity_id = uuid.uuid4()
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "expected_subject_type": "person",
            "expected_object_type": None,
        }
        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": entity_id, "entity_type": "location"},  # wrong type
                None,  # alias resolution
                registry_row,
                None,
            ]
        )
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        result = await store_fact(
            pool,
            "London",
            "parent_of",
            "something",
            embedding_engine,
            entity_id=entity_id,
        )

        warnings = result.get("warnings", [])
        assert warnings, "Expected warnings for type mismatch"
        warning_text = " ".join(warnings)
        assert "person" in warning_text  # expected
        assert "location" in warning_text  # actual


# ---------------------------------------------------------------------------
# Scenario 2: Object type mismatch → warning
# ---------------------------------------------------------------------------


class TestObjectTypeMismatch:
    """Object entity type doesn't match expected_object_type → warning in response."""

    async def test_object_type_mismatch_produces_warning(self, embedding_engine):
        """WHEN object entity_type='person' but expected_object_type='organization',
        THEN the fact is still stored AND the response includes a warning.
        """
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()
        registry_row = {
            "is_edge": True,
            "is_temporal": False,
            "expected_subject_type": None,
            "expected_object_type": "organization",
        }
        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": entity_id, "entity_type": "person"},  # subject entity check
                {"id": object_entity_id, "entity_type": "person"},  # object entity (wrong type)
                None,  # alias resolution
                registry_row,  # registry lookup
                None,  # supersession check
            ]
        )
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        result = await store_fact(
            pool,
            "Alice",
            "works_at",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        # Fact must still be stored
        assert isinstance(result, dict)
        assert isinstance(result["id"], uuid.UUID)

        # Warning must be in response
        assert "warnings" in result, "Expected 'warnings' key for object type mismatch"
        warning_text = " ".join(result["warnings"])
        assert "object" in warning_text.lower() or "Object" in warning_text
        assert "organization" in warning_text  # expected
        assert "person" in warning_text  # actual

    async def test_object_type_mismatch_fact_is_stored_successfully(self, embedding_engine):
        """Object type mismatch must NOT raise — the fact is stored as-is."""
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()
        registry_row = {
            "is_edge": True,
            "is_temporal": False,
            "expected_subject_type": None,
            "expected_object_type": "organization",
        }
        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": entity_id, "entity_type": "person"},
                {"id": object_entity_id, "entity_type": "person"},
                None,  # alias resolution
                registry_row,
                None,
            ]
        )
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        # Must not raise
        result = await store_fact(
            pool,
            "Alice",
            "works_at",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        assert isinstance(result, dict)
        assert "id" in result


# ---------------------------------------------------------------------------
# Scenario 3: NULL expected types → no validation, no warning
# ---------------------------------------------------------------------------


class TestNullExpectedTypesSkipValidation:
    """NULL expected_subject_type or expected_object_type skips type validation."""

    async def test_null_expected_subject_type_produces_no_warning(self, embedding_engine):
        """WHEN expected_subject_type is NULL, no subject type warning is produced."""
        entity_id = uuid.uuid4()
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "expected_subject_type": None,  # NULL — skip validation
            "expected_object_type": None,
        }
        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": entity_id, "entity_type": "organization"},  # any entity_type
                None,  # alias resolution
                registry_row,
                None,
            ]
        )
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        result = await store_fact(
            pool,
            "AcmeCorp",
            "birthday",
            "2000-01-01",
            embedding_engine,
            entity_id=entity_id,
        )

        assert isinstance(result, dict)
        # No warnings should appear when expected_subject_type is NULL
        assert "warnings" not in result or result["warnings"] == []

    async def test_null_expected_object_type_produces_no_warning(self, embedding_engine):
        """WHEN expected_object_type is NULL, no object type warning is produced."""
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()
        registry_row = {
            "is_edge": True,
            "is_temporal": False,
            "expected_subject_type": None,
            "expected_object_type": None,  # NULL — skip validation
        }
        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": entity_id, "entity_type": "person"},
                {"id": object_entity_id, "entity_type": "location"},  # mismatched but NULL expected
                None,  # alias resolution
                registry_row,
                None,
            ]
        )
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        result = await store_fact(
            pool,
            "Alice",
            "works_at",
            "SomePlaceObj",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        assert isinstance(result, dict)
        assert "warnings" not in result or result["warnings"] == []

    async def test_no_entity_id_no_warning_even_when_expected_type_set(self, embedding_engine):
        """WHEN entity_id is not provided, no entity type is known → no warning.

        The actual entity_type can only be validated when entity_id is provided.
        Without entity_id, entity_type is None and validation is skipped.
        """
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "expected_subject_type": "person",
            "expected_object_type": None,
        }
        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])
        # No entity_id → fetchrow calls: alias, registry, supersession
        conn.fetchrow = AsyncMock(side_effect=[None, registry_row, None])
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        result = await store_fact(
            pool,
            "freetext_subject",
            "birthday",
            "1990-01-01",
            embedding_engine,
            # entity_id intentionally omitted
        )

        assert isinstance(result, dict)
        assert "warnings" not in result or result["warnings"] == []


# ---------------------------------------------------------------------------
# Scenario 4: Matching types → no warning
# ---------------------------------------------------------------------------


class TestMatchingTypesNoWarning:
    """When actual entity types match expected types, no warning is produced."""

    async def test_matching_subject_type_produces_no_warning(self, embedding_engine):
        """WHEN entity_type='person' and expected_subject_type='person', no warning."""
        entity_id = uuid.uuid4()
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "expected_subject_type": "person",
            "expected_object_type": None,
        }
        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": entity_id, "entity_type": "person"},  # matching type
                None,  # alias resolution
                registry_row,
                None,
            ]
        )
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        result = await store_fact(
            pool,
            "Alice",
            "parent_of",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
        )

        assert isinstance(result, dict)
        assert "warnings" not in result or result["warnings"] == []

    async def test_matching_both_types_produces_no_warning(self, embedding_engine):
        """WHEN both subject and object entity types match expectations, no warning."""
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()
        registry_row = {
            "is_edge": True,
            "is_temporal": False,
            "expected_subject_type": "person",
            "expected_object_type": "person",
        }
        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": entity_id, "entity_type": "person"},  # subject matches
                {"id": object_entity_id, "entity_type": "person"},  # object matches
                None,  # alias resolution
                registry_row,
                None,
            ]
        )
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        result = await store_fact(
            pool,
            "Alice",
            "parent_of",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        assert isinstance(result, dict)
        assert "warnings" not in result or result["warnings"] == []


# ---------------------------------------------------------------------------
# Task 12.1: entity_type fetched in same query (no extra round-trip)
# ---------------------------------------------------------------------------


class TestEntityTypeFetchedInSameQuery:
    """entity_type is fetched in the same query as the existence check (D7)."""

    async def test_entity_validation_uses_select_id_entity_type(self, embedding_engine):
        """The entity existence check fetches entity_type in the same query.

        WHEN store_fact() validates entity_id,
        THEN the SQL query MUST include 'entity_type' (not just 'SELECT 1').
        This ensures no additional round-trip is needed for type validation.
        """
        entity_id = uuid.uuid4()
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "expected_subject_type": None,
            "expected_object_type": None,
        }
        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": entity_id, "entity_type": "person"},
                None,  # alias resolution
                registry_row,
                None,
            ]
        )
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        await store_fact(
            pool,
            "Alice",
            "birthday",
            "1990-01-01",
            embedding_engine,
            entity_id=entity_id,
        )

        # Verify the first fetchrow call queries shared.entities with entity_type
        entity_calls = [
            c
            for c in conn.fetchrow.call_args_list
            if "shared.entities" in (c.args[0] if c.args else "")
        ]
        assert entity_calls, "Expected a fetchrow call targeting shared.entities"
        entity_sql = entity_calls[0].args[0]
        assert "entity_type" in entity_sql, (
            "Entity existence check must fetch entity_type in the same query"
        )

    async def test_object_entity_validation_uses_select_id_entity_type(self, embedding_engine):
        """The object entity existence check also fetches entity_type in the same query."""
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()
        registry_row = {
            "is_edge": True,
            "is_temporal": False,
            "expected_subject_type": None,
            "expected_object_type": None,
        }
        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": entity_id, "entity_type": "person"},
                {"id": object_entity_id, "entity_type": "organization"},
                None,  # alias resolution
                registry_row,
                None,
            ]
        )
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        await store_fact(
            pool,
            "Alice",
            "works_at",
            "AcmeCorp",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        # Verify both entity fetchrow calls include entity_type
        entity_calls = [
            c
            for c in conn.fetchrow.call_args_list
            if "shared.entities" in (c.args[0] if c.args else "")
        ]
        assert len(entity_calls) == 2, "Expected two entity existence fetchrow calls"
        for call in entity_calls:
            assert "entity_type" in call.args[0], (
                "Each entity existence check must fetch entity_type"
            )


# ---------------------------------------------------------------------------
# Combined: both subject AND object type mismatch
# ---------------------------------------------------------------------------


class TestBothTypesMismatch:
    """Both subject and object types mismatch → two warnings."""

    async def test_both_type_mismatches_produce_two_warnings(self, embedding_engine):
        """WHEN both subject and object types mismatch, the response includes two warnings."""
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()
        registry_row = {
            "is_edge": True,
            "is_temporal": False,
            "expected_subject_type": "person",
            "expected_object_type": "organization",
        }
        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": entity_id, "entity_type": "organization"},  # subject wrong
                {"id": object_entity_id, "entity_type": "person"},  # object wrong
                None,  # alias resolution
                registry_row,
                None,
            ]
        )
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        result = await store_fact(
            pool,
            "AcmeCorp",
            "works_at",
            "Alice",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        assert isinstance(result, dict)
        assert "warnings" in result
        assert len(result["warnings"]) == 2, (
            f"Expected 2 warnings for both type mismatches, got {len(result['warnings'])}"
        )
        warning_text = " ".join(result["warnings"])
        # Subject warning
        assert "organization" in warning_text  # actual subject type
        assert "person" in warning_text  # expected subject type
        # Object warning (person vs organization overlap — just check organization appears)
        # Both 'organization' and 'person' appear in different warnings


# ---------------------------------------------------------------------------
# Unregistered predicate → no type warnings
# ---------------------------------------------------------------------------


class TestUnregisteredPredicateNoTypeWarnings:
    """Novel predicates (not in registry) never produce type warnings."""

    async def test_novel_predicate_with_entity_produces_no_type_warning(self, embedding_engine):
        """Unregistered predicate with entity: no type warning (no expected types to compare)."""
        entity_id = uuid.uuid4()
        # registry_row = None means unregistered
        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": entity_id, "entity_type": "organization"},
                None,  # alias resolution
                None,  # registry lookup → not found
                None,  # supersession check
            ]
        )
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        result = await store_fact(
            pool,
            "AcmeCorp",
            "custom_novel_predicate",
            "some value",
            embedding_engine,
            entity_id=entity_id,
        )

        assert isinstance(result, dict)
        assert "warnings" not in result or result["warnings"] == []
