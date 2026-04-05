"""Tests for butlers.core.ingestion_events — ingestion event query module — condensed.

Covers:
- Column spec integrity (_UNION_COLUMN_SPEC architectural invariants)
- ingestion_event_get: get / unified lookup (ingested + filtered fallback)
- ingestion_events_list: list with filters
- ingestion_events_count: count with status/channel filters
- ingestion_event_sessions: fan-out, merge, field mapping
- ingestion_event_rollup: pure-function aggregation
- ingestion_event_replay_request: atomic update + conflict/not-found outcomes
- ingestion_event_get_inbox_lifecycle: lifecycle state lookup
"""

from __future__ import annotations

import json as _json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fake infrastructure
# ---------------------------------------------------------------------------


class _FakeRecord(dict):
    """Dict that behaves like an asyncpg.Record."""


def _make_event_record(**kwargs: Any) -> _FakeRecord:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "received_at": datetime.now(UTC),
        "source_channel": "email",
        "source_provider": "gmail",
        "source_endpoint_identity": "inbox@example.com",
        "source_sender_identity": "alice@example.com",
        "source_thread_identity": None,
        "external_event_id": "<abc@example.com>",
        "dedupe_key": "dedup-key-1",
        "dedupe_strategy": "connector_api",
        "ingestion_tier": "full",
        "policy_tier": "default",
        "triage_decision": None,
        "triage_target": None,
        "status": "ingested",
        "filter_reason": None,
        "error_detail": None,
    }
    defaults.update(kwargs)
    return _FakeRecord(defaults)


def _make_filtered_event_record(**kwargs: Any) -> _FakeRecord:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "received_at": datetime.now(UTC),
        "source_channel": "telegram_bot",
        "source_provider": None,
        "source_endpoint_identity": "bot@example.com",
        "source_sender_identity": "user123",
        "source_thread_identity": None,
        "external_event_id": "tg-msg-42",
        "dedupe_key": None,
        "dedupe_strategy": None,
        "ingestion_tier": None,
        "policy_tier": None,
        "triage_decision": None,
        "triage_target": None,
        "status": "filtered",
        "filter_reason": "rate_limit",
        "error_detail": None,
    }
    defaults.update(kwargs)
    return _FakeRecord(defaults)


def _make_session_record(**kwargs: Any) -> _FakeRecord:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "trigger_source": "route",
        "started_at": datetime.now(UTC),
        "completed_at": None,
        "success": True,
        "input_tokens": 100,
        "output_tokens": 50,
        "cost": {"total_usd": 0.005},
        "trace_id": None,
    }
    defaults.update(kwargs)
    return _FakeRecord(defaults)


class _FakePool:
    def __init__(
        self,
        fetchrow_result: Any = None,
        fetch_results: list | None = None,
        fetchval_result: Any = None,
        fetchrow_results: list | None = None,
    ) -> None:
        if fetchrow_results is not None:
            self._fetchrow_results: list[Any] = list(fetchrow_results)
        else:
            self._fetchrow_results = [fetchrow_result]
        self._fetch_results: list[Any] = fetch_results if fetch_results is not None else []
        self._fetchval_result = fetchval_result
        self.calls: list[tuple[str, str, tuple]] = []

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.calls.append(("fetchrow", sql, args))
        if self._fetchrow_results:
            return self._fetchrow_results.pop(0)
        return None

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        self.calls.append(("fetch", sql, args))
        return self._fetch_results

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.calls.append(("fetchval", sql, args))
        return self._fetchval_result


class _FakeDatabaseManager:
    def __init__(self, results: dict[str, list[Any]] | None = None) -> None:
        self._results: dict[str, list[Any]] = results if results is not None else {}
        self.fan_out_calls: list[tuple[str, tuple, list[str] | None]] = []

    @property
    def butler_names(self) -> list[str]:
        return list(self._results.keys())

    async def fan_out(
        self,
        query: str,
        args: tuple[Any, ...] = (),
        butler_names: list[str] | None = None,
    ) -> dict[str, list[Any]]:
        self.fan_out_calls.append((query, args, butler_names))
        if butler_names is not None:
            return {k: self._results.get(k, []) for k in butler_names}
        return dict(self._results)


# ---------------------------------------------------------------------------
# Column spec integrity (architectural invariants)
# ---------------------------------------------------------------------------


class TestUnionColumnSpec:
    def test_column_spec_contract(self) -> None:
        """Column strings exact; ingested+filtered same count as spec; no duplicate aliases."""
        from butlers.core.ingestion_events import (
            _EVENT_COLUMNS,
            _FILTERED_COLS,
            _INGESTED_COLS,
            _UNION_COLUMN_SPEC,
        )

        assert _INGESTED_COLS == (
            "id, received_at, source_channel, source_provider, "
            "source_endpoint_identity, source_sender_identity, "
            "source_thread_identity, external_event_id, dedupe_key, "
            "dedupe_strategy, ingestion_tier, policy_tier, "
            "triage_decision, triage_target, "
            "status, "
            "NULL::text AS filter_reason, "
            "error_detail"
        )
        assert _FILTERED_COLS == (
            "id, received_at, source_channel, "
            "NULL::text AS source_provider, "
            "endpoint_identity AS source_endpoint_identity, "
            "sender_identity AS source_sender_identity, "
            "NULL::text AS source_thread_identity, "
            "external_message_id AS external_event_id, "
            "NULL::text AS dedupe_key, NULL::text AS dedupe_strategy, "
            "NULL::text AS ingestion_tier, NULL::text AS policy_tier, "
            "NULL::text AS triage_decision, NULL::text AS triage_target, "
            "status, filter_reason, error_detail"
        )
        assert _EVENT_COLUMNS == (
            "id, received_at, source_channel, source_provider, "
            "source_endpoint_identity, source_sender_identity, "
            "source_thread_identity, external_event_id, dedupe_key, "
            "dedupe_strategy, ingestion_tier, policy_tier, "
            "triage_decision, triage_target, "
            "status, error_detail"
        )

        n = len(_UNION_COLUMN_SPEC)
        assert len(_INGESTED_COLS.split(",")) == n
        assert len(_FILTERED_COLS.split(",")) == n

        aliases = [alias for alias, _, _ in _UNION_COLUMN_SPEC]
        assert len(aliases) == len(set(aliases))


# ---------------------------------------------------------------------------
# ingestion_event_get — point lookup + unified fallback
# ---------------------------------------------------------------------------


_EXPECTED_EVENT_FIELDS = (
    "id",
    "received_at",
    "source_channel",
    "source_provider",
    "source_endpoint_identity",
    "source_sender_identity",
    "source_thread_identity",
    "external_event_id",
    "dedupe_key",
    "dedupe_strategy",
    "ingestion_tier",
    "policy_tier",
    "triage_decision",
    "triage_target",
    "status",
    "filter_reason",
    "error_detail",
)


async def test_ingestion_event_get_ingested():
    """get returns row with all fields; id is str; status=ingested; accepts UUID or str."""
    from butlers.core.ingestion_events import ingestion_event_get

    row = _make_event_record(source_channel="email")
    pool = _FakePool(fetchrow_result=row)
    event_id = uuid.uuid4()
    result = await ingestion_event_get(pool, event_id)
    assert result is not None
    assert result["source_channel"] == "email"
    assert result["status"] == "ingested"
    assert result["filter_reason"] is None
    assert isinstance(result["id"], str)
    for field in _EXPECTED_EVENT_FIELDS:
        assert field in result

    # Accepts string UUID
    pool2 = _FakePool(fetchrow_result=_make_event_record())
    result2 = await ingestion_event_get(pool2, str(event_id))
    assert result2 is not None


async def test_ingestion_event_get_unified_lookup():
    """Falls back to filtered_events when not in ingestion_events; returns None when both miss."""
    from butlers.core.ingestion_events import ingestion_event_get

    # Both tables miss → None
    pool_miss = _FakePool(fetchrow_results=[None, None])
    assert await ingestion_event_get(pool_miss, uuid.uuid4()) is None
    fetchrow_calls = [c for c in pool_miss.calls if c[0] == "fetchrow"]
    assert len(fetchrow_calls) == 2

    # Filtered event found → status=filtered, all fields present
    filtered_row = _make_filtered_event_record(
        status="filtered", filter_reason="rate_limit", error_detail=None
    )
    pool_filtered = _FakePool(fetchrow_results=[None, filtered_row])
    result = await ingestion_event_get(pool_filtered, uuid.uuid4())
    assert result is not None
    assert result["status"] == "filtered"
    assert result["filter_reason"] == "rate_limit"
    assert isinstance(result["id"], str)
    for field in _EXPECTED_EVENT_FIELDS:
        assert field in result

    # replay_pending status supported
    rp_row = _make_filtered_event_record(status="replay_pending")
    pool_rp = _FakePool(fetchrow_results=[None, rp_row])
    result_rp = await ingestion_event_get(pool_rp, uuid.uuid4())
    assert result_rp is not None and result_rp["status"] == "replay_pending"

    # No second query when found in ingestion_events
    pool_hit = _FakePool(fetchrow_result=_make_event_record())
    await ingestion_event_get(pool_hit, uuid.uuid4())
    assert len([c for c in pool_hit.calls if c[0] == "fetchrow"]) == 1


# ---------------------------------------------------------------------------
# ingestion_events_list
# ---------------------------------------------------------------------------


async def test_ingestion_events_list():
    """Returns list of dicts; empty when no rows; channel filter applied; id is str."""
    from butlers.core.ingestion_events import ingestion_events_list

    # Empty
    assert await ingestion_events_list(_FakePool(fetch_results=[])) == []

    # Returns rows with correct channels
    rows = [_make_event_record(source_channel="email"), _make_event_record(source_channel="tg")]
    result = await ingestion_events_list(_FakePool(fetch_results=rows))
    assert len(result) == 2
    assert {r["source_channel"] for r in result} == {"email", "tg"}
    assert isinstance(result[0]["id"], str)

    # Channel filter uses source_channel in SQL
    pool = _FakePool(fetch_results=[])
    await ingestion_events_list(pool, source_channel="telegram_bot", limit=5, offset=3)
    _, sql, args = pool.calls[0]
    assert "source_channel" in sql
    assert "telegram_bot" in args


# ---------------------------------------------------------------------------
# ingestion_events_count
# ---------------------------------------------------------------------------


async def test_ingestion_events_count():
    """Returns 0 for None/empty; integer for rows; filters applied correctly."""
    from butlers.core.ingestion_events import ingestion_events_count

    assert await ingestion_events_count(_FakePool(fetchval_result=None)) == 0
    assert await ingestion_events_count(_FakePool(fetchval_result=42)) == 42

    # No status filter: both tables queried
    pool = _FakePool(fetchval_result=0)
    await ingestion_events_count(pool)
    _, sql, _ = pool.calls[0]
    assert "public.ingestion_events" in sql and "connectors.filtered_events" in sql

    # Status filter applied
    pool2 = _FakePool(fetchval_result=5)
    await ingestion_events_count(pool2, status="ingested", source_channel="email")
    _, sql2, args2 = pool2.calls[0]
    assert "ingested" in args2 and "email" in args2


# ---------------------------------------------------------------------------
# ingestion_event_sessions
# ---------------------------------------------------------------------------


async def test_ingestion_event_sessions():
    """Fan-out merges rows from multiple butlers; cost JSONB decoded; fields present."""
    from butlers.core.ingestion_events import ingestion_event_sessions

    # Empty
    assert await ingestion_event_sessions(_FakeDatabaseManager(), "req-001") == []

    # Single butler
    row = _make_session_record()
    db = _FakeDatabaseManager(results={"atlas": [row]})
    result = await ingestion_event_sessions(db, "req-001")
    assert len(result) == 1 and result[0]["butler_name"] == "atlas"

    # Multiple butlers merged
    db2 = _FakeDatabaseManager(
        results={"atlas": [_make_session_record()], "herald": [_make_session_record()]}
    )
    result2 = await ingestion_event_sessions(db2, "req-001")
    assert len(result2) == 2
    assert {r["butler_name"] for r in result2} == {"atlas", "herald"}

    # All expected fields present
    for field in (
        "id",
        "trigger_source",
        "started_at",
        "completed_at",
        "success",
        "input_tokens",
        "output_tokens",
        "cost",
        "trace_id",
        "butler_name",
    ):
        assert field in result[0]

    # Cost JSONB string decoded to dict
    json_cost_row = _make_session_record(cost=_json.dumps({"total_usd": 0.01}))
    db3 = _FakeDatabaseManager(results={"atlas": [json_cost_row]})
    result3 = await ingestion_event_sessions(db3, "req-001")
    assert isinstance(result3[0]["cost"], dict) and result3[0]["cost"]["total_usd"] == 0.01


# ---------------------------------------------------------------------------
# ingestion_event_rollup (pure function)
# ---------------------------------------------------------------------------


def test_ingestion_event_rollup():
    """Empty rollup returns zero totals; sessions aggregate tokens/costs/by_butler."""
    from butlers.core.ingestion_events import ingestion_event_rollup

    empty = ingestion_event_rollup("req-001", [])
    assert empty["request_id"] == "req-001"
    assert empty["total_sessions"] == 0
    assert empty["total_input_tokens"] == 0
    assert empty["by_butler"] == {}
    for key in ("total_sessions", "total_input_tokens", "total_output_tokens", "total_cost",
                "by_butler"):
        assert key in empty

    # aggregation
    sessions = [
        {
            "butler_name": "atlas",
            "input_tokens": 100,
            "output_tokens": 50,
            "cost": {"total_usd": 0.005},
        },
        {
            "butler_name": "atlas",
            "input_tokens": 200,
            "output_tokens": 75,
            "cost": {"total_usd": "0.010"},
        },
        {"butler_name": "herald", "input_tokens": None, "output_tokens": None, "cost": None},
        {"butler_name": "herald"},  # missing token fields
    ]
    result = ingestion_event_rollup("req-001", sessions)
    assert result["total_sessions"] == 4
    assert result["total_input_tokens"] == 300
    assert result["total_output_tokens"] == 125
    assert abs(result["total_cost"] - 0.015) < 1e-9
    assert isinstance(result["total_cost"], float)

    assert result["by_butler"]["atlas"]["sessions"] == 2
    assert result["by_butler"]["atlas"]["input_tokens"] == 300
    assert abs(result["by_butler"]["atlas"]["cost"] - 0.015) < 1e-9
    assert result["by_butler"]["herald"]["cost"] == 0.0
    assert result["by_butler"]["herald"]["input_tokens"] == 0


# ---------------------------------------------------------------------------
# ingestion_event_replay_request
# ---------------------------------------------------------------------------


async def test_ingestion_event_replay_request_outcomes():
    """ok/not_found/conflict outcomes; string UUID accepted; invalid UUID raises."""
    from butlers.core.ingestion_events import ingestion_event_replay_request

    # Success path: UPDATE RETURNING hits ingestion_events
    event_id = uuid.uuid4()
    ok_row = _FakeRecord({"id": event_id})
    pool_ok = _FakePool(fetchrow_result=ok_row)
    result = await ingestion_event_replay_request(pool_ok, event_id)
    assert result["outcome"] == "ok"
    assert result["id"] == str(event_id)

    # String UUID accepted
    pool_str = _FakePool(fetchrow_result=ok_row)
    result_str = await ingestion_event_replay_request(pool_str, str(event_id))
    assert result_str["outcome"] == "ok"

    # Not found: UPDATE misses, SELECT also None
    pool_nf = _FakePool(fetchrow_result=None, fetchval_result=None)
    result_nf = await ingestion_event_replay_request(pool_nf, uuid.uuid4())
    assert result_nf["outcome"] == "not_found"

    # Conflict: UPDATE misses, SELECT returns non-replayable status
    pool_cf = _FakePool(fetchrow_result=None, fetchval_result="replay_pending")
    result_cf = await ingestion_event_replay_request(pool_cf, uuid.uuid4())
    assert result_cf["outcome"] == "conflict"
    assert result_cf["current_status"] == "replay_pending"

    # Invalid UUID raises
    with pytest.raises(ValueError):
        await ingestion_event_replay_request(_FakePool(), "not-a-uuid")

    # Fallback to filtered_events when ingestion_events misses
    returning_row = _FakeRecord({"id": event_id})
    pool_fe = _FakePool(fetchrow_results=[None, None, returning_row], fetchval_result=None)
    result_fe = await ingestion_event_replay_request(pool_fe, event_id)
    assert result_fe["outcome"] == "ok"


# ---------------------------------------------------------------------------
# ingestion_event_get_inbox_lifecycle
# ---------------------------------------------------------------------------


async def test_ingestion_event_get_inbox_lifecycle():
    """Returns lifecycle_state/decomposition_output; None when no row; JSON string decoded."""
    from butlers.core.ingestion_events import ingestion_event_get_inbox_lifecycle

    # Returns lifecycle_state and decomposition_output
    decomp = {"signals": [], "reason": "no_signals"}
    row = _FakeRecord({"lifecycle_state": "decomposed_empty", "decomposition_output": decomp})
    result = await ingestion_event_get_inbox_lifecycle(_FakePool(fetchrow_result=row), uuid.uuid4())
    assert result is not None
    assert result["lifecycle_state"] == "decomposed_empty"
    assert result["decomposition_output"] == decomp

    # None when no row
    result_none = await ingestion_event_get_inbox_lifecycle(
        _FakePool(fetchrow_result=None), uuid.uuid4()
    )
    assert result_none is None

    # Null decomposition_output returned as None
    row_null = _FakeRecord({"lifecycle_state": "accepted", "decomposition_output": None})
    result_null = await ingestion_event_get_inbox_lifecycle(
        _FakePool(fetchrow_result=row_null), uuid.uuid4()
    )
    assert result_null is not None and result_null["decomposition_output"] is None

    # JSON string decomposition_output decoded to dict
    decomp2 = {"signals": [{"butler": "atlas"}]}
    row_json = _FakeRecord(
        {
            "lifecycle_state": "routed",
            "decomposition_output": _json.dumps(decomp2),
        }
    )
    result_json = await ingestion_event_get_inbox_lifecycle(
        _FakePool(fetchrow_result=row_json), uuid.uuid4()
    )
    assert result_json is not None and result_json["decomposition_output"] == decomp2

    # String UUID accepted
    row2 = _FakeRecord({"lifecycle_state": "routed", "decomposition_output": None})
    event_id = uuid.uuid4()
    result2 = await ingestion_event_get_inbox_lifecycle(
        _FakePool(fetchrow_result=row2), str(event_id)
    )
    assert result2 is not None
