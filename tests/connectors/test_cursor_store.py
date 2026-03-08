"""Tests for cursor_store module (DB-backed cursor persistence)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.cursor_store import (
    _SELECT_SQL,
    _UPSERT_SQL,
    load_cursor,
    save_cursor,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_pool() -> MagicMock:
    """Create a mock asyncpg pool with acquire() returning an async context manager."""
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_ctx
    return mock_pool


# ---------------------------------------------------------------------------
# save_cursor tests
# ---------------------------------------------------------------------------


class TestSaveCursor:
    """Tests for save_cursor()."""

    async def test_save_cursor_calls_upsert(self) -> None:
        """save_cursor issues an upsert with the correct parameters."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value

        await save_cursor(pool, "gmail", "gmail:user:alice@example.com", '{"history_id":"123"}')

        conn.execute.assert_awaited_once()
        args = conn.execute.await_args
        assert args[0][0] == _UPSERT_SQL
        assert args[0][1] == "gmail"
        assert args[0][2] == "gmail:user:alice@example.com"
        assert args[0][3] == '{"history_id":"123"}'
        # 4th arg is the timestamp
        assert args[0][4] is not None

    async def test_save_cursor_propagates_errors(self) -> None:
        """save_cursor does not swallow DB errors."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.execute.side_effect = RuntimeError("DB down")

        with pytest.raises(RuntimeError, match="DB down"):
            await save_cursor(pool, "telegram_bot", "bot:test", '{"last_update_id":1}')


# ---------------------------------------------------------------------------
# load_cursor tests
# ---------------------------------------------------------------------------


class TestLoadCursor:
    """Tests for load_cursor()."""

    async def test_load_cursor_returns_value(self) -> None:
        """load_cursor returns the stored checkpoint_cursor string."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.return_value = {"checkpoint_cursor": '{"history_id":"456"}'}

        result = await load_cursor(pool, "gmail", "gmail:user:bob@example.com")

        assert result == '{"history_id":"456"}'
        conn.fetchrow.assert_awaited_once()
        args = conn.fetchrow.await_args
        assert args[0][0] == _SELECT_SQL
        assert args[0][1] == "gmail"
        assert args[0][2] == "gmail:user:bob@example.com"

    async def test_load_cursor_returns_none_when_row_missing(self) -> None:
        """load_cursor returns None when no row exists for the connector."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.return_value = None

        result = await load_cursor(pool, "telegram_bot", "bot:nonexistent")

        assert result is None

    async def test_load_cursor_returns_none_when_cursor_null(self) -> None:
        """load_cursor returns None when the row exists but checkpoint_cursor is NULL."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.return_value = {"checkpoint_cursor": None}

        result = await load_cursor(pool, "discord", "discord:user:123")

        assert result is None

    async def test_load_cursor_propagates_errors(self) -> None:
        """load_cursor does not swallow DB errors."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.side_effect = RuntimeError("DB down")

        with pytest.raises(RuntimeError, match="DB down"):
            await load_cursor(pool, "gmail", "gmail:user:alice@example.com")


# ---------------------------------------------------------------------------
# Round-trip test (mock level)
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Test save then load cursor round-trip using mock pool."""

    async def test_save_then_load(self) -> None:
        """Saving and then loading returns the same cursor value."""
        stored: dict[str, str | None] = {}

        async def mock_execute(sql: str, *args: object) -> None:
            if "INSERT" in sql:
                stored["cursor"] = str(args[2])  # cursor_value

        async def mock_fetchrow(sql: str, *args: object) -> dict | None:
            if "SELECT" in sql and "cursor" in stored:
                return {"checkpoint_cursor": stored["cursor"]}
            return None

        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.execute.side_effect = mock_execute
        conn.fetchrow.side_effect = mock_fetchrow

        cursor_val = json.dumps({"last_update_id": 42})
        await save_cursor(pool, "telegram_bot", "bot:test", cursor_val)
        result = await load_cursor(pool, "telegram_bot", "bot:test")

        assert result == cursor_val
        loaded = json.loads(result)
        assert loaded["last_update_id"] == 42
