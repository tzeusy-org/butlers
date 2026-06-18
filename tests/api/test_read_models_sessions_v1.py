"""Tests for the sessions_v1 versioned read-model boundary.

Verifies:
- ``row_to_summary`` converts a raw record to the typed DTO
- ``row_to_detail`` converts a raw record with JSON coercion
- ``query_session_summaries_fan_out`` returns a typed FanOutSummaryResult
- ``query_session_detail_fan_out`` returns the first matching butler's row
- ``query_session_detail_single`` returns a typed SingleDetailResult
- Version marker is stable and matches the module name
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from butlers.api.db import DatabaseManager
from butlers.api.read_models.sessions_v1 import (
    DETAIL_COLUMNS,
    READ_MODEL_VERSION,
    SUMMARY_COLUMNS,
    FanOutDetailResult,
    FanOutSummaryResult,
    SingleDetailResult,
    query_session_detail_fan_out,
    query_session_detail_single,
    query_session_summaries_fan_out,
    row_to_detail,
    row_to_summary,
)

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)
_SESSION_ID = uuid4()
_PARENT_ID = uuid4()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summary_record(**overrides) -> dict:
    base = {
        "id": _SESSION_ID,
        "prompt": "test prompt",
        "trigger_source": "schedule",
        "request_id": "req-abc",
        "success": True,
        "started_at": _NOW,
        "completed_at": _NOW,
        "duration_ms": 1500,
        "model": "claude-sonnet",
        "complexity": "simple",
        "input_tokens": 100,
        "output_tokens": 50,
    }
    base.update(overrides)
    return base


def _detail_record(**overrides) -> dict:
    base = {
        "id": _SESSION_ID,
        "prompt": "test prompt",
        "trigger_source": "api",
        "result": "done",
        "tool_calls": json.dumps([{"name": "read_file", "input": {}}]),
        "duration_ms": 2000,
        "trace_id": "trace-xyz",
        "request_id": "req-def",
        "cost": json.dumps({"total": 0.01}),
        "started_at": _NOW,
        "completed_at": _NOW,
        "success": True,
        "error": None,
        "model": "claude-opus",
        "input_tokens": 200,
        "output_tokens": 80,
        "parent_session_id": _PARENT_ID,
        "complexity": "medium",
        "resolution_source": "cache",
    }
    base.update(overrides)
    return base


def _make_record(d: dict):
    """Wrap a dict in a MagicMock that supports subscript access."""
    m = MagicMock()
    m.__getitem__ = lambda self, k: d[k]
    return m


# ---------------------------------------------------------------------------
# Version marker
# ---------------------------------------------------------------------------


def test_version_marker_is_sessions_v1():
    """READ_MODEL_VERSION must equal 'sessions_v1' — change only on breaking update."""
    assert READ_MODEL_VERSION == "sessions_v1"


# ---------------------------------------------------------------------------
# row_to_summary
# ---------------------------------------------------------------------------


def test_row_to_summary_maps_all_fields():
    row = _make_record(_summary_record())
    dto = row_to_summary(row, butler="atlas")

    assert dto.id == _SESSION_ID
    assert dto.butler == "atlas"
    assert dto.prompt == "test prompt"
    assert dto.trigger_source == "schedule"
    assert dto.request_id == "req-abc"
    assert dto.success is True
    assert dto.started_at == _NOW
    assert dto.completed_at == _NOW
    assert dto.duration_ms == 1500
    assert dto.model == "claude-sonnet"
    assert dto.complexity == "simple"
    assert dto.input_tokens == 100
    assert dto.output_tokens == 50


def test_row_to_summary_none_butler_allowed():
    row = _make_record(_summary_record())
    dto = row_to_summary(row)
    assert dto.butler is None


# ---------------------------------------------------------------------------
# row_to_detail — JSON coercion
# ---------------------------------------------------------------------------


def test_row_to_detail_coerces_tool_calls_json_string():
    """tool_calls stored as a JSON string must be parsed to a list."""
    row = _make_record(_detail_record(tool_calls=json.dumps([{"name": "search"}])))
    dto = row_to_detail(row, butler="atlas")
    assert isinstance(dto.tool_calls, list)
    assert dto.tool_calls[0]["name"] == "search"


def test_row_to_detail_accepts_tool_calls_as_list():
    """tool_calls already a list (e.g. asyncpg JSONB decode) must pass through."""
    row = _make_record(_detail_record(tool_calls=[{"name": "write_file"}]))
    dto = row_to_detail(row, butler="atlas")
    assert dto.tool_calls[0]["name"] == "write_file"


def test_row_to_detail_coerces_cost_json_string():
    """cost stored as a JSON string must be parsed to a dict."""
    row = _make_record(_detail_record(cost=json.dumps({"total": 0.02})))
    dto = row_to_detail(row, butler="atlas")
    assert isinstance(dto.cost, dict)
    assert dto.cost["total"] == 0.02


def test_row_to_detail_none_tool_calls_becomes_empty_list():
    """None tool_calls should become an empty list (falsy guard)."""
    row = _make_record(_detail_record(tool_calls=None))
    dto = row_to_detail(row, butler="atlas")
    assert dto.tool_calls == []


def test_row_to_detail_maps_all_scalar_fields():
    row = _make_record(_detail_record())
    dto = row_to_detail(row, butler="atlas")

    assert dto.id == _SESSION_ID
    assert dto.butler == "atlas"
    assert dto.result == "done"
    assert dto.duration_ms == 2000
    assert dto.trace_id == "trace-xyz"
    assert dto.model == "claude-opus"
    assert dto.input_tokens == 200
    assert dto.output_tokens == 80
    assert dto.parent_session_id == _PARENT_ID
    assert dto.complexity == "medium"
    assert dto.resolution_source == "cache"


# ---------------------------------------------------------------------------
# query_session_summaries_fan_out
# ---------------------------------------------------------------------------


async def test_query_session_summaries_fan_out_aggregates_total():
    """Total count is the sum of count rows across all butlers."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas", "general"]

    count_result = {"atlas": [[3]], "general": [[2]]}
    summary_row = _make_record(_summary_record())
    data_result = {
        "atlas": [summary_row, summary_row],
        "general": [summary_row],
    }

    mock_db.fan_out = AsyncMock(side_effect=[count_result, data_result])

    result = await query_session_summaries_fan_out(mock_db, "", ())

    assert isinstance(result, FanOutSummaryResult)
    assert result.total == 5
    assert len(result.rows) == 3


async def test_query_session_summaries_fan_out_passes_where_and_butler_names():
    """WHERE clause, args, and butler_names are forwarded to fan_out calls."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas"]
    # First call is COUNT, second call is data rows (empty is fine here)
    mock_db.fan_out = AsyncMock(side_effect=[{"atlas": [[1]]}, {"atlas": []}])

    await query_session_summaries_fan_out(
        mock_db, " WHERE success = $1", (True,), butler_names=["atlas"]
    )

    # Both fan_out calls should use butler_names=["atlas"]
    calls = mock_db.fan_out.call_args_list
    assert all(call.kwargs.get("butler_names") == ["atlas"] for call in calls)
    # WHERE clause should be in the SQL
    assert " WHERE success = $1" in calls[0].args[0]


async def test_query_session_summaries_fan_out_empty_results():
    """When no butlers have data, returns total=0 and empty rows."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = []
    mock_db.fan_out = AsyncMock(return_value={})

    result = await query_session_summaries_fan_out(mock_db, "", ())
    assert result.total == 0
    assert result.rows == []


# ---------------------------------------------------------------------------
# query_session_detail_fan_out
# ---------------------------------------------------------------------------


async def test_query_session_detail_fan_out_returns_first_match():
    """Returns the first butler that has a row for the given session_id."""
    detail_row = _make_record(_detail_record())
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas", "general"]
    mock_db.fan_out = AsyncMock(return_value={"atlas": [], "general": [detail_row]})

    result = await query_session_detail_fan_out(mock_db, _SESSION_ID)

    assert isinstance(result, FanOutDetailResult)
    assert result.butler == "general"
    assert result.row is not None
    assert result.row.id == _SESSION_ID


async def test_query_session_detail_fan_out_not_found():
    """Returns FanOutDetailResult with row=None when no butler has the session."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas"]
    mock_db.fan_out = AsyncMock(return_value={"atlas": []})

    result = await query_session_detail_fan_out(mock_db, _SESSION_ID)
    assert result.row is None
    assert result.butler is None


# ---------------------------------------------------------------------------
# query_session_detail_single
# ---------------------------------------------------------------------------


async def test_query_session_detail_single_found():
    """Returns a SingleDetailResult with the row when found."""
    detail_row = _make_record(_detail_record())
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=detail_row)

    result = await query_session_detail_single(mock_pool, _SESSION_ID, butler="atlas")

    assert isinstance(result, SingleDetailResult)
    assert result.found is True
    assert result.row is not None
    assert result.row.butler == "atlas"


async def test_query_session_detail_single_not_found():
    """Returns SingleDetailResult with found=False when pool returns None."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=None)

    result = await query_session_detail_single(mock_pool, _SESSION_ID)
    assert result.found is False
    assert result.row is None


# ---------------------------------------------------------------------------
# SUMMARY_COLUMNS / DETAIL_COLUMNS are non-empty strings
# ---------------------------------------------------------------------------


def test_summary_columns_is_non_empty_string():
    assert isinstance(SUMMARY_COLUMNS, str)
    assert len(SUMMARY_COLUMNS) > 0
    # Must include the key columns the DTO expects
    assert "started_at" in SUMMARY_COLUMNS
    assert "input_tokens" in SUMMARY_COLUMNS


def test_detail_columns_is_non_empty_string():
    assert isinstance(DETAIL_COLUMNS, str)
    assert len(DETAIL_COLUMNS) > 0
    assert "tool_calls" in DETAIL_COLUMNS
    assert "resolution_source" in DETAIL_COLUMNS
