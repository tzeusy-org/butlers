"""Tests for get_memory() with reference bumping in Memory Butler."""

from __future__ import annotations

import importlib.util
import uuid
from unittest.mock import AsyncMock

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
get_memory = _mod.get_memory
_VALID_MEMORY_TYPES = _mod._VALID_MEMORY_TYPES
_TYPE_TABLE = _mod._TYPE_TABLE

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Return an AsyncMock asyncpg pool."""
    pool = AsyncMock()
    return pool


@pytest.fixture()
def sample_uuid() -> uuid.UUID:
    """Return a fixed UUID for deterministic testing."""
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetMemoryReturnsDict:
    """get_memory returns a dict when the record is found."""

    async def test_returns_dict_when_found(
        self, mock_pool: AsyncMock, sample_uuid: uuid.UUID
    ) -> None:
        mock_record = {"id": sample_uuid, "content": "test episode", "reference_count": 2}
        mock_pool.fetchrow = AsyncMock(return_value=mock_record)

        result = await get_memory(mock_pool, "episode", sample_uuid)

        assert isinstance(result, dict)
        assert result["id"] == sample_uuid
        assert result["content"] == "test episode"
        assert result["reference_count"] == 2


class TestGetMemoryReturnsNone:
    """get_memory returns None when the record is not found."""

    async def test_returns_none_when_not_found(
        self, mock_pool: AsyncMock, sample_uuid: uuid.UUID
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value=None)

        result = await get_memory(mock_pool, "fact", sample_uuid)

        assert result is None


class TestGetMemoryInvalidType:
    """get_memory raises ValueError for invalid memory types."""

    async def test_raises_valueerror_for_invalid_type(
        self, mock_pool: AsyncMock, sample_uuid: uuid.UUID
    ) -> None:
        with pytest.raises(ValueError, match="Invalid memory_type: 'invalid'"):
            await get_memory(mock_pool, "invalid", sample_uuid)

    async def test_raises_valueerror_for_empty_string(
        self, mock_pool: AsyncMock, sample_uuid: uuid.UUID
    ) -> None:
        with pytest.raises(ValueError, match="Invalid memory_type"):
            await get_memory(mock_pool, "", sample_uuid)

    async def test_does_not_call_pool_on_invalid_type(
        self, mock_pool: AsyncMock, sample_uuid: uuid.UUID
    ) -> None:
        with pytest.raises(ValueError):
            await get_memory(mock_pool, "bogus", sample_uuid)

        mock_pool.fetchrow.assert_not_awaited()


class TestGetMemoryTableNames:
    """get_memory uses the correct table name for each memory type."""

    async def test_uses_episodes_table_for_episode(
        self, mock_pool: AsyncMock, sample_uuid: uuid.UUID
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value=None)

        await get_memory(mock_pool, "episode", sample_uuid)

        sql = mock_pool.fetchrow.call_args[0][0]
        assert "UPDATE episodes" in sql

    async def test_uses_facts_table_for_fact(
        self, mock_pool: AsyncMock, sample_uuid: uuid.UUID
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value=None)

        await get_memory(mock_pool, "fact", sample_uuid)

        sql = mock_pool.fetchrow.call_args[0][0]
        assert "UPDATE facts" in sql

    async def test_uses_rules_table_for_rule(
        self, mock_pool: AsyncMock, sample_uuid: uuid.UUID
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value=None)

        await get_memory(mock_pool, "rule", sample_uuid)

        sql = mock_pool.fetchrow.call_args[0][0]
        assert "UPDATE rules" in sql


class TestGetMemoryReferenceBump:
    """get_memory SQL includes reference_count bump and last_referenced_at update."""

    async def test_sql_includes_reference_count_bump(
        self, mock_pool: AsyncMock, sample_uuid: uuid.UUID
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value=None)

        await get_memory(mock_pool, "episode", sample_uuid)

        sql = mock_pool.fetchrow.call_args[0][0]
        assert "reference_count = reference_count + 1" in sql

    async def test_sql_includes_last_referenced_at_update(
        self, mock_pool: AsyncMock, sample_uuid: uuid.UUID
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value=None)

        await get_memory(mock_pool, "fact", sample_uuid)

        sql = mock_pool.fetchrow.call_args[0][0]
        assert "last_referenced_at = now()" in sql

    async def test_sql_includes_returning_star(
        self, mock_pool: AsyncMock, sample_uuid: uuid.UUID
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value=None)

        await get_memory(mock_pool, "rule", sample_uuid)

        sql = mock_pool.fetchrow.call_args[0][0]
        assert "RETURNING *" in sql


class TestGetMemoryPassesUUID:
    """get_memory passes the correct UUID to the query."""

    async def test_passes_correct_uuid(self, mock_pool: AsyncMock, sample_uuid: uuid.UUID) -> None:
        mock_pool.fetchrow = AsyncMock(return_value=None)

        await get_memory(mock_pool, "episode", sample_uuid)

        call_args = mock_pool.fetchrow.call_args[0]
        assert call_args[1] == sample_uuid

    async def test_passes_different_uuid(self, mock_pool: AsyncMock) -> None:
        other_uuid = uuid.uuid4()
        mock_pool.fetchrow = AsyncMock(return_value=None)

        await get_memory(mock_pool, "fact", other_uuid)

        call_args = mock_pool.fetchrow.call_args[0]
        assert call_args[1] == other_uuid
