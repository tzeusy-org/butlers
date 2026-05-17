"""Tests for butlers.core.ingestion_events — ingestion event query module — condensed.

Covers:
- Column spec integrity (_UNION_COLUMN_SPEC architectural invariants)
- ingestion_event_get: get / unified lookup (ingested + filtered fallback)
- ingestion_events_list: cursor-paginated list with filters and has_more detection
- encode_cursor / decode_cursor: round-trip and error cases
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


class _FakeRecord(dict):
    pass


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
        self, fetchrow_result=None, fetch_results=None, fetchval_result=None, fetchrow_results=None
    ):
        self._fetchrow_results = (
            list(fetchrow_results) if fetchrow_results is not None else [fetchrow_result]
        )
        self._fetch_results = fetch_results or []
        self._fetchval_result = fetchval_result
        self.calls: list = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self._fetchrow_results.pop(0) if self._fetchrow_results else None

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self._fetch_results

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self._fetchval_result


class _FakeDatabaseManager:
    def __init__(self, results=None):
        self._results = results or {}
        self.fan_out_calls = []

    @property
    def butler_names(self):
        return list(self._results.keys())

    async def fan_out(self, query, args=(), butler_names=None):
        self.fan_out_calls.append((query, args, butler_names))
        if butler_names is not None:
            return {k: self._results.get(k, []) for k in butler_names}
        return dict(self._results)


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


def test_column_spec_contract() -> None:
    """Column strings exact; ingested+filtered same count as spec; no duplicate aliases."""
    from butlers.core.ingestion_events import (
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
    n = len(_UNION_COLUMN_SPEC)
    assert len(_INGESTED_COLS.split(",")) == n
    assert len(_FILTERED_COLS.split(",")) == n
    aliases = [alias for alias, _, _ in _UNION_COLUMN_SPEC]
    assert len(aliases) == len(set(aliases))


async def test_ingestion_event_get() -> None:
    """get returns row with all fields; id is str; falls back to filtered_events;
    None when both miss."""
    from butlers.core.ingestion_events import ingestion_event_get

    # Found in ingestion_events
    event_id = uuid.uuid4()
    result = await ingestion_event_get(
        _FakePool(fetchrow_result=_make_event_record(source_channel="email")), event_id
    )
    assert (
        result is not None and result["source_channel"] == "email" and isinstance(result["id"], str)
    )
    for field in _EXPECTED_EVENT_FIELDS:
        assert field in result

    # Accepts string UUID
    assert (
        await ingestion_event_get(_FakePool(fetchrow_result=_make_event_record()), str(event_id))
        is not None
    )

    # Both tables miss → None; two fetchrow calls made
    pool_miss = _FakePool(fetchrow_results=[None, None])
    assert await ingestion_event_get(pool_miss, uuid.uuid4()) is None
    assert len([c for c in pool_miss.calls if c[0] == "fetchrow"]) == 2

    # Filtered event found → status=filtered, all fields present
    result2 = await ingestion_event_get(
        _FakePool(
            fetchrow_results=[
                None,
                _make_filtered_event_record(status="filtered", filter_reason="rate_limit"),
            ]
        ),
        uuid.uuid4(),
    )
    assert (
        result2 is not None
        and result2["status"] == "filtered"
        and result2["filter_reason"] == "rate_limit"
    )
    for field in _EXPECTED_EVENT_FIELDS:
        assert field in result2

    # No second query when found in ingestion_events
    pool_hit = _FakePool(fetchrow_result=_make_event_record())
    await ingestion_event_get(pool_hit, uuid.uuid4())
    assert len([c for c in pool_hit.calls if c[0] == "fetchrow"]) == 1


async def test_ingestion_events_list_and_sessions() -> None:
    """list returns cursor-paginated result; has_more / next_cursor set correctly;
    channel filter passed to SQL; sessions fan-out/merge/cost-decode/rollup."""
    from butlers.core.ingestion_events import (
        decode_cursor,
        encode_cursor,
        ingestion_event_rollup,
        ingestion_event_sessions,
        ingestion_events_list,
    )

    # List: empty → items=[], has_more=False, next_cursor=None
    result = await ingestion_events_list(_FakePool(fetch_results=[]))
    assert result["items"] == [] and not result["has_more"] and result["next_cursor"] is None

    # List: limit=2, 2 rows returned → has_more=False (fetched limit+1=3, got 2)
    rows = [_make_event_record(source_channel="email"), _make_event_record(source_channel="tg")]
    result2 = await ingestion_events_list(_FakePool(fetch_results=rows), limit=2)
    assert len(result2["items"]) == 2
    assert not result2["has_more"] and result2["next_cursor"] is None
    assert isinstance(result2["items"][0]["id"], str)

    # List: limit=2, 3 rows returned (limit+1) → has_more=True, next_cursor set
    extra_row = _make_event_record(source_channel="extra")
    three_rows = rows + [extra_row]
    result3 = await ingestion_events_list(_FakePool(fetch_results=three_rows), limit=2)
    assert len(result3["items"]) == 2  # only limit rows exposed
    assert result3["has_more"] and result3["next_cursor"] is not None

    # The next_cursor must round-trip through decode_cursor
    decoded_ra, decoded_id = decode_cursor(result3["next_cursor"])
    assert decoded_id == result3["items"][-1]["id"]

    # encode_cursor / decode_cursor round-trip
    from datetime import UTC, datetime

    ra = datetime(2026, 5, 17, 14, 30, 0, tzinfo=UTC)
    import uuid

    uid = uuid.uuid4()
    token = encode_cursor(ra, uid)
    d_ra, d_id = decode_cursor(token)
    assert d_id == str(uid)
    assert d_ra.isoformat() == ra.isoformat()

    # decode_cursor raises ValueError on garbage input
    with pytest.raises(ValueError):
        decode_cursor("not-a-valid-cursor")

    # List: channel filter in SQL
    pool = _FakePool(fetch_results=[])
    await ingestion_events_list(pool, source_channel="telegram_bot", limit=5)
    _, sql, args = pool.calls[0]
    assert "source_channel" in sql and "telegram_bot" in args

    # Sessions: empty; single butler; multiple butlers merged; cost JSONB decoded
    assert await ingestion_event_sessions(_FakeDatabaseManager(), "req-001") == []
    db = _FakeDatabaseManager(results={"atlas": [_make_session_record()]})
    r = await ingestion_event_sessions(db, "req-001")
    assert len(r) == 1 and r[0]["butler_name"] == "atlas"
    db2 = _FakeDatabaseManager(
        results={"atlas": [_make_session_record()], "herald": [_make_session_record()]}
    )
    r2 = await ingestion_event_sessions(db2, "req-001")
    assert len(r2) == 2 and {x["butler_name"] for x in r2} == {"atlas", "herald"}
    json_cost_row = _make_session_record(cost=_json.dumps({"total_usd": 0.01}))
    r3 = await ingestion_event_sessions(
        _FakeDatabaseManager(results={"atlas": [json_cost_row]}), "req-001"
    )
    assert isinstance(r3[0]["cost"], dict) and r3[0]["cost"]["total_usd"] == 0.01

    # Rollup: empty returns zero totals; sessions aggregate tokens/costs/by_butler
    empty = ingestion_event_rollup("req-001", [])
    assert empty["total_sessions"] == 0 and empty["by_butler"] == {}

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
        {"butler_name": "herald"},
    ]
    rollup_result = ingestion_event_rollup("req-001", sessions)
    assert rollup_result["total_sessions"] == 4 and rollup_result["total_input_tokens"] == 300
    assert abs(rollup_result["total_cost"] - 0.015) < 1e-9
    assert (
        rollup_result["by_butler"]["atlas"]["sessions"] == 2
        and abs(rollup_result["by_butler"]["atlas"]["cost"] - 0.015) < 1e-9
    )
    assert rollup_result["by_butler"]["herald"]["cost"] == 0.0


async def test_replay_request_and_inbox_lifecycle() -> None:
    """replay_request ok/not_found/conflict outcomes; inbox_lifecycle state lookup."""
    from butlers.core.ingestion_events import (
        ingestion_event_get_inbox_lifecycle,
        ingestion_event_replay_request,
    )

    event_id = uuid.uuid4()
    ok_row = _FakeRecord({"id": event_id})

    # Success
    result = await ingestion_event_replay_request(_FakePool(fetchrow_result=ok_row), event_id)
    assert result["outcome"] == "ok" and result["id"] == str(event_id)

    # String UUID accepted
    assert (await ingestion_event_replay_request(_FakePool(fetchrow_result=ok_row), str(event_id)))[
        "outcome"
    ] == "ok"

    # Not found
    assert (
        await ingestion_event_replay_request(
            _FakePool(fetchrow_result=None, fetchval_result=None), uuid.uuid4()
        )
    )["outcome"] == "not_found"

    # Conflict
    result_cf = await ingestion_event_replay_request(
        _FakePool(fetchrow_result=None, fetchval_result="replay_pending"), uuid.uuid4()
    )
    assert result_cf["outcome"] == "conflict" and result_cf["current_status"] == "replay_pending"

    # Invalid UUID raises
    with pytest.raises(ValueError):
        await ingestion_event_replay_request(_FakePool(), "not-a-uuid")

    # inbox_lifecycle: returns state; None when no row; JSON decoded; null decomposition
    decomp = {"signals": [], "reason": "no_signals"}
    result2 = await ingestion_event_get_inbox_lifecycle(
        _FakePool(
            fetchrow_result=_FakeRecord(
                {"lifecycle_state": "decomposed_empty", "decomposition_output": decomp}
            )
        ),
        uuid.uuid4(),
    )
    assert (
        result2["lifecycle_state"] == "decomposed_empty"
        and result2["decomposition_output"] == decomp
    )

    assert (
        await ingestion_event_get_inbox_lifecycle(_FakePool(fetchrow_result=None), uuid.uuid4())
        is None
    )

    result3 = await ingestion_event_get_inbox_lifecycle(
        _FakePool(
            fetchrow_result=_FakeRecord(
                {"lifecycle_state": "routed", "decomposition_output": _json.dumps({"signals": []})}
            )
        ),
        uuid.uuid4(),
    )
    assert result3["decomposition_output"] == {"signals": []}


async def test_ingestion_event_replay_history() -> None:
    """replay_history returns chronological list from audit_log; handles empty/malformed rows;
    safe when DB query fails; accepts both UUID and str event_id."""
    from datetime import UTC, datetime

    from butlers.core.ingestion_events import ingestion_event_replay_history

    event_id = uuid.uuid4()
    ts1 = datetime(2026, 5, 17, 10, 0, 0, tzinfo=UTC)
    ts2 = datetime(2026, 5, 17, 10, 5, 0, tzinfo=UTC)

    # Row with well-formed JSON note
    row1 = _FakeRecord(
        {"ts": ts1, "actor": "dashboard", "note": _json.dumps({"result": "pending", "cost": 0.01})}
    )
    # Row with no note
    row2 = _FakeRecord({"ts": ts2, "actor": "scheduler", "note": None})

    # Returns list of entries with extracted fields
    result = await ingestion_event_replay_history(_FakePool(fetch_results=[row1, row2]), event_id)
    assert len(result) == 2
    assert result[0]["actor"] == "dashboard"
    assert result[0]["result"] == "pending"
    assert abs(result[0]["cost"] - 0.01) < 1e-9
    assert result[1]["actor"] == "scheduler"
    assert result[1]["result"] is None
    assert result[1]["cost"] is None

    # String UUID accepted
    result2 = await ingestion_event_replay_history(_FakePool(fetch_results=[row1]), str(event_id))
    assert len(result2) == 1

    # Invalid string UUID returns empty list (no raise)
    result3 = await ingestion_event_replay_history(_FakePool(), "not-a-uuid")
    assert result3 == []

    # Empty DB result → empty list
    result4 = await ingestion_event_replay_history(_FakePool(fetch_results=[]), event_id)
    assert result4 == []

    # DB error → empty list (fail-open, no exception propagated)
    class _ErrorPool(_FakePool):
        async def fetch(self, sql, *args):  # type: ignore[override]
            raise RuntimeError("DB unavailable")

    result5 = await ingestion_event_replay_history(_ErrorPool(), event_id)
    assert result5 == []

    # Row with malformed (non-JSON) note — graceful: fields default to None
    row_bad = _FakeRecord({"ts": ts1, "actor": "agent", "note": "not-json-{{"})
    result6 = await ingestion_event_replay_history(_FakePool(fetch_results=[row_bad]), event_id)
    assert len(result6) == 1
    assert result6[0]["result"] is None
    assert result6[0]["cost"] is None
