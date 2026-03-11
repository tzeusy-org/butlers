"""Tests for butlers.core.ingestion_events — ingestion event query module.

All tests use fake asyncpg infrastructure (no Docker required) to keep the
suite fast and portable.  The fakes capture SQL + args so we can assert the
correct queries are issued without needing a live database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Column spec integrity — ensures _UNION_COLUMN_SPEC stays correct
# ---------------------------------------------------------------------------


class TestUnionColumnSpec:
    """Verify that the column spec produces the correct SQL strings.

    These tests lock down the exact column lists that both sides of the UNION ALL
    must emit so that adding a new column only requires a single spec entry.
    """

    def test_ingested_cols_exact_content(self) -> None:
        """_INGESTED_COLS must match the original hardcoded ingestion_events SELECT list."""
        from butlers.core.ingestion_events import _INGESTED_COLS

        expected = (
            "id, received_at, source_channel, source_provider, "
            "source_endpoint_identity, source_sender_identity, "
            "source_thread_identity, external_event_id, dedupe_key, "
            "dedupe_strategy, ingestion_tier, policy_tier, "
            "triage_decision, triage_target, "
            "'ingested'::text AS status, "
            "NULL::text AS filter_reason, "
            "NULL::text AS error_detail"
        )
        assert _INGESTED_COLS == expected

    def test_filtered_cols_exact_content(self) -> None:
        """_FILTERED_COLS must match the original hardcoded filtered_events SELECT list."""
        from butlers.core.ingestion_events import _FILTERED_COLS

        expected = (
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
        assert _FILTERED_COLS == expected

    def test_event_columns_exact_content(self) -> None:
        """_EVENT_COLUMNS must match the original ingestion_events column list for point lookups."""
        from butlers.core.ingestion_events import _EVENT_COLUMNS

        expected = (
            "id, received_at, source_channel, source_provider, "
            "source_endpoint_identity, source_sender_identity, "
            "source_thread_identity, external_event_id, dedupe_key, "
            "dedupe_strategy, ingestion_tier, policy_tier, "
            "triage_decision, triage_target"
        )
        assert _EVENT_COLUMNS == expected

    def test_ingested_and_filtered_have_same_column_count(self) -> None:
        """Both UNION branches must produce the same number of columns as the spec."""
        from butlers.core.ingestion_events import _FILTERED_COLS, _INGESTED_COLS, _UNION_COLUMN_SPEC

        ingested_count = len(_INGESTED_COLS.split(","))
        filtered_count = len(_FILTERED_COLS.split(","))
        assert ingested_count == filtered_count == len(_UNION_COLUMN_SPEC)

    def test_spec_has_no_duplicate_aliases(self) -> None:
        """Each output_alias in _UNION_COLUMN_SPEC must be unique."""
        from butlers.core.ingestion_events import _UNION_COLUMN_SPEC

        aliases = [alias for alias, _, _ in _UNION_COLUMN_SPEC]
        assert len(aliases) == len(set(aliases)), "Duplicate aliases found in _UNION_COLUMN_SPEC"


# ---------------------------------------------------------------------------
# Fake asyncpg / DatabaseManager infrastructure
# ---------------------------------------------------------------------------


class _FakeRecord(dict):
    """Dict that behaves like an asyncpg.Record for dict(row) calls."""


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
        # Literal columns returned by _INGESTED_COLS (from _UNION_COLUMN_SPEC)
        "status": "ingested",
        "filter_reason": None,
        "error_detail": None,
    }
    defaults.update(kwargs)
    return _FakeRecord(defaults)


def _make_filtered_event_record(**kwargs: Any) -> _FakeRecord:
    """Simulate a row from connectors.filtered_events (mapped to shared shape)."""
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "received_at": datetime.now(UTC),
        "source_channel": "telegram",
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
    """Minimal fake asyncpg pool that captures fetchrow / fetchval / fetch calls.

    ``fetchrow_results`` is a list of return values consumed in order for each
    ``fetchrow`` call.  Pass a single-element list for the common case.  The
    legacy ``fetchrow_result`` kwarg is still accepted for backwards compat and
    is treated as ``fetchrow_results=[fetchrow_result]``.
    """

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
            # Legacy single-result compat
            self._fetchrow_results = [fetchrow_result]
        self._fetch_results: list[Any] = fetch_results if fetch_results is not None else []
        self._fetchval_result = fetchval_result
        # Captured calls: list of (method, sql, args)
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
    """Minimal fake DatabaseManager that records fan_out calls."""

    def __init__(self, results: dict[str, list[Any]] | None = None) -> None:
        # Map butler_name -> list of FakeRecord rows
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
# ingestion_event_get
# ---------------------------------------------------------------------------


class TestIngestionEventGet:
    async def test_returns_none_when_no_row(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_get

        pool = _FakePool(fetchrow_result=None)
        event_id = uuid.uuid4()
        result = await ingestion_event_get(pool, event_id)
        assert result is None

    async def test_returns_dict_when_row_found(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_get

        row = _make_event_record(source_channel="telegram")
        pool = _FakePool(fetchrow_result=row)
        result = await ingestion_event_get(pool, uuid.uuid4())
        assert result is not None
        assert result["source_channel"] == "telegram"

    async def test_queries_shared_ingestion_events(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_get

        pool = _FakePool(fetchrow_result=None)
        await ingestion_event_get(pool, uuid.uuid4())
        assert pool.calls, "fetchrow should have been called"
        _, sql, _ = pool.calls[0]
        assert "shared.ingestion_events" in sql

    async def test_passes_event_id_as_param(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_get

        pool = _FakePool(fetchrow_result=None)
        event_id = uuid.uuid4()
        await ingestion_event_get(pool, event_id)
        _, _, args = pool.calls[0]
        assert args[0] == event_id

    async def test_accepts_string_event_id(self) -> None:
        """ingestion_event_get should accept a UUID string and convert it."""
        from butlers.core.ingestion_events import ingestion_event_get

        pool = _FakePool(fetchrow_result=None)
        event_id = uuid.uuid4()
        await ingestion_event_get(pool, str(event_id))
        _, _, args = pool.calls[0]
        assert args[0] == event_id

    async def test_result_id_is_string(self) -> None:
        """The returned id should be a string (serialisation-friendly)."""
        from butlers.core.ingestion_events import ingestion_event_get

        row = _make_event_record()
        pool = _FakePool(fetchrow_result=row)
        result = await ingestion_event_get(pool, uuid.uuid4())
        assert result is not None
        assert isinstance(result["id"], str)

    async def test_all_expected_fields_present(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_get

        row = _make_event_record()
        pool = _FakePool(fetchrow_result=row)
        result = await ingestion_event_get(pool, uuid.uuid4())
        assert result is not None
        for field in (
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
        ):
            assert field in result, f"Missing field: {field}"

    async def test_ingested_event_has_status_ingested(self) -> None:
        """ingested events must return status='ingested' and filter_reason=None."""
        from butlers.core.ingestion_events import ingestion_event_get

        row = _make_event_record()
        pool = _FakePool(fetchrow_result=row)
        result = await ingestion_event_get(pool, uuid.uuid4())
        assert result is not None
        assert result["status"] == "ingested"
        assert result["filter_reason"] is None


# ---------------------------------------------------------------------------
# ingestion_event_get — unified lookup (filtered events)
# ---------------------------------------------------------------------------


class TestIngestionEventGetUnifiedLookup:
    """Covers the fallback to connectors.filtered_events when an event is not
    found in shared.ingestion_events."""

    async def test_returns_none_when_not_in_either_table(self) -> None:
        """Both fetchrow calls return None → result is None."""
        from butlers.core.ingestion_events import ingestion_event_get

        # fetchrow_results=[None, None]: first for ingestion_events, second for filtered_events
        pool = _FakePool(fetchrow_results=[None, None])
        result = await ingestion_event_get(pool, uuid.uuid4())
        assert result is None

    async def test_two_fetchrow_calls_on_miss(self) -> None:
        """When shared.ingestion_events misses, filtered_events is also queried."""
        from butlers.core.ingestion_events import ingestion_event_get

        pool = _FakePool(fetchrow_results=[None, None])
        await ingestion_event_get(pool, uuid.uuid4())
        fetchrow_calls = [c for c in pool.calls if c[0] == "fetchrow"]
        assert len(fetchrow_calls) == 2, "Expected two fetchrow calls for the unified lookup"

    async def test_first_fetchrow_targets_shared_ingestion_events(self) -> None:
        """First lookup must query shared.ingestion_events."""
        from butlers.core.ingestion_events import ingestion_event_get

        pool = _FakePool(fetchrow_results=[None, None])
        await ingestion_event_get(pool, uuid.uuid4())
        fetchrow_calls = [c for c in pool.calls if c[0] == "fetchrow"]
        assert "shared.ingestion_events" in fetchrow_calls[0][1]

    async def test_second_fetchrow_targets_connectors_filtered_events(self) -> None:
        """Fallback lookup must query connectors.filtered_events."""
        from butlers.core.ingestion_events import ingestion_event_get

        pool = _FakePool(fetchrow_results=[None, None])
        await ingestion_event_get(pool, uuid.uuid4())
        fetchrow_calls = [c for c in pool.calls if c[0] == "fetchrow"]
        assert "connectors.filtered_events" in fetchrow_calls[1][1]

    async def test_returns_filtered_event_when_found_in_filtered_events(self) -> None:
        """When shared.ingestion_events misses but filtered_events has the row,
        the filtered event is returned with its real status and filter_reason."""
        from butlers.core.ingestion_events import ingestion_event_get

        filtered_row = _make_filtered_event_record(status="filtered", filter_reason="rate_limit")
        # First fetchrow (ingestion_events) → None; second (filtered_events) → filtered_row
        pool = _FakePool(fetchrow_results=[None, filtered_row])
        result = await ingestion_event_get(pool, uuid.uuid4())
        assert result is not None
        assert result["status"] == "filtered"
        assert result["filter_reason"] == "rate_limit"

    async def test_filtered_event_source_channel_present(self) -> None:
        """source_channel must be present in the filtered event result."""
        from butlers.core.ingestion_events import ingestion_event_get

        filtered_row = _make_filtered_event_record(source_channel="telegram")
        pool = _FakePool(fetchrow_results=[None, filtered_row])
        result = await ingestion_event_get(pool, uuid.uuid4())
        assert result is not None
        assert result["source_channel"] == "telegram"

    async def test_filtered_event_id_is_string(self) -> None:
        """The id in the filtered event result must be a string."""
        from butlers.core.ingestion_events import ingestion_event_get

        filtered_row = _make_filtered_event_record()
        pool = _FakePool(fetchrow_results=[None, filtered_row])
        result = await ingestion_event_get(pool, uuid.uuid4())
        assert result is not None
        assert isinstance(result["id"], str)

    async def test_filtered_event_all_expected_fields_present(self) -> None:
        """All unified-shape fields must be present for a filtered event."""
        from butlers.core.ingestion_events import ingestion_event_get

        filtered_row = _make_filtered_event_record()
        pool = _FakePool(fetchrow_results=[None, filtered_row])
        result = await ingestion_event_get(pool, uuid.uuid4())
        assert result is not None
        for field in (
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
        ):
            assert field in result, f"Missing field in filtered event result: {field}"

    async def test_no_second_fetchrow_when_found_in_ingestion_events(self) -> None:
        """When shared.ingestion_events returns a row, no fallback query is issued."""
        from butlers.core.ingestion_events import ingestion_event_get

        row = _make_event_record()
        pool = _FakePool(fetchrow_result=row)
        await ingestion_event_get(pool, uuid.uuid4())
        fetchrow_calls = [c for c in pool.calls if c[0] == "fetchrow"]
        assert len(fetchrow_calls) == 1, "Must not query filtered_events when ingestion_events hits"

    async def test_filtered_event_with_replay_pending_status(self) -> None:
        """Filtered events in replay_pending state are returned correctly."""
        from butlers.core.ingestion_events import ingestion_event_get

        filtered_row = _make_filtered_event_record(status="replay_pending", filter_reason="dedupe")
        pool = _FakePool(fetchrow_results=[None, filtered_row])
        result = await ingestion_event_get(pool, uuid.uuid4())
        assert result is not None
        assert result["status"] == "replay_pending"

    async def test_both_fetchrow_calls_pass_same_event_id(self) -> None:
        """Both queries must use the same event_id parameter."""
        from butlers.core.ingestion_events import ingestion_event_get

        event_id = uuid.uuid4()
        pool = _FakePool(fetchrow_results=[None, None])
        await ingestion_event_get(pool, event_id)
        fetchrow_calls = [c for c in pool.calls if c[0] == "fetchrow"]
        assert fetchrow_calls[0][2][0] == event_id
        assert fetchrow_calls[1][2][0] == event_id

    async def test_filtered_event_error_detail_propagated(self) -> None:
        """error_detail from connectors.filtered_events must be returned in the detail result."""
        from butlers.core.ingestion_events import ingestion_event_get

        filtered_row = _make_filtered_event_record(
            status="error", filter_reason=None, error_detail="Connection refused"
        )
        pool = _FakePool(fetchrow_results=[None, filtered_row])
        result = await ingestion_event_get(pool, uuid.uuid4())
        assert result is not None
        assert result["error_detail"] == "Connection refused"

    async def test_ingested_event_error_detail_is_none(self) -> None:
        """Ingested events must return error_detail=None (NULL::text from _INGESTED_COLS)."""
        from butlers.core.ingestion_events import ingestion_event_get

        row = _make_event_record()
        pool = _FakePool(fetchrow_result=row)
        result = await ingestion_event_get(pool, uuid.uuid4())
        assert result is not None
        assert result["error_detail"] is None


# ---------------------------------------------------------------------------
# ingestion_events_list
# ---------------------------------------------------------------------------


class TestIngestionEventsList:
    async def test_returns_empty_list_when_no_rows(self) -> None:
        from butlers.core.ingestion_events import ingestion_events_list

        pool = _FakePool(fetch_results=[])
        result = await ingestion_events_list(pool)
        assert result == []

    async def test_returns_list_of_dicts(self) -> None:
        from butlers.core.ingestion_events import ingestion_events_list

        rows = [
            _make_event_record(source_channel="email"),
            _make_event_record(source_channel="telegram"),
        ]
        pool = _FakePool(fetch_results=rows)
        result = await ingestion_events_list(pool)
        assert len(result) == 2
        channels = {r["source_channel"] for r in result}
        assert channels == {"email", "telegram"}

    async def test_default_limit_offset_passed_to_query(self) -> None:
        from butlers.core.ingestion_events import ingestion_events_list

        pool = _FakePool(fetch_results=[])
        await ingestion_events_list(pool)
        _, sql, args = pool.calls[0]
        # Default limit=20, offset=0 should be in args when no channel filter
        assert args == (20, 0)

    async def test_custom_limit_offset(self) -> None:
        from butlers.core.ingestion_events import ingestion_events_list

        pool = _FakePool(fetch_results=[])
        await ingestion_events_list(pool, limit=5, offset=10)
        _, sql, args = pool.calls[0]
        assert args == (5, 10)

    async def test_no_source_channel_filter_query_has_no_where(self) -> None:
        from butlers.core.ingestion_events import ingestion_events_list

        pool = _FakePool(fetch_results=[])
        await ingestion_events_list(pool)
        _, sql, _ = pool.calls[0]
        assert "WHERE" not in sql.upper() or "source_channel" not in sql

    async def test_source_channel_filter_adds_where_clause(self) -> None:
        from butlers.core.ingestion_events import ingestion_events_list

        pool = _FakePool(fetch_results=[])
        await ingestion_events_list(pool, source_channel="telegram")
        _, sql, args = pool.calls[0]
        assert "source_channel" in sql
        assert args[0] == "telegram"

    async def test_source_channel_filter_passes_channel_as_first_arg(self) -> None:
        from butlers.core.ingestion_events import ingestion_events_list

        pool = _FakePool(fetch_results=[])
        await ingestion_events_list(pool, limit=3, offset=1, source_channel="email")
        _, _, args = pool.calls[0]
        assert args[0] == "email"
        assert args[1] == 3  # limit
        assert args[2] == 1  # offset

    async def test_ordered_by_received_at_desc_in_sql(self) -> None:
        from butlers.core.ingestion_events import ingestion_events_list

        pool = _FakePool(fetch_results=[])
        await ingestion_events_list(pool)
        _, sql, _ = pool.calls[0]
        assert "received_at" in sql.lower()
        assert "desc" in sql.lower()

    async def test_queries_shared_ingestion_events_table(self) -> None:
        from butlers.core.ingestion_events import ingestion_events_list

        pool = _FakePool(fetch_results=[])
        await ingestion_events_list(pool)
        _, sql, _ = pool.calls[0]
        assert "shared.ingestion_events" in sql

    async def test_id_is_string_in_results(self) -> None:
        from butlers.core.ingestion_events import ingestion_events_list

        rows = [_make_event_record()]
        pool = _FakePool(fetch_results=rows)
        result = await ingestion_events_list(pool)
        assert isinstance(result[0]["id"], str)


# ---------------------------------------------------------------------------
# ingestion_events_count
# ---------------------------------------------------------------------------


class TestIngestionEventsCount:
    async def test_returns_zero_when_fetchval_returns_none(self) -> None:
        """fetchval returning None (empty table) should be normalised to 0."""
        from butlers.core.ingestion_events import ingestion_events_count

        pool = _FakePool(fetchval_result=None)
        result = await ingestion_events_count(pool)
        assert result == 0

    async def test_returns_integer_count(self) -> None:
        from butlers.core.ingestion_events import ingestion_events_count

        pool = _FakePool(fetchval_result=42)
        result = await ingestion_events_count(pool)
        assert result == 42

    async def test_no_status_queries_both_tables(self) -> None:
        """Without a status filter both shared.ingestion_events and connectors.filtered_events
        should be referenced in the SQL."""
        from butlers.core.ingestion_events import ingestion_events_count

        pool = _FakePool(fetchval_result=0)
        await ingestion_events_count(pool)
        assert pool.calls, "fetchval should have been called"
        _, sql, _ = pool.calls[0]
        assert "shared.ingestion_events" in sql
        assert "connectors.filtered_events" in sql

    async def test_no_status_no_channel_passes_no_args(self) -> None:
        """UNION ALL count without filters should need no bind args."""
        from butlers.core.ingestion_events import ingestion_events_count

        pool = _FakePool(fetchval_result=0)
        await ingestion_events_count(pool)
        _, _, args = pool.calls[0]
        assert args == ()

    async def test_no_status_with_channel_passes_channel_arg(self) -> None:
        """UNION ALL count with source_channel filter should pass channel as bind arg."""
        from butlers.core.ingestion_events import ingestion_events_count

        pool = _FakePool(fetchval_result=0)
        await ingestion_events_count(pool, source_channel="telegram")
        _, _, args = pool.calls[0]
        assert args == ("telegram",)

    async def test_status_ingested_queries_only_ingestion_events(self) -> None:
        from butlers.core.ingestion_events import ingestion_events_count

        pool = _FakePool(fetchval_result=5)
        await ingestion_events_count(pool, status="ingested")
        _, sql, _ = pool.calls[0]
        assert "shared.ingestion_events" in sql
        assert "connectors.filtered_events" not in sql

    async def test_status_ingested_with_channel(self) -> None:
        from butlers.core.ingestion_events import ingestion_events_count

        pool = _FakePool(fetchval_result=3)
        await ingestion_events_count(pool, status="ingested", source_channel="email")
        _, sql, args = pool.calls[0]
        assert "shared.ingestion_events" in sql
        assert "source_channel" in sql
        assert args == ("email",)

    async def test_status_filtered_queries_only_filtered_events(self) -> None:
        from butlers.core.ingestion_events import ingestion_events_count

        pool = _FakePool(fetchval_result=7)
        await ingestion_events_count(pool, status="filtered")
        _, sql, args = pool.calls[0]
        assert "connectors.filtered_events" in sql
        assert "shared.ingestion_events" not in sql
        assert "filtered" in args

    async def test_status_filtered_with_channel(self) -> None:
        from butlers.core.ingestion_events import ingestion_events_count

        pool = _FakePool(fetchval_result=2)
        await ingestion_events_count(pool, status="filtered", source_channel="telegram")
        _, sql, args = pool.calls[0]
        assert "connectors.filtered_events" in sql
        assert "source_channel" in sql
        assert "filtered" in args
        assert "telegram" in args

    async def test_status_error_queries_filtered_events(self) -> None:
        """status='error' (non-ingested) should query filtered_events."""
        from butlers.core.ingestion_events import ingestion_events_count

        pool = _FakePool(fetchval_result=1)
        await ingestion_events_count(pool, status="error")
        _, sql, args = pool.calls[0]
        assert "connectors.filtered_events" in sql
        assert "error" in args


# ---------------------------------------------------------------------------
# ingestion_event_sessions
# ---------------------------------------------------------------------------


class TestIngestionEventSessions:
    async def test_returns_empty_list_when_no_sessions(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_sessions

        db = _FakeDatabaseManager(results={})
        result = await ingestion_event_sessions(db, "req-001")
        assert result == []

    async def test_returns_sessions_with_butler_name(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_sessions

        row = _make_session_record()
        db = _FakeDatabaseManager(results={"atlas": [row]})
        result = await ingestion_event_sessions(db, "req-001")
        assert len(result) == 1
        assert result[0]["butler_name"] == "atlas"

    async def test_fan_out_called_with_request_id(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_sessions

        db = _FakeDatabaseManager(results={"atlas": []})
        await ingestion_event_sessions(db, "req-42")
        assert db.fan_out_calls, "fan_out must be called"
        _, args, _ = db.fan_out_calls[0]
        assert "req-42" in args

    async def test_sessions_query_filters_by_request_id(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_sessions

        db = _FakeDatabaseManager(results={"atlas": []})
        await ingestion_event_sessions(db, "some-request-id")
        sql, _, _ = db.fan_out_calls[0]
        assert "request_id" in sql

    async def test_merges_results_from_multiple_butlers(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_sessions

        row_a = _make_session_record()
        row_b = _make_session_record()
        db = _FakeDatabaseManager(results={"atlas": [row_a], "butler2": [row_b]})
        result = await ingestion_event_sessions(db, "req-001")
        assert len(result) == 2
        butler_names = {r["butler_name"] for r in result}
        assert butler_names == {"atlas", "butler2"}

    async def test_empty_butler_contributes_no_rows(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_sessions

        row = _make_session_record()
        db = _FakeDatabaseManager(results={"atlas": [row], "empty-butler": []})
        result = await ingestion_event_sessions(db, "req-001")
        assert len(result) == 1

    async def test_cost_jsonb_string_is_decoded(self) -> None:
        """cost field returned as JSON string should be decoded to dict."""
        import json as _json

        from butlers.core.ingestion_events import ingestion_event_sessions

        row = _make_session_record(cost=_json.dumps({"total_usd": 0.01}))
        db = _FakeDatabaseManager(results={"atlas": [row]})
        result = await ingestion_event_sessions(db, "req-001")
        assert isinstance(result[0]["cost"], dict)
        assert result[0]["cost"]["total_usd"] == 0.01

    async def test_cost_dict_passthrough(self) -> None:
        """cost field already a dict should pass through unchanged."""
        from butlers.core.ingestion_events import ingestion_event_sessions

        row = _make_session_record(cost={"total_usd": 0.02})
        db = _FakeDatabaseManager(results={"atlas": [row]})
        result = await ingestion_event_sessions(db, "req-001")
        assert result[0]["cost"] == {"total_usd": 0.02}

    async def test_session_fields_present(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_sessions

        row = _make_session_record()
        db = _FakeDatabaseManager(results={"atlas": [row]})
        result = await ingestion_event_sessions(db, "req-001")
        session = result[0]
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
            assert field in session, f"Missing field: {field}"

    async def test_results_sorted_by_started_at_asc(self) -> None:
        """Sessions should be ordered by started_at ascending."""
        from butlers.core.ingestion_events import ingestion_event_sessions

        earlier = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        later = datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)
        row_late = _make_session_record(started_at=later)
        row_early = _make_session_record(started_at=earlier)
        db = _FakeDatabaseManager(results={"atlas": [row_late, row_early]})
        result = await ingestion_event_sessions(db, "req-001")
        assert result[0]["started_at"] == earlier
        assert result[1]["started_at"] == later


# ---------------------------------------------------------------------------
# ingestion_event_rollup
# ---------------------------------------------------------------------------


class TestIngestionEventRollup:
    def test_empty_sessions_produces_zero_totals(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_rollup

        result = ingestion_event_rollup("req-001", [])
        assert result["total_sessions"] == 0
        assert result["total_input_tokens"] == 0
        assert result["total_output_tokens"] == 0
        assert result["total_cost"] == 0.0
        assert result["by_butler"] == {}

    def test_request_id_echoed_in_result(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_rollup

        result = ingestion_event_rollup("req-abc", [])
        assert result["request_id"] == "req-abc"

    def test_total_sessions_count(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_rollup

        sessions = [
            {
                "butler_name": "atlas",
                "input_tokens": 10,
                "output_tokens": 5,
                "cost": {"total_usd": 0.001},
            },
            {
                "butler_name": "atlas",
                "input_tokens": 20,
                "output_tokens": 10,
                "cost": {"total_usd": 0.002},
            },
        ]
        result = ingestion_event_rollup("req-001", sessions)
        assert result["total_sessions"] == 2

    def test_total_input_output_tokens_summed(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_rollup

        sessions = [
            {"butler_name": "atlas", "input_tokens": 100, "output_tokens": 50, "cost": None},
            {"butler_name": "atlas", "input_tokens": 200, "output_tokens": 75, "cost": None},
        ]
        result = ingestion_event_rollup("req-001", sessions)
        assert result["total_input_tokens"] == 300
        assert result["total_output_tokens"] == 125

    def test_total_cost_sums_total_usd(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_rollup

        sessions = [
            {
                "butler_name": "atlas",
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": {"total_usd": 0.005},
            },
            {
                "butler_name": "atlas",
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": {"total_usd": 0.010},
            },
        ]
        result = ingestion_event_rollup("req-001", sessions)
        assert abs(result["total_cost"] - 0.015) < 1e-9

    def test_null_cost_treated_as_zero(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_rollup

        sessions = [
            {"butler_name": "atlas", "input_tokens": 10, "output_tokens": 5, "cost": None},
        ]
        result = ingestion_event_rollup("req-001", sessions)
        assert result["total_cost"] == 0.0

    def test_cost_without_total_usd_key_treated_as_zero(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_rollup

        sessions = [
            {
                "butler_name": "atlas",
                "input_tokens": 10,
                "output_tokens": 5,
                "cost": {"other_key": 1.0},
            },
        ]
        result = ingestion_event_rollup("req-001", sessions)
        assert result["total_cost"] == 0.0

    def test_by_butler_breakdown_single_butler(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_rollup

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
                "cost": {"total_usd": 0.010},
            },
        ]
        result = ingestion_event_rollup("req-001", sessions)
        assert "atlas" in result["by_butler"]
        entry = result["by_butler"]["atlas"]
        assert entry["sessions"] == 2
        assert entry["input_tokens"] == 300
        assert entry["output_tokens"] == 125
        assert abs(entry["cost"] - 0.015) < 1e-9

    def test_by_butler_breakdown_multiple_butlers(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_rollup

        sessions = [
            {
                "butler_name": "atlas",
                "input_tokens": 100,
                "output_tokens": 50,
                "cost": {"total_usd": 0.005},
            },
            {
                "butler_name": "herald",
                "input_tokens": 50,
                "output_tokens": 25,
                "cost": {"total_usd": 0.002},
            },
        ]
        result = ingestion_event_rollup("req-001", sessions)
        assert set(result["by_butler"].keys()) == {"atlas", "herald"}
        assert result["by_butler"]["atlas"]["sessions"] == 1
        assert result["by_butler"]["herald"]["sessions"] == 1
        assert result["total_sessions"] == 2

    def test_null_input_output_tokens_treated_as_zero(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_rollup

        sessions = [
            {"butler_name": "atlas", "input_tokens": None, "output_tokens": None, "cost": None},
        ]
        result = ingestion_event_rollup("req-001", sessions)
        assert result["total_input_tokens"] == 0
        assert result["total_output_tokens"] == 0

    def test_missing_tokens_fields_treated_as_zero(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_rollup

        # Sessions that have no input_tokens / output_tokens keys at all
        sessions = [{"butler_name": "atlas", "cost": None}]
        result = ingestion_event_rollup("req-001", sessions)
        assert result["total_input_tokens"] == 0
        assert result["total_output_tokens"] == 0

    def test_by_butler_cost_zero_when_no_cost(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_rollup

        sessions = [
            {"butler_name": "atlas", "input_tokens": 10, "output_tokens": 5, "cost": None},
        ]
        result = ingestion_event_rollup("req-001", sessions)
        assert result["by_butler"]["atlas"]["cost"] == 0.0

    def test_total_cost_is_float(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_rollup

        sessions = [
            {
                "butler_name": "atlas",
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": {"total_usd": 0.001},
            },
        ]
        result = ingestion_event_rollup("req-001", sessions)
        assert isinstance(result["total_cost"], float)

    def test_total_cost_usd_as_string_is_handled(self) -> None:
        """total_usd stored as string should be cast to float gracefully."""
        from butlers.core.ingestion_events import ingestion_event_rollup

        sessions = [
            {
                "butler_name": "atlas",
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": {"total_usd": "0.005"},
            },
        ]
        result = ingestion_event_rollup("req-001", sessions)
        assert abs(result["total_cost"] - 0.005) < 1e-9

    def test_result_has_all_expected_keys(self) -> None:
        from butlers.core.ingestion_events import ingestion_event_rollup

        result = ingestion_event_rollup("req-001", [])
        for key in (
            "request_id",
            "total_sessions",
            "total_input_tokens",
            "total_output_tokens",
            "total_cost",
            "by_butler",
        ):
            assert key in result, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# ingestion_event_replay_request
# ---------------------------------------------------------------------------


class TestIngestionEventReplayRequest:
    """Tests for the atomic replay transition in ingestion_event_replay_request.

    The implementation uses a single UPDATE … WHERE status = ANY(replayable) RETURNING id
    to avoid TOCTOU races.  A follow-up SELECT is only issued on the miss path.
    """

    async def test_ok_outcome_when_update_returns_row(self) -> None:
        """UPDATE RETURNING id succeeds → outcome='ok' with the event id."""
        from butlers.core.ingestion_events import ingestion_event_replay_request

        event_id = uuid.uuid4()
        returning_row = _FakeRecord({"id": event_id})
        pool = _FakePool(fetchrow_result=returning_row)
        result = await ingestion_event_replay_request(pool, event_id)
        assert result["outcome"] == "ok"
        assert result["id"] == str(event_id)

    async def test_ok_id_is_string(self) -> None:
        """The id in the ok result should be a string."""
        from butlers.core.ingestion_events import ingestion_event_replay_request

        event_id = uuid.uuid4()
        returning_row = _FakeRecord({"id": event_id})
        pool = _FakePool(fetchrow_result=returning_row)
        result = await ingestion_event_replay_request(pool, event_id)
        assert isinstance(result["id"], str)

    async def test_ok_accepts_string_event_id(self) -> None:
        """String event_id should be accepted and converted to UUID."""
        from butlers.core.ingestion_events import ingestion_event_replay_request

        event_id = uuid.uuid4()
        returning_row = _FakeRecord({"id": event_id})
        pool = _FakePool(fetchrow_result=returning_row)
        result = await ingestion_event_replay_request(pool, str(event_id))
        assert result["outcome"] == "ok"

    async def test_update_uses_fetchrow_not_execute(self) -> None:
        """The atomic UPDATE must use fetchrow (RETURNING), not pool.execute."""
        from butlers.core.ingestion_events import ingestion_event_replay_request

        event_id = uuid.uuid4()
        returning_row = _FakeRecord({"id": event_id})
        pool = _FakePool(fetchrow_result=returning_row)
        await ingestion_event_replay_request(pool, event_id)
        methods_used = [method for method, _, _ in pool.calls]
        # On the happy path only one call: fetchrow for the UPDATE RETURNING
        assert methods_used == ["fetchrow"], f"Unexpected calls: {methods_used}"

    async def test_update_sql_contains_returning(self) -> None:
        """The UPDATE statement must contain RETURNING to be atomic."""
        from butlers.core.ingestion_events import ingestion_event_replay_request

        event_id = uuid.uuid4()
        returning_row = _FakeRecord({"id": event_id})
        pool = _FakePool(fetchrow_result=returning_row)
        await ingestion_event_replay_request(pool, event_id)
        _, sql, _ = pool.calls[0]
        assert "RETURNING" in sql.upper()

    async def test_update_sql_uses_any_for_status_filter(self) -> None:
        """The UPDATE WHERE clause must use ANY($) to filter replayable statuses."""
        from butlers.core.ingestion_events import ingestion_event_replay_request

        event_id = uuid.uuid4()
        returning_row = _FakeRecord({"id": event_id})
        pool = _FakePool(fetchrow_result=returning_row)
        await ingestion_event_replay_request(pool, event_id)
        _, sql, args = pool.calls[0]
        assert "ANY" in sql.upper()
        # The replayable list must be passed as a query parameter
        replayable_arg = None
        for arg in args:
            if isinstance(arg, list):
                replayable_arg = arg
                break
        assert replayable_arg is not None, "Expected a list argument for ANY($)"
        assert set(replayable_arg) == {"filtered", "error", "replay_failed"}

    async def test_update_targets_connectors_filtered_events(self) -> None:
        """The UPDATE must target connectors.filtered_events."""
        from butlers.core.ingestion_events import ingestion_event_replay_request

        event_id = uuid.uuid4()
        returning_row = _FakeRecord({"id": event_id})
        pool = _FakePool(fetchrow_result=returning_row)
        await ingestion_event_replay_request(pool, event_id)
        _, sql, _ = pool.calls[0]
        assert "connectors.filtered_events" in sql

    async def test_not_found_when_update_miss_and_no_row(self) -> None:
        """UPDATE returns no row AND follow-up SELECT also returns None → not_found."""
        from butlers.core.ingestion_events import ingestion_event_replay_request

        event_id = uuid.uuid4()
        # fetchrow returns None (UPDATE matched nothing)
        # fetchval also returns None (row doesn't exist)
        pool = _FakePool(fetchrow_result=None, fetchval_result=None)
        result = await ingestion_event_replay_request(pool, event_id)
        assert result["outcome"] == "not_found"

    async def test_not_found_only_one_fetchval_on_miss(self) -> None:
        """On the miss path a single fetchval SELECT determines not_found."""
        from butlers.core.ingestion_events import ingestion_event_replay_request

        event_id = uuid.uuid4()
        pool = _FakePool(fetchrow_result=None, fetchval_result=None)
        await ingestion_event_replay_request(pool, event_id)
        fetchval_calls = [c for c in pool.calls if c[0] == "fetchval"]
        assert len(fetchval_calls) == 1

    async def test_conflict_when_update_miss_and_row_exists(self) -> None:
        """UPDATE misses (status not replayable) AND SELECT returns current status → conflict."""
        from butlers.core.ingestion_events import ingestion_event_replay_request

        event_id = uuid.uuid4()
        # fetchrow returns None (UPDATE missed because status is replay_pending)
        # fetchval returns the current non-replayable status
        pool = _FakePool(fetchrow_result=None, fetchval_result="replay_pending")
        result = await ingestion_event_replay_request(pool, event_id)
        assert result["outcome"] == "conflict"
        assert result["current_status"] == "replay_pending"

    async def test_conflict_includes_current_status(self) -> None:
        """Conflict response must include current_status for the caller."""
        from butlers.core.ingestion_events import ingestion_event_replay_request

        event_id = uuid.uuid4()
        pool = _FakePool(fetchrow_result=None, fetchval_result="replay_complete")
        result = await ingestion_event_replay_request(pool, event_id)
        assert result["outcome"] == "conflict"
        assert result["current_status"] == "replay_complete"

    async def test_no_fetchval_on_success_path(self) -> None:
        """When UPDATE RETURNING succeeds there must be no follow-up SELECT."""
        from butlers.core.ingestion_events import ingestion_event_replay_request

        event_id = uuid.uuid4()
        returning_row = _FakeRecord({"id": event_id})
        pool = _FakePool(fetchrow_result=returning_row)
        await ingestion_event_replay_request(pool, event_id)
        fetchval_calls = [c for c in pool.calls if c[0] == "fetchval"]
        assert fetchval_calls == [], "fetchval must NOT be called when UPDATE RETURNING succeeds"

    async def test_miss_path_select_queries_by_event_id(self) -> None:
        """The miss-path SELECT must filter by the correct event_id."""
        from butlers.core.ingestion_events import ingestion_event_replay_request

        event_id = uuid.uuid4()
        pool = _FakePool(fetchrow_result=None, fetchval_result=None)
        await ingestion_event_replay_request(pool, event_id)
        fetchval_calls = [(sql, args) for method, sql, args in pool.calls if method == "fetchval"]
        assert fetchval_calls, "Expected at least one fetchval call"
        sql, args = fetchval_calls[0]
        assert args[0] == event_id

    async def test_invalid_uuid_raises_value_error(self) -> None:
        """Passing a non-UUID string should raise ValueError."""
        from butlers.core.ingestion_events import ingestion_event_replay_request

        pool = _FakePool()
        with pytest.raises(ValueError):
            await ingestion_event_replay_request(pool, "not-a-uuid")
