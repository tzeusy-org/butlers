"""Tests for forget_memory() soft-delete in Memory Butler storage."""

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
forget_memory = _mod.forget_memory
_VALID_MEMORY_TYPES = _mod._VALID_MEMORY_TYPES

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Return an AsyncMock asyncpg pool with execute returning 'UPDATE 1'."""
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="UPDATE 1")
    return pool


@pytest.fixture()
def memory_id() -> uuid.UUID:
    """Return a fixed UUID for testing."""
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestForgetMemory:
    """Tests for forget_memory()."""

    async def test_returns_true_when_fact_updated(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """forget_memory returns True when the fact row is found and updated."""
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")
        result = await forget_memory(mock_pool, "fact", memory_id)
        assert result is True

    async def test_returns_false_when_row_not_found(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """forget_memory returns False when no row matches the given ID."""
        mock_pool.execute = AsyncMock(return_value="UPDATE 0")
        result = await forget_memory(mock_pool, "fact", memory_id)
        assert result is False

    async def test_raises_value_error_for_invalid_type(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """forget_memory raises ValueError for an unrecognised memory type."""
        with pytest.raises(ValueError, match="Invalid memory_type"):
            await forget_memory(mock_pool, "invalid_type", memory_id)

    async def test_fact_uses_validity_retracted(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """For facts, the UPDATE sets validity = 'retracted'."""
        await forget_memory(mock_pool, "fact", memory_id)
        sql = mock_pool.execute.call_args[0][0]
        assert "UPDATE facts" in sql
        assert "validity = 'retracted'" in sql

    async def test_episode_uses_expires_at_now(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """For episodes, the UPDATE sets expires_at = now()."""
        await forget_memory(mock_pool, "episode", memory_id)
        sql = mock_pool.execute.call_args[0][0]
        assert "UPDATE episodes" in sql
        assert "expires_at = now()" in sql

    async def test_rule_uses_metadata_forgotten_flag(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """For rules, the UPDATE merges {"forgotten": true} into metadata."""
        await forget_memory(mock_pool, "rule", memory_id)
        sql = mock_pool.execute.call_args[0][0]
        assert "UPDATE rules" in sql
        assert '"forgotten": true' in sql
        assert "metadata" in sql

    async def test_passes_correct_uuid(self, mock_pool: AsyncMock, memory_id: uuid.UUID) -> None:
        """The memory_id UUID is passed as the $1 parameter."""
        await forget_memory(mock_pool, "fact", memory_id)
        passed_id = mock_pool.execute.call_args[0][1]
        assert passed_id == memory_id

    async def test_all_three_types_accepted(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """All three valid memory types ('episode', 'fact', 'rule') are accepted."""
        for memory_type in ("episode", "fact", "rule"):
            mock_pool.execute = AsyncMock(return_value="UPDATE 1")
            result = await forget_memory(mock_pool, memory_type, memory_id)
            assert result is True, f"Expected True for memory_type={memory_type!r}"

    async def test_returns_true_for_episode_updated(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """forget_memory returns True when an episode row is updated."""
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")
        result = await forget_memory(mock_pool, "episode", memory_id)
        assert result is True

    async def test_returns_true_for_rule_updated(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """forget_memory returns True when a rule row is updated."""
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")
        result = await forget_memory(mock_pool, "rule", memory_id)
        assert result is True

    async def test_returns_false_for_episode_not_found(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """forget_memory returns False when no episode matches."""
        mock_pool.execute = AsyncMock(return_value="UPDATE 0")
        result = await forget_memory(mock_pool, "episode", memory_id)
        assert result is False

    async def test_returns_false_for_rule_not_found(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """forget_memory returns False when no rule matches."""
        mock_pool.execute = AsyncMock(return_value="UPDATE 0")
        result = await forget_memory(mock_pool, "rule", memory_id)
        assert result is False

    async def test_valid_memory_types_constant(self) -> None:
        """The _VALID_MEMORY_TYPES constant contains exactly the three expected types."""
        assert _VALID_MEMORY_TYPES == {"episode", "fact", "rule"}

    async def test_pool_execute_called_once_per_call(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """Each forget_memory call invokes pool.execute exactly once."""
        await forget_memory(mock_pool, "fact", memory_id)
        mock_pool.execute.assert_awaited_once()
