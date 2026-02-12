"""Tests for memory reading and feedback MCP tools in tools.py."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Load tools module (mocking sentence_transformers first)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Load tools module
# ---------------------------------------------------------------------------

from butlers.tools.memory import (
    memory_search,
    memory_recall,
    memory_get,
    memory_confirm,
    memory_mark_helpful,
    memory_mark_harmful,
    _serialize_row,
)
from butlers.tools.memory import _helpers

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Return an AsyncMock asyncpg pool."""
    return AsyncMock()


@pytest.fixture()
def mock_embedding_engine() -> MagicMock:
    """Return a mock embedding engine."""
    engine = MagicMock()
    engine.embed = MagicMock(return_value=[0.1] * 384)
    return engine


@pytest.fixture()
def sample_uuid() -> uuid.UUID:
    """Return a fixed UUID for deterministic testing."""
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture()
def sample_uuid_str() -> str:
    """Return a fixed UUID string for tool calls."""
    return "12345678-1234-5678-1234-567812345678"


# ---------------------------------------------------------------------------
# _serialize_row tests
# ---------------------------------------------------------------------------


class TestSerializeRow:
    """_serialize_row converts UUIDs and datetimes to JSON-serializable format."""

    def test_converts_uuid_to_string(self, sample_uuid: uuid.UUID) -> None:
        row = {"id": sample_uuid, "content": "test"}
        result = _serialize_row(row)
        assert result["id"] == str(sample_uuid)
        assert isinstance(result["id"], str)

    def test_converts_datetime_to_isoformat(self) -> None:
        dt = datetime(2025, 1, 15, 10, 30, 0, tzinfo=UTC)
        row = {"created_at": dt, "name": "test"}
        result = _serialize_row(row)
        assert result["created_at"] == dt.isoformat()
        assert isinstance(result["created_at"], str)

    def test_leaves_other_types_unchanged(self) -> None:
        row = {"count": 42, "name": "test", "active": True, "tags": ["a", "b"]}
        result = _serialize_row(row)
        assert result == row

    def test_handles_mixed_types(self, sample_uuid: uuid.UUID) -> None:
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        row = {"id": sample_uuid, "created_at": dt, "content": "hello", "score": 0.95}
        result = _serialize_row(row)
        assert result["id"] == str(sample_uuid)
        assert result["created_at"] == dt.isoformat()
        assert result["content"] == "hello"
        assert result["score"] == 0.95

    def test_handles_empty_dict(self) -> None:
        result = _serialize_row({})
        assert result == {}


# ---------------------------------------------------------------------------
# memory_search tests
# ---------------------------------------------------------------------------


class TestMemorySearch:
    """memory_search delegates to _search.search and serializes results."""

    async def test_delegates_to_search(
        self,
        mock_pool: AsyncMock,
        mock_embedding_engine: MagicMock,
        sample_uuid: uuid.UUID,
    ) -> None:
        raw_results = [
            {"id": sample_uuid, "content": "test", "memory_type": "fact"},
        ]
        _helpers._search.search = AsyncMock(return_value=raw_results)

        results = await memory_search(
            mock_pool,
            mock_embedding_engine,
            "test query",
        )

        _helpers._search.search.assert_awaited_once_with(
            mock_pool,
            "test query",
            mock_embedding_engine,
            types=None,
            scope=None,
            mode="hybrid",
            limit=10,
            min_confidence=0.2,
        )
        assert len(results) == 1
        assert results[0]["id"] == str(sample_uuid)

    async def test_passes_optional_params(
        self,
        mock_pool: AsyncMock,
        mock_embedding_engine: MagicMock,
    ) -> None:
        _helpers._search.search = AsyncMock(return_value=[])

        await memory_search(
            mock_pool,
            mock_embedding_engine,
            "query",
            types=["fact", "rule"],
            scope="butler-a",
            mode="semantic",
            limit=5,
            min_confidence=0.5,
        )

        _helpers._search.search.assert_awaited_once_with(
            mock_pool,
            "query",
            mock_embedding_engine,
            types=["fact", "rule"],
            scope="butler-a",
            mode="semantic",
            limit=5,
            min_confidence=0.5,
        )

    async def test_serializes_results(
        self,
        mock_pool: AsyncMock,
        mock_embedding_engine: MagicMock,
        sample_uuid: uuid.UUID,
    ) -> None:
        dt = datetime(2025, 3, 1, tzinfo=UTC)
        raw_results = [
            {"id": sample_uuid, "created_at": dt, "content": "fact content"},
        ]
        _helpers._search.search = AsyncMock(return_value=raw_results)

        results = await memory_search(mock_pool, mock_embedding_engine, "q")

        assert results[0]["id"] == str(sample_uuid)
        assert results[0]["created_at"] == dt.isoformat()


# ---------------------------------------------------------------------------
# memory_recall tests
# ---------------------------------------------------------------------------


class TestMemoryRecall:
    """memory_recall delegates to _search.recall and serializes results."""

    async def test_delegates_to_recall(
        self,
        mock_pool: AsyncMock,
        mock_embedding_engine: MagicMock,
        sample_uuid: uuid.UUID,
    ) -> None:
        raw_results = [
            {"id": sample_uuid, "content": "rule content", "composite_score": 0.8},
        ]
        _helpers._search.recall = AsyncMock(return_value=raw_results)

        results = await memory_recall(
            mock_pool,
            mock_embedding_engine,
            "some topic",
        )

        _helpers._search.recall.assert_awaited_once_with(
            mock_pool,
            "some topic",
            mock_embedding_engine,
            scope=None,
            limit=10,
        )
        assert len(results) == 1
        assert results[0]["id"] == str(sample_uuid)

    async def test_passes_optional_params(
        self,
        mock_pool: AsyncMock,
        mock_embedding_engine: MagicMock,
    ) -> None:
        _helpers._search.recall = AsyncMock(return_value=[])

        await memory_recall(
            mock_pool,
            mock_embedding_engine,
            "topic",
            scope="butler-b",
            limit=5,
        )

        _helpers._search.recall.assert_awaited_once_with(
            mock_pool,
            "topic",
            mock_embedding_engine,
            scope="butler-b",
            limit=5,
        )

    async def test_serializes_results(
        self,
        mock_pool: AsyncMock,
        mock_embedding_engine: MagicMock,
        sample_uuid: uuid.UUID,
    ) -> None:
        dt = datetime(2025, 5, 20, 8, 0, 0, tzinfo=UTC)
        raw_results = [
            {"id": sample_uuid, "last_referenced_at": dt, "composite_score": 0.75},
        ]
        _helpers._search.recall = AsyncMock(return_value=raw_results)

        results = await memory_recall(mock_pool, mock_embedding_engine, "t")

        assert results[0]["id"] == str(sample_uuid)
        assert results[0]["last_referenced_at"] == dt.isoformat()


# ---------------------------------------------------------------------------
# memory_get tests
# ---------------------------------------------------------------------------


class TestMemoryGet:
    """memory_get delegates to _storage.get_memory with UUID conversion."""

    async def test_returns_none_for_not_found(
        self,
        mock_pool: AsyncMock,
        sample_uuid_str: str,
    ) -> None:
        _helpers._storage.get_memory = AsyncMock(return_value=None)

        result = await memory_get(mock_pool, "fact", sample_uuid_str)

        assert result is None

    async def test_converts_string_id_to_uuid(
        self,
        mock_pool: AsyncMock,
        sample_uuid: uuid.UUID,
        sample_uuid_str: str,
    ) -> None:
        _helpers._storage.get_memory = AsyncMock(return_value={"id": sample_uuid, "content": "c"})

        await memory_get(mock_pool, "episode", sample_uuid_str)

        call_args = _helpers._storage.get_memory.call_args[0]
        assert call_args[0] is mock_pool
        assert call_args[1] == "episode"
        assert call_args[2] == sample_uuid
        assert isinstance(call_args[2], uuid.UUID)

    async def test_serializes_result(
        self,
        mock_pool: AsyncMock,
        sample_uuid: uuid.UUID,
        sample_uuid_str: str,
    ) -> None:
        dt = datetime(2025, 4, 10, 14, 0, 0, tzinfo=UTC)
        _helpers._storage.get_memory = AsyncMock(
            return_value={"id": sample_uuid, "created_at": dt, "content": "hello"}
        )

        result = await memory_get(mock_pool, "fact", sample_uuid_str)

        assert result is not None
        assert result["id"] == str(sample_uuid)
        assert result["created_at"] == dt.isoformat()
        assert result["content"] == "hello"


# ---------------------------------------------------------------------------
# memory_confirm tests
# ---------------------------------------------------------------------------


class TestMemoryConfirm:
    """memory_confirm delegates to _storage.confirm_memory."""

    async def test_returns_confirmed_true(
        self,
        mock_pool: AsyncMock,
        sample_uuid_str: str,
    ) -> None:
        _helpers._storage.confirm_memory = AsyncMock(return_value=True)

        result = await memory_confirm(mock_pool, "fact", sample_uuid_str)

        assert result == {"confirmed": True}

    async def test_returns_confirmed_false(
        self,
        mock_pool: AsyncMock,
        sample_uuid_str: str,
    ) -> None:
        _helpers._storage.confirm_memory = AsyncMock(return_value=False)

        result = await memory_confirm(mock_pool, "rule", sample_uuid_str)

        assert result == {"confirmed": False}

    async def test_converts_string_id_to_uuid(
        self,
        mock_pool: AsyncMock,
        sample_uuid: uuid.UUID,
        sample_uuid_str: str,
    ) -> None:
        _helpers._storage.confirm_memory = AsyncMock(return_value=True)

        await memory_confirm(mock_pool, "fact", sample_uuid_str)

        call_args = _helpers._storage.confirm_memory.call_args[0]
        assert call_args[2] == sample_uuid
        assert isinstance(call_args[2], uuid.UUID)


# ---------------------------------------------------------------------------
# memory_mark_helpful tests
# ---------------------------------------------------------------------------


class TestMemoryMarkHelpful:
    """memory_mark_helpful delegates to _storage.mark_helpful."""

    async def test_returns_serialized_result(
        self,
        mock_pool: AsyncMock,
        sample_uuid: uuid.UUID,
        sample_uuid_str: str,
    ) -> None:
        dt = datetime(2025, 7, 1, tzinfo=UTC)
        _helpers._storage.mark_helpful = AsyncMock(
            return_value={
                "id": sample_uuid,
                "last_applied_at": dt,
                "success_count": 3,
                "effectiveness_score": 0.75,
            }
        )

        result = await memory_mark_helpful(mock_pool, sample_uuid_str)

        assert result["id"] == str(sample_uuid)
        assert result["last_applied_at"] == dt.isoformat()
        assert result["success_count"] == 3

    async def test_returns_error_when_not_found(
        self,
        mock_pool: AsyncMock,
        sample_uuid_str: str,
    ) -> None:
        _helpers._storage.mark_helpful = AsyncMock(return_value=None)

        result = await memory_mark_helpful(mock_pool, sample_uuid_str)

        assert result == {"error": "Rule not found"}

    async def test_converts_string_id_to_uuid(
        self,
        mock_pool: AsyncMock,
        sample_uuid: uuid.UUID,
        sample_uuid_str: str,
    ) -> None:
        _helpers._storage.mark_helpful = AsyncMock(return_value={"id": sample_uuid})

        await memory_mark_helpful(mock_pool, sample_uuid_str)

        call_args = _helpers._storage.mark_helpful.call_args[0]
        assert call_args[1] == sample_uuid
        assert isinstance(call_args[1], uuid.UUID)


# ---------------------------------------------------------------------------
# memory_mark_harmful tests
# ---------------------------------------------------------------------------


class TestMemoryMarkHarmful:
    """memory_mark_harmful delegates to _storage.mark_harmful."""

    async def test_returns_serialized_result(
        self,
        mock_pool: AsyncMock,
        sample_uuid: uuid.UUID,
        sample_uuid_str: str,
    ) -> None:
        dt = datetime(2025, 8, 1, tzinfo=UTC)
        _helpers._storage.mark_harmful = AsyncMock(
            return_value={
                "id": sample_uuid,
                "last_applied_at": dt,
                "harmful_count": 2,
                "effectiveness_score": 0.3,
            }
        )

        result = await memory_mark_harmful(mock_pool, sample_uuid_str)

        assert result["id"] == str(sample_uuid)
        assert result["last_applied_at"] == dt.isoformat()
        assert result["harmful_count"] == 2

    async def test_returns_error_when_not_found(
        self,
        mock_pool: AsyncMock,
        sample_uuid_str: str,
    ) -> None:
        _helpers._storage.mark_harmful = AsyncMock(return_value=None)

        result = await memory_mark_harmful(mock_pool, sample_uuid_str)

        assert result == {"error": "Rule not found"}

    async def test_passes_reason(
        self,
        mock_pool: AsyncMock,
        sample_uuid: uuid.UUID,
        sample_uuid_str: str,
    ) -> None:
        _helpers._storage.mark_harmful = AsyncMock(
            return_value={"id": sample_uuid, "harmful_count": 1}
        )

        await memory_mark_harmful(
            mock_pool,
            sample_uuid_str,
            reason="caused timeout",
        )

        _helpers._storage.mark_harmful.assert_awaited_once()
        call_kwargs = _helpers._storage.mark_harmful.call_args[1]
        assert call_kwargs["reason"] == "caused timeout"

    async def test_reason_defaults_to_none(
        self,
        mock_pool: AsyncMock,
        sample_uuid: uuid.UUID,
        sample_uuid_str: str,
    ) -> None:
        _helpers._storage.mark_harmful = AsyncMock(
            return_value={"id": sample_uuid, "harmful_count": 1}
        )

        await memory_mark_harmful(mock_pool, sample_uuid_str)

        call_kwargs = _helpers._storage.mark_harmful.call_args[1]
        assert call_kwargs["reason"] is None

    async def test_converts_string_id_to_uuid(
        self,
        mock_pool: AsyncMock,
        sample_uuid: uuid.UUID,
        sample_uuid_str: str,
    ) -> None:
        _helpers._storage.mark_harmful = AsyncMock(
            return_value={"id": sample_uuid, "harmful_count": 1}
        )

        await memory_mark_harmful(mock_pool, sample_uuid_str)

        call_args = _helpers._storage.mark_harmful.call_args[0]
        assert call_args[1] == sample_uuid
        assert isinstance(call_args[1], uuid.UUID)
