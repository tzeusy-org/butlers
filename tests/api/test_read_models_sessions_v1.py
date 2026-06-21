"""Tests for the sessions_v1 versioned read-model boundary.

Verifies:
- ``row_to_summary`` converts a raw record to the typed DTO
- ``row_to_detail`` converts a raw record with JSON coercion
- ``query_session_summaries_keyset_fan_out`` merges + truncates + cursors
- ``query_session_aggregate_fan_out`` sums scalars + builds by_butler
- ``encode_session_cursor`` / ``decode_session_cursor`` round-trip
- ``query_session_detail_fan_out`` returns the first matching butler's row
- ``query_session_detail_single`` returns a typed SingleDetailResult
- Version marker is stable and matches the module name
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from butlers.api.db import DatabaseManager
from butlers.api.read_models.sessions_v1 import (
    DETAIL_COLUMNS,
    READ_MODEL_VERSION,
    SUMMARY_COLUMNS,
    FanOutAggregateResult,
    FanOutDetailResult,
    FanOutKeysetResult,
    SingleDetailResult,
    decode_session_cursor,
    encode_session_cursor,
    query_session_aggregate_fan_out,
    query_session_detail_fan_out,
    query_session_detail_single,
    query_session_summaries_keyset_fan_out,
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
# Cursor round-trip
# ---------------------------------------------------------------------------


def test_session_cursor_round_trip():
    """encode -> decode reproduces the (started_at, id) keyset position."""
    sid = uuid4()
    cursor = encode_session_cursor(_NOW, sid)
    decoded_at, decoded_id = decode_session_cursor(cursor)
    assert decoded_at == _NOW
    assert decoded_id == sid


def test_decode_session_cursor_rejects_garbage():
    """A non-base64 / non-JSON cursor raises ValueError (router maps to 422)."""
    with pytest.raises(ValueError):
        decode_session_cursor("not-a-cursor")


def test_decode_session_cursor_rejects_missing_fields():
    """A base64url JSON token missing 't'/'id' is rejected."""
    import base64

    bad = base64.urlsafe_b64encode(json.dumps({"t": _NOW.isoformat()}).encode()).decode()
    with pytest.raises(ValueError):
        decode_session_cursor(bad)


# ---------------------------------------------------------------------------
# query_session_summaries_keyset_fan_out
# ---------------------------------------------------------------------------


def _summary_at(started_at: datetime):
    """A summary record with a unique id and the given started_at."""
    return _make_record(_summary_record(id=uuid4(), started_at=started_at))


async def test_keyset_fan_out_merges_and_sorts_across_butlers():
    """Rows from all butlers merge and sort by (started_at DESC, id DESC)."""
    a1 = _summary_at(_NOW)
    a2 = _summary_at(_NOW - timedelta(seconds=10))
    g1 = _summary_at(_NOW - timedelta(seconds=5))

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas", "general"]
    mock_db.fan_out = AsyncMock(return_value={"atlas": [a1, a2], "general": [g1]})

    result = await query_session_summaries_keyset_fan_out(mock_db, "", (), limit=50)

    assert isinstance(result, FanOutKeysetResult)
    started = [r.started_at for r in result.rows]
    assert started == sorted(started, reverse=True)
    # Every butler's rows must survive the merge and interleave by (started_at DESC).
    # A descending order alone is satisfied by any single butler's subset, so pin
    # the row count, the id set, and the exact interleaved order (a1=_NOW newest,
    # g1=_NOW-5s, a2=_NOW-10s). Source records are subscriptable MagicMocks (use
    # a1["id"]); merged rows are real SessionSummaryRow objects (use r.id).
    assert len(result.rows) == 3
    assert {r.id for r in result.rows} == {a1["id"], a2["id"], g1["id"]}
    assert [r.id for r in result.rows] == [a1["id"], g1["id"], a2["id"]]
    assert result.has_more is False
    assert result.next_cursor is None


async def test_keyset_fan_out_has_more_and_next_cursor():
    """When merged length > limit, has_more is True and next_cursor is set.

    next_cursor must encode the LAST returned row (index limit-1), not the
    dropped sentinel.
    """
    rows = [_summary_at(_NOW - timedelta(seconds=i)) for i in range(3)]
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas"]
    mock_db.fan_out = AsyncMock(return_value={"atlas": rows})

    result = await query_session_summaries_keyset_fan_out(mock_db, "", (), limit=2)

    assert result.has_more is True
    assert len(result.rows) == 2
    decoded_at, decoded_id = decode_session_cursor(result.next_cursor)
    assert decoded_id == result.rows[-1].id
    assert decoded_at == result.rows[-1].started_at


async def test_keyset_fan_out_has_more_boundary_crosses_butlers():
    """The (limit+1)th global row may live in a DIFFERENT butler than row[0].

    Each butler returns up to limit+1 rows; the merge-then-truncate boundary,
    has_more, and next_cursor must all be computed on the MERGED set, not per
    butler. With a single butler "compute has_more on merged length" and
    "compute has_more per butler" are indistinguishable, so this pins the
    cross-shard contract documented in query_session_summaries_keyset_fan_out.
    """
    # atlas holds the two newest, general the next three (older).
    a0 = _summary_at(_NOW)
    a1 = _summary_at(_NOW - timedelta(seconds=10))
    g2 = _summary_at(_NOW - timedelta(seconds=20))
    g3 = _summary_at(_NOW - timedelta(seconds=30))
    g4 = _summary_at(_NOW - timedelta(seconds=40))

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas", "general"]
    mock_db.fan_out = AsyncMock(return_value={"atlas": [a0, a1], "general": [g2, g3, g4]})

    result = await query_session_summaries_keyset_fan_out(
        mock_db, "", (), limit=3, butler_names=["atlas", "general"]
    )

    # Merged DESC = [a0, a1, g2, g3, g4]; kept = first 3, has_more from the rest.
    assert len(result.rows) == 3
    assert result.has_more is True
    assert result.rows[0].butler == "atlas"
    # The limit-th (last kept) row crosses into the OTHER butler.
    assert result.rows[-1].butler == "general"
    assert result.rows[-1].id == g2["id"]
    decoded_at, decoded_id = decode_session_cursor(result.next_cursor)
    assert (decoded_at, decoded_id) == (g2["started_at"], g2["id"])
    # next_cursor points at the last RETURNED row, not the first dropped one.
    assert decoded_id != g3["id"]


async def test_keyset_fan_out_appends_cursor_predicate():
    """With a cursor, the SQL gains the (started_at, id) < (...) keyset predicate."""
    cursor = encode_session_cursor(_NOW, uuid4())
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas"]
    mock_db.fan_out = AsyncMock(return_value={"atlas": []})

    await query_session_summaries_keyset_fan_out(
        mock_db, " WHERE success = $1", (True,), limit=50, cursor=cursor, butler_names=["atlas"]
    )

    call = mock_db.fan_out.call_args_list[0]
    sql = call.args[0]
    assert "(started_at, id) < ($2, $3)" in sql
    assert "LIMIT 51" in sql  # limit + 1
    # The cursor (started_at, id) is appended after the existing WHERE arg, and
    # the appended VALUES must be the decoded cursor tuple (a swapped/reordered
    # or raw-string bind would still produce 3 args, so pin the values too).
    assert len(call.args[1]) == 3
    started_at, row_id = decode_session_cursor(cursor)
    assert tuple(call.args[1][-2:]) == (started_at, row_id)
    assert call.kwargs.get("butler_names") == ["atlas"]


async def test_keyset_fan_out_first_page_no_predicate():
    """Without a cursor, no keyset predicate is added and only WHERE args bind."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas"]
    mock_db.fan_out = AsyncMock(return_value={"atlas": []})

    await query_session_summaries_keyset_fan_out(mock_db, "", (), limit=50)

    sql = mock_db.fan_out.call_args_list[0].args[0]
    assert "(started_at, id) <" not in sql
    assert "ORDER BY started_at DESC, id DESC" in sql


# ---------------------------------------------------------------------------
# query_session_aggregate_fan_out
# ---------------------------------------------------------------------------


def _agg_row(**vals):
    base = {
        "total": 0,
        "success_count": 0,
        "failed_count": 0,
        "running_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    base.update(vals)
    return _make_record(base)


async def test_aggregate_fan_out_sums_scalars_across_butlers():
    """Scalar fields are summed; by_butler holds per-butler totals desc."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas", "general"]
    mock_db.fan_out = AsyncMock(
        return_value={
            "atlas": [
                _agg_row(
                    total=10,
                    success_count=8,
                    failed_count=1,
                    running_count=1,
                    input_tokens=1000,
                    output_tokens=400,
                )
            ],
            "general": [
                _agg_row(
                    total=5,
                    success_count=5,
                    failed_count=0,
                    running_count=0,
                    input_tokens=200,
                    output_tokens=100,
                )
            ],
        }
    )

    result = await query_session_aggregate_fan_out(mock_db, "", ())

    assert isinstance(result, FanOutAggregateResult)
    assert result.total == 15
    assert result.success_count == 13
    assert result.failed_count == 1
    assert result.running_count == 1
    assert result.input_tokens == 1200
    assert result.output_tokens == 500
    # by_butler sorted by count desc, count>0 only
    assert [(b.butler, b.count) for b in result.by_butler] == [("atlas", 10), ("general", 5)]


async def test_aggregate_fan_out_omits_zero_count_butlers():
    """Butlers with total==0 are excluded from by_butler."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas", "idle"]
    mock_db.fan_out = AsyncMock(
        return_value={
            "atlas": [_agg_row(total=3, success_count=3)],
            "idle": [_agg_row(total=0)],
        }
    )

    result = await query_session_aggregate_fan_out(mock_db, "", ())

    assert result.total == 3
    assert [b.butler for b in result.by_butler] == ["atlas"]


async def test_aggregate_fan_out_uses_filter_sql_and_butler_names():
    """The aggregate SQL carries the FILTER clauses, WHERE, and butler_names."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas"]
    mock_db.fan_out = AsyncMock(return_value={"atlas": [_agg_row(total=1, success_count=1)]})

    await query_session_aggregate_fan_out(
        mock_db, " WHERE success = $1", (True,), butler_names=["atlas"]
    )

    call = mock_db.fan_out.call_args_list[0]
    sql = call.args[0]
    assert "FILTER (WHERE success IS NULL)" in sql
    assert "coalesce(sum(input_tokens), 0)" in sql
    assert " WHERE success = $1" in sql
    assert call.kwargs.get("butler_names") == ["atlas"]


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
