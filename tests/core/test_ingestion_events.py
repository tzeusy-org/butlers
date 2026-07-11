"""Tests for butlers.core.ingestion_events — ingestion event query module — condensed.

Covers:
- Column spec integrity (_UNION_COLUMN_SPEC architectural invariants)
- ingestion_event_get: get / unified lookup (ingested + filtered fallback)
- ingestion_events_list: cursor-paginated list with filters and has_more detection
- encode_cursor / decode_cursor: round-trip and error cases
- encode_cost_cursor / decode_cost_cursor: cost-sort cursor round-trip (core_126)
- ingestion_events_list sort=cost: offset-based pagination (core_126)
- ingestion_event_set_cost_usd: lazy write-through UPDATE (core_126)
- ingestion_event_sessions: fan-out, merge, field mapping
- ingestion_event_rollup: pure-function aggregation
- ingestion_event_replay_request: atomic update + conflict/not-found outcomes
- ingestion_event_get_inbox_lifecycle: lifecycle state lookup
- ingestion_window_rollup: cost aggregation (pricing present → summed; absent → null)
"""

from __future__ import annotations

import asyncio
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
        "cost_usd": None,  # core_126: denormalized cost; NULL until rollup fetched
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
        "cost_usd": None,  # core_126: filtered events have no sessions, always null
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

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "UPDATE 1"


class _FakeDatabaseManager:
    def __init__(self, results=None):
        self._results = results or {}
        self.fan_out_calls = []

    @property
    def butler_names(self):
        return list(self._results.keys())

    async def fan_out_with_status(self, query, args=(), butler_names=None):
        self.fan_out_calls.append((query, args, butler_names))
        if butler_names is not None:
            return {k: self._results.get(k, []) for k in butler_names}, []
        return dict(self._results), []


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
    "cost_usd",  # core_126: denormalized cost column
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
        "CASE WHEN status = 'ingested' AND triage_decision = 'skip' "
        "THEN 'skipped' ELSE status END AS status, "
        "NULL::text AS filter_reason, "
        "error_detail, "
        "cost_usd"  # core_126: denormalized cost column
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

    # List: channel filter in SQL — single channel via channels list
    pool = _FakePool(fetch_results=[])
    await ingestion_events_list(pool, channels=["telegram_bot"], limit=5)
    _, sql, args = pool.calls[0]
    assert "source_channel" in sql and ["telegram_bot"] in args

    # List: multi-channel filter — channels = ANY(...)
    pool2 = _FakePool(fetch_results=[])
    await ingestion_events_list(pool2, channels=["email", "telegram"], limit=5)
    _, sql2, args2 = pool2.calls[0]
    assert "ANY(" in sql2 and ["email", "telegram"] in args2

    # List: statuses filter — status = ANY(...); single status param ignored
    pool3 = _FakePool(fetch_results=[])
    await ingestion_events_list(pool3, status="filtered", statuses=["ingested", "error"], limit=5)
    _, sql3, args3 = pool3.calls[0]
    assert "status = ANY(" in sql3 and ["ingested", "error"] in args3
    assert "filtered" not in args3

    # List: single status filter still works when statuses is absent
    pool4 = _FakePool(fetch_results=[])
    await ingestion_events_list(pool4, status="error", limit=5)
    _, sql4, args4 = pool4.calls[0]
    assert "status = $" in sql4 and "error" in args4

    # The unified SELECT derives 'skipped' for skip-triaged ingested rows
    from butlers.core.ingestion_events import _INGESTED_COLS

    assert "THEN 'skipped'" in _INGESTED_COLS

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


async def test_ingestion_events_list_event_ids_filter() -> None:
    """event_ids pushes an `id = ANY(...)` filter into SQL (drill-down spine, bu-86c4c.3).

    An explicit empty list must still restrict to zero rows (`is not None`
    check, not truthy) — a trace_id that matched no session should yield an
    empty page, not fall through to "no filter" the way an empty
    channels/statuses list does.
    """
    from butlers.core.ingestion_events import ingestion_events_list

    ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    pool = _FakePool(fetch_results=[])
    await ingestion_events_list(pool, event_ids=ids, limit=5)
    _, sql, args = pool.calls[0]
    assert "id = ANY(" in sql and "::uuid[]" in sql
    assert [uuid.UUID(i) for i in ids] in args

    # Explicit empty list still adds the filter (not skipped like channels=[])
    pool2 = _FakePool(fetch_results=[])
    await ingestion_events_list(pool2, event_ids=[], limit=5)
    _, sql2, args2 = pool2.calls[0]
    assert "id = ANY(" in sql2
    assert [] in args2

    # event_ids=None (default) omits the filter entirely
    pool3 = _FakePool(fetch_results=[])
    await ingestion_events_list(pool3, limit=5)
    _, sql3, _args3 = pool3.calls[0]
    assert "id = ANY(" not in sql3

    # Filter also applies under sort=cost (shared where_parts before the branch)
    pool4 = _FakePool(fetch_results=[])
    await ingestion_events_list(pool4, event_ids=ids, sort="cost", limit=5)
    _, sql4, args4 = pool4.calls[0]
    assert "id = ANY(" in sql4
    assert [uuid.UUID(i) for i in ids] in args4


async def test_ingestion_events_request_ids_for_trace() -> None:
    """Resolves a trace_id to distinct request_ids across all registered butlers.

    Covers: no match -> [], single butler, cross-butler dedup, sorted output,
    and that the fan-out query targets `sessions.trace_id`.
    """
    from butlers.core.ingestion_events import ingestion_events_request_ids_for_trace

    # No session anywhere carries this trace -> empty list, not an error.
    assert await ingestion_events_request_ids_for_trace(_FakeDatabaseManager(), "trace-x") == []

    # Single butler, single session.
    db = _FakeDatabaseManager(results={"atlas": [_FakeRecord({"request_id": "req-1"})]})
    assert await ingestion_events_request_ids_for_trace(db, "trace-x") == ["req-1"]
    query, args, butler_names = db.fan_out_calls[0]
    assert "trace_id" in query and args == ("trace-x",) and butler_names is None

    # Cross-butler: same request_id fanned out to two butlers (shared trace)
    # dedupes to one entry; a distinct request_id from another butler is kept
    # and the result is sorted.
    db2 = _FakeDatabaseManager(
        results={
            "atlas": [_FakeRecord({"request_id": "req-2"})],
            "herald": [
                _FakeRecord({"request_id": "req-2"}),
                _FakeRecord({"request_id": "req-1"}),
            ],
        }
    )
    assert await ingestion_events_request_ids_for_trace(db2, "trace-y") == ["req-1", "req-2"]

    # Falsy request_id values are skipped, not coerced into a phantom entry.
    db3 = _FakeDatabaseManager(results={"atlas": [_FakeRecord({"request_id": None})]})
    assert await ingestion_events_request_ids_for_trace(db3, "trace-z") == []


async def test_ingestion_events_received_at_bounds() -> None:
    """(min, max) received_at for a set of event ids (bu-1f81d: trace-scoped
    histogram window auto-widen).

    Covers: empty event_ids short-circuits without a query, a populated
    result forwards (min_ts, max_ts) from the row, and the SQL pushes an
    `id = ANY(...)` filter over the same unified UNION ALL read.
    """
    from butlers.core.ingestion_events import ingestion_events_received_at_bounds

    # Empty event_ids -> (None, None), no query issued.
    pool_empty = _FakePool()
    assert await ingestion_events_received_at_bounds(pool_empty, []) == (None, None)
    assert pool_empty.calls == []

    # Populated event_ids -> forwards (min_ts, max_ts) from the row.
    min_ts = datetime(2026, 1, 1, tzinfo=UTC)
    max_ts = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    pool = _FakePool(fetchrow_result={"min_ts": min_ts, "max_ts": max_ts})
    result = await ingestion_events_received_at_bounds(pool, ids)
    assert result == (min_ts, max_ts)
    sql, args = pool.calls[0][1], pool.calls[0][2]
    assert "MIN(received_at)" in sql and "MAX(received_at)" in sql
    assert "id = ANY(" in sql and "::uuid[]" in sql
    assert [uuid.UUID(i) for i in ids] in args

    # No matching row -> (None, None), not an error.
    pool_none = _FakePool(fetchrow_result=None)
    assert await ingestion_events_received_at_bounds(pool_none, ids) == (None, None)


async def test_cost_cursor_and_cost_sort() -> None:
    """Cost cursor round-trips correctly; sort=cost uses offset pagination (core_126)."""
    import uuid as _uuid

    from butlers.core.ingestion_events import (
        decode_cost_cursor,
        encode_cost_cursor,
        ingestion_event_set_cost_usd,
        ingestion_events_list,
    )

    # encode_cost_cursor / decode_cost_cursor round-trip
    assert decode_cost_cursor(encode_cost_cursor(0)) == 0
    assert decode_cost_cursor(encode_cost_cursor(40)) == 40
    assert decode_cost_cursor(encode_cost_cursor(100)) == 100

    # decode_cost_cursor raises ValueError on garbage input
    with pytest.raises(ValueError):
        decode_cost_cursor("not-a-cursor")

    # decode_cost_cursor raises ValueError on a keyset cursor (wrong type)
    from datetime import UTC, datetime

    from butlers.core.ingestion_events import encode_cursor

    keyset_cursor = encode_cursor(datetime(2026, 1, 1, tzinfo=UTC), _uuid.uuid4())
    with pytest.raises(ValueError):
        decode_cost_cursor(keyset_cursor)

    # decode_cost_cursor raises ValueError on negative offset
    with pytest.raises(ValueError):
        decode_cost_cursor(encode_cost_cursor(-10))

    # decode_cost_cursor raises ValueError on non-dict payload
    import base64 as _b64
    import json as _json

    non_dict_cursor = _b64.urlsafe_b64encode(_json.dumps([1, 2, 3]).encode()).decode()
    with pytest.raises(ValueError):
        decode_cost_cursor(non_dict_cursor)

    # sort=cost: SQL uses ORDER BY cost_usd DESC NULLS LAST + OFFSET
    pool = _FakePool(fetch_results=[])
    await ingestion_events_list(pool, sort="cost", limit=5)
    _, sql, args = pool.calls[0]
    assert "cost_usd DESC NULLS LAST" in sql
    assert "OFFSET" in sql
    assert 6 in args  # limit+1 sentinel
    assert 0 in args  # initial offset

    # sort=cost, first page → has_more=False, next_cursor=None when ≤limit rows
    rows = [_make_event_record(cost_usd=0.05), _make_event_record(cost_usd=0.01)]
    result = await ingestion_events_list(_FakePool(fetch_results=rows), sort="cost", limit=5)
    assert not result["has_more"] and result["next_cursor"] is None
    assert len(result["items"]) == 2

    # sort=cost, limit+1 rows returned → has_more=True, cursor encodes offset=limit
    three = [_make_event_record(cost_usd=x) for x in [0.05, 0.02, 0.01]]
    result2 = await ingestion_events_list(_FakePool(fetch_results=three), sort="cost", limit=2)
    assert result2["has_more"] and result2["next_cursor"] is not None
    assert decode_cost_cursor(result2["next_cursor"]) == 2  # next offset = limit

    # sort=cost with cursor → offset sent to SQL
    cursor_val = encode_cost_cursor(20)
    pool2 = _FakePool(fetch_results=[])
    await ingestion_events_list(pool2, sort="cost", cursor=cursor_val, limit=10)
    _, sql2, args2 = pool2.calls[0]
    assert 20 in args2  # decoded offset

    # ingestion_event_set_cost_usd writes UPDATE to pool
    event_uuid = _uuid.uuid4()
    pool3 = _FakePool()
    await ingestion_event_set_cost_usd(pool3, event_uuid, 0.0123)
    assert len(pool3.calls) == 1
    kind, sql3, sql_args3 = pool3.calls[0]
    assert kind == "execute"
    assert "UPDATE public.ingestion_events" in sql3
    assert "cost_usd" in sql3
    assert 0.0123 in sql_args3


async def test_ingestion_events_list_q_search_coverage() -> None:
    """q search includes event id (primary fix) and other readily-available fields.

    Verifies that the WHERE clause built by ingestion_events_list contains an
    ILIKE predicate for each of the newly-covered columns, and that the single
    bound parameter is reused for all of them (one ILIKE arg, not N).
    """
    from butlers.core.ingestion_events import ingestion_events_list

    # Each field that must appear in the SQL predicate
    expected_columns = [
        "id::text",
        "source_channel",
        "source_sender_identity",
        "source_endpoint_identity",
        "external_event_id",
        "triage_target",
        "triage_decision",
        "filter_reason",
        "error_detail",
    ]

    pool = _FakePool(fetch_results=[])
    await ingestion_events_list(pool, q="abc123", limit=5)
    _, sql, args = pool.calls[0]

    # Every expected column is in the WHERE clause
    for col in expected_columns:
        assert col in sql, f"Expected column {col!r} missing from q search SQL: {sql!r}"

    # The ILIKE pattern appears exactly once in the args list (all branches reuse $N)
    assert args.count("%abc123%") == 1, f"Expected exactly one ILIKE arg for q; got: {args}"

    # Cursor pagination still composes correctly: adding a cursor must not break
    # the q predicate already in the WHERE clause.
    import base64
    import json as _json_mod
    from datetime import UTC, datetime

    cursor_payload = {"ra": datetime(2026, 5, 1, tzinfo=UTC).isoformat(), "id": str(uuid.uuid4())}
    cursor = base64.urlsafe_b64encode(_json_mod.dumps(cursor_payload).encode()).decode()

    pool2 = _FakePool(fetch_results=[])
    await ingestion_events_list(pool2, q="xyz", cursor=cursor, limit=5)
    _, sql2, args2 = pool2.calls[0]

    # Both the id::text ILIKE and the keyset clause must be present
    assert "id::text ILIKE" in sql2
    assert "(received_at, id) <" in sql2
    assert "%xyz%" in args2

    # Searching by triage_target covers "butler name" match
    pool3 = _FakePool(fetch_results=[])
    await ingestion_events_list(pool3, q="atlas", limit=5)
    _, sql3, args3 = pool3.calls[0]
    assert "triage_target ILIKE" in sql3 and "%atlas%" in args3


async def test_ingestion_events_list_skips_ilike_for_empty_or_whitespace_q() -> None:
    """Empty/whitespace-only q must not emit an ILIKE '%%' full-window scan.

    Mirrors the guard applied to ingestion_events_histogram (PR #2853): q=None,
    q="", and q="   " all skip the ILIKE predicate entirely, while a real
    query string still produces one.
    """
    from butlers.core.ingestion_events import ingestion_events_list

    for blank_q in (None, "", "   "):
        pool = _FakePool(fetch_results=[])
        await ingestion_events_list(pool, q=blank_q, limit=5)
        _, sql, args = pool.calls[0]
        assert "ILIKE" not in sql, f"q={blank_q!r} should not emit ILIKE: {sql!r}"
        assert "%%" not in "".join(a for a in args if isinstance(a, str))

    # Sanity: a non-blank q still filters as before.
    pool2 = _FakePool(fetch_results=[])
    await ingestion_events_list(pool2, q="alice", limit=5)
    _, sql2, args2 = pool2.calls[0]
    assert "ILIKE" in sql2 and "%alice%" in args2


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


# ---------------------------------------------------------------------------
# ingestion_window_rollup — cost aggregation
# ---------------------------------------------------------------------------


def _make_pricing(model_id: str = "claude-test", price_per_token: float = 1e-6):
    """Build a minimal PricingConfig with a single model entry."""
    from butlers.api.pricing import ModelPricing, PricingConfig

    return PricingConfig({model_id: ModelPricing(price_per_token, price_per_token * 2)})


class _FakePoolForRollup:
    """Pool that returns a fixed event count and a fixed list of event ID rows."""

    def __init__(self, event_count: int = 5, event_ids: list | None = None):
        self._event_count = event_count
        self._event_ids = (
            event_ids if event_ids is not None else [{"id": uuid.uuid4()}] * event_count
        )
        self.calls: list = []

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self._event_count

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self._event_ids


async def test_ingestion_window_rollup_cost_with_pricing() -> None:
    """When pricing is supplied and sessions have token data, cost is summed correctly."""
    from butlers.core.ingestion_events import ingestion_window_rollup

    pricing = _make_pricing("claude-test", price_per_token=1e-6)

    # Two model buckets across two butler schemas
    session_rows_butler1 = [
        # 1000 input tokens, 500 output tokens for claude-test
        {"cnt": 3, "model": "claude-test", "input_tokens": 1000, "output_tokens": 500},
    ]
    session_rows_butler2 = [
        {"cnt": 2, "model": "claude-test", "input_tokens": 600, "output_tokens": 300},
    ]
    db = _FakeDatabaseManager(
        results={"butler1": session_rows_butler1, "butler2": session_rows_butler2}
    )
    pool = _FakePoolForRollup(event_count=5)

    result = await ingestion_window_rollup(pool, db=db, pricing=pricing)

    # session_count: 3 + 2 = 5
    assert result["sessions"] == 5
    assert result["events"] == 5

    # cost: estimate_session_cost sums both buckets
    # butler1: 1000 * 1e-6 + 500 * 2e-6 = 0.001 + 0.001 = 0.002
    # butler2: 600 * 1e-6 + 300 * 2e-6 = 0.0006 + 0.0006 = 0.0012
    # total ≈ 0.0032
    assert result["cost"] is not None
    assert isinstance(result["cost"], float)
    assert abs(result["cost"] - 0.0032) < 1e-9


async def test_ingestion_window_rollup_cost_null_without_pricing() -> None:
    """When pricing is None, cost is always None regardless of session token data."""
    from butlers.core.ingestion_events import ingestion_window_rollup

    session_rows = [{"cnt": 3, "model": "claude-test", "input_tokens": 1000, "output_tokens": 500}]
    db = _FakeDatabaseManager(results={"butler1": session_rows})
    pool = _FakePoolForRollup(event_count=3)

    result = await ingestion_window_rollup(pool, db=db, pricing=None)

    assert result["sessions"] == 3
    assert result["cost"] is None


async def test_ingestion_window_rollup_cost_null_no_sessions() -> None:
    """When no sessions exist (empty fan-out), cost is None even with pricing."""
    from butlers.core.ingestion_events import ingestion_window_rollup

    pricing = _make_pricing()
    db = _FakeDatabaseManager(results={"butler1": []})
    pool = _FakePoolForRollup(event_count=2)

    result = await ingestion_window_rollup(pool, db=db, pricing=pricing)

    assert result["sessions"] == 0
    assert result["cost"] is None


async def test_ingestion_window_rollup_cost_skips_unknown_model() -> None:
    """Sessions with a model not in the pricing catalog contribute $0; cost still populated."""
    from butlers.core.ingestion_events import ingestion_window_rollup

    pricing = _make_pricing("known-model", price_per_token=1e-6)

    # One row with known model (has tokens), one with unknown model
    session_rows = [
        {"cnt": 1, "model": "known-model", "input_tokens": 1000, "output_tokens": 500},
        {"cnt": 1, "model": "unknown-model-xyz", "input_tokens": 2000, "output_tokens": 1000},
    ]
    db = _FakeDatabaseManager(results={"butler1": session_rows})
    pool = _FakePoolForRollup(event_count=2)

    result = await ingestion_window_rollup(pool, db=db, pricing=pricing)

    assert result["sessions"] == 2
    # Only known-model contributes cost; unknown model → 0
    # known-model: 1000 * 1e-6 + 500 * 2e-6 = 0.001 + 0.001 = 0.002
    assert result["cost"] is not None
    assert abs(result["cost"] - 0.002) < 1e-9


async def test_ingestion_window_rollup_cost_zero_all_unknown_models() -> None:
    """When pricing is present and sessions exist but all models are unknown, cost is 0.0 not None.

    None means "pricing unavailable"; 0.0 means "sessions found, nothing chargeable".
    """
    from butlers.core.ingestion_events import ingestion_window_rollup

    pricing = _make_pricing("known-model", price_per_token=1e-6)

    # All sessions have an unknown model (not in the pricing catalog)
    session_rows = [
        {"cnt": 2, "model": "unknown-model-xyz", "input_tokens": 2000, "output_tokens": 1000},
    ]
    db = _FakeDatabaseManager(results={"butler1": session_rows})
    pool = _FakePoolForRollup(event_count=2)

    result = await ingestion_window_rollup(pool, db=db, pricing=pricing)

    assert result["sessions"] == 2
    # Pricing was available and sessions existed, so cost must not be None
    assert result["cost"] == 0.0


async def test_ingestion_window_rollup_cost_null_when_db_none() -> None:
    """When db=None, no fan-out runs; sessions=0 and cost=None."""
    from butlers.core.ingestion_events import ingestion_window_rollup

    pricing = _make_pricing()
    pool = _FakePoolForRollup(event_count=10)

    result = await ingestion_window_rollup(pool, db=None, pricing=pricing)

    assert result["sessions"] == 0
    assert result["cost"] is None


async def test_ingestion_window_rollup_skips_ilike_for_empty_or_whitespace_q() -> None:
    """Empty/whitespace-only q must not emit an ILIKE '%%' full-window scan.

    Mirrors the guard applied to ingestion_events_histogram (PR #2853) and
    ingestion_events_list — the rollup's event-count query is unpaginated, so
    a blank q forcing an ILIKE '%%' scan is exactly what this guards against.
    """
    from butlers.core.ingestion_events import ingestion_window_rollup

    for blank_q in (None, "", "   "):
        pool = _FakePoolForRollup(event_count=0)
        await ingestion_window_rollup(pool, q=blank_q, db=None)
        fetchval_calls = [c for c in pool.calls if c[0] == "fetchval"]
        assert len(fetchval_calls) == 1
        sql = fetchval_calls[0][1]
        assert "ILIKE" not in sql, f"q={blank_q!r} should not emit ILIKE: {sql!r}"

    # Sanity: a non-blank q still filters as before.
    pool2 = _FakePoolForRollup(event_count=0)
    await ingestion_window_rollup(pool2, q="alice", db=None)
    fetchval_calls2 = [c for c in pool2.calls if c[0] == "fetchval"]
    sql2, args2 = fetchval_calls2[0][1], fetchval_calls2[0][2]
    assert "ILIKE" in sql2 and "%alice%" in args2


async def test_ingestion_window_rollup_event_ids_filter() -> None:
    """event_ids pushes an `id = ANY(...)` filter into SQL (bu-q750c: trace-scoped footer band).

    Mirrors ingestion_events_list's event_ids handling — an explicit empty
    list must still restrict to zero rows (`is not None` check, not truthy),
    so a trace that matched no session yields a zeroed rollup rather than
    falling through to "no filter". The filter must apply to BOTH the event
    count query and the session-fan-out id query (they share where_clause/args).
    """
    from butlers.core.ingestion_events import ingestion_window_rollup

    ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    pool = _FakePoolForRollup(event_count=2)
    await ingestion_window_rollup(pool, event_ids=ids, db=None)
    fetchval_calls = [c for c in pool.calls if c[0] == "fetchval"]
    assert len(fetchval_calls) == 1
    sql, args = fetchval_calls[0][1], fetchval_calls[0][2]
    assert "id = ANY(" in sql and "::uuid[]" in sql
    assert [uuid.UUID(i) for i in ids] in args

    # Explicit empty list still adds the filter (not skipped like channels=[])
    # and the event count query is expected to return 0 rows.
    pool2 = _FakePoolForRollup(event_count=0)
    result2 = await ingestion_window_rollup(pool2, event_ids=[], db=None)
    fetchval_calls2 = [c for c in pool2.calls if c[0] == "fetchval"]
    sql2, args2 = fetchval_calls2[0][1], fetchval_calls2[0][2]
    assert "id = ANY(" in sql2
    assert [] in args2
    assert result2["events"] == 0

    # event_ids=None (default) omits the filter entirely
    pool3 = _FakePoolForRollup(event_count=5)
    await ingestion_window_rollup(pool3, event_ids=None, db=None)
    fetchval_calls3 = [c for c in pool3.calls if c[0] == "fetchval"]
    sql3 = fetchval_calls3[0][1]
    assert "id = ANY(" not in sql3

    # The filter also reaches the session-fan-out id query (shared where_clause).
    db = _FakeDatabaseManager(results={"butler1": []})
    pool4 = _FakePoolForRollup(event_count=2)
    await ingestion_window_rollup(pool4, event_ids=ids, db=db)
    fetch_calls4 = [c for c in pool4.calls if c[0] == "fetch"]
    assert len(fetch_calls4) == 1
    id_sql, id_args = fetch_calls4[0][1], fetch_calls4[0][2]
    assert "id = ANY(" in id_sql
    assert [uuid.UUID(i) for i in ids] in id_args


# ---------------------------------------------------------------------------
# ingestion_events_sessions_for_ids / ingestion_events_list_enrichment (bu-4utdw.3)
#
# List-view row enrichment: ONE grouped fan-out for a whole page of ids (not
# one query per event), feeding tokens/cost/session-summary fields onto each
# IngestionEventSummary. Kills the N+1 request storm the per-row
# rollup/sender-contact hooks previously caused.
# ---------------------------------------------------------------------------


def _make_bulk_session_record(request_id: str, **kwargs: Any) -> _FakeRecord:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "request_id": request_id,
        "trigger_source": "route",
        "started_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        "completed_at": datetime(2026, 1, 1, 12, 0, 5, tzinfo=UTC),
        "success": True,
        "input_tokens": 100,
        "output_tokens": 50,
        "cost": {"total_usd": 0.005},
        "trace_id": None,
        "model": None,
    }
    defaults.update(kwargs)
    return _FakeRecord(defaults)


async def test_ingestion_events_sessions_for_ids_empty_ids_short_circuits() -> None:
    """Empty event_ids returns {} without any fan-out call."""
    from butlers.core.ingestion_events import ingestion_events_sessions_for_ids

    db = _FakeDatabaseManager(results={"atlas": [_make_bulk_session_record("req-1")]})
    result = await ingestion_events_sessions_for_ids(db, [])

    assert result == {}
    assert db.fan_out_calls == []


async def test_ingestion_events_sessions_for_ids_one_query_per_butler_groups_by_request_id() -> (
    None
):
    """Sessions from multiple butlers are grouped by request_id via a single fan-out call."""
    from butlers.core.ingestion_events import ingestion_events_sessions_for_ids

    db = _FakeDatabaseManager(
        results={
            "atlas": [
                _make_bulk_session_record("req-1", input_tokens=100, output_tokens=50),
                _make_bulk_session_record("req-2", input_tokens=10, output_tokens=5),
            ],
            "herald": [_make_bulk_session_record("req-1", input_tokens=20, output_tokens=10)],
        }
    )

    result = await ingestion_events_sessions_for_ids(db, ["req-1", "req-2", "req-3"])

    # Exactly one fan_out call for the whole page — not one per event.
    assert len(db.fan_out_calls) == 1
    query, args, _ = db.fan_out_calls[0]
    assert "request_id = ANY($1::text[])" in query
    assert args == (["req-1", "req-2", "req-3"],)

    assert {s["butler_name"] for s in result["req-1"]} == {"atlas", "herald"}
    assert len(result["req-2"]) == 1 and result["req-2"][0]["butler_name"] == "atlas"
    # An id with no sessions is present as an empty list, never omitted.
    assert result["req-3"] == []
    # cost_usd was computed from the legacy JSONB fallback.
    assert result["req-1"][0]["cost_usd"] == 0.005


def test_ingestion_events_list_enrichment_aggregates_and_caps_sessions() -> None:
    """tokens/cost summed; session_count uncapped; sessions list capped at 8."""
    from butlers.core.ingestion_events import ingestion_events_list_enrichment

    many_sessions = [
        {
            "butler_name": f"butler-{i}",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_usd": 0.001,
            "success": True,
            "started_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
            "completed_at": datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC),
        }
        for i in range(10)
    ]
    sessions_by_id = {
        "req-1": many_sessions,
        "req-2": [],
    }

    enrichment = ingestion_events_list_enrichment(sessions_by_id)

    req1 = enrichment["req-1"]
    assert req1["tokens_in"] == 100 and req1["tokens_out"] == 50
    assert abs(req1["session_cost_usd"] - 0.01) < 1e-9
    assert req1["session_count"] == 10  # uncapped
    assert len(req1["sessions"]) == 8  # capped at 8
    assert req1["sessions"][0]["duration_ms"] == 1000
    assert req1["sessions"][0]["butler_name"] == "butler-0"
    assert req1["sessions"][0]["success"] is True

    req2 = enrichment["req-2"]
    assert req2["tokens_in"] == 0 and req2["tokens_out"] == 0
    # No sessions → session_cost_usd is None (distinct from 0.0), so the
    # router falls back to the denormalized cost_usd column.
    assert req2["session_cost_usd"] is None
    assert req2["session_count"] == 0
    assert req2["sessions"] == []


def test_ingestion_events_list_enrichment_unknown_cost_matches_drawer_parity() -> None:
    """Unknown per-session cost sums as 0.0, matching ingestion_event_rollup's
    drawer-side total — NOT None — so a list row and the drawer agree on the
    same event's cost. (Only "no sessions at all" yields None; see the other
    aggregation test above.)
    """
    from butlers.core.ingestion_events import ingestion_events_list_enrichment

    sessions_by_id = {
        "req-1": [
            {
                "butler_name": "butler-a",
                "input_tokens": 10,
                "output_tokens": 5,
                "cost_usd": None,
                "success": True,
                "started_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
                "completed_at": datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC),
            }
        ],
        "req-2": [
            {
                "butler_name": "butler-b",
                "input_tokens": 10,
                "output_tokens": 5,
                "cost_usd": None,
                "success": True,
                "started_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
                "completed_at": datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC),
            },
            {
                "butler_name": "butler-c",
                "input_tokens": 10,
                "output_tokens": 5,
                "cost_usd": 0.02,
                "success": True,
                "started_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
                "completed_at": datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC),
            },
        ],
    }

    enrichment = ingestion_events_list_enrichment(sessions_by_id)

    # All sessions unknown-cost → 0.0 (matches the drawer rollup's fallback).
    assert enrichment["req-1"]["session_cost_usd"] == 0.0
    # Mixed known/unknown → unknown contributes 0.0 to the sum.
    assert abs(enrichment["req-2"]["session_cost_usd"] - 0.02) < 1e-9


def test_ingestion_events_list_enrichment_duration_none_when_incomplete() -> None:
    """A session missing completed_at yields duration_ms=None rather than raising."""
    from butlers.core.ingestion_events import ingestion_events_list_enrichment

    sessions_by_id = {
        "req-1": [
            {
                "butler_name": "atlas",
                "input_tokens": 1,
                "output_tokens": 1,
                "cost_usd": None,
                "success": False,
                "started_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
                "completed_at": None,
            }
        ]
    }

    enrichment = ingestion_events_list_enrichment(sessions_by_id)

    assert enrichment["req-1"]["sessions"][0]["duration_ms"] is None
    assert enrichment["req-1"]["sessions"][0]["success"] is False


# ---------------------------------------------------------------------------
# ingestion_events_histogram (bu-4utdw.6) — validation, guardrail, aggregation.
#
# NOTE: these tests exercise the Python-side validation/aggregation logic
# against a mocked pool. They do NOT prove the date_bin/UNION SQL is valid
# against a real partitioned Postgres backend — that is covered separately by
# the real-Postgres integration test (tests/integration/test_ingestion_events_histogram_db.py).
# ---------------------------------------------------------------------------


def _make_histogram_row(ts: datetime, status: str, cnt: int) -> _FakeRecord:
    return _FakeRecord({"bucket_ts": ts, "status": status, "cnt": cnt})


async def test_ingestion_events_histogram_rejects_unknown_bucket() -> None:
    from butlers.core.ingestion_events import ingestion_events_histogram

    pool = _FakePool()
    with pytest.raises(ValueError, match="Invalid bucket"):
        await ingestion_events_histogram(
            pool,
            from_dt=datetime(2026, 1, 1, tzinfo=UTC),
            to_dt=datetime(2026, 1, 1, 1, tzinfo=UTC),
            bucket="30s",
        )
    # Guardrail/validation short-circuits before any query is issued.
    assert pool.calls == []


async def test_ingestion_events_histogram_rejects_to_not_after_from() -> None:
    from datetime import timedelta

    from butlers.core.ingestion_events import ingestion_events_histogram

    pool = _FakePool()
    same = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="'to' must be after 'from'"):
        await ingestion_events_histogram(pool, from_dt=same, to_dt=same)
    with pytest.raises(ValueError, match="'to' must be after 'from'"):
        await ingestion_events_histogram(pool, from_dt=same, to_dt=same - timedelta(hours=1))
    assert pool.calls == []


async def test_ingestion_events_histogram_guardrail_rejects_1m_over_48h() -> None:
    """1m bucket over exactly 48h is allowed; one second past it is rejected."""
    from datetime import timedelta

    from butlers.core.ingestion_events import ingestion_events_histogram

    from_dt = datetime(2026, 1, 1, tzinfo=UTC)

    # Exactly 48h → 2880 buckets → allowed (query issued).
    pool_ok = _FakePool(fetch_results=[])
    await ingestion_events_histogram(
        pool_ok, from_dt=from_dt, to_dt=from_dt + timedelta(hours=48), bucket="1m"
    )
    assert len(pool_ok.calls) == 1

    # One second over 48h → exceeds the 2880-bucket cap → rejected before querying.
    pool_reject = _FakePool()
    with pytest.raises(ValueError, match="too wide for bucket '1m'"):
        await ingestion_events_histogram(
            pool_reject,
            from_dt=from_dt,
            to_dt=from_dt + timedelta(hours=48, seconds=1),
            bucket="1m",
        )
    assert pool_reject.calls == []

    # The same wide range is fine at a coarser bucket (5m).
    pool_5m = _FakePool(fetch_results=[])
    await ingestion_events_histogram(
        pool_5m,
        from_dt=from_dt,
        to_dt=from_dt + timedelta(hours=48, seconds=1),
        bucket="5m",
    )
    assert len(pool_5m.calls) == 1


async def test_ingestion_events_histogram_aggregates_and_zero_fills_present_buckets() -> None:
    """Rows are grouped by bucket_ts, zero-filled to all 8 statuses; buckets sorted ascending."""
    from butlers.core.ingestion_events import ingestion_events_histogram

    ts_a = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    ts_b = datetime(2026, 1, 1, 12, 1, 0, tzinfo=UTC)
    pool = _FakePool(
        fetch_results=[
            _make_histogram_row(ts_b, "error", 2),
            _make_histogram_row(ts_a, "ingested", 3),
            _make_histogram_row(ts_a, "filtered", 1),
        ]
    )

    result = await ingestion_events_histogram(
        pool,
        from_dt=datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC),
        to_dt=datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC),
        bucket="1m",
    )

    assert result["bucket"] == "1m"
    assert [b["ts"] for b in result["buckets"]] == [ts_a, ts_b]  # sorted ascending

    bucket_a = result["buckets"][0]["counts"]
    assert bucket_a == {
        "ingested": 3,
        "skipped": 0,
        "filtered": 1,
        "error": 0,
        "failed": 0,
        "replay_pending": 0,
        "replay_complete": 0,
        "replay_failed": 0,
    }
    bucket_b = result["buckets"][1]["counts"]
    assert bucket_b["error"] == 2
    assert bucket_b["ingested"] == 0

    # No zero-count bucket appears for minutes with no rows at all.
    assert len(result["buckets"]) == 2


async def test_ingestion_events_histogram_counts_failed_status() -> None:
    """'failed' (routing failure post-ingestion, see ingestion_event_mark_failed)
    is a first-class histogram status, not silently dropped into the zero-fill
    (bu-lkzsf.2: the hourly chart previously undercounted outages)."""
    from butlers.core.ingestion_events import ingestion_events_histogram

    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    pool = _FakePool(fetch_results=[_make_histogram_row(ts, "failed", 4)])

    result = await ingestion_events_histogram(
        pool,
        from_dt=datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC),
        to_dt=datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC),
        bucket="1m",
    )

    assert len(result["buckets"]) == 1
    counts = result["buckets"][0]["counts"]
    assert counts["failed"] == 4
    assert "failed" in counts  # present even when zero, on every bucket


async def test_ingestion_events_histogram_forwards_filters_to_sql() -> None:
    """channels/statuses/q are forwarded as WHERE-clause args, same as ingestion_events_list."""
    from datetime import timedelta

    from butlers.core.ingestion_events import ingestion_events_histogram

    pool = _FakePool(fetch_results=[])
    await ingestion_events_histogram(
        pool,
        from_dt=datetime(2026, 1, 1, tzinfo=UTC),
        to_dt=datetime(2026, 1, 1, 1, tzinfo=UTC),
        bucket="1h",
        channels=["email", "telegram"],
        statuses=["ingested", "error"],
        q="alice",
    )

    assert len(pool.calls) == 1
    sql, args = pool.calls[0][1], pool.calls[0][2]
    assert "date_bin($3" in sql
    assert "source_channel = ANY(" in sql
    assert "status = ANY(" in sql
    assert "ILIKE" in sql
    # The bucket interval is bound as a real timedelta ($3), not a text literal —
    # asyncpg cannot encode a Python str as an `interval`-typed parameter (it
    # infers the wire type from date_bin()'s own signature and expects an
    # object with .days/.months/.microseconds, i.e. a timedelta).
    assert args[2] == timedelta(hours=1)
    assert ["ingested", "error"] in args  # statuses list bound
    assert ["email", "telegram"] in args  # channels list bound
    assert "%alice%" in args  # q pattern bound


async def test_ingestion_events_histogram_event_ids_filter() -> None:
    """event_ids pushes an `id = ANY(...)` filter into SQL (bu-q750c: trace-scoped hour strip).

    Mirrors ingestion_events_list's event_ids handling — an explicit empty
    list must still restrict to zero rows (`is not None` check, not truthy),
    so a trace that matched no session yields an empty histogram rather than
    falling through to "no filter".
    """
    from butlers.core.ingestion_events import ingestion_events_histogram

    ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    pool = _FakePool(fetch_results=[])
    await ingestion_events_histogram(
        pool,
        from_dt=datetime(2026, 1, 1, tzinfo=UTC),
        to_dt=datetime(2026, 1, 1, 1, tzinfo=UTC),
        event_ids=ids,
    )
    sql, args = pool.calls[0][1], pool.calls[0][2]
    assert "id = ANY(" in sql and "::uuid[]" in sql
    assert [uuid.UUID(i) for i in ids] in args

    # Explicit empty list still adds the filter (not skipped like channels=[])
    pool2 = _FakePool(fetch_results=[])
    await ingestion_events_histogram(
        pool2,
        from_dt=datetime(2026, 1, 1, tzinfo=UTC),
        to_dt=datetime(2026, 1, 1, 1, tzinfo=UTC),
        event_ids=[],
    )
    sql2, args2 = pool2.calls[0][1], pool2.calls[0][2]
    assert "id = ANY(" in sql2
    assert [] in args2

    # event_ids=None (default) omits the filter entirely
    pool3 = _FakePool(fetch_results=[])
    await ingestion_events_histogram(
        pool3,
        from_dt=datetime(2026, 1, 1, tzinfo=UTC),
        to_dt=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )
    sql3 = pool3.calls[0][1]
    assert "id = ANY(" not in sql3


class _SlowFakePool:
    """Fake pool whose ``execute`` takes ``delay_s`` to finish.

    Used to prove that ``ingestion_event_reconcile_after_processing`` shields
    its write from cancellation of the calling task: the write must still
    reach ``completed_calls`` even when the caller is cancelled mid-flight.
    """

    def __init__(self, delay_s: float = 0.05) -> None:
        self._delay_s = delay_s
        self.started_calls: list[tuple[str, tuple]] = []
        self.completed_calls: list[tuple[str, tuple]] = []

    async def execute(self, sql, *args):
        self.started_calls.append((sql, args))
        await asyncio.sleep(self._delay_s)
        self.completed_calls.append((sql, args))
        return "UPDATE 1"


async def test_reconcile_after_processing_marks_replay_complete_on_success() -> None:
    """routing_failed=False routes to mark_replay_complete's ingested UPDATE."""
    from butlers.core.ingestion_events import ingestion_event_reconcile_after_processing

    event_id = uuid.uuid4()
    pool = _FakePool()

    result = await ingestion_event_reconcile_after_processing(pool, event_id, routing_failed=False)

    assert result is True
    assert len(pool.calls) == 1
    _, sql, args = pool.calls[0]
    assert "status = 'ingested'" in sql
    assert "status = 'replay_pending'" in sql
    assert args[0] == event_id


async def test_reconcile_after_processing_marks_failed_on_failure() -> None:
    """routing_failed=True routes to mark_failed with the given error_detail."""
    from butlers.core.ingestion_events import ingestion_event_reconcile_after_processing

    event_id = uuid.uuid4()
    pool = _FakePool()

    result = await ingestion_event_reconcile_after_processing(
        pool, event_id, routing_failed=True, error_detail="failed_targets: ['general']"
    )

    assert result is True
    assert len(pool.calls) == 1
    _, sql, args = pool.calls[0]
    assert "replay_failed" in sql
    assert args[0] == event_id
    assert args[1] == "failed_targets: ['general']"


async def test_reconcile_after_processing_swallows_write_errors() -> None:
    """A DB error during reconciliation is logged and returns False, not raised.

    Mirrors the pre-existing behavior of the two call sites (DurableBuffer
    worker and the ingest create_task fallback) that must never let a
    reconciliation failure crash message processing.
    """
    from butlers.core.ingestion_events import ingestion_event_reconcile_after_processing

    class _ExplodingPool:
        async def execute(self, sql, *args):
            raise RuntimeError("connection reset")

    result = await ingestion_event_reconcile_after_processing(
        _ExplodingPool(), uuid.uuid4(), routing_failed=False
    )
    assert result is False


async def test_reconcile_after_processing_shields_write_from_cancellation() -> None:
    """Cancelling the caller must not abort the reconciliation write.

    Regression test for bu-nqkha: DurableBuffer.stop() force-cancels any
    worker task still running once its shutdown drain grace period elapses.
    Slower, multi-target dispatches (e.g. an email digest routed to several
    butlers) are the most likely to still be in flight at that point. Before
    this fix, a bare ``except Exception`` around the reconciliation call did
    not catch ``asyncio.CancelledError``, so a cancelled worker whose message
    had *already* finished processing successfully would permanently strand
    the ingestion_events row in ``replay_pending``. asyncio.shield() must let
    the write finish in the background even though cancellation still
    propagates to the caller.
    """
    from butlers.core.ingestion_events import ingestion_event_reconcile_after_processing

    pool = _SlowFakePool(delay_s=0.05)
    event_id = uuid.uuid4()

    task = asyncio.ensure_future(
        ingestion_event_reconcile_after_processing(pool, event_id, routing_failed=False)
    )
    # Let the coroutine run far enough to enter the shielded write.
    await asyncio.sleep(0.01)
    assert pool.started_calls, "write should have started before cancellation"
    assert not pool.completed_calls, "test is racy if the write already finished"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Cancellation must propagate to the caller promptly, without waiting for
    # the shielded write...
    assert not pool.completed_calls

    # ...but the write itself must still complete shortly after, unblocked.
    await asyncio.sleep(0.1)
    assert pool.completed_calls, (
        "shielded reconciliation write should still complete after the "
        "caller was cancelled — this is the bu-nqkha fix"
    )
