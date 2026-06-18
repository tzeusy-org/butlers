"""Tests for the butlers_v1 versioned read-model boundary.

Verifies:
- ``READ_MODEL_VERSION`` is the stable string ``'butlers_v1'``
- ``SESSIONS_24H_SQL`` is a non-empty string with the expected structural markers
- ``query_sessions_24h`` returns a ``{butler: int}`` mapping on success
- ``query_sessions_24h`` swallows exceptions and returns ``{}``
- ``query_sessions_24h`` swallows ``asyncio.TimeoutError`` and returns ``{}``
- ``query_sessions_24h`` passes timeout_s through to ``asyncio.wait_for``
- Fan-out results with missing / malformed rows are handled gracefully
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.api.read_models.butlers_v1 import (
    READ_MODEL_VERSION,
    SESSIONS_24H_SQL,
    query_sessions_24h,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Version marker
# ---------------------------------------------------------------------------


def test_version_marker_is_butlers_v1():
    """READ_MODEL_VERSION must equal 'butlers_v1' — change only on breaking update."""
    assert READ_MODEL_VERSION == "butlers_v1"


# ---------------------------------------------------------------------------
# SQL constant
# ---------------------------------------------------------------------------


def test_sessions_24h_sql_is_non_empty_string():
    assert isinstance(SESSIONS_24H_SQL, str)
    assert len(SESSIONS_24H_SQL) > 0


def test_sessions_24h_sql_has_to_regclass_guard():
    """SQL must use to_regclass to guard against missing sessions table."""
    assert "to_regclass" in SESSIONS_24H_SQL
    assert "sessions" in SESSIONS_24H_SQL


def test_sessions_24h_sql_has_started_at_filter():
    """SQL must filter by started_at with a positional parameter."""
    assert "started_at" in SESSIONS_24H_SQL
    assert "$1" in SESSIONS_24H_SQL


def test_sessions_24h_sql_returns_zero_when_no_table():
    """SQL must return 0 (not an error) when sessions table is absent."""
    assert "ELSE 0" in SESSIONS_24H_SQL


# ---------------------------------------------------------------------------
# query_sessions_24h — success paths
# ---------------------------------------------------------------------------


def _make_fan_out_row(count: int) -> MagicMock:
    """Row where row[0] returns count (asyncpg Record-like)."""
    m = MagicMock()
    m.__getitem__ = lambda self, k: count
    return m


def _make_db(fan_out_result: dict) -> MagicMock:
    db = MagicMock()
    db.fan_out = AsyncMock(return_value=fan_out_result)
    return db


async def test_query_sessions_24h_returns_counts():
    """Returns {butler_name: count} for each butler with rows."""
    db = _make_db(
        {
            "assistant": [_make_fan_out_row(3)],
            "secretary": [_make_fan_out_row(7)],
        }
    )
    result = await query_sessions_24h(db)
    assert result == {"assistant": 3, "secretary": 7}


async def test_query_sessions_24h_empty_fan_out_returns_empty_dict():
    """Returns {} when no butler has rows."""
    db = _make_db({})
    result = await query_sessions_24h(db)
    assert result == {}


async def test_query_sessions_24h_empty_rows_per_butler_skipped():
    """Butlers with an empty row list are omitted from the result."""
    db = _make_db({"assistant": [], "secretary": [_make_fan_out_row(2)]})
    result = await query_sessions_24h(db)
    assert "assistant" not in result
    assert result["secretary"] == 2


async def test_query_sessions_24h_zero_count_included():
    """A count of 0 is a valid result and must be kept in the mapping."""
    db = _make_db({"assistant": [_make_fan_out_row(0)]})
    result = await query_sessions_24h(db)
    assert result == {"assistant": 0}


async def test_query_sessions_24h_passes_butler_names():
    """butler_names kwarg is forwarded to fan_out."""
    db = _make_db({"assistant": [_make_fan_out_row(1)]})
    await query_sessions_24h(db, butler_names=["assistant"])
    _, kwargs = db.fan_out.call_args
    assert kwargs.get("butler_names") == ["assistant"]


# ---------------------------------------------------------------------------
# query_sessions_24h — timeout forwarding
# ---------------------------------------------------------------------------


async def test_query_sessions_24h_default_timeout_is_five_seconds():
    """Default timeout_s must be 5.0 seconds."""
    db = _make_db({"assistant": [_make_fan_out_row(1)]})
    with patch("butlers.api.read_models.butlers_v1.asyncio.wait_for") as mock_wait:
        mock_wait.return_value = {"assistant": [_make_fan_out_row(1)]}
        await query_sessions_24h(db)
        _, kwargs = mock_wait.call_args
        assert kwargs.get("timeout") == 5.0


async def test_query_sessions_24h_custom_timeout_forwarded():
    """Custom timeout_s is passed to asyncio.wait_for."""
    db = _make_db({"assistant": [_make_fan_out_row(1)]})
    with patch("butlers.api.read_models.butlers_v1.asyncio.wait_for") as mock_wait:
        mock_wait.return_value = {"assistant": [_make_fan_out_row(1)]}
        await query_sessions_24h(db, timeout_s=2.5)
        _, kwargs = mock_wait.call_args
        assert kwargs.get("timeout") == 2.5


# ---------------------------------------------------------------------------
# query_sessions_24h — graceful error handling
# ---------------------------------------------------------------------------


async def test_query_sessions_24h_swallows_db_exception():
    """DB errors are swallowed — returns {} rather than raising."""
    db = MagicMock()
    db.fan_out = AsyncMock(side_effect=RuntimeError("db failure"))
    result = await query_sessions_24h(db)
    assert result == {}


async def test_query_sessions_24h_swallows_timeout():
    """asyncio.TimeoutError is swallowed — returns {}."""
    db = MagicMock()
    db.fan_out = AsyncMock(side_effect=TimeoutError())
    result = await query_sessions_24h(db)
    assert result == {}


async def test_query_sessions_24h_malformed_row_defaults_to_zero():
    """A row whose first element raises ValueError yields count 0."""
    bad_row = MagicMock()
    bad_row.__getitem__ = lambda self, k: "not_an_int"
    db = _make_db({"assistant": [bad_row]})
    result = await query_sessions_24h(db)
    assert result == {"assistant": 0}
