"""Tests for butlers.core.ingestion_events — ingestion event query module — condensed.

Covers:
- Column spec integrity (_UNION_COLUMN_SPEC architectural invariants)
- ingestion_event_get: get / unified lookup (ingested + filtered fallback)
- ingestion_events_list: cursor-paginated list with filters and next_cursor detection
- ingestion_events_list sort=cost: offset-based pagination (core_126)
- ingestion_event_set_cost_usd: lazy write-through UPDATE (core_126)
- ingestion_event_sessions: fan-out, merge, field mapping
- ingestion_event_rollup: pure-function aggregation
- ingestion_event_replay_request: public refusal + atomic filtered replay + conflicts
- ingestion_event_get_inbox_lifecycle: lifecycle state lookup
- ingestion_window_rollup: cost aggregation (pricing present → summed; absent → null)

SQL that filters the unified stream (list filters and search, cost sort, bounds,
window counts) runs against a real migrated database. The histogram's SQL and the
replay-policy SQL have their own real-Postgres suites
(tests/integration/test_ingestion_events_histogram_db.py,
tests/integration/test_ingestion_replay_policy_db.py,
tests/integration/test_ingestion_replay_reconcile_db.py). The rest drives the
Python logic through strict ``fake_pool`` seams: rows are queued in call order and
an unconfigured method raises.
"""

from __future__ import annotations

import asyncio
import json as _json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.testing.fake_pool import fake_pool
from butlers.testing.records import Record

pytestmark = pytest.mark.unit

docker_available = shutil.which("docker") is not None


def _make_event_record(**kwargs: Any) -> Record:
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
    return Record(defaults)


def _make_filtered_event_record(**kwargs: Any) -> Record:
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
    return Record(defaults)


def _make_session_record(**kwargs: Any) -> Record:
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
    return Record(defaults)


def _queued(*rows: Any) -> AsyncMock:
    """Results returned one per call, in order; an extra call fails the test."""
    return AsyncMock(side_effect=list(rows))


def _sql(mock: AsyncMock, call: int = 0) -> str:
    return mock.await_args_list[call].args[0]


def _args(mock: AsyncMock, call: int = 0) -> tuple:
    return mock.await_args_list[call].args[1:]


# ---------------------------------------------------------------------------
# Real-database seeds (public.ingestion_events + partitioned filtered_events)
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)


async def _seed_event(pool: asyncpg.Pool, **columns: Any) -> str:
    event_id = uuid.uuid4()
    values: dict[str, Any] = {
        "received_at": _T0,
        "source_channel": "email",
        "source_provider": "gmail",
        "source_endpoint_identity": "inbox@example.com",
        "source_sender_identity": "sender@example.com",
        "external_event_id": f"ext-{event_id}",
        "dedupe_key": f"dedupe-{event_id}",
        "dedupe_strategy": "connector_api",
        "ingestion_tier": "full",
        "policy_tier": "default",
        "triage_decision": None,
        "triage_target": None,
        "status": "ingested",
        "error_detail": None,
    } | columns
    names = ", ".join(["id", *values])
    params = ", ".join(f"${i}" for i in range(1, len(values) + 2))
    await pool.execute(
        f"INSERT INTO public.ingestion_events ({names}) VALUES ({params})",
        event_id,
        *values.values(),
    )
    return str(event_id)


async def _seed_filtered(pool: asyncpg.Pool, **columns: Any) -> str:
    event_id = uuid.uuid4()
    values: dict[str, Any] = {
        "received_at": _T0,
        "connector_type": "telegram_bot",
        "endpoint_identity": "bot-1",
        "external_message_id": f"ext-{event_id}",
        "source_channel": "telegram_bot",
        "sender_identity": "user123",
        "filter_reason": "noise",
        "status": "filtered",
        "full_payload": {},
    } | columns
    await pool.fetchval(
        "SELECT connectors.connectors_filtered_events_ensure_partition($1)", values["received_at"]
    )
    names = ", ".join(["id", *values])
    params = ", ".join(f"${i}" for i in range(1, len(values) + 2))
    await pool.execute(
        f"INSERT INTO connectors.filtered_events ({names}) VALUES ({params})",
        event_id,
        *values.values(),
    )
    return str(event_id)


def _minutes(n: int) -> datetime:
    return _T0 + timedelta(minutes=n)


_REAL_DB = (
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
)


def _real_db(test):
    for mark in reversed(_REAL_DB):
        test = mark(test)
    return test


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
        fake_pool(fetchrow=_make_event_record(source_channel="email")), event_id
    )
    assert (
        result is not None and result["source_channel"] == "email" and isinstance(result["id"], str)
    )
    for field in _EXPECTED_EVENT_FIELDS:
        assert field in result

    # Accepts string UUID
    assert (
        await ingestion_event_get(fake_pool(fetchrow=_make_event_record()), str(event_id))
        is not None
    )

    # Both tables miss → None; two fetchrow calls made
    pool_miss = fake_pool(fetchrow=_queued(None, None))
    assert await ingestion_event_get(pool_miss, uuid.uuid4()) is None
    assert pool_miss.fetchrow.await_count == 2
    assert "FROM public.ingestion_events" in _sql(pool_miss.fetchrow, 0)
    assert "FROM connectors.filtered_events" in _sql(pool_miss.fetchrow, 1)

    # Filtered event found → status=filtered, all fields present
    result2 = await ingestion_event_get(
        fake_pool(
            fetchrow=_queued(
                None, _make_filtered_event_record(status="filtered", filter_reason="rate_limit")
            )
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
    pool_hit = fake_pool(fetchrow=_queued(_make_event_record()))
    await ingestion_event_get(pool_hit, uuid.uuid4())
    assert pool_hit.fetchrow.await_count == 1


async def test_ingestion_events_replay_policy_fails_closed() -> None:
    """Only one explicit live registry policy can permit a replay."""
    from butlers.core.ingestion_events import ingestion_events_replay_policy

    email_id = str(uuid.uuid4())
    safe_id = str(uuid.uuid4())
    unresolved_id = str(uuid.uuid4())
    blank_endpoint_id = str(uuid.uuid4())
    ambiguous_id = str(uuid.uuid4())
    pool = fake_pool(
        fetch=[
            Record(
                {
                    "id": email_id,
                    "source_channel": "email",
                    "endpoint_identity": "inbox@example.com",
                    "connector_match_count": 1,
                    "replay_safe": True,
                }
            ),
            Record(
                {
                    "id": safe_id,
                    # Accepted Telegram events use the provider ``telegram``
                    # but the registry/heartbeat connector type ``telegram_bot``.
                    "source_channel": "telegram_bot",
                    "endpoint_identity": "bot-1",
                    "connector_match_count": 1,
                    "replay_safe": True,
                }
            ),
            Record(
                {
                    "id": unresolved_id,
                    "source_channel": "webhook",
                    "endpoint_identity": None,
                    "connector_match_count": 0,
                    "replay_safe": None,
                }
            ),
            Record(
                {
                    "id": blank_endpoint_id,
                    "source_channel": "telegram_bot",
                    "endpoint_identity": "   ",
                    "connector_match_count": 1,
                    "replay_safe": True,
                }
            ),
            Record(
                {
                    "id": ambiguous_id,
                    "source_channel": "sms",
                    "endpoint_identity": "owner",
                    "connector_match_count": 2,
                    "replay_safe": True,
                }
            ),
        ]
    )

    policies = await ingestion_events_replay_policy(
        pool, [email_id, safe_id, unresolved_id, blank_endpoint_id, ambiguous_id]
    )

    assert policies[email_id].replay_safe is False
    assert policies[email_id].block_reason == "Email events cannot be replayed safely"
    assert policies[safe_id].replay_safe is True
    assert policies[unresolved_id].replay_safe is False
    assert (
        policies[unresolved_id].block_reason == "Replay safety is not configured for this connector"
    )
    assert policies[blank_endpoint_id].replay_safe is False
    assert (
        policies[blank_endpoint_id].block_reason
        == "Replay safety is not configured for this connector"
    )
    assert policies[ambiguous_id].replay_safe is False
    assert policies[ambiguous_id].block_reason == "Replay safety is ambiguous for this connector"
    # One batched lookup; its candidate matching against the registry runs on
    # Postgres in tests/integration/test_ingestion_replay_policy_db.py.
    pool.fetch.assert_awaited_once()
    assert [uuid.UUID(i) for i in (email_id, safe_id, unresolved_id)] == _args(pool.fetch)[0][:3]


async def test_ingestion_events_list_and_sessions() -> None:
    """list returns cursor-paginated result; next_cursor set correctly;
    sessions fan-out/merge/cost-decode/rollup."""
    from butlers.core.ingestion_events import (
        ingestion_event_rollup,
        ingestion_event_sessions,
        ingestion_events_list,
    )
    from butlers.pagination import decode_cursor

    # List: empty → items=[], next_cursor=None
    result = await ingestion_events_list(fake_pool(fetch=[]))
    assert result["items"] == [] and result["next_cursor"] is None

    # List: limit=2, 2 rows returned → last page (fetched limit+1=3, got 2)
    rows = [_make_event_record(source_channel="email"), _make_event_record(source_channel="tg")]
    pool = fake_pool(fetch=rows)
    result2 = await ingestion_events_list(pool, limit=2)
    assert len(result2["items"]) == 2
    assert result2["next_cursor"] is None
    assert isinstance(result2["items"][0]["id"], str)
    assert _args(pool.fetch)[-1] == 3  # limit + 1 sentinel

    # List: limit=2, 3 rows returned (limit+1) → next_cursor set
    extra_row = _make_event_record(source_channel="extra")
    three_rows = rows + [extra_row]
    result3 = await ingestion_events_list(fake_pool(fetch=three_rows), limit=2)
    assert len(result3["items"]) == 2  # only limit rows exposed
    assert result3["next_cursor"] is not None

    # The next_cursor carries the last returned row's keyset position
    _decoded_ra, decoded_id = decode_cursor(result3["next_cursor"], scope="ingestion-events.recent")
    assert str(decoded_id) == result3["items"][-1]["id"]

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
    assert rollup_result["unpriced_session_count"] == 0
    assert rollup_result["no_usage_session_count"] == 2
    assert (
        rollup_result["by_butler"]["atlas"]["sessions"] == 2
        and abs(rollup_result["by_butler"]["atlas"]["cost"] - 0.015) < 1e-9
    )
    assert rollup_result["by_butler"]["herald"]["cost"] is None
    assert rollup_result["by_butler"]["herald"]["unpriced_session_count"] == 0
    assert rollup_result["by_butler"]["herald"]["no_usage_session_count"] == 2


@_real_db
async def test_ingestion_events_list_filters_and_searches_the_unified_stream(db_pool) -> None:
    """channels/status/statuses/q/event_ids filter both stores; keyset pages compose with q.

    event_ids (drill-down spine, bu-86c4c.3): an explicit empty list restricts to
    zero rows, unlike an empty channels/statuses list. q matches the event id and
    the readily-available text columns case-insensitively; a blank q is no filter.
    """
    from butlers.core.ingestion_events import ingestion_events_list

    email = await _seed_event(db_pool, received_at=_minutes(0), source_sender_identity="alice@x.io")
    routed = await _seed_event(
        db_pool,
        received_at=_minutes(1),
        source_channel="telegram_bot",
        source_provider="telegram",
        triage_decision="route_to",
        triage_target="atlas",
    )
    failed = await _seed_event(
        db_pool, received_at=_minutes(2), status="failed", error_detail="boom-xyz"
    )
    skipped = await _seed_event(db_pool, received_at=_minutes(3), triage_decision="skip")
    filtered = await _seed_filtered(db_pool, received_at=_minutes(4), filter_reason="rate_limit")
    everything = {email, routed, failed, skipped, filtered}

    window = {"from_dt": _minutes(0), "to_dt": _minutes(5)}

    async def ids(**filters: Any) -> set[str]:
        page = await ingestion_events_list(db_pool, limit=10, **window, **filters)
        return {item["id"] for item in page["items"]}

    assert await ids() == everything
    assert await ids(channels=["telegram_bot"]) == {routed, filtered}
    assert await ids(channels=["email", "telegram_bot"]) == everything
    # statuses wins over status; 'skipped' is derived for skip-triaged ingested rows.
    assert await ids(status="filtered", statuses=["ingested", "failed"]) == {email, routed, failed}
    assert await ids(status="failed") == {failed}
    assert await ids(status="skipped") == {skipped}

    # q: id prefix, sender, triage target/decision, filter reason, error detail.
    assert await ids(q=email[:8]) == {email}
    assert await ids(q="ALICE") == {email}
    assert await ids(q="atlas") == {routed}
    assert await ids(q="route_to") == {routed}
    assert await ids(q="rate_limit") == {filtered}
    assert await ids(q="boom-xyz") == {failed}
    for blank_q in ("", "   "):
        assert await ids(q=blank_q) == everything

    assert await ids(event_ids=[email, filtered]) == {email, filtered}
    assert await ids(event_ids=[]) == set()
    assert await ids(event_ids=[email], channels=["telegram_bot"]) == set()

    # Keyset pages (received_at DESC, id DESC) compose with a q predicate.
    seen: list[str] = []
    cursor = None
    for _ in range(3):
        page = await ingestion_events_list(
            db_pool, q="example.com", limit=2, cursor=cursor, **window
        )
        seen += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    # filtered has no example.com column; the rest are newest-first, two pages.
    assert seen == [skipped, failed, routed, email]


async def test_ingestion_events_request_ids_for_trace() -> None:
    """Resolves a trace_id to distinct request_ids across all registered butlers.

    Covers: no match -> [], single butler, cross-butler dedup, sorted output,
    and that the fan-out query targets `sessions.trace_id`.
    """
    from butlers.core.ingestion_events import ingestion_events_request_ids_for_trace

    # No session anywhere carries this trace -> empty list, not an error.
    assert await ingestion_events_request_ids_for_trace(_FakeDatabaseManager(), "trace-x") == []

    # Single butler, single session.
    db = _FakeDatabaseManager(results={"atlas": [Record({"request_id": "req-1"})]})
    assert await ingestion_events_request_ids_for_trace(db, "trace-x") == ["req-1"]
    query, args, butler_names = db.fan_out_calls[0]
    assert "trace_id" in query and args == ("trace-x",) and butler_names is None

    # Cross-butler: same request_id fanned out to two butlers (shared trace)
    # dedupes to one entry; a distinct request_id from another butler is kept
    # and the result is sorted.
    db2 = _FakeDatabaseManager(
        results={
            "atlas": [Record({"request_id": "req-2"})],
            "herald": [
                Record({"request_id": "req-2"}),
                Record({"request_id": "req-1"}),
            ],
        }
    )
    assert await ingestion_events_request_ids_for_trace(db2, "trace-y") == ["req-1", "req-2"]

    # Falsy request_id values are skipped, not coerced into a phantom entry.
    db3 = _FakeDatabaseManager(results={"atlas": [Record({"request_id": None})]})
    assert await ingestion_events_request_ids_for_trace(db3, "trace-z") == []


async def test_ingestion_events_received_at_bounds_short_circuits_empty_ids() -> None:
    """Empty event_ids -> (None, None) without a query (bu-1f81d)."""
    from butlers.core.ingestion_events import ingestion_events_received_at_bounds

    assert await ingestion_events_received_at_bounds(fake_pool(), []) == (None, None)


@_real_db
async def test_received_at_bounds_and_window_counts_honour_event_ids_and_q(db_pool) -> None:
    """Bounds span both stores (bu-1f81d); window counts apply event_ids and q (bu-q750c).

    An explicit empty event_ids list restricts the window to zero events rather
    than falling through to "no filter".
    """
    from butlers.core.ingestion_events import (
        ingestion_events_received_at_bounds,
        ingestion_window_rollup,
    )

    first = await _seed_event(db_pool, received_at=_minutes(0), source_sender_identity="alice@x")
    second = await _seed_event(db_pool, received_at=_minutes(5))
    filtered = await _seed_filtered(db_pool, received_at=_minutes(10))
    await _seed_event(db_pool, received_at=_minutes(60))

    assert await ingestion_events_received_at_bounds(db_pool, [first, filtered]) == (
        _minutes(0),
        _minutes(10),
    )
    assert await ingestion_events_received_at_bounds(db_pool, [str(uuid.uuid4())]) == (
        None,
        None,
    )

    window = {"from_dt": _minutes(0), "to_dt": _minutes(61)}

    async def events(**filters: Any) -> int:
        return (await ingestion_window_rollup(db_pool, db=None, **window, **filters))["events"]

    assert await events() == 4
    assert await events(event_ids=[first, second, filtered]) == 3
    assert await events(event_ids=[]) == 0
    assert await events(q="alice") == 1
    assert await events(q="   ") == 4


async def test_cost_cursor_and_cost_sort() -> None:
    """Cost cursor shape is enforced; sort=cost pages by offset (core_126)."""
    import uuid as _uuid
    from datetime import UTC, datetime

    from butlers.core.ingestion_events import ingestion_events_list
    from butlers.pagination import InvalidCursorError, decode_cursor, encode_cursor

    # sort=cost refuses a keyset cursor (wrong shape) and a negative offset, before querying
    keyset_cursor = encode_cursor(
        (datetime(2026, 1, 1, tzinfo=UTC), _uuid.uuid4()), scope="ingestion-events.cost"
    )
    with pytest.raises(InvalidCursorError):
        await ingestion_events_list(fake_pool(), sort="cost", cursor=keyset_cursor)
    with pytest.raises(InvalidCursorError):
        await ingestion_events_list(
            fake_pool(),
            sort="cost",
            cursor=encode_cursor((-10,), scope="ingestion-events.cost"),
        )

    # sort=cost, first page → next_cursor=None when ≤limit rows
    rows = [_make_event_record(cost_usd=0.05), _make_event_record(cost_usd=0.01)]
    result = await ingestion_events_list(fake_pool(fetch=rows), sort="cost", limit=5)
    assert result["next_cursor"] is None
    assert len(result["items"]) == 2

    # sort=cost, limit+1 rows returned → cursor encodes offset=limit
    three = [_make_event_record(cost_usd=x) for x in [0.05, 0.02, 0.01]]
    result2 = await ingestion_events_list(fake_pool(fetch=three), sort="cost", limit=2)
    assert decode_cursor(result2["next_cursor"], scope="ingestion-events.cost") == (
        2,
    )  # next offset = limit


@_real_db
async def test_cost_sort_orders_written_costs_first_and_pages_by_offset(db_pool) -> None:
    """set_cost_usd writes through; sort=cost orders cost DESC NULLS LAST then recency."""
    from butlers.core.ingestion_events import (
        ingestion_event_set_cost_usd,
        ingestion_events_list,
    )
    from butlers.pagination import decode_cursor

    cheap = await _seed_event(db_pool, received_at=_minutes(0))
    dear = await _seed_event(db_pool, received_at=_minutes(1))
    unpriced = await _seed_event(db_pool, received_at=_minutes(2))
    filtered = await _seed_filtered(db_pool, received_at=_minutes(3))

    await ingestion_event_set_cost_usd(db_pool, uuid.UUID(dear), 0.05)
    await ingestion_event_set_cost_usd(db_pool, cheap, 0.0123)  # str id accepted
    assert await db_pool.fetchval(
        "SELECT cost_usd FROM public.ingestion_events WHERE id = $1", uuid.UUID(cheap)
    ) == Decimal("0.0123")

    page1 = await ingestion_events_list(db_pool, sort="cost", limit=2)
    assert [item["id"] for item in page1["items"]] == [dear, cheap]
    assert decode_cursor(page1["next_cursor"], scope="ingestion-events.cost") == (2,)

    page2 = await ingestion_events_list(db_pool, sort="cost", limit=2, cursor=page1["next_cursor"])
    # NULL costs last, newest first among them.
    assert [item["id"] for item in page2["items"]] == [filtered, unpriced]
    assert page2["next_cursor"] is None

    # Filters are shared with the default sort.
    scoped = await ingestion_events_list(db_pool, sort="cost", event_ids=[cheap, filtered], limit=5)
    assert [item["id"] for item in scoped["items"]] == [cheap, filtered]


async def test_ingestion_events_list_skips_ilike_for_empty_or_whitespace_q() -> None:
    """Empty/whitespace-only q must not emit an ILIKE '%%' full-window scan.

    Mirrors the guard applied to ingestion_events_histogram (PR #2853): q=None,
    q="", and q="   " all skip the ILIKE predicate entirely, while a real
    query string still produces one. The predicate's effect is covered against
    a real database above; this pins the query shape the guard exists for.
    """
    from butlers.core.ingestion_events import ingestion_events_list

    for blank_q in (None, "", "   "):
        pool = fake_pool(fetch=[])
        await ingestion_events_list(pool, q=blank_q, limit=5)
        assert "ILIKE" not in _sql(pool.fetch), f"q={blank_q!r} should not emit ILIKE"
        assert "%%" not in "".join(a for a in _args(pool.fetch) if isinstance(a, str))

    # Sanity: a non-blank q still filters as before.
    pool2 = fake_pool(fetch=[])
    await ingestion_events_list(pool2, q="alice", limit=5)
    assert "ILIKE" in _sql(pool2.fetch) and "%alice%" in _args(pool2.fetch)


async def test_replay_request_and_inbox_lifecycle() -> None:
    """replay_request ok/not_found/conflict/unsafe outcomes; inbox_lifecycle state lookup.

    The policy-locked transition SQL itself is exercised against Postgres in
    tests/integration/test_ingestion_replay_policy_db.py.
    """
    from butlers.core.ingestion_events import (
        ingestion_event_get_inbox_lifecycle,
        ingestion_event_replay_request,
    )

    event_id = uuid.uuid4()
    ok_row = Record({"id": event_id, "status": "failed"})

    # Public refusal: the policy-gated public read answers first.
    safe_transition_pool = fake_pool(fetchrow=_queued(ok_row))
    result = await ingestion_event_replay_request(safe_transition_pool, event_id)
    assert result["outcome"] == "unrecoverable" and result["id"] == str(event_id)
    assert "FROM public.ingestion_events" in _sql(safe_transition_pool.fetchrow)

    # Not public: the filtered store's locked transition queues the replay.
    filtered_transition_pool = fake_pool(fetchrow=_queued(None, ok_row), fetchval=None)
    filtered_result = await ingestion_event_replay_request(filtered_transition_pool, event_id)
    assert filtered_result["outcome"] == "ok"
    assert "UPDATE connectors.filtered_events" in _sql(filtered_transition_pool.fetchrow, 1)

    # String UUID accepted
    assert (await ingestion_event_replay_request(fake_pool(fetchrow=ok_row), str(event_id)))[
        "outcome"
    ] == "unrecoverable"

    # Not found: no public or filtered row, and no policy row to explain.
    assert (
        await ingestion_event_replay_request(
            fake_pool(fetchrow=_queued(None, None), fetchval=_queued(None, None), fetch=[]),
            uuid.uuid4(),
        )
    )["outcome"] == "not_found"

    # Conflict in the filtered store: the transition refused, the row is replay_pending.
    result_cf = await ingestion_event_replay_request(
        fake_pool(fetchrow=_queued(None, None), fetchval=_queued(None, "replay_pending"), fetch=[]),
        uuid.uuid4(),
    )
    assert result_cf["outcome"] == "conflict" and result_cf["current_status"] == "replay_pending"

    unsafe_result = await ingestion_event_replay_request(
        fake_pool(
            fetchrow=_queued(None, None),
            fetch=[
                Record(
                    {
                        "id": str(event_id),
                        "source_channel": "email",
                        "endpoint_identity": "inbox@example.com",
                        "connector_match_count": 1,
                        "replay_safe": False,
                    }
                )
            ],
            fetchval=None,
        ),
        event_id,
    )
    assert unsafe_result == {
        "outcome": "unsafe",
        "reason": "Email events cannot be replayed safely",
        "source_channel": "email",
    }

    # Invalid UUID raises before any query
    with pytest.raises(ValueError):
        await ingestion_event_replay_request(fake_pool(), "not-a-uuid")

    # inbox_lifecycle: returns state; None when no row; JSON decoded; null decomposition
    decomp = {"signals": [], "reason": "no_signals"}
    result2 = await ingestion_event_get_inbox_lifecycle(
        fake_pool(
            fetchrow=Record({"lifecycle_state": "decomposed_empty", "decomposition_output": decomp})
        ),
        uuid.uuid4(),
    )
    assert (
        result2["lifecycle_state"] == "decomposed_empty"
        and result2["decomposition_output"] == decomp
    )

    assert await ingestion_event_get_inbox_lifecycle(fake_pool(fetchrow=None), uuid.uuid4()) is None

    result3 = await ingestion_event_get_inbox_lifecycle(
        fake_pool(
            fetchrow=Record(
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
    row1 = Record(
        {"ts": ts1, "actor": "dashboard", "note": _json.dumps({"result": "pending", "cost": 0.01})}
    )
    # Row with no note
    row2 = Record({"ts": ts2, "actor": "scheduler", "note": None})

    # Returns list of entries with extracted fields
    result = await ingestion_event_replay_history(fake_pool(fetch=[row1, row2]), event_id)
    assert len(result) == 2
    assert result[0]["actor"] == "dashboard"
    assert result[0]["result"] == "pending"
    assert abs(result[0]["cost"] - 0.01) < 1e-9
    assert result[1]["actor"] == "scheduler"
    assert result[1]["result"] is None
    assert result[1]["cost"] is None

    # String UUID accepted
    result2 = await ingestion_event_replay_history(fake_pool(fetch=[row1]), str(event_id))
    assert len(result2) == 1

    # Invalid string UUID returns empty list (no raise)
    result3 = await ingestion_event_replay_history(fake_pool(), "not-a-uuid")
    assert result3 == []

    # Empty DB result → empty list
    result4 = await ingestion_event_replay_history(fake_pool(fetch=[]), event_id)
    assert result4 == []

    # DB error → empty list (fail-open, no exception propagated)
    error_pool = fake_pool(fetch=AsyncMock(side_effect=RuntimeError("DB unavailable")))
    result5 = await ingestion_event_replay_history(error_pool, event_id)
    assert result5 == []

    # Row with malformed (non-JSON) note — graceful: fields default to None
    row_bad = Record({"ts": ts1, "actor": "agent", "note": "not-json-{{"})
    result6 = await ingestion_event_replay_history(fake_pool(fetch=[row_bad]), event_id)
    assert len(result6) == 1
    assert result6[0]["result"] is None
    assert result6[0]["cost"] is None


# ---------------------------------------------------------------------------
# ingestion_window_rollup — cost aggregation
# ---------------------------------------------------------------------------


def _make_pricing(model_id: str = "claude-test", price_per_token: float = 1e-6):
    """Build a minimal PricingConfig with a single model entry."""
    from butlers.core.pricing import ModelPricing, PricingConfig

    return PricingConfig({model_id: ModelPricing(price_per_token, price_per_token * 2)})


async def test_ingestion_window_rollup_cost_with_pricing() -> None:
    """When pricing is supplied and sessions have token data, cost is summed correctly."""
    from butlers.core.ingestion_events import ingestion_window_rollup

    pricing = _make_pricing("claude-test", price_per_token=1e-6)

    # Exact cost-signature groups across two butler schemas. Token values are
    # per session; cnt is the number of identical signatures in that group.
    session_rows_butler1 = [
        # Three sessions, each with 1000 input and 500 output tokens.
        {"cnt": 3, "model": "claude-test", "input_tokens": 1000, "output_tokens": 500},
    ]
    session_rows_butler2 = [
        {"cnt": 2, "model": "claude-test", "input_tokens": 600, "output_tokens": 300},
    ]
    db = _FakeDatabaseManager(
        results={"butler1": session_rows_butler1, "butler2": session_rows_butler2}
    )
    pool = fake_pool(fetchval=5)

    result = await ingestion_window_rollup(pool, db=db, pricing=pricing)

    # session_count: 3 + 2 = 5
    assert result["sessions"] == 5
    assert result["events"] == 5

    # cost: estimate_session_cost evaluates each signature then multiplies by
    # its count. butler1: 3 * (0.001 + 0.001) = 0.006; butler2:
    # 2 * (0.0006 + 0.0006) = 0.0024; total = 0.0084.
    assert result["cost"] is not None
    assert isinstance(result["cost"], float)
    assert abs(result["cost"] - 0.0084) < 1e-9
    assert result["unpriced_session_count"] == 0


async def test_ingestion_window_rollup_cost_null_without_pricing() -> None:
    """When pricing is None, cost is always None regardless of session token data."""
    from butlers.core.ingestion_events import ingestion_window_rollup

    session_rows = [{"cnt": 3, "model": "claude-test", "input_tokens": 1000, "output_tokens": 500}]
    db = _FakeDatabaseManager(results={"butler1": session_rows})
    pool = fake_pool(fetchval=3)

    result = await ingestion_window_rollup(pool, db=db, pricing=None)

    assert result["sessions"] == 3
    assert result["cost"] is None
    assert result["unpriced_session_count"] == 3


async def test_ingestion_window_rollup_preserves_stored_cost_without_pricing() -> None:
    """A legacy stored cost is priced evidence even when catalog pricing is absent."""
    from butlers.core.ingestion_events import ingestion_window_rollup

    session_rows = [
        {
            "cnt": 2,
            "model": "",
            "input_tokens": 0,
            "output_tokens": 0,
            "cost": {"total_usd": 0.0042},
        }
    ]
    db = _FakeDatabaseManager(results={"butler1": session_rows})
    pool = fake_pool(fetchval=2)

    result = await ingestion_window_rollup(pool, db=db, pricing=None)

    assert result["sessions"] == 2
    assert result["cost"] == 0.0084
    assert result["unpriced_session_count"] == 0
    assert result["no_usage_session_count"] == 0


async def test_ingestion_window_rollup_cost_null_no_sessions() -> None:
    """When no sessions exist (empty fan-out), cost is None even with pricing."""
    from butlers.core.ingestion_events import ingestion_window_rollup

    pricing = _make_pricing()
    db = _FakeDatabaseManager(results={"butler1": []})
    pool = fake_pool(fetchval=2)

    result = await ingestion_window_rollup(pool, db=db, pricing=pricing)

    assert result["sessions"] == 0
    assert result["cost"] is None
    assert result["unpriced_session_count"] == 0


async def test_ingestion_window_rollup_cost_skips_unknown_model() -> None:
    """Known prices still produce a subtotal when another model is unpriced."""
    from butlers.core.ingestion_events import ingestion_window_rollup

    pricing = _make_pricing("known-model", price_per_token=1e-6)

    # One row with known model (has tokens), one with unknown model
    session_rows = [
        {"cnt": 1, "model": "known-model", "input_tokens": 1000, "output_tokens": 500},
        {"cnt": 1, "model": "unknown-model-xyz", "input_tokens": 2000, "output_tokens": 1000},
    ]
    db = _FakeDatabaseManager(results={"butler1": session_rows})
    pool = fake_pool(fetchval=2)

    result = await ingestion_window_rollup(pool, db=db, pricing=pricing)

    assert result["sessions"] == 2
    # Only known-model contributes the numerical subtotal; unknown usage is
    # not fabricated as a zero-priced session.
    # known-model: 1000 * 1e-6 + 500 * 2e-6 = 0.001 + 0.001 = 0.002
    assert result["cost"] is not None
    assert abs(result["cost"] - 0.002) < 1e-9
    assert result["unpriced_session_count"] == 1


async def test_ingestion_window_rollup_preserves_no_usage_same_model_sessions() -> None:
    """A no-usage session must not hide inside another priced model bucket."""
    from butlers.core.ingestion_events import ingestion_window_rollup

    pricing = _make_pricing("known-model", price_per_token=1e-6)
    # The exact-cost-signature query returns separate rows for a priced session
    # and one with no metered usage. Grouping only by model would merge them
    # and incorrectly report two priced sessions.
    session_rows = [
        {
            "cnt": 1,
            "model": "known-model",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cost": None,
        },
        {"cnt": 1, "model": "known-model", "input_tokens": 0, "output_tokens": 0, "cost": None},
    ]
    db = _FakeDatabaseManager(results={"butler1": session_rows})
    pool = fake_pool(fetchval=2)

    result = await ingestion_window_rollup(pool, db=db, pricing=pricing)

    assert result["sessions"] == 2
    assert result["cost"] == 0.002
    assert result["unpriced_session_count"] == 0
    assert result["no_usage_session_count"] == 1
    query = " ".join(db.fan_out_calls[-1][0].split())
    assert "GROUP BY model, COALESCE(input_tokens, 0)" in query


async def test_ingestion_window_rollup_cost_null_all_unknown_models() -> None:
    """All-unpriced session usage must not be rendered as a calm zero."""
    from butlers.core.ingestion_events import ingestion_window_rollup

    pricing = _make_pricing("known-model", price_per_token=1e-6)

    # All sessions have an unknown model (not in the pricing catalog)
    session_rows = [
        {"cnt": 2, "model": "unknown-model-xyz", "input_tokens": 2000, "output_tokens": 1000},
    ]
    db = _FakeDatabaseManager(results={"butler1": session_rows})
    pool = fake_pool(fetchval=2)

    result = await ingestion_window_rollup(pool, db=db, pricing=pricing)

    assert result["sessions"] == 2
    assert result["cost"] is None
    assert result["unpriced_session_count"] == 2


async def test_ingestion_window_rollup_cost_null_when_db_none() -> None:
    """When db=None, no fan-out runs; sessions=0 and cost=None."""
    from butlers.core.ingestion_events import ingestion_window_rollup

    pricing = _make_pricing()
    pool = fake_pool(fetchval=10)

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
        pool = fake_pool(fetchval=0)
        await ingestion_window_rollup(pool, q=blank_q, db=None)
        pool.fetchval.assert_awaited_once()
        assert "ILIKE" not in _sql(pool.fetchval), f"q={blank_q!r} should not emit ILIKE"

    # Sanity: a non-blank q still filters as before.
    pool2 = fake_pool(fetchval=0)
    await ingestion_window_rollup(pool2, q="alice", db=None)
    assert "ILIKE" in _sql(pool2.fetchval) and "%alice%" in _args(pool2.fetchval)


async def test_ingestion_window_rollup_event_ids_reach_the_session_aggregate() -> None:
    """event_ids also scope the relational session aggregate, without an ID cap (bu-q750c).

    The event count's own event_ids filter is covered against a real database above.
    """
    from butlers.core.ingestion_events import ingestion_window_rollup

    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    db = _FakeDatabaseManager(results={"butler1": []})
    pool = fake_pool(fetchval=2)  # fetch is unconfigured: ids never leave Postgres
    await ingestion_window_rollup(pool, event_ids=ids, db=db)
    id_sql, id_args, _ = db.fan_out_calls[0]
    assert "id = ANY(" in id_sql
    assert [uuid.UUID(i) for i in ids] in id_args
    assert "LIMIT 10000" not in id_sql


async def test_window_rollup_source_failure_is_unavailable_not_zero() -> None:
    from butlers.core.ingestion_events import ingestion_window_rollup

    pool = fake_pool(fetchval=AsyncMock(side_effect=RuntimeError("synthetic failure")))
    with pytest.raises(RuntimeError):
        await ingestion_window_rollup(pool)

    pool = fake_pool(fetchval=2)
    db = _FakeDatabaseManager(results={"butler1": []})
    db.fan_out_with_status = AsyncMock(return_value=({"butler1": []}, ["butler1"]))
    with pytest.raises(RuntimeError):
        await ingestion_window_rollup(pool, db=db)


# ---------------------------------------------------------------------------
# ingestion_events_sessions_for_ids / ingestion_events_list_enrichment (bu-4utdw.3)
#
# List-view row enrichment: ONE grouped fan-out for a whole page of ids (not
# one query per event), feeding tokens/cost/session-summary fields onto each
# IngestionEventSummary. Kills the N+1 request storm the per-row
# rollup/sender-contact hooks previously caused.
# ---------------------------------------------------------------------------


def _make_bulk_session_record(request_id: str, **kwargs: Any) -> Record:
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
    return Record(defaults)


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
    assert req1["unpriced_session_count"] == 0
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
    assert req2["unpriced_session_count"] == 0
    assert req2["session_count"] == 0
    assert req2["sessions"] == []


def test_ingestion_events_list_enrichment_preserves_unknown_and_known_zero_cost() -> None:
    """Unknown session prices stay explicit while declared zero remains known."""
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

    # All sessions unknown-cost have no subtotal, not a fabricated $0.
    assert enrichment["req-1"]["session_cost_usd"] is None
    assert enrichment["req-1"]["unpriced_session_count"] == 1
    # Mixed known/unknown keeps the known subtotal and exposes the omission.
    assert abs(enrichment["req-2"]["session_cost_usd"] - 0.02) < 1e-9
    assert enrichment["req-2"]["unpriced_session_count"] == 1

    known_zero = ingestion_events_list_enrichment(
        {
            "req-3": [
                {
                    "butler_name": "butler-d",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cost_usd": 0.0,
                    "success": True,
                    "started_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
                    "completed_at": datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC),
                }
            ]
        }
    )["req-3"]
    assert known_zero["session_cost_usd"] == 0.0
    assert known_zero["unpriced_session_count"] == 0


def test_ingestion_events_list_enrichment_separates_no_usage_from_unpriced() -> None:
    """Failed launches without token evidence are not missing-price sessions."""
    from butlers.core.ingestion_events import ingestion_events_list_enrichment

    enrichment = ingestion_events_list_enrichment(
        {
            "req-1": [
                {
                    "butler_name": "model-mismatch",
                    "input_tokens": 12,
                    "output_tokens": 4,
                    "cost_usd": None,
                    "success": False,
                },
                {
                    "butler_name": "auth-failure",
                    "input_tokens": None,
                    "output_tokens": None,
                    "cached_input_tokens": None,
                    "cache_creation_tokens": None,
                    "cost_usd": None,
                    "success": False,
                },
            ]
        }
    )["req-1"]

    assert enrichment["unpriced_session_count"] == 1
    assert enrichment["no_usage_session_count"] == 1
    assert [session["cost_evidence"] for session in enrichment["sessions"]] == [
        "unpriced",
        "no_usage",
    ]


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


def _make_histogram_row(ts: datetime, status: str, cnt: int) -> Record:
    return Record({"bucket_ts": ts, "status": status, "cnt": cnt})


async def test_ingestion_events_histogram_rejects_unknown_bucket() -> None:
    from butlers.core.ingestion_events import ingestion_events_histogram

    pool = fake_pool()
    with pytest.raises(ValueError, match="Invalid bucket"):
        await ingestion_events_histogram(
            pool,
            from_dt=datetime(2026, 1, 1, tzinfo=UTC),
            to_dt=datetime(2026, 1, 1, 1, tzinfo=UTC),
            bucket="30s",
        )
    # Guardrail/validation short-circuits before any query is issued.
    pool.fetch.assert_not_awaited()


async def test_ingestion_events_histogram_rejects_to_not_after_from() -> None:
    from datetime import timedelta

    from butlers.core.ingestion_events import ingestion_events_histogram

    pool = fake_pool()
    same = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="'to' must be after 'from'"):
        await ingestion_events_histogram(pool, from_dt=same, to_dt=same)
    with pytest.raises(ValueError, match="'to' must be after 'from'"):
        await ingestion_events_histogram(pool, from_dt=same, to_dt=same - timedelta(hours=1))
    pool.fetch.assert_not_awaited()


async def test_ingestion_events_histogram_guardrail_rejects_1m_over_48h() -> None:
    """1m bucket over exactly 48h is allowed; one second past it is rejected."""
    from datetime import timedelta

    from butlers.core.ingestion_events import ingestion_events_histogram

    from_dt = datetime(2026, 1, 1, tzinfo=UTC)

    # Exactly 48h → 2880 buckets → allowed (query issued).
    pool_ok = fake_pool(fetch=[])
    await ingestion_events_histogram(
        pool_ok, from_dt=from_dt, to_dt=from_dt + timedelta(hours=48), bucket="1m"
    )
    pool_ok.fetch.assert_awaited_once()

    # One second over 48h → exceeds the 2880-bucket cap → rejected before querying.
    pool_reject = fake_pool()
    with pytest.raises(ValueError, match="too wide for bucket '1m'"):
        await ingestion_events_histogram(
            pool_reject,
            from_dt=from_dt,
            to_dt=from_dt + timedelta(hours=48, seconds=1),
            bucket="1m",
        )
    pool_reject.fetch.assert_not_awaited()

    # The same wide range is fine at a coarser bucket (5m).
    pool_5m = fake_pool(fetch=[])
    await ingestion_events_histogram(
        pool_5m,
        from_dt=from_dt,
        to_dt=from_dt + timedelta(hours=48, seconds=1),
        bucket="5m",
    )
    pool_5m.fetch.assert_awaited_once()


async def test_ingestion_events_histogram_aggregates_and_zero_fills_present_buckets() -> None:
    """Rows are grouped by bucket_ts, zero-filled to all 8 statuses; buckets sorted ascending."""
    from butlers.core.ingestion_events import ingestion_events_histogram

    ts_a = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    ts_b = datetime(2026, 1, 1, 12, 1, 0, tzinfo=UTC)
    pool = fake_pool(
        fetch=[
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
    pool = fake_pool(fetch=[_make_histogram_row(ts, "failed", 4)])

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
    pool = fake_pool(execute="UPDATE 1")

    result = await ingestion_event_reconcile_after_processing(pool, event_id, routing_failed=False)

    assert result is True
    pool.execute.assert_awaited_once()
    sql, args = _sql(pool.execute), _args(pool.execute)
    assert "status = 'ingested'" in sql
    assert "status = 'replay_pending'" in sql
    assert args[0] == event_id


async def test_reconcile_after_processing_marks_failed_on_failure() -> None:
    """routing_failed=True routes to mark_failed with the given error_detail."""
    from butlers.core.ingestion_events import ingestion_event_reconcile_after_processing

    event_id = uuid.uuid4()
    pool = fake_pool(execute="UPDATE 1")

    result = await ingestion_event_reconcile_after_processing(
        pool, event_id, routing_failed=True, error_detail="failed_targets: ['general']"
    )

    assert result is True
    pool.execute.assert_awaited_once()
    sql, args = _sql(pool.execute), _args(pool.execute)
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

    exploding = fake_pool(execute=AsyncMock(side_effect=RuntimeError("connection reset")))
    result = await ingestion_event_reconcile_after_processing(
        exploding, uuid.uuid4(), routing_failed=False
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


async def test_public_replay_missing_plan_never_writes_event_or_inbox() -> None:
    # REQ-ingestion-target-delivery-recovery-006: legacy rows have no proven intent.
    from butlers.core.ingestion_events import ingestion_event_replay_request

    event_id = uuid.uuid4()
    for status in ("failed", "ingested", "replay_failed", "replay_pending"):
        for _ in range(2):  # Repeated requests must remain read-only.
            pool = fake_pool(fetchrow=Record({"id": event_id, "status": status}))
            inbox_pool = fake_pool()  # any call on it fails the test
            result = await ingestion_event_replay_request(
                pool, event_id, switchboard_pool=inbox_pool
            )
            assert result == {
                "outcome": "unrecoverable",
                "id": str(event_id),
                "source": "ingestion_events",
                "current_status": status,
                "reason": "no_retained_delivery_plan",
            }
            # One policy-gated read; execute is unconfigured, so nothing was written.
            pool.fetchrow.assert_awaited_once()
            assert "UPDATE" not in _sql(pool.fetchrow)
            inbox_pool.acquire.assert_not_called()


async def test_public_other_statuses_remain_conflicts() -> None:
    # REQ-ingestion-target-delivery-recovery-006: other statuses remain non-replayable.
    from butlers.core.ingestion_events import ingestion_event_replay_request

    for status in ("filtered", "error", "replay_complete", "unknown"):
        event_id = uuid.uuid4()
        pool = fake_pool(fetchrow=Record({"id": event_id, "status": status}))
        inbox = fake_pool()  # any call on it fails the test
        assert await ingestion_event_replay_request(pool, event_id, switchboard_pool=inbox) == {
            "outcome": "conflict",
            "current_status": status,
        }
        pool.fetchrow.assert_awaited_once()
        assert "UPDATE" not in _sql(pool.fetchrow)
        inbox.acquire.assert_not_called()
