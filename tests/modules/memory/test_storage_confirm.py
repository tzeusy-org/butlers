"""Tests for confirm_memory() confidence decay reset in Memory Butler storage."""

from __future__ import annotations

import importlib.util
import uuid
from unittest.mock import AsyncMock

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load storage module from disk (roster/ is not a Python package).
# Mock sentence_transformers before loading to avoid heavy dependency.
# ---------------------------------------------------------------------------

_STORAGE_PATH = MEMORY_MODULE_PATH / "storage.py"


def _load_storage_module():
    # Pre-mock sentence_transformers to avoid import failure
    # (mock setup removed — no longer needed after refactor)

    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_storage_module()
confirm_memory = _mod.confirm_memory
_VALID_MEMORY_TYPES = _mod._VALID_MEMORY_TYPES
_TYPE_TABLE = _mod._TYPE_TABLE

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


class TestConfirmMemory:
    """Tests for confirm_memory()."""

    async def test_confirm_fact_updates_last_confirmed_at(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """Confirming a fact calls UPDATE with last_confirmed_at = now()."""
        await confirm_memory(mock_pool, "fact", memory_id)
        sql = mock_pool.execute.call_args[0][0]
        assert "last_confirmed_at = now()" in sql

    async def test_confirm_rule_updates_last_confirmed_at(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """Confirming a rule calls UPDATE with last_confirmed_at = now()."""
        await confirm_memory(mock_pool, "rule", memory_id)
        sql = mock_pool.execute.call_args[0][0]
        assert "last_confirmed_at = now()" in sql

    async def test_episode_raises_value_error(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """Attempting to confirm an episode raises ValueError."""
        with pytest.raises(ValueError, match="Episodes cannot be confirmed"):
            await confirm_memory(mock_pool, "episode", memory_id)

    async def test_invalid_type_raises_value_error(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """An unrecognised memory type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid memory_type"):
            await confirm_memory(mock_pool, "bogus", memory_id)

    async def test_returns_true_when_row_found(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """Returns True when execute returns 'UPDATE 1'."""
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")
        result = await confirm_memory(mock_pool, "fact", memory_id)
        assert result is True

    async def test_returns_false_when_row_not_found(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """Returns False when execute returns 'UPDATE 0'."""
        mock_pool.execute = AsyncMock(return_value="UPDATE 0")
        result = await confirm_memory(mock_pool, "fact", memory_id)
        assert result is False

    async def test_correct_table_for_fact(self, mock_pool: AsyncMock, memory_id: uuid.UUID) -> None:
        """Fact confirmation targets the 'facts' table."""
        await confirm_memory(mock_pool, "fact", memory_id)
        sql = mock_pool.execute.call_args[0][0]
        assert "UPDATE facts" in sql

    async def test_correct_table_for_rule(self, mock_pool: AsyncMock, memory_id: uuid.UUID) -> None:
        """Rule confirmation targets the 'rules' table."""
        await confirm_memory(mock_pool, "rule", memory_id)
        sql = mock_pool.execute.call_args[0][0]
        assert "UPDATE rules" in sql

    async def test_uuid_passed_correctly(self, mock_pool: AsyncMock, memory_id: uuid.UUID) -> None:
        """The memory_id UUID is passed as the $1 parameter."""
        await confirm_memory(mock_pool, "fact", memory_id)
        passed_id = mock_pool.execute.call_args[0][1]
        assert passed_id == memory_id

    async def test_no_execute_call_when_episode_rejected(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """pool.execute is never called when episode validation fails."""
        with pytest.raises(ValueError):
            await confirm_memory(mock_pool, "episode", memory_id)
        mock_pool.execute.assert_not_awaited()

    async def test_no_execute_call_when_invalid_type(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """pool.execute is never called when type validation fails."""
        with pytest.raises(ValueError):
            await confirm_memory(mock_pool, "invalid_type", memory_id)
        mock_pool.execute.assert_not_awaited()
