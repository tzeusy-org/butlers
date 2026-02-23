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


def _make_conn(*, fetchval_return=None, fetchrow_return=None):
    """Build a mock conn with configurable fetchval/fetchrow returns."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=fetchval_return)
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
        conn = _make_conn(fetchval_return=1)  # entity found
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "Alice",
            "job_title",
            "Engineer",
            embedding_engine,
            entity_id=eid,
        )

        assert isinstance(result, uuid.UUID)

    async def test_no_entity_validation_when_entity_id_omitted(self, embedding_engine):
        """fetchval (entity check) is NOT called when entity_id is None."""
        conn = _make_conn()
        pool = _make_pool(conn)

        await store_fact(pool, "user", "color", "blue", embedding_engine)

        conn.fetchval.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: entity_id stored in fact row
# ---------------------------------------------------------------------------


class TestEntityIdStoredInFact:
    """entity_id is persisted in the INSERT statement."""

    async def test_entity_id_included_in_insert(self, embedding_engine):
        """When entity_id is provided, it appears as the last positional arg."""
        eid = uuid.uuid4()
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
        # entity_id is $18 — last positional arg
        assert insert_call.args[-1] == eid

    async def test_entity_id_null_in_insert_when_omitted(self, embedding_engine):
        """When entity_id is not provided, NULL is stored."""
        conn = _make_conn()
        pool = _make_pool(conn)

        await store_fact(pool, "user", "color", "blue", embedding_engine)

        insert_call = conn.execute.call_args_list[0]
        assert "entity_id" in insert_call.args[0]
        assert insert_call.args[-1] is None


# ---------------------------------------------------------------------------
# Tests: entity-keyed supersession
# ---------------------------------------------------------------------------


class TestEntityKeyedSupersession:
    """When entity_id is provided, supersession uses (entity_id, scope, predicate)."""

    @pytest.fixture()
    def pool_with_existing_entity_fact(self):
        """Pool/conn where an existing entity-keyed fact is found."""
        old_id = uuid.uuid4()
        eid = uuid.uuid4()
        conn = _make_conn(fetchval_return=1, fetchrow_return={"id": old_id})
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
        assert fetchrow_call.args[1] == eid

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

        new_id = await store_fact(
            pool, "Alice", "job_title", "Senior Engineer", embedding_engine, entity_id=eid
        )

        link_call = next(
            c for c in conn.execute.call_args_list if "INSERT INTO memory_links" in c.args[0]
        )
        assert "'supersedes'" in link_call.args[0]
        assert link_call.args[1] == new_id
        assert link_call.args[2] == old_id

    async def test_entity_keyed_execute_count_with_supersession(
        self, pool_with_existing_entity_fact, embedding_engine
    ):
        """Three execute calls: UPDATE old, INSERT fact, INSERT link."""
        pool, conn, _old_id, eid = pool_with_existing_entity_fact

        await store_fact(
            pool, "Alice", "job_title", "Senior Engineer", embedding_engine, entity_id=eid
        )

        assert conn.execute.call_count == 3

    async def test_entity_keyed_no_supersession_when_no_existing(self, embedding_engine):
        """No supersession when no existing entity-keyed fact found."""
        eid = uuid.uuid4()
        conn = _make_conn(fetchval_return=1, fetchrow_return=None)
        pool = _make_pool(conn)

        await store_fact(pool, "Alice", "job_title", "Engineer", embedding_engine, entity_id=eid)

        assert conn.execute.call_count == 1
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
        conn = _make_conn(fetchrow_return={"id": old_id})
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

        assert conn.execute.call_count == 3

    async def test_subject_keyed_null_entity_id_in_insert(
        self, pool_with_existing_subject_fact, embedding_engine
    ):
        """entity_id in INSERT is None for subject-keyed facts."""
        pool, conn, _old_id = pool_with_existing_subject_fact

        await store_fact(pool, "user", "city", "Munich", embedding_engine)

        insert_call = next(
            c for c in conn.execute.call_args_list if "INSERT INTO facts" in c.args[0]
        )
        assert insert_call.args[-1] is None  # entity_id = None


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
        storage_result = {"id": uuid.uuid4(), "superseded_id": uuid.uuid4()}

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            result = await memory_store_fact(
                pool, engine, "Alice", "job_title", "Engineer", entity_id=str(eid)
            )
            assert set(result.keys()) == {"id", "superseded_id"}
            assert result["superseded_id"] is not None
