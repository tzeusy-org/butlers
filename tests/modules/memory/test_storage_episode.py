"""Tests for episode storage in Memory Butler."""

from __future__ import annotations

import importlib.util
import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load storage module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_STORAGE_PATH = MEMORY_MODULE_PATH / "storage.py"


def _load_storage_module():
    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_storage_module()
store_episode = _mod.store_episode
_DEFAULT_EPISODE_TTL_DAYS = _mod._DEFAULT_EPISODE_TTL_DAYS

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Return an AsyncMock asyncpg pool with a mocked execute method."""
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=None)
    return pool


@pytest.fixture()
def mock_embedding_engine() -> MagicMock:
    """Return a MagicMock EmbeddingEngine that returns a fixed 384-d vector."""
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStoreEpisode:
    """Tests for store_episode()."""

    async def test_returns_uuid(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """store_episode returns a UUID for the new episode."""
        result = await store_episode(
            mock_pool, "test content", "test-butler", mock_embedding_engine
        )
        assert isinstance(result, uuid.UUID)

    async def test_pool_execute_called(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """pool.execute is called exactly once with an INSERT statement."""
        await store_episode(mock_pool, "test content", "test-butler", mock_embedding_engine)
        mock_pool.execute.assert_awaited_once()
        call_args = mock_pool.execute.call_args
        sql = call_args[0][0]
        assert "INSERT INTO episodes" in sql

    async def test_embedding_engine_called_with_content(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """embed() is called with the episode content."""
        await store_episode(
            mock_pool, "specific content here", "test-butler", mock_embedding_engine
        )
        mock_embedding_engine.embed.assert_called_once_with("specific content here")

    async def test_preprocess_text_called(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """The preprocessed text is passed to pool.execute for the search vector."""
        content = "hello world test"
        await store_episode(mock_pool, content, "test-butler", mock_embedding_engine)
        call_args = mock_pool.execute.call_args[0]
        # $6 is the search_text parameter (index 6 in positional args, 0-based index 5)
        search_text_arg = call_args[6]
        # preprocess_text should return the same text when it's already clean
        assert search_text_arg == content

    async def test_session_id_none_handled(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """session_id=None is correctly passed through to the INSERT."""
        await store_episode(
            mock_pool, "test content", "test-butler", mock_embedding_engine, session_id=None
        )
        call_args = mock_pool.execute.call_args[0]
        # $3 is session_id (index 3 in positional args, 0-based index 3)
        session_id_arg = call_args[3]
        assert session_id_arg is None

    async def test_session_id_uuid_passed(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """A provided session_id UUID is passed through to the INSERT."""
        sid = uuid.uuid4()
        await store_episode(
            mock_pool, "test content", "test-butler", mock_embedding_engine, session_id=sid
        )
        call_args = mock_pool.execute.call_args[0]
        session_id_arg = call_args[3]
        assert session_id_arg == sid

    async def test_custom_importance_passed(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """A custom importance value is passed through to the INSERT."""
        await store_episode(
            mock_pool, "test content", "test-butler", mock_embedding_engine, importance=8.5
        )
        call_args = mock_pool.execute.call_args[0]
        # $7 is importance (index 7 in positional args, 0-based index 7)
        importance_arg = call_args[7]
        assert importance_arg == 8.5

    async def test_default_importance_is_five(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """Default importance is 5.0 when not specified."""
        await store_episode(mock_pool, "test content", "test-butler", mock_embedding_engine)
        call_args = mock_pool.execute.call_args[0]
        importance_arg = call_args[7]
        assert importance_arg == 5.0

    async def test_custom_metadata_json_serialized(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """Custom metadata dict is serialized to JSON string."""
        meta = {"source": "email", "tags": ["important", "follow-up"]}
        await store_episode(
            mock_pool, "test content", "test-butler", mock_embedding_engine, metadata=meta
        )
        call_args = mock_pool.execute.call_args[0]
        # $9 is metadata (index 9 in positional args, 0-based index 9)
        meta_arg = call_args[9]
        assert json.loads(meta_arg) == meta

    async def test_none_metadata_becomes_empty_json(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """metadata=None is serialized as '{}'."""
        await store_episode(
            mock_pool, "test content", "test-butler", mock_embedding_engine, metadata=None
        )
        call_args = mock_pool.execute.call_args[0]
        meta_arg = call_args[9]
        assert json.loads(meta_arg) == {}

    async def test_expires_at_approx_seven_days(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """expires_at is set to approximately 7 days from now."""
        before = datetime.now(UTC) + timedelta(days=_DEFAULT_EPISODE_TTL_DAYS)
        await store_episode(mock_pool, "test content", "test-butler", mock_embedding_engine)
        after = datetime.now(UTC) + timedelta(days=_DEFAULT_EPISODE_TTL_DAYS)

        call_args = mock_pool.execute.call_args[0]
        # $8 is expires_at (index 8 in positional args, 0-based index 8)
        expires_at_arg = call_args[8]
        assert isinstance(expires_at_arg, datetime)
        assert before <= expires_at_arg <= after

    async def test_embedding_converted_to_string(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """The embedding vector is converted to a string for pgvector."""
        mock_embedding_engine.embed.return_value = [0.5, 0.25, -0.1]
        await store_episode(mock_pool, "test content", "test-butler", mock_embedding_engine)
        call_args = mock_pool.execute.call_args[0]
        # $5 is embedding (index 5 in positional args, 0-based index 5)
        embedding_arg = call_args[5]
        assert isinstance(embedding_arg, str)
        assert embedding_arg == str([0.5, 0.25, -0.1])

    async def test_butler_name_passed(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """The butler name is correctly passed to the INSERT."""
        await store_episode(mock_pool, "test content", "memory-butler", mock_embedding_engine)
        call_args = mock_pool.execute.call_args[0]
        # $2 is butler (index 2 in positional args, 0-based index 2)
        butler_arg = call_args[2]
        assert butler_arg == "memory-butler"

    async def test_content_passed_to_insert(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """The raw content is passed to the INSERT."""
        await store_episode(mock_pool, "raw episode text", "test-butler", mock_embedding_engine)
        call_args = mock_pool.execute.call_args[0]
        # $4 is content (index 4 in positional args, 0-based index 4)
        content_arg = call_args[4]
        assert content_arg == "raw episode text"

    async def test_sql_contains_tsvector_expression(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """The SQL statement contains the to_tsvector() expression."""
        await store_episode(mock_pool, "test content", "test-butler", mock_embedding_engine)
        sql = mock_pool.execute.call_args[0][0]
        assert "to_tsvector('english', $6)" in sql
