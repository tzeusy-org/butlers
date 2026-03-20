"""Unit tests for inverse_of and is_symmetric auto-fact creation in store_fact().

Covers issue bu-h2la: Inverse and symmetric predicates for bidirectional traversal.

Scenarios:
  Symmetric predicates:
    - is_symmetric=true auto-creates mirrored fact (object→subject, same predicate)
    - mirrored fact is stored in the same transaction
    - idempotency key for mirrored temporal fact prevents duplicates
    - non-symmetric predicate does NOT create mirrored fact

  Inverse predicates:
    - inverse_of='child_of' auto-creates fact with inverse predicate
    - inverse subject/content labels are swapped
    - existing inverse property fact is superseded

  Edge-only:
    - no inverse fact is created for non-edge facts (entity_id or object_entity_id absent)
    - no inverse fact is created for novel predicates (no registry row)
    - no inverse fact when is_symmetric=false and inverse_of=NULL
"""

from __future__ import annotations

import importlib.util
import inspect
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.modules.memory._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load storage module from disk (not a package).
# ---------------------------------------------------------------------------
_MEMORY_MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "src" / "butlers" / "modules" / "memory"
)
_STORAGE_PATH = _MEMORY_MODULE_PATH / "storage.py"


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
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


@pytest.fixture()
def embedding_engine():
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


def _make_registry_row(
    *,
    is_edge: bool = True,
    is_temporal: bool = False,
    status: str = "active",
    superseded_by: str | None = None,
    expected_subject_type: str | None = "person",
    expected_object_type: str | None = "person",
    inverse_of: str | None = None,
    is_symmetric: bool = False,
) -> dict:
    return {
        "is_edge": is_edge,
        "is_temporal": is_temporal,
        "status": status,
        "superseded_by": superseded_by,
        "expected_subject_type": expected_subject_type,
        "expected_object_type": expected_object_type,
        "inverse_of": inverse_of,
        "is_symmetric": is_symmetric,
    }


def _make_pool(
    *,
    registry_row: dict | None,
    entity_id: uuid.UUID,
    object_entity_id: uuid.UUID,
    has_existing_inverse: bool = False,
    is_temporal: bool = False,
):
    """Build a (pool, conn) mock pair for edge-fact tests.

    fetchrow call order (property edge fact):
      1. entity_id existence check
      2. object_entity_id existence check
      3. predicate_registry lookup
      4. supersession check (forward) → None
      5. supersession check (inverse, if applicable) → None or existing row

    fetchval order (temporal edge fact):
      1. idempotency dedup check (forward) → None
      2. idempotency dedup check (inverse) → None
    """
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    conn.execute = AsyncMock()

    _entity_row = {"id": entity_id, "entity_type": "person"}
    _obj_entity_row = {"id": object_entity_id, "entity_type": "person"}
    _existing_inv_row = {"id": uuid.uuid4()} if has_existing_inverse else None

    if is_temporal:
        # Property supersession checks are skipped for temporal facts;
        # idempotency uses fetchval instead.
        conn.fetchval = AsyncMock(return_value=None)

        async def _fetchrow_temporal(sql, *args):
            if "shared.entities" in sql:
                _fetchrow_temporal._count += 1
                return _entity_row if _fetchrow_temporal._count == 1 else _obj_entity_row
            elif "predicate_registry" in sql:
                return registry_row
            return None

        _fetchrow_temporal._count = 0
        conn.fetchrow = AsyncMock(side_effect=_fetchrow_temporal)
    else:
        # Property fact: multiple fetchrow calls in sequence.
        async def _fetchrow_dispatch(sql, *args):
            if "shared.entities" in sql:
                _fetchrow_dispatch._entity_call_count += 1
                if _fetchrow_dispatch._entity_call_count == 1:
                    return _entity_row
                return _obj_entity_row
            elif "predicate_registry" in sql:
                return registry_row
            else:
                # Supersession checks: forward first, then inverse (if any).
                _fetchrow_dispatch._super_call_count += 1
                if _fetchrow_dispatch._super_call_count == 1:
                    return None  # no existing forward fact
                else:
                    return _existing_inv_row  # may have existing inverse

        _fetchrow_dispatch._entity_call_count = 0
        _fetchrow_dispatch._super_call_count = 0
        conn.fetchrow = AsyncMock(side_effect=_fetchrow_dispatch)
        conn.fetchval = AsyncMock(return_value=None)

    conn.fetch = AsyncMock(return_value=[])

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool, conn


# ---------------------------------------------------------------------------
# Symmetric predicates
# ---------------------------------------------------------------------------


class TestSymmetricPredicates:
    """is_symmetric=true edge predicates auto-create a mirrored (reversed) fact."""

    async def test_symmetric_creates_two_inserts(self, embedding_engine):
        """Storing sibling_of(A→B) also stores sibling_of(B→A) in the same TX.

        WHEN store_fact() is called with is_symmetric=true predicate 'sibling_of'
        AND entity_id + object_entity_id are both set (edge fact),
        THEN conn.execute is called at least twice for INSERT INTO facts —
        once for the forward fact and once for the inverse.
        """
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()
        row = _make_registry_row(is_symmetric=True)
        pool, conn = _make_pool(
            registry_row=row,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        await store_fact(
            pool,
            "Alice",
            "sibling_of",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        insert_calls = [
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        ]
        assert len(insert_calls) == 2, (
            f"Expected 2 INSERT INTO facts calls (forward + inverse), got {len(insert_calls)}"
        )

    async def test_symmetric_inverse_uses_same_predicate(self, embedding_engine):
        """The mirrored fact for a symmetric predicate uses the same predicate name."""
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()
        row = _make_registry_row(is_symmetric=True)
        pool, conn = _make_pool(
            registry_row=row,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        await store_fact(
            pool,
            "Alice",
            "sibling_of",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        insert_calls = [
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        ]
        assert len(insert_calls) == 2
        # Both INSERTs must use 'sibling_of' as the predicate (param index 3).
        for ic in insert_calls:
            # The predicate is the 3rd positional param ($3 → index 3 in args).
            assert ic.args[3] == "sibling_of", (
                f"Expected 'sibling_of' predicate in inverse INSERT, got {ic.args[3]!r}"
            )

    async def test_symmetric_inverse_swaps_entity_ids(self, embedding_engine):
        """Mirrored fact has entity_id/object_entity_id swapped relative to forward fact."""
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()
        row = _make_registry_row(is_symmetric=True)
        pool, conn = _make_pool(
            registry_row=row,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        await store_fact(
            pool,
            "Alice",
            "sibling_of",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        insert_calls = [
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        ]
        assert len(insert_calls) == 2

        # Collect (entity_id, object_entity_id) from both INSERTs.
        # The INSERT SQL has 26 $-params; entity_id is $18 (args index 18) and
        # object_entity_id is $19 (args index 19).
        # args[0] is the SQL; args[1..26] are the param values.
        first_args = insert_calls[0].args
        second_args = insert_calls[1].args

        # entity_id is $18 (args index 18), object_entity_id is $19 (args index 19).
        fwd_entity = first_args[18]
        fwd_object = first_args[19]
        inv_entity = second_args[18]
        inv_object = second_args[19]

        assert fwd_entity == entity_id
        assert fwd_object == object_entity_id
        assert inv_entity == object_entity_id
        assert inv_object == entity_id

    async def test_non_symmetric_no_inverse_created(self, embedding_engine):
        """Non-symmetric predicate without inverse_of does NOT create a second fact."""
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()
        row = _make_registry_row(is_symmetric=False, inverse_of=None)
        pool, conn = _make_pool(
            registry_row=row,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        await store_fact(
            pool,
            "Alice",
            "works_at",
            "Acme Corp",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        insert_calls = [
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        ]
        assert len(insert_calls) == 1, (
            f"Expected exactly 1 INSERT for non-symmetric predicate, got {len(insert_calls)}"
        )


# ---------------------------------------------------------------------------
# Inverse predicates
# ---------------------------------------------------------------------------


class TestInversePredicates:
    """inverse_of edge predicates auto-create a fact with the named inverse predicate."""

    async def test_inverse_of_creates_two_inserts(self, embedding_engine):
        """Storing parent_of(A→B) also stores child_of(B→A).

        WHEN store_fact() is called with predicate 'parent_of' (inverse_of='child_of')
        AND entity_id + object_entity_id are both set,
        THEN two INSERT INTO facts calls are made.
        """
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()
        row = _make_registry_row(inverse_of="child_of", is_symmetric=False)
        pool, conn = _make_pool(
            registry_row=row,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        await store_fact(
            pool,
            "Alice",
            "parent_of",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        insert_calls = [
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        ]
        assert len(insert_calls) == 2

    async def test_inverse_fact_uses_inverse_predicate_name(self, embedding_engine):
        """The inverse fact uses the inverse_of predicate name, not the forward predicate."""
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()
        row = _make_registry_row(inverse_of="child_of", is_symmetric=False)
        pool, conn = _make_pool(
            registry_row=row,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        await store_fact(
            pool,
            "Alice",
            "parent_of",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        insert_calls = [
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        ]
        assert len(insert_calls) == 2
        predicates = {ic.args[3] for ic in insert_calls}
        assert "parent_of" in predicates
        assert "child_of" in predicates

    async def test_inverse_fact_swaps_entity_ids(self, embedding_engine):
        """The inverse fact has entity_id/object_entity_id swapped."""
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()
        row = _make_registry_row(inverse_of="child_of", is_symmetric=False)
        pool, conn = _make_pool(
            registry_row=row,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        await store_fact(
            pool,
            "Alice",
            "parent_of",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        insert_calls = [
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        ]
        assert len(insert_calls) == 2

        # Find the inverse INSERT (predicate == 'child_of').
        # entity_id is $18 (args index 18), object_entity_id is $19 (args index 19).
        inv_call = next(ic for ic in insert_calls if ic.args[3] == "child_of")
        inv_entity = inv_call.args[18]
        inv_object = inv_call.args[19]
        assert inv_entity == object_entity_id
        assert inv_object == entity_id


# ---------------------------------------------------------------------------
# No inverse for non-edge / novel-predicate / no-registry scenarios
# ---------------------------------------------------------------------------


class TestNoInverseForNonEdge:
    """Inverse/symmetric logic is only applied to edge facts with a registry row."""

    async def test_no_entity_id_no_inverse(self, embedding_engine):
        """No inverse created when entity_id is None (non-edge fact)."""
        # No entity_id means no edge fact; no object_entity_id either.
        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(side_effect=[None, None])  # registry=None, supersession=None
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        await store_fact(
            pool,
            "Alice",
            "note",
            "some note",
            embedding_engine,
            # No entity_id or object_entity_id
        )

        insert_calls = [
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        ]
        assert len(insert_calls) == 1

    async def test_novel_predicate_no_inverse(self, embedding_engine):
        """No inverse created when predicate is not in the registry (novel predicate)."""
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()

        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])

        _entity_row = {"id": entity_id, "entity_type": "person"}
        _obj_entity_row = {"id": object_entity_id, "entity_type": "person"}

        async def _fetchrow_dispatch(sql, *args):
            if "shared.entities" in sql:
                _fetchrow_dispatch._count += 1
                return _entity_row if _fetchrow_dispatch._count == 1 else _obj_entity_row
            elif "predicate_registry" in sql:
                return None  # novel predicate — not in registry
            return None  # supersession

        _fetchrow_dispatch._count = 0
        conn.fetchrow = AsyncMock(side_effect=_fetchrow_dispatch)

        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        await store_fact(
            pool,
            "Alice",
            "novel_edge_predicate",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        insert_calls = [
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        ]
        # Only the forward fact; novel predicates have no inverse_of or is_symmetric.
        assert len(insert_calls) == 1

    async def test_no_inverse_when_both_flags_absent(self, embedding_engine):
        """Edge predicate with is_symmetric=false and inverse_of=None: no inverse created."""
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()
        row = _make_registry_row(is_symmetric=False, inverse_of=None)
        pool, conn = _make_pool(
            registry_row=row,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        await store_fact(
            pool,
            "Alice",
            "manages",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        insert_calls = [
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        ]
        assert len(insert_calls) == 1


# ---------------------------------------------------------------------------
# Supersession of existing inverse property fact
# ---------------------------------------------------------------------------


class TestInverseSupersession:
    """When an inverse property fact already exists, it is superseded."""

    async def test_existing_inverse_property_fact_is_superseded(self, embedding_engine):
        """If a prior inverse fact exists, it is marked superseded before inserting the new one.

        WHEN parent_of(A→B) is stored (inverse_of='child_of')
        AND child_of(B→A) already exists as an active property fact,
        THEN the old inverse fact is superseded (UPDATE facts SET validity='superseded').
        """
        entity_id = uuid.uuid4()
        object_entity_id = uuid.uuid4()
        row = _make_registry_row(inverse_of="child_of", is_symmetric=False)
        pool, conn = _make_pool(
            registry_row=row,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
            has_existing_inverse=True,
        )

        await store_fact(
            pool,
            "Alice",
            "parent_of",
            "Bob",
            embedding_engine,
            entity_id=entity_id,
            object_entity_id=object_entity_id,
        )

        # There should be at least one UPDATE facts SET validity='superseded' call.
        update_calls = [
            c
            for c in conn.execute.call_args_list
            if "UPDATE facts" in c.args[0] and "superseded" in c.args[0]
        ]
        assert len(update_calls) >= 1, (
            "Expected at least one supersession UPDATE for the prior inverse fact"
        )


# ---------------------------------------------------------------------------
# Migration structure test
# ---------------------------------------------------------------------------


def _load_migration_025():
    """Load and return the migration 025 module."""
    mig = MEMORY_MODULE_PATH / "migrations" / "025_predicate_inverse_symmetric.py"
    spec = importlib.util.spec_from_file_location("mig_025", mig)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMigration025Structure:
    """Validate that migration 025 has the correct structure."""

    def test_migration_file_exists(self) -> None:
        mig = MEMORY_MODULE_PATH / "migrations" / "025_predicate_inverse_symmetric.py"
        assert mig.exists(), f"Expected migration file at {mig}"

    def test_revision_identifiers(self) -> None:
        mod = _load_migration_025()
        assert mod.revision == "mem_025"
        assert mod.down_revision == "mem_024"

    def test_upgrade_adds_inverse_of_column(self) -> None:
        mod = _load_migration_025()
        source = inspect.getsource(mod.upgrade)
        assert "inverse_of" in source
        assert "is_symmetric" in source

    def test_seeds_symmetric_predicates(self) -> None:
        """Module-level _SYMMETRIC_PREDICATES constant lists expected predicates."""
        mod = _load_migration_025()
        symmetric = mod._SYMMETRIC_PREDICATES
        for p in ("sibling_of", "knows", "lives_with"):
            assert p in symmetric, f"Expected {p!r} in _SYMMETRIC_PREDICATES"

    def test_seeds_inverse_pairs(self) -> None:
        """_INVERSE_PAIRS contains parent_of/child_of and manages/managed_by."""
        mod = _load_migration_025()
        pairs = mod._INVERSE_PAIRS
        pair_names = {(fwd, inv) for fwd, inv, *_ in pairs}
        assert ("parent_of", "child_of") in pair_names
        assert ("manages", "managed_by") in pair_names

    def test_has_upgrade_and_downgrade(self) -> None:
        mod = _load_migration_025()
        assert callable(getattr(mod, "upgrade", None))
        assert callable(getattr(mod, "downgrade", None))
