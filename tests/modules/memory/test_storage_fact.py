"""Tests for store_fact() in the Memory butler storage module."""

from __future__ import annotations

import importlib.util
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load the storage module from disk (roster/ is not a Python package).
# We also need to mock sentence_transformers before importing storage,
# since storage imports embedding.py which imports SentenceTransformer.
# ---------------------------------------------------------------------------
_STORAGE_PATH = MEMORY_MODULE_PATH / "storage.py"


def _load_storage_module():
    """Load storage.py with sentence_transformers mocked out."""

    # Provide a mock for sentence_transformers so embedding.py loads without
    # the real ML library installed.
    mock_st = MagicMock()
    mock_st.SentenceTransformer.return_value = MagicMock()
    # sys.modules.setdefault("sentence_transformers", mock_st)

    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_storage_module()
store_fact = _mod.store_fact
_PERMANENCE_DECAY = _mod._PERMANENCE_DECAY

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


@pytest.fixture()
def mock_pool():
    """Return (pool, conn) mocks wired up like asyncpg.

    pool.acquire() returns an async context manager yielding conn.
    conn.transaction() returns an async context manager.
    """
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)  # entity check (not called when entity_id=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM(None))

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))

    return pool, conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStoreFactBasic:
    """Basic store_fact behaviour."""

    async def test_returns_uuid(self, mock_pool, embedding_engine):
        pool, _conn = mock_pool
        result = await store_fact(pool, "user", "favorite_color", "blue", embedding_engine)
        assert isinstance(result, uuid.UUID)

    async def test_embedding_called_with_searchable_text(self, mock_pool, embedding_engine):
        pool, _conn = mock_pool
        await store_fact(pool, "user", "name", "Alice", embedding_engine)
        embedding_engine.embed.assert_called_once_with("user name Alice")

    async def test_insert_called(self, mock_pool, embedding_engine):
        pool, conn = mock_pool
        await store_fact(pool, "user", "city", "Berlin", embedding_engine)
        # At least one execute call for the INSERT
        assert conn.execute.call_count >= 1
        insert_call = conn.execute.call_args_list[0]
        sql = insert_call.args[0]
        assert "INSERT INTO facts" in sql

    async def test_tags_json_serialized(self, mock_pool, embedding_engine):
        pool, conn = mock_pool
        await store_fact(
            pool, "user", "hobbies", "hiking", embedding_engine, tags=["outdoor", "sport"]
        )
        insert_call = conn.execute.call_args_list[0]
        args = insert_call.args
        # tags_json is parameter $16 -> index 16 in args (0-based: args[16])
        tags_arg = args[16]
        assert json.loads(tags_arg) == ["outdoor", "sport"]

    async def test_tags_default_empty_list(self, mock_pool, embedding_engine):
        pool, conn = mock_pool
        await store_fact(pool, "user", "hobbies", "hiking", embedding_engine)
        insert_call = conn.execute.call_args_list[0]
        args = insert_call.args
        tags_arg = args[16]
        assert json.loads(tags_arg) == []

    async def test_scope_passed_through(self, mock_pool, embedding_engine):
        pool, conn = mock_pool
        await store_fact(pool, "user", "pref", "dark mode", embedding_engine, scope="butler:editor")
        insert_call = conn.execute.call_args_list[0]
        args = insert_call.args
        # scope is parameter $14 -> index 14 in args
        assert args[14] == "butler:editor"

    async def test_scope_defaults_to_global(self, mock_pool, embedding_engine):
        pool, conn = mock_pool
        await store_fact(pool, "user", "pref", "vim", embedding_engine)
        insert_call = conn.execute.call_args_list[0]
        args = insert_call.args
        assert args[14] == "global"


class TestPermanenceDecayMapping:
    """Each permanence level maps to the correct decay_rate."""

    @pytest.mark.parametrize(
        ("permanence", "expected_decay"),
        [
            ("permanent", 0.0),
            ("stable", 0.002),
            ("standard", 0.008),
            ("volatile", 0.03),
            ("ephemeral", 0.1),
        ],
    )
    async def test_permanence_decay_rate(
        self, mock_pool, embedding_engine, permanence, expected_decay
    ):
        pool, conn = mock_pool
        await store_fact(pool, "user", "data", "value", embedding_engine, permanence=permanence)
        insert_call = conn.execute.call_args_list[0]
        args = insert_call.args
        # decay_rate is parameter $9 -> index 9 in args
        assert args[9] == expected_decay

    async def test_unknown_permanence_raises_value_error(self, mock_pool, embedding_engine):
        pool, conn = mock_pool
        with pytest.raises(ValueError, match="Invalid permanence"):
            await store_fact(pool, "user", "data", "value", embedding_engine, permanence="invented")


class TestSupersession:
    """Supersession: replacing an active fact with same (subject, predicate)."""

    @pytest.fixture()
    def mock_pool_with_existing(self, mock_pool):
        """Pool where fetchrow returns an existing fact row."""
        pool, conn = mock_pool
        old_id = uuid.uuid4()
        conn.fetchrow = AsyncMock(return_value={"id": old_id})
        return pool, conn, old_id

    async def test_old_fact_marked_superseded(self, mock_pool_with_existing, embedding_engine):
        pool, conn, old_id = mock_pool_with_existing
        await store_fact(pool, "user", "city", "Munich", embedding_engine)

        # First execute: UPDATE old fact validity
        update_call = conn.execute.call_args_list[0]
        sql = update_call.args[0]
        assert "UPDATE facts SET validity = 'superseded'" in sql
        assert update_call.args[1] == old_id

    async def test_supersedes_id_set_on_new_fact(self, mock_pool_with_existing, embedding_engine):
        pool, conn, old_id = mock_pool_with_existing
        await store_fact(pool, "user", "city", "Munich", embedding_engine)

        # Second execute: INSERT new fact
        insert_call = conn.execute.call_args_list[1]
        args = insert_call.args
        # supersedes_id is parameter $13 -> index 13 in args
        assert args[13] == old_id

    async def test_memory_link_created(self, mock_pool_with_existing, embedding_engine):
        pool, conn, old_id = mock_pool_with_existing
        new_id = await store_fact(pool, "user", "city", "Munich", embedding_engine)

        # Third execute: INSERT memory_link
        link_call = conn.execute.call_args_list[2]
        sql = link_call.args[0]
        assert "INSERT INTO memory_links" in sql
        assert "'supersedes'" in sql
        assert link_call.args[1] == new_id
        assert link_call.args[2] == old_id

    async def test_no_supersession_when_no_existing(self, mock_pool, embedding_engine):
        pool, conn = mock_pool
        await store_fact(pool, "user", "city", "Berlin", embedding_engine)

        # Only one execute call: the INSERT (no UPDATE, no link INSERT)
        assert conn.execute.call_count == 1
        insert_call = conn.execute.call_args_list[0]
        sql = insert_call.args[0]
        assert "INSERT INTO facts" in sql
        # supersedes_id ($13 -> index 13) should be None
        assert insert_call.args[13] is None

    async def test_supersession_execute_count(self, mock_pool_with_existing, embedding_engine):
        pool, conn, _old_id = mock_pool_with_existing
        await store_fact(pool, "user", "city", "Munich", embedding_engine)

        # Three execute calls: UPDATE old, INSERT new, INSERT link
        assert conn.execute.call_count == 3


class TestTemporalFacts:
    """Temporal facts: valid_at param and NULL-based supersession skip."""

    async def test_valid_at_stored_in_insert(self, mock_pool, embedding_engine):
        """valid_at is passed through to the INSERT."""
        pool, conn = mock_pool
        ts = datetime(2026, 3, 6, 8, 0, 0, tzinfo=UTC)
        await store_fact(pool, "user", "meal_breakfast", "oatmeal", embedding_engine, valid_at=ts)

        insert_call = conn.execute.call_args_list[0]
        # valid_at is $20; tenant_id=$21, request_id=$22,
        # idempotency_key=$23, observed_at=$24, retention_class=$25, sensitivity=$26 follow after.
        # So valid_at is at args[-7] (7 args from the end).
        assert insert_call.args[-7] == ts

    async def test_valid_at_defaults_to_null_when_omitted(self, mock_pool, embedding_engine):
        """When valid_at is omitted, NULL is stored (property fact semantics)."""
        pool, conn = mock_pool
        await store_fact(pool, "user", "city", "Berlin", embedding_engine)

        insert_call = conn.execute.call_args_list[0]
        # valid_at is $20 in the INSERT; at args[-7]
        # (last 6: request_id, idem_key, observed_at, retention_class, sensitivity).
        stored_valid_at = insert_call.args[-7]
        # Property facts must store NULL, not a timestamp
        assert stored_valid_at is None

    async def test_temporal_fact_skips_supersession(self, mock_pool, embedding_engine):
        """When valid_at is provided (temporal fact), no supersession check is done."""
        pool, conn = mock_pool
        ts = datetime(2026, 3, 6, 8, 0, 0, tzinfo=UTC)
        # fetchrow would return an existing fact if supersession were attempted
        conn.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})

        await store_fact(pool, "user", "meal_breakfast", "oatmeal", embedding_engine, valid_at=ts)

        # Only INSERT — no UPDATE supersession, no memory_links INSERT
        assert conn.execute.call_count == 1
        assert "INSERT INTO facts" in conn.execute.call_args_list[0].args[0]
        # fetchrow (supersession check) should never be called for temporal facts
        conn.fetchrow.assert_not_awaited()

    async def test_property_fact_still_supersedes(self, mock_pool, embedding_engine):
        """When valid_at is None (property fact), supersession happens normally."""
        pool, conn = mock_pool
        old_id = uuid.uuid4()
        conn.fetchrow = AsyncMock(return_value={"id": old_id})

        await store_fact(pool, "user", "city", "Munich", embedding_engine)

        # Three execute calls: UPDATE, INSERT, INSERT link
        assert conn.execute.call_count == 3
        update_call = conn.execute.call_args_list[0]
        assert "UPDATE facts SET validity = 'superseded'" in update_call.args[0]

    async def test_predicate_registry_not_queried_for_supersession(
        self, mock_pool, embedding_engine
    ):
        """fetchval is NOT called for predicate_registry (supersession is now NULL-based)."""
        pool, conn = mock_pool
        await store_fact(pool, "user", "meal_lunch", "salad", embedding_engine)

        # No fetchval calls expected — entity_id is None so no entity check,
        # and supersession is determined by valid_at nullness, not registry.
        conn.fetchval.assert_not_awaited()

    async def test_supersession_sql_filters_valid_at_null(self, mock_pool, embedding_engine):
        """Supersession lookup SQL includes AND valid_at IS NULL filter."""
        pool, conn = mock_pool
        conn.fetchrow = AsyncMock(return_value=None)  # no existing fact

        await store_fact(pool, "user", "city", "Berlin", embedding_engine)

        fetchrow_call = conn.fetchrow.call_args
        sql = fetchrow_call.args[0]
        assert "valid_at IS NULL" in sql

    async def test_multiple_temporal_facts_coexist(self, mock_pool, embedding_engine):
        """Storing two temporal facts with the same predicate creates two INSERT calls."""
        pool, conn = mock_pool

        ts1 = datetime(2026, 3, 6, 8, 0, tzinfo=UTC)
        ts2 = datetime(2026, 3, 6, 12, 0, tzinfo=UTC)

        id1 = await store_fact(
            pool, "user", "meal_breakfast", "oatmeal", embedding_engine, valid_at=ts1
        )
        id2 = await store_fact(pool, "user", "meal_lunch", "salad", embedding_engine, valid_at=ts2)

        assert isinstance(id1, uuid.UUID)
        assert isinstance(id2, uuid.UUID)
        assert id1 != id2
        # Two INSERT calls, no UPDATEs
        assert conn.execute.call_count == 2
        for call in conn.execute.call_args_list:
            assert "INSERT INTO facts" in call.args[0]

    async def test_property_fact_does_not_supersede_temporal_fact(
        self, mock_pool, embedding_engine
    ):
        """A new property fact (valid_at=None) does NOT supersede existing temporal facts.

        The supersession lookup SQL uses AND valid_at IS NULL, so a temporal fact
        (valid_at IS NOT NULL) in the DB would not match the query.  We verify
        that the supersession logic does not trigger if the only fetchrow result
        that would be returned is a temporal fact row.
        """
        pool, conn = mock_pool
        # fetchrow returns nothing (simulating: only temporal facts exist in DB,
        # which would NOT be returned by the valid_at IS NULL query)
        conn.fetchrow = AsyncMock(return_value=None)

        await store_fact(pool, "user", "city", "Berlin", embedding_engine)

        # Only INSERT — no supersession
        assert conn.execute.call_count == 1
        assert "INSERT INTO facts" in conn.execute.call_args_list[0].args[0]

    async def test_temporal_fact_does_not_supersede_property_fact(
        self, mock_pool, embedding_engine
    ):
        """A new temporal fact (valid_at=T1) never supersedes any fact."""
        pool, conn = mock_pool
        # Even if a property fact exists in DB, temporal facts skip supersession
        conn.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})

        ts = datetime(2026, 3, 6, 8, 0, 0, tzinfo=UTC)
        await store_fact(pool, "user", "city", "Berlin", embedding_engine, valid_at=ts)

        # Only INSERT — no supersession check, no UPDATE
        assert conn.execute.call_count == 1
        conn.fetchrow.assert_not_awaited()
