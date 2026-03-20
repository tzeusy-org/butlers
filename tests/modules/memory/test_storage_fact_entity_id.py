"""Tests for entity_id support in store_fact() storage layer.

Covers acceptance criteria for butlers-nrov.6:
  1. Facts stored with entity_id use entity-keyed uniqueness/supersession.
  2. Facts stored without entity_id use subject-keyed uniqueness (backward compat).
  3. Invalid entity_id raises a clear ValueError.
  4. Supersession logic works correctly with entity-keyed facts.
  5. Supersession logic works correctly with subject-keyed facts (unchanged).
"""

from __future__ import annotations

import importlib.util
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.modules.memory._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load storage module
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
    """Simple async context manager wrapper returning a fixed value."""

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


def _make_pool(conn):
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool


def _make_conn(
    *,
    fetchval_return=None,
    fetchrow_return=None,
    fetchval_side_effect=None,
    fetchrow_side_effect=None,
):
    """Build a mock conn with configurable fetchval/fetchrow returns.

    fetchval_side_effect overrides fetchval_return when provided. Use it to
    supply a sequence of return values for multiple fetchval calls (e.g.
    [1, 1] means entity-valid then object-entity-valid; no is_temporal call
    is made since supersession is now based on valid_at nullness, not registry).

    fetchrow_side_effect overrides fetchrow_return when provided. Use it to
    supply a sequence of return values for multiple fetchrow calls. The first
    fetchrow call is always the predicate_registry lookup inside store_fact();
    subsequent calls are supersession checks. When a supersession fixture needs
    to return an existing fact row, pass side_effect=[None, {"id": old_id}] so
    the registry lookup gets None (unregistered predicate) and the supersession
    check gets the existing row.
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
    return conn


# ---------------------------------------------------------------------------
# Tests: entity_id validation
# ---------------------------------------------------------------------------


class TestEntityIdValidation:
    """entity_id is validated before any write."""

    async def test_invalid_entity_id_raises_value_error(self, embedding_engine):
        """Providing a non-existent entity_id raises ValueError."""
        bad_eid = uuid.uuid4()
        conn = _make_conn(fetchval_return=None)  # entity not found
        pool = _make_pool(conn)

        with pytest.raises(ValueError, match="does not exist in entities table"):
            await store_fact(
                pool,
                "Alice",
                "job_title",
                "Engineer",
                embedding_engine,
                entity_id=bad_eid,
            )

    async def test_entity_validation_query_uses_correct_id(self, embedding_engine):
        """Validation SELECT is called with the supplied entity_id."""
        eid = uuid.uuid4()
        conn = _make_conn(fetchval_return=None)
        pool = _make_pool(conn)

        with pytest.raises(ValueError):
            await store_fact(
                pool,
                "Alice",
                "job_title",
                "Engineer",
                embedding_engine,
                entity_id=eid,
            )

        conn.fetchval.assert_awaited_once()
        call_args = conn.fetchval.call_args
        assert "entities" in call_args.args[0]
        assert call_args.args[1] == eid

    async def test_valid_entity_id_does_not_raise(self, embedding_engine):
        """Valid entity_id (exists in DB) proceeds without error."""
        eid = uuid.uuid4()
        # fetchval returns: entity=1 (exists); no is_temporal check (supersession
        # is now based on valid_at nullness, not predicate_registry).
        conn = _make_conn(fetchval_return=1)
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "Alice",
            "job_title",
            "Engineer",
            embedding_engine,
            entity_id=eid,
        )

        # store_fact() now returns a dict with 'id' (UUID) and optional keys
        assert isinstance(result, dict)
        assert isinstance(result["id"], uuid.UUID)

    async def test_no_entity_validation_when_entity_id_omitted(self, embedding_engine):
        """Neither entity check nor predicate_registry is queried when entity_id is None.

        Supersession is now determined by valid_at nullness, not registry lookup.
        No fetchval calls are expected when entity_id is not provided.
        """
        conn = _make_conn()
        pool = _make_pool(conn)

        await store_fact(pool, "user", "color", "blue", embedding_engine)

        # No fetchval calls at all — entity check skipped, predicate registry not consulted
        conn.fetchval.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: entity_id stored in fact row
# ---------------------------------------------------------------------------


class TestEntityIdStoredInFact:
    """entity_id is persisted in the INSERT statement."""

    async def test_entity_id_included_in_insert(self, embedding_engine):
        """When entity_id is provided, it appears in the INSERT args."""
        eid = uuid.uuid4()
        # fetchval returns: entity=1 (exists); no is_temporal check needed.
        conn = _make_conn(fetchval_return=1)
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "Alice",
            "job_title",
            "Engineer",
            embedding_engine,
            entity_id=eid,
        )

        # Find the INSERT INTO facts call
        insert_call = next(
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        )
        sql = insert_call.args[0]
        assert "entity_id" in sql
        # entity_id=$18, object_entity_id=$19, valid_at=$20, tenant_id=$21, request_id=$22,
        # idempotency_key=$23, observed_at=$24, retention_class=$25, sensitivity=$26
        assert insert_call.args[-9] == eid
        assert insert_call.args[-8] is None  # object_entity_id not set
        # valid_at is NULL (property fact — omitted valid_at)
        assert insert_call.args[-7] is None

    async def test_entity_id_null_in_insert_when_omitted(self, embedding_engine):
        """When entity_id is not provided, NULL is stored."""
        conn = _make_conn()
        pool = _make_pool(conn)

        await store_fact(pool, "user", "color", "blue", embedding_engine)

        insert_call = conn.execute.call_args_list[0]
        assert "entity_id" in insert_call.args[0]
        assert insert_call.args[-9] is None  # entity_id
        assert insert_call.args[-8] is None  # object_entity_id
        # valid_at is NULL (property fact — omitted valid_at)
        assert insert_call.args[-7] is None


# ---------------------------------------------------------------------------
# Tests: entity-keyed supersession
# ---------------------------------------------------------------------------


class TestEntityKeyedSupersession:
    """When entity_id is provided, supersession uses (entity_id, scope, predicate)."""

    @pytest.fixture()
    def pool_with_existing_entity_fact(self):
        """Pool/conn where an existing entity-keyed fact is found.

        First fetchrow: predicate_registry lookup → None (unregistered predicate).
        Second fetchrow: supersession check → existing fact row.
        """
        old_id = uuid.uuid4()
        eid = uuid.uuid4()
        # fetchval: entity=1 (exists); supersession is now valid_at-based, not registry-based.
        conn = _make_conn(
            fetchval_return=1,
            fetchrow_side_effect=[None, {"id": old_id}],
        )
        pool = _make_pool(conn)
        return pool, conn, old_id, eid

    async def test_entity_keyed_supersession_lookup_sql(
        self, pool_with_existing_entity_fact, embedding_engine
    ):
        """Supersession lookup uses entity_id, scope, predicate — not subject."""
        pool, conn, _old_id, eid = pool_with_existing_entity_fact

        await store_fact(
            pool,
            "Alice",
            "job_title",
            "Senior Engineer",
            embedding_engine,
            entity_id=eid,
            scope="global",
        )

        # The fetchrow call (supersession check) should query by entity_id
        fetchrow_call = conn.fetchrow.call_args
        sql = fetchrow_call.args[0]
        assert "entity_id" in sql
        assert "subject" not in sql
        # args[1] = tenant_id, args[2] = entity_id (tenant_id=$1, entity_id=$2 in SQL)
        assert fetchrow_call.args[2] == eid

    async def test_entity_keyed_old_fact_marked_superseded(
        self, pool_with_existing_entity_fact, embedding_engine
    ):
        """Old fact is marked superseded when entity-keyed match found."""
        pool, conn, old_id, eid = pool_with_existing_entity_fact

        await store_fact(
            pool, "Alice", "job_title", "Senior Engineer", embedding_engine, entity_id=eid
        )

        update_call = conn.execute.call_args_list[0]
        assert "UPDATE facts SET validity = 'superseded'" in update_call.args[0]
        assert update_call.args[1] == old_id

    async def test_entity_keyed_supersedes_id_set_on_new_fact(
        self, pool_with_existing_entity_fact, embedding_engine
    ):
        """New fact's supersedes_id is set to old fact ID."""
        pool, conn, old_id, eid = pool_with_existing_entity_fact

        await store_fact(
            pool, "Alice", "job_title", "Senior Engineer", embedding_engine, entity_id=eid
        )

        insert_call = next(
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        )
        # supersedes_id is $13 (index 13)
        assert insert_call.args[13] == old_id

    async def test_entity_keyed_memory_link_created(
        self, pool_with_existing_entity_fact, embedding_engine
    ):
        """A memory_links supersedes row is created for entity-keyed supersession."""
        pool, conn, old_id, eid = pool_with_existing_entity_fact

        result = await store_fact(
            pool, "Alice", "job_title", "Senior Engineer", embedding_engine, entity_id=eid
        )
        # store_fact() now returns a dict with 'id'
        new_id = result["id"]

        link_call = next(
            c for c in conn.execute.call_args_list if "INSERT INTO memory_links" in c.args[0]
        )
        assert "'supersedes'" in link_call.args[0]
        assert link_call.args[1] == new_id
        assert link_call.args[2] == old_id

    async def test_entity_keyed_execute_count_with_supersession(
        self, pool_with_existing_entity_fact, embedding_engine
    ):
        """Four execute calls: UPDATE old, INSERT fact, INSERT link, INSERT predicate_registry."""
        pool, conn, _old_id, eid = pool_with_existing_entity_fact

        await store_fact(
            pool, "Alice", "job_title", "Senior Engineer", embedding_engine, entity_id=eid
        )

        assert conn.execute.call_count == 4

    async def test_entity_keyed_no_supersession_when_no_existing(self, embedding_engine):
        """No supersession when no existing entity-keyed property fact found."""
        eid = uuid.uuid4()
        # fetchval: entity=1 (exists), entity_type='person' (for auto-registration);
        # fetchrow: no existing property fact.
        conn = _make_conn(fetchval_side_effect=[1, "person"], fetchrow_return=None)
        pool = _make_pool(conn)

        await store_fact(pool, "Alice", "job_title", "Engineer", embedding_engine, entity_id=eid)

        # Two execute calls: INSERT facts + INSERT predicate_registry (auto-registration).
        assert conn.execute.call_count == 2
        insert_call = conn.execute.call_args_list[0]
        assert "INSERT INTO facts" in insert_call.args[0]
        assert insert_call.args[13] is None  # supersedes_id = None


# ---------------------------------------------------------------------------
# Tests: subject-keyed supersession unchanged (backward compat)
# ---------------------------------------------------------------------------


class TestSubjectKeyedSupersessionUnchanged:
    """Without entity_id, supersession behaviour is unchanged."""

    @pytest.fixture()
    def pool_with_existing_subject_fact(self):
        old_id = uuid.uuid4()
        # First fetchrow: registry lookup → None; second: supersession → existing fact.
        conn = _make_conn(fetchrow_side_effect=[None, {"id": old_id}])
        pool = _make_pool(conn)
        return pool, conn, old_id

    async def test_subject_keyed_supersession_lookup_sql(
        self, pool_with_existing_subject_fact, embedding_engine
    ):
        """Supersession lookup uses subject + predicate when entity_id is None."""
        pool, conn, _old_id = pool_with_existing_subject_fact

        await store_fact(pool, "user", "city", "Berlin", embedding_engine)

        fetchrow_call = conn.fetchrow.call_args
        sql = fetchrow_call.args[0]
        assert "subject" in sql
        # entity_id IS NULL filter must be present
        assert "entity_id IS NULL" in sql

    async def test_subject_keyed_old_fact_marked_superseded(
        self, pool_with_existing_subject_fact, embedding_engine
    ):
        pool, conn, old_id = pool_with_existing_subject_fact

        await store_fact(pool, "user", "city", "Munich", embedding_engine)

        update_call = conn.execute.call_args_list[0]
        assert "UPDATE facts SET validity = 'superseded'" in update_call.args[0]
        assert update_call.args[1] == old_id

    async def test_subject_keyed_execute_count_with_supersession(
        self, pool_with_existing_subject_fact, embedding_engine
    ):
        pool, conn, _old_id = pool_with_existing_subject_fact

        await store_fact(pool, "user", "city", "Munich", embedding_engine)

        # Four calls: UPDATE old, INSERT facts, INSERT link, INSERT predicate_registry
        assert conn.execute.call_count == 4

    async def test_subject_keyed_null_entity_id_in_insert(
        self, pool_with_existing_subject_fact, embedding_engine
    ):
        """entity_id, object_entity_id, and valid_at are all None for subject-keyed facts."""
        pool, conn, _old_id = pool_with_existing_subject_fact

        await store_fact(pool, "user", "city", "Munich", embedding_engine)

        insert_call = next(
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        )
        # entity_id=$18, object_entity_id=$19, valid_at=$20, tenant_id=$21, request_id=$22,
        # idempotency_key=$23, observed_at=$24, retention_class=$25, sensitivity=$26
        assert insert_call.args[-9] is None  # entity_id = None
        assert insert_call.args[-8] is None  # object_entity_id = None
        # valid_at = NULL (property fact — omitted valid_at)
        assert insert_call.args[-7] is None


# ---------------------------------------------------------------------------
# Tests: tool layer — memory_store_fact entity_id forwarding
# ---------------------------------------------------------------------------


class TestMemoryStoreFactEntityIdTool:
    """Test entity_id in the MCP tool wrapper (writing.py)."""

    async def test_entity_id_forwarded_as_uuid(self):
        """entity_id str is parsed to UUID and forwarded to storage."""
        from unittest.mock import patch

        from butlers.modules.memory.tools import _helpers, memory_store_fact

        pool = AsyncMock()
        engine = MagicMock()
        eid = uuid.uuid4()
        eid_str = str(eid)
        storage_result = {"id": uuid.uuid4()}

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_fact(
                pool, engine, "Alice", "job_title", "Engineer", entity_id=eid_str
            )
            call_kwargs = mock_store.call_args.kwargs
            assert call_kwargs["entity_id"] == eid

    async def test_entity_id_none_when_omitted(self):
        """entity_id is None when not supplied — backward compat."""
        from unittest.mock import patch

        from butlers.modules.memory.tools import _helpers, memory_store_fact

        pool = AsyncMock()
        engine = MagicMock()
        storage_result = {"id": uuid.uuid4()}

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_fact(pool, engine, "user", "city", "Berlin")
            call_kwargs = mock_store.call_args.kwargs
            assert call_kwargs["entity_id"] is None

    async def test_invalid_entity_id_uuid_string_raises(self):
        """Passing a non-UUID string for entity_id raises ValueError."""
        from unittest.mock import patch

        from butlers.modules.memory.tools import _helpers, memory_store_fact

        pool = AsyncMock()
        engine = MagicMock()

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock):
            with pytest.raises(ValueError):
                await memory_store_fact(
                    pool, engine, "user", "city", "Berlin", entity_id="not-a-uuid"
                )

    async def test_result_still_has_expected_keys_with_entity_id(self):
        """Result dict still contains 'id' and 'superseded_id' when entity_id used."""
        from unittest.mock import patch

        from butlers.modules.memory.tools import _helpers, memory_store_fact

        pool = AsyncMock()
        engine = MagicMock()
        eid = uuid.uuid4()
        storage_result = {"id": uuid.uuid4(), "supersedes_id": uuid.uuid4()}

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            result = await memory_store_fact(
                pool, engine, "Alice", "job_title", "Engineer", entity_id=str(eid)
            )
            assert set(result.keys()) == {"id", "superseded_id"}
            assert result["superseded_id"] is not None


# ---------------------------------------------------------------------------
# Tests: object_entity_id validation
# ---------------------------------------------------------------------------


class TestObjectEntityIdValidation:
    """object_entity_id is validated before any write."""

    async def test_object_entity_id_without_entity_id_raises(self, embedding_engine):
        """Providing object_entity_id without entity_id raises ValueError."""
        obj_eid = uuid.uuid4()
        conn = _make_conn()
        pool = _make_pool(conn)

        with pytest.raises(ValueError, match="requires entity_id"):
            await store_fact(
                pool,
                "Alice",
                "works_at",
                "Acme Corp",
                embedding_engine,
                object_entity_id=obj_eid,
            )

    async def test_self_referencing_edge_raises(self, embedding_engine):
        """entity_id == object_entity_id raises ValueError."""
        eid = uuid.uuid4()
        conn = _make_conn()
        conn.fetchval = AsyncMock(return_value=1)
        pool = _make_pool(conn)

        with pytest.raises(ValueError, match="[Ss]elf-referencing"):
            await store_fact(
                pool,
                "Alice",
                "knows",
                "herself",
                embedding_engine,
                entity_id=eid,
                object_entity_id=eid,
            )

    async def test_invalid_object_entity_id_raises_value_error(self, embedding_engine):
        """Providing a non-existent object_entity_id raises ValueError."""
        eid = uuid.uuid4()
        obj_eid = uuid.uuid4()
        # First fetchval (entity_id check) returns 1, second (object_entity_id) returns None
        conn = _make_conn()
        conn.fetchval = AsyncMock(side_effect=[1, None])
        pool = _make_pool(conn)

        with pytest.raises(ValueError, match="object_entity_id.*does not exist"):
            await store_fact(
                pool,
                "Alice",
                "works_at",
                "Acme Corp",
                embedding_engine,
                entity_id=eid,
                object_entity_id=obj_eid,
            )

    async def test_valid_object_entity_id_does_not_raise(self, embedding_engine):
        """Valid object_entity_id (exists in DB) proceeds without error."""
        eid = uuid.uuid4()
        obj_eid = uuid.uuid4()
        # fetchval: entity=1, obj=1, entity_type='person' (auto-registration lookup)
        conn = _make_conn(fetchval_side_effect=[1, 1, "person"])
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "Alice",
            "works_at",
            "engineer",
            embedding_engine,
            entity_id=eid,
            object_entity_id=obj_eid,
        )

        # store_fact() now returns a dict with 'id' (UUID) and optional keys
        assert isinstance(result, dict)
        assert isinstance(result["id"], uuid.UUID)

    async def test_object_entity_id_validation_calls_fetchval_twice(self, embedding_engine):
        """fetchval called for: entity_id, object_entity_id, and entity_type lookup."""
        eid = uuid.uuid4()
        obj_eid = uuid.uuid4()
        # fetchval: entity=1, obj=1, entity_type='person' (auto-registration lookup)
        conn = _make_conn(fetchval_side_effect=[1, 1, "person"])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "Alice",
            "works_at",
            "engineer",
            embedding_engine,
            entity_id=eid,
            object_entity_id=obj_eid,
        )

        # Three fetchval calls: entity_id check, object_entity_id check,
        # entity_type lookup for auto-registration.
        assert conn.fetchval.call_count == 3
        assert conn.fetchval.call_args_list[0].args[1] == eid
        assert conn.fetchval.call_args_list[1].args[1] == obj_eid
        # Third call: entity_type lookup for auto-registration subject type inference
        assert conn.fetchval.call_args_list[2].args[1] == eid


# ---------------------------------------------------------------------------
# Tests: edge-fact stored in fact row
# ---------------------------------------------------------------------------


class TestObjectEntityIdStoredInFact:
    """object_entity_id is persisted in the INSERT statement."""

    async def test_object_entity_id_included_in_insert(self, embedding_engine):
        """When object_entity_id is provided, it appears in the INSERT args."""
        eid = uuid.uuid4()
        obj_eid = uuid.uuid4()
        # fetchval: entity=1, obj=1, entity_type='person' (auto-registration lookup)
        conn = _make_conn(fetchval_side_effect=[1, 1, "person"])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "Alice",
            "works_at",
            "engineer",
            embedding_engine,
            entity_id=eid,
            object_entity_id=obj_eid,
        )

        insert_call = next(
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        )
        sql = insert_call.args[0]
        assert "object_entity_id" in sql
        # entity_id=$18, object_entity_id=$19, valid_at=$20, tenant_id=$21, request_id=$22,
        # idempotency_key=$23, observed_at=$24, retention_class=$25, sensitivity=$26
        assert insert_call.args[-8] == obj_eid
        assert insert_call.args[-9] == eid
        # valid_at = NULL (property fact — omitted valid_at)
        assert insert_call.args[-7] is None

    async def test_object_entity_id_null_when_omitted(self, embedding_engine):
        """When object_entity_id is not provided, NULL is stored."""
        conn = _make_conn()
        pool = _make_pool(conn)

        await store_fact(pool, "user", "color", "blue", embedding_engine)

        insert_call = conn.execute.call_args_list[0]
        assert "object_entity_id" in insert_call.args[0]
        # entity_id=$18, object_entity_id=$19, valid_at=$20, tenant_id=$21, request_id=$22,
        # idempotency_key=$23, observed_at=$24, retention_class=$25, sensitivity=$26
        assert insert_call.args[-8] is None  # object_entity_id


# ---------------------------------------------------------------------------
# Tests: edge-fact supersession
# ---------------------------------------------------------------------------


class TestEdgeFactSupersession:
    """When object_entity_id is provided, supersession uses
    (entity_id, object_entity_id, scope, predicate)."""

    @pytest.fixture()
    def pool_with_existing_edge_fact(self):
        """Pool/conn where an existing edge-fact is found.

        First fetchrow: registry lookup → None (unregistered predicate).
        Second fetchrow: supersession check → existing edge-fact row.
        """
        old_id = uuid.uuid4()
        eid = uuid.uuid4()
        obj_eid = uuid.uuid4()
        # fetchval: entity=1, obj=1, entity_type='person' (auto-registration lookup).
        # Supersession is now valid_at-based, not registry-based.
        conn = _make_conn(
            fetchrow_side_effect=[None, {"id": old_id}],
            fetchval_side_effect=[1, 1, "person"],
        )
        pool = _make_pool(conn)
        return pool, conn, old_id, eid, obj_eid

    async def test_edge_fact_supersession_lookup_sql(
        self, pool_with_existing_edge_fact, embedding_engine
    ):
        """Supersession lookup uses entity_id, object_entity_id, scope, predicate."""
        pool, conn, _old_id, eid, obj_eid = pool_with_existing_edge_fact

        await store_fact(
            pool,
            "Alice",
            "works_at",
            "senior engineer",
            embedding_engine,
            entity_id=eid,
            object_entity_id=obj_eid,
            scope="global",
        )

        fetchrow_call = conn.fetchrow.call_args
        sql = fetchrow_call.args[0]
        assert "entity_id" in sql
        assert "object_entity_id" in sql
        assert "subject" not in sql
        # args[1] = tenant_id, args[2] = entity_id, args[3] = object_entity_id
        assert fetchrow_call.args[2] == eid
        assert fetchrow_call.args[3] == obj_eid

    async def test_edge_fact_old_fact_marked_superseded(
        self, pool_with_existing_edge_fact, embedding_engine
    ):
        """Old edge-fact is marked superseded when match found."""
        pool, conn, old_id, eid, obj_eid = pool_with_existing_edge_fact

        await store_fact(
            pool,
            "Alice",
            "works_at",
            "CTO",
            embedding_engine,
            entity_id=eid,
            object_entity_id=obj_eid,
        )

        update_call = conn.execute.call_args_list[0]
        assert "UPDATE facts SET validity = 'superseded'" in update_call.args[0]
        assert update_call.args[1] == old_id

    async def test_edge_fact_supersedes_id_set_on_new_fact(
        self, pool_with_existing_edge_fact, embedding_engine
    ):
        """New edge-fact's supersedes_id is set to old fact ID."""
        pool, conn, old_id, eid, obj_eid = pool_with_existing_edge_fact

        await store_fact(
            pool,
            "Alice",
            "works_at",
            "CTO",
            embedding_engine,
            entity_id=eid,
            object_entity_id=obj_eid,
        )

        insert_call = next(
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        )
        # supersedes_id is $13 (index 13)
        assert insert_call.args[13] == old_id

    async def test_edge_fact_memory_link_created(
        self, pool_with_existing_edge_fact, embedding_engine
    ):
        """A memory_links supersedes row is created for edge-fact supersession."""
        pool, conn, old_id, eid, obj_eid = pool_with_existing_edge_fact

        result = await store_fact(
            pool,
            "Alice",
            "works_at",
            "CTO",
            embedding_engine,
            entity_id=eid,
            object_entity_id=obj_eid,
        )
        # store_fact() now returns a dict with 'id'
        new_id = result["id"]

        link_call = next(
            c for c in conn.execute.call_args_list if "INSERT INTO memory_links" in c.args[0]
        )
        assert "'supersedes'" in link_call.args[0]
        assert link_call.args[1] == new_id
        assert link_call.args[2] == old_id

    async def test_edge_fact_execute_count_with_supersession(
        self, pool_with_existing_edge_fact, embedding_engine
    ):
        """Four execute calls: UPDATE old, INSERT fact, INSERT link, INSERT predicate_registry."""
        pool, conn, _old_id, eid, obj_eid = pool_with_existing_edge_fact

        await store_fact(
            pool,
            "Alice",
            "works_at",
            "CTO",
            embedding_engine,
            entity_id=eid,
            object_entity_id=obj_eid,
        )

        assert conn.execute.call_count == 4

    async def test_edge_fact_no_supersession_when_no_existing(self, embedding_engine):
        """No supersession when no existing edge-fact found."""
        eid = uuid.uuid4()
        obj_eid = uuid.uuid4()
        # fetchval: entity=1, obj=1, entity_type='person' (auto-registration lookup)
        conn = _make_conn(fetchrow_return=None, fetchval_side_effect=[1, 1, "person"])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "Alice",
            "works_at",
            "engineer",
            embedding_engine,
            entity_id=eid,
            object_entity_id=obj_eid,
        )

        # Two execute calls: INSERT facts + INSERT predicate_registry (auto-registration).
        assert conn.execute.call_count == 2
        insert_call = conn.execute.call_args_list[0]
        assert "INSERT INTO facts" in insert_call.args[0]
        assert insert_call.args[13] is None  # supersedes_id = None


# ---------------------------------------------------------------------------
# Tests: tool layer — memory_store_fact object_entity_id forwarding
# ---------------------------------------------------------------------------


class TestMemoryStoreFactObjectEntityIdTool:
    """Test object_entity_id in the MCP tool wrapper (writing.py)."""

    async def test_object_entity_id_forwarded_as_uuid(self):
        """object_entity_id str is parsed to UUID and forwarded to storage."""
        from unittest.mock import patch

        from butlers.modules.memory.tools import _helpers, memory_store_fact

        pool = AsyncMock()
        engine = MagicMock()
        obj_eid = uuid.uuid4()
        storage_result = {"id": uuid.uuid4()}

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_fact(
                pool,
                engine,
                "Alice",
                "works_at",
                "engineer",
                object_entity_id=str(obj_eid),
            )
            call_kwargs = mock_store.call_args.kwargs
            assert call_kwargs["object_entity_id"] == obj_eid

    async def test_object_entity_id_none_when_omitted(self):
        """object_entity_id is None when not supplied — backward compat."""
        from unittest.mock import patch

        from butlers.modules.memory.tools import _helpers, memory_store_fact

        pool = AsyncMock()
        engine = MagicMock()
        storage_result = {"id": uuid.uuid4()}

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_fact(pool, engine, "user", "city", "Berlin")
            call_kwargs = mock_store.call_args.kwargs
            assert call_kwargs["object_entity_id"] is None

    async def test_invalid_object_entity_id_uuid_string_raises(self):
        """Passing a non-UUID string for object_entity_id raises ValueError."""
        from unittest.mock import patch

        from butlers.modules.memory.tools import _helpers, memory_store_fact

        pool = AsyncMock()
        engine = MagicMock()

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock):
            with pytest.raises(ValueError):
                await memory_store_fact(
                    pool,
                    engine,
                    "user",
                    "city",
                    "Berlin",
                    object_entity_id="not-a-uuid",
                )


# ---------------------------------------------------------------------------
# Tests: tool layer — valid_at forwarding
# ---------------------------------------------------------------------------


class TestMemoryStoreFactValidAtTool:
    """Test valid_at in the MCP tool wrapper (writing.py)."""

    async def test_valid_at_forwarded_as_datetime(self):
        """valid_at ISO string is parsed to datetime and forwarded to storage."""
        from unittest.mock import patch

        from butlers.modules.memory.tools import _helpers, memory_store_fact

        pool = AsyncMock()
        engine = MagicMock()
        storage_result = {"id": uuid.uuid4()}

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_fact(
                pool,
                engine,
                "Owner",
                "meal_breakfast",
                "oatmeal",
                valid_at="2026-03-06T08:00:00Z",
            )
            call_kwargs = mock_store.call_args.kwargs
            assert call_kwargs["valid_at"] is not None
            assert call_kwargs["valid_at"].tzinfo is not None
            assert call_kwargs["valid_at"].year == 2026
            assert call_kwargs["valid_at"].month == 3
            assert call_kwargs["valid_at"].day == 6

    async def test_valid_at_none_when_omitted(self):
        """valid_at is None when not supplied."""
        from unittest.mock import patch

        from butlers.modules.memory.tools import _helpers, memory_store_fact

        pool = AsyncMock()
        engine = MagicMock()
        storage_result = {"id": uuid.uuid4()}

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_fact(pool, engine, "user", "city", "Berlin")
            call_kwargs = mock_store.call_args.kwargs
            assert call_kwargs["valid_at"] is None

    async def test_valid_at_naive_datetime_gets_utc(self):
        """valid_at without timezone info is assumed UTC."""
        from datetime import UTC
        from unittest.mock import patch

        from butlers.modules.memory.tools import _helpers, memory_store_fact

        pool = AsyncMock()
        engine = MagicMock()
        storage_result = {"id": uuid.uuid4()}

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_fact(
                pool,
                engine,
                "Owner",
                "meal_dinner",
                "pasta",
                valid_at="2026-03-06T19:00:00",  # no timezone
            )
            call_kwargs = mock_store.call_args.kwargs
            assert call_kwargs["valid_at"].tzinfo == UTC


# ---------------------------------------------------------------------------
# Tests: UUID-in-content guard
# ---------------------------------------------------------------------------


class TestUuidInContentGuard:
    """Reject facts that embed entity UUIDs in content without object_entity_id."""

    async def test_uuid_in_content_without_object_entity_id_raises(self, embedding_engine):
        """Content containing a UUID without object_entity_id raises ValueError."""
        eid = uuid.uuid4()
        target_uuid = uuid.uuid4()
        # entity_id valid (fetchval returns 1), then no supersession match (None)
        conn = _make_conn(fetchval_side_effect=[1, None])
        pool = _make_pool(conn)

        with pytest.raises(ValueError, match="embedded UUID"):
            await store_fact(
                pool,
                "Chloe",
                "relationship",
                f"Added 'parent' relationship with {target_uuid}",
                embedding_engine,
                entity_id=eid,
            )

    async def test_uuid_in_content_with_object_entity_id_passes(self, embedding_engine):
        """Content with UUID is allowed when object_entity_id is properly set."""
        eid = uuid.uuid4()
        obj_eid = uuid.uuid4()
        # fetchval sequence: entity_id valid (1), object_entity_id valid (1),
        # no supersession match (None)
        conn = _make_conn(fetchval_side_effect=[1, 1, None])
        pool = _make_pool(conn)

        # Should not raise — object_entity_id is set
        await store_fact(
            pool,
            "Chloe",
            "parent",
            f"Parent relationship with {obj_eid}",
            embedding_engine,
            entity_id=eid,
            object_entity_id=obj_eid,
        )

    async def test_content_without_uuid_passes(self, embedding_engine):
        """Normal content without UUIDs is not affected by the guard."""
        eid = uuid.uuid4()
        # entity_id valid (1), no supersession match (None)
        conn = _make_conn(fetchval_side_effect=[1, None])
        pool = _make_pool(conn)

        # Should not raise
        await store_fact(
            pool,
            "Phillip",
            "family_father_of",
            "Phillip is Chloe's dad",
            embedding_engine,
            entity_id=eid,
        )
