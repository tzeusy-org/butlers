"""Tests for store_fact() in the Memory butler storage module."""

from __future__ import annotations

import importlib.util
import json
import uuid
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

    async def test_embedding_called_with_content(self, mock_pool, embedding_engine):
        pool, _conn = mock_pool
        await store_fact(pool, "user", "name", "Alice", embedding_engine)
        embedding_engine.embed.assert_called_once_with("Alice")

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
