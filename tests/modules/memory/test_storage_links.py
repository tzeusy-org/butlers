"""Tests for create_link() and get_links() in the Memory butler storage module."""

from __future__ import annotations

import importlib.util
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load the storage module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------
_STORAGE_PATH = MEMORY_MODULE_PATH / "storage.py"


def _load_storage_module():
    """Load storage.py with sentence_transformers mocked out."""

    mock_st = MagicMock()
    mock_st.SentenceTransformer.return_value = MagicMock()
    # sys.modules.setdefault("sentence_transformers", mock_st)

    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_storage_module()
create_link = _mod.create_link
get_links = _mod.get_links
_VALID_RELATIONS = _mod._VALID_RELATIONS
_VALID_MEMORY_TYPES = _mod._VALID_MEMORY_TYPES

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool():
    """Return an AsyncMock pool mimicking asyncpg.Pool."""
    pool = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    return pool


# ---------------------------------------------------------------------------
# Tests — create_link
# ---------------------------------------------------------------------------


class TestCreateLink:
    """Tests for the create_link() function."""

    async def test_succeeds_with_valid_inputs(self, mock_pool):
        src_id = uuid.uuid4()
        tgt_id = uuid.uuid4()
        await create_link(mock_pool, "fact", src_id, "episode", tgt_id, "derived_from")
        mock_pool.execute.assert_called_once()

    async def test_raises_for_invalid_relation(self, mock_pool):
        with pytest.raises(ValueError, match="Invalid relation"):
            await create_link(mock_pool, "fact", uuid.uuid4(), "rule", uuid.uuid4(), "invented")

    async def test_raises_for_invalid_source_type(self, mock_pool):
        with pytest.raises(ValueError, match="Invalid source_type"):
            await create_link(mock_pool, "memory", uuid.uuid4(), "fact", uuid.uuid4(), "supports")

    async def test_raises_for_invalid_target_type(self, mock_pool):
        with pytest.raises(ValueError, match="Invalid target_type"):
            await create_link(mock_pool, "fact", uuid.uuid4(), "thought", uuid.uuid4(), "supports")

    async def test_sql_uses_on_conflict_do_nothing(self, mock_pool):
        await create_link(mock_pool, "fact", uuid.uuid4(), "rule", uuid.uuid4(), "supports")
        sql = mock_pool.execute.call_args.args[0]
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql

    async def test_passes_all_params_to_execute(self, mock_pool):
        src_id = uuid.uuid4()
        tgt_id = uuid.uuid4()
        await create_link(mock_pool, "episode", src_id, "rule", tgt_id, "contradicts")
        call_args = mock_pool.execute.call_args.args
        assert call_args[1] == "episode"
        assert call_args[2] == src_id
        assert call_args[3] == "rule"
        assert call_args[4] == tgt_id
        assert call_args[5] == "contradicts"

    @pytest.mark.parametrize("relation", sorted(_VALID_RELATIONS))
    async def test_all_valid_relations_accepted(self, mock_pool, relation):
        await create_link(mock_pool, "fact", uuid.uuid4(), "fact", uuid.uuid4(), relation)
        mock_pool.execute.assert_called_once()

    @pytest.mark.parametrize("mem_type", sorted(_VALID_MEMORY_TYPES))
    async def test_all_valid_memory_types_accepted_as_source(self, mock_pool, mem_type):
        await create_link(mock_pool, mem_type, uuid.uuid4(), "fact", uuid.uuid4(), "related_to")
        mock_pool.execute.assert_called_once()

    @pytest.mark.parametrize("mem_type", sorted(_VALID_MEMORY_TYPES))
    async def test_all_valid_memory_types_accepted_as_target(self, mock_pool, mem_type):
        await create_link(mock_pool, "fact", uuid.uuid4(), mem_type, uuid.uuid4(), "related_to")
        mock_pool.execute.assert_called_once()

    async def test_no_execute_on_validation_error(self, mock_pool):
        with pytest.raises(ValueError):
            await create_link(mock_pool, "fact", uuid.uuid4(), "fact", uuid.uuid4(), "bad")
        mock_pool.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — get_links
# ---------------------------------------------------------------------------


class TestGetLinks:
    """Tests for the get_links() function."""

    async def test_outgoing_queries_source_columns(self, mock_pool):
        mem_id = uuid.uuid4()
        await get_links(mock_pool, "fact", mem_id, direction="outgoing")

        mock_pool.fetch.assert_called_once()
        sql = mock_pool.fetch.call_args.args[0]
        assert "source_type = $1" in sql
        assert "source_id = $2" in sql

    async def test_incoming_queries_target_columns(self, mock_pool):
        mem_id = uuid.uuid4()
        await get_links(mock_pool, "fact", mem_id, direction="incoming")

        mock_pool.fetch.assert_called_once()
        sql = mock_pool.fetch.call_args.args[0]
        assert "target_type = $1" in sql
        assert "target_id = $2" in sql

    async def test_both_returns_combined_results(self, mock_pool):
        mem_id = uuid.uuid4()
        outgoing_row = {
            "source_type": "fact",
            "source_id": mem_id,
            "target_type": "episode",
            "target_id": uuid.uuid4(),
            "relation": "derived_from",
            "created_at": "2025-01-01T00:00:00+00:00",
        }
        incoming_row = {
            "source_type": "rule",
            "source_id": uuid.uuid4(),
            "target_type": "fact",
            "target_id": mem_id,
            "relation": "supports",
            "created_at": "2025-01-02T00:00:00+00:00",
        }
        mock_pool.fetch = AsyncMock(side_effect=[[outgoing_row], [incoming_row]])

        results = await get_links(mock_pool, "fact", mem_id, direction="both")

        assert len(results) == 2
        assert mock_pool.fetch.call_count == 2

    async def test_raises_for_invalid_memory_type(self, mock_pool):
        with pytest.raises(ValueError, match="Invalid memory_type"):
            await get_links(mock_pool, "thought", uuid.uuid4())

    async def test_default_direction_is_both(self, mock_pool):
        mem_id = uuid.uuid4()
        mock_pool.fetch = AsyncMock(return_value=[])
        await get_links(mock_pool, "fact", mem_id)

        # Should have been called twice: once for outgoing, once for incoming
        assert mock_pool.fetch.call_count == 2

    async def test_outgoing_does_not_query_incoming(self, mock_pool):
        mem_id = uuid.uuid4()
        await get_links(mock_pool, "rule", mem_id, direction="outgoing")

        # Only one fetch call for outgoing
        assert mock_pool.fetch.call_count == 1
        sql = mock_pool.fetch.call_args.args[0]
        assert "source_type" in sql
        assert "target_type = $1" not in sql

    async def test_incoming_does_not_query_outgoing(self, mock_pool):
        mem_id = uuid.uuid4()
        await get_links(mock_pool, "rule", mem_id, direction="incoming")

        assert mock_pool.fetch.call_count == 1
        sql = mock_pool.fetch.call_args.args[0]
        assert "target_type = $1" in sql

    async def test_returns_list_of_dicts(self, mock_pool):
        mem_id = uuid.uuid4()
        row = {
            "source_type": "fact",
            "source_id": mem_id,
            "target_type": "rule",
            "target_id": uuid.uuid4(),
            "relation": "supports",
            "created_at": "2025-01-01T00:00:00+00:00",
        }
        mock_pool.fetch = AsyncMock(side_effect=[[row], []])

        results = await get_links(mock_pool, "fact", mem_id, direction="both")

        assert isinstance(results, list)
        assert len(results) == 1
        assert isinstance(results[0], dict)
        assert results[0]["relation"] == "supports"

    async def test_empty_results(self, mock_pool):
        results = await get_links(mock_pool, "episode", uuid.uuid4(), direction="outgoing")
        assert results == []

    async def test_no_fetch_on_validation_error(self, mock_pool):
        with pytest.raises(ValueError):
            await get_links(mock_pool, "invalid", uuid.uuid4())
        mock_pool.fetch.assert_not_called()
