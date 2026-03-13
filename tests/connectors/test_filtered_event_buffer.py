"""Tests for FilteredEventBuffer — accumulation, flush, reason helpers, crash safety.

Also tests for the standalone drain_replay_pending helper.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.filtered_event_buffer import (
    _INSERT_SQL,
    _REPLAY_SELECT_SQL,
    _REPLAY_UPDATE_SQL,
    FilteredEventBuffer,
    drain_replay_pending,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_pool() -> MagicMock:
    """Create a mock asyncpg pool with acquire() and execute() as async methods."""
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_ctx
    return mock_pool


def _make_buffer(
    connector_type: str = "gmail",
    endpoint_identity: str = "gmail:user:alice@example.com",
) -> FilteredEventBuffer:
    return FilteredEventBuffer(
        connector_type=connector_type,
        endpoint_identity=endpoint_identity,
    )


def _sample_payload() -> dict:
    return FilteredEventBuffer.full_payload(
        channel="email",
        provider="gmail",
        endpoint_identity="gmail:user:alice@example.com",
        external_event_id="msg-001",
        external_thread_id="thread-001",
        observed_at="2026-03-11T10:00:00Z",
        sender_identity="sender@example.com",
        raw={"headers": [], "body": "Hello"},
        normalized_text="Hello",
        policy_tier="full",
    )


# ---------------------------------------------------------------------------
# Accumulation tests
# ---------------------------------------------------------------------------


class TestAccumulation:
    """Buffer accumulates events without touching the database."""

    def test_new_buffer_is_empty(self) -> None:
        buf = _make_buffer()
        assert len(buf) == 0

    def test_record_single_event(self) -> None:
        buf = _make_buffer()
        buf.record(
            external_message_id="msg-1",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview="Hello",
            filter_reason=FilteredEventBuffer.reason_label_exclude("CATEGORY_PROMOTIONS"),
            full_payload=_sample_payload(),
        )
        assert len(buf) == 1

    def test_record_multiple_events(self) -> None:
        buf = _make_buffer()
        for i in range(5):
            buf.record(
                external_message_id=f"msg-{i}",
                source_channel="email",
                sender_identity="sender@example.com",
                subject_or_preview=None,
                filter_reason=FilteredEventBuffer.reason_validation_error(),
                full_payload=_sample_payload(),
                status="error",
            )
        assert len(buf) == 5

    def test_record_uses_utc_now_by_default(self) -> None:
        buf = _make_buffer()
        before = datetime.now(UTC)
        buf.record(
            external_message_id="msg-ts",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview=None,
            filter_reason="validation_error",
            full_payload=_sample_payload(),
        )
        after = datetime.now(UTC)
        # Extract the stored timestamp from the internal row
        ts = buf._rows[0][0]
        assert before <= ts <= after

    def test_record_uses_provided_received_at(self) -> None:
        buf = _make_buffer()
        fixed_ts = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        buf.record(
            external_message_id="msg-fixed",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview=None,
            filter_reason="label_exclude:SPAM",
            full_payload=_sample_payload(),
            received_at=fixed_ts,
        )
        assert buf._rows[0][0] == fixed_ts

    def test_record_full_payload_serialized_to_json(self) -> None:
        buf = _make_buffer()
        payload = _sample_payload()
        buf.record(
            external_message_id="msg-json",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview=None,
            filter_reason="submission_error",
            full_payload=payload,
        )
        stored_json = buf._rows[0][9]  # full_payload column index
        assert json.loads(stored_json) == payload


# ---------------------------------------------------------------------------
# Flush tests
# ---------------------------------------------------------------------------


class TestFlush:
    """Flush issues a single batch INSERT and clears the buffer."""

    async def test_flush_empty_buffer_is_noop(self) -> None:
        buf = _make_buffer()
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value

        await buf.flush(pool)

        conn.executemany.assert_not_awaited()
        assert len(buf) == 0

    async def test_flush_calls_executemany_with_correct_sql(self) -> None:
        buf = _make_buffer()
        buf.record(
            external_message_id="msg-1",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview="Subject",
            filter_reason=FilteredEventBuffer.reason_label_exclude("SPAM"),
            full_payload=_sample_payload(),
        )
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value

        await buf.flush(pool)

        conn.executemany.assert_awaited_once()
        call_args = conn.executemany.call_args
        assert call_args[0][0] == _INSERT_SQL

    async def test_flush_passes_all_rows_in_single_call(self) -> None:
        buf = _make_buffer()
        for i in range(3):
            buf.record(
                external_message_id=f"msg-{i}",
                source_channel="email",
                sender_identity="sender@example.com",
                subject_or_preview=None,
                filter_reason=FilteredEventBuffer.reason_validation_error(),
                full_payload=_sample_payload(),
                status="error",
            )
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value

        await buf.flush(pool)

        # Only one executemany call, not three individual execute calls
        conn.executemany.assert_awaited_once()
        rows_arg = conn.executemany.call_args[0][1]
        assert len(rows_arg) == 3

    async def test_flush_clears_buffer_after_success(self) -> None:
        buf = _make_buffer()
        buf.record(
            external_message_id="msg-1",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview=None,
            filter_reason="label_exclude:X",
            full_payload=_sample_payload(),
        )
        pool = _make_mock_pool()

        await buf.flush(pool)

        assert len(buf) == 0

    async def test_flush_row_contains_correct_connector_metadata(self) -> None:
        buf = FilteredEventBuffer(
            connector_type="telegram_bot",
            endpoint_identity="telegram:bot:123456",
        )
        buf.record(
            external_message_id="update-999",
            source_channel="chat",
            sender_identity="telegram:user:555",
            subject_or_preview=None,
            filter_reason=FilteredEventBuffer.reason_policy_rule(
                "global_rule", "skip", "sender_domain"
            ),
            full_payload=_sample_payload(),
        )
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value

        await buf.flush(pool)

        rows = conn.executemany.call_args[0][1]
        row = rows[0]
        # column order: received_at, connector_type, endpoint_identity,
        #               external_message_id, source_channel, sender_identity,
        #               subject_or_preview, filter_reason, status, full_payload, error_detail
        assert row[1] == "telegram_bot"
        assert row[2] == "telegram:bot:123456"
        assert row[3] == "update-999"
        assert row[4] == "chat"
        assert row[5] == "telegram:user:555"
        assert row[6] is None  # subject_or_preview
        assert row[7] == "global_rule:skip:sender_domain"
        assert row[8] == "filtered"
        assert row[10] is None  # error_detail

    async def test_flush_error_row_has_correct_status_and_detail(self) -> None:
        buf = _make_buffer()
        buf.record(
            external_message_id="bad-msg",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview=None,
            filter_reason=FilteredEventBuffer.reason_submission_error(),
            full_payload=_sample_payload(),
            status="error",
            error_detail="HTTPStatusError: 503 Service Unavailable",
        )
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value

        await buf.flush(pool)

        rows = conn.executemany.call_args[0][1]
        row = rows[0]
        assert row[8] == "error"
        assert row[10] == "HTTPStatusError: 503 Service Unavailable"

    async def test_flush_calls_ensure_partition_before_insert(self) -> None:
        """flush() calls connectors_filtered_events_ensure_partition via pool.execute."""
        buf = _make_buffer()
        buf.record(
            external_message_id="msg-1",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview=None,
            filter_reason="label_exclude:X",
            full_payload=_sample_payload(),
        )
        pool = _make_mock_pool()

        await buf.flush(pool)

        pool.execute.assert_awaited_once()
        sql_arg = pool.execute.call_args[0][0]
        assert "connectors.connectors_filtered_events_ensure_partition" in sql_arg

    async def test_flush_empty_does_not_call_ensure_partition(self) -> None:
        """flush() on an empty buffer is a no-op — ensure_partition is not called."""
        buf = _make_buffer()
        pool = _make_mock_pool()

        await buf.flush(pool)

        pool.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# Flush failure / crash-safety tests
# ---------------------------------------------------------------------------


class TestFlushFailure:
    """Flush failures are logged as warnings and do not raise."""

    async def test_flush_failure_does_not_raise(self) -> None:
        buf = _make_buffer()
        buf.record(
            external_message_id="msg-1",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview=None,
            filter_reason="label_exclude:X",
            full_payload=_sample_payload(),
        )
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.executemany.side_effect = RuntimeError("DB connection refused")

        # Must not raise
        await buf.flush(pool)

    async def test_flush_ensure_partition_failure_does_not_raise(self) -> None:
        """If ensure_partition fails, flush does not raise and events are dropped."""
        buf = _make_buffer()
        buf.record(
            external_message_id="msg-1",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview=None,
            filter_reason="label_exclude:X",
            full_payload=_sample_payload(),
        )
        pool = _make_mock_pool()
        pool.execute.side_effect = RuntimeError("permission denied for schema connectors")

        # Must not raise
        await buf.flush(pool)

    async def test_flush_failure_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        buf = _make_buffer()
        buf.record(
            external_message_id="msg-warn",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview=None,
            filter_reason="validation_error",
            full_payload=_sample_payload(),
        )
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.executemany.side_effect = OSError("network error")

        with caplog.at_level(logging.WARNING, logger="butlers.connectors.filtered_event_buffer"):
            await buf.flush(pool)

        assert any("filtered events" in rec.message for rec in caplog.records)
        assert any(rec.levelno == logging.WARNING for rec in caplog.records)

    async def test_flush_failure_buffer_not_cleared(self) -> None:
        """When flush fails the buffer is NOT cleared (rows remain)."""
        buf = _make_buffer()
        buf.record(
            external_message_id="msg-1",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview=None,
            filter_reason="submission_error",
            full_payload=_sample_payload(),
        )
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.executemany.side_effect = RuntimeError("DB down")

        await buf.flush(pool)

        # The internal rows list reflects what was attempted; the caller can
        # decide whether to retry.  The important thing is flush did not raise.
        # Behaviour here: rows are retained since flush failed before clear.
        # (Implementation clears only on success.)
        assert len(buf) == 1


# ---------------------------------------------------------------------------
# Filter-reason helper tests
# ---------------------------------------------------------------------------


class TestFilterReasonHelpers:
    """Static helper methods produce correctly formatted reason strings."""

    def test_reason_label_exclude(self) -> None:
        assert FilteredEventBuffer.reason_label_exclude("CATEGORY_PROMOTIONS") == (
            "label_exclude:CATEGORY_PROMOTIONS"
        )

    def test_reason_label_exclude_arbitrary_label(self) -> None:
        assert FilteredEventBuffer.reason_label_exclude("SPAM") == "label_exclude:SPAM"

    def test_reason_policy_rule_global_skip(self) -> None:
        assert (
            FilteredEventBuffer.reason_policy_rule("global_rule", "skip", "sender_domain")
            == "global_rule:skip:sender_domain"
        )

    def test_reason_policy_rule_connector_block(self) -> None:
        assert (
            FilteredEventBuffer.reason_policy_rule("connector_rule", "block", "subject_pattern")
            == "connector_rule:block:subject_pattern"
        )

    def test_reason_validation_error(self) -> None:
        assert FilteredEventBuffer.reason_validation_error() == "validation_error"

    def test_reason_submission_error(self) -> None:
        assert FilteredEventBuffer.reason_submission_error() == "submission_error"


# ---------------------------------------------------------------------------
# full_payload helper tests
# ---------------------------------------------------------------------------


class TestFullPayloadHelper:
    """full_payload builds correctly structured envelope dicts."""

    def test_full_payload_keys_present(self) -> None:
        p = FilteredEventBuffer.full_payload(
            channel="email",
            provider="gmail",
            endpoint_identity="gmail:user:x@example.com",
            external_event_id="msg-1",
            external_thread_id="thread-1",
            observed_at="2026-03-11T10:00:00Z",
            sender_identity="sender@example.com",
            raw={"key": "value"},
        )
        assert set(p.keys()) == {"source", "event", "sender", "payload", "control"}

    def test_full_payload_no_schema_version(self) -> None:
        p = _sample_payload()
        assert "schema_version" not in p

    def test_full_payload_source_structure(self) -> None:
        p = FilteredEventBuffer.full_payload(
            channel="chat",
            provider="telegram_bot",
            endpoint_identity="telegram:bot:123",
            external_event_id="upd-1",
            external_thread_id=None,
            observed_at="2026-03-11T10:00:00Z",
            sender_identity="telegram:user:456",
            raw={"text": "hi"},
        )
        assert p["source"] == {
            "channel": "chat",
            "provider": "telegram_bot",
            "endpoint_identity": "telegram:bot:123",
        }

    def test_full_payload_event_structure(self) -> None:
        p = FilteredEventBuffer.full_payload(
            channel="email",
            provider="gmail",
            endpoint_identity="gmail:user:alice@example.com",
            external_event_id="msg-abc",
            external_thread_id="thread-xyz",
            observed_at="2026-03-11T10:00:00Z",
            sender_identity="sender@example.com",
            raw={},
        )
        assert p["event"] == {
            "external_event_id": "msg-abc",
            "external_thread_id": "thread-xyz",
            "observed_at": "2026-03-11T10:00:00Z",
        }

    def test_full_payload_optional_fields_default_none(self) -> None:
        p = FilteredEventBuffer.full_payload(
            channel="email",
            provider="gmail",
            endpoint_identity="gmail:user:x@example.com",
            external_event_id="msg-1",
            external_thread_id=None,
            observed_at="2026-03-11T10:00:00Z",
            sender_identity="sender@example.com",
            raw={},
        )
        assert p["payload"]["normalized_text"] is None
        assert p["control"]["policy_tier"] is None

    def test_full_payload_values_are_json_serializable(self) -> None:
        p = _sample_payload()
        # Should not raise
        serialized = json.dumps(p)
        assert isinstance(serialized, str)


# ---------------------------------------------------------------------------
# drain_replay_pending helper tests
# ---------------------------------------------------------------------------


def _make_mock_pool_with_transaction(rows: list) -> tuple[MagicMock, AsyncMock, AsyncMock]:
    """Return (pool, conn, tx_ctx) mocks wired to yield *rows* from fetch()."""
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=rows)
    mock_conn.execute = AsyncMock()

    # Transaction context manager
    mock_tx = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=None)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction = MagicMock(return_value=mock_tx)

    # Acquire context manager
    mock_acquire_ctx = AsyncMock()
    mock_acquire_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_acquire_ctx)

    return mock_pool, mock_conn, mock_tx


def _make_row(
    row_id: int = 1,
    external_message_id: str = "msg-001",
    full_payload: str | dict | None = None,
) -> MagicMock:
    """Build a mock asyncpg Record-like row."""
    if full_payload is None:
        full_payload = json.dumps(
            {
                "source": {"channel": "email", "provider": "gmail", "endpoint_identity": "x"},
                "event": {
                    "external_event_id": "msg-001",
                    "external_thread_id": None,
                    "observed_at": "2026-03-11T10:00:00Z",
                },
                "sender": {"identity": "sender@example.com"},
                "payload": {"raw": {}, "normalized_text": None},
                "control": {"policy_tier": None},
            }
        )
    row = MagicMock()
    row.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "id": row_id,
            "received_at": datetime(2026, 3, 11, 10, 0, 0, tzinfo=UTC),
            "external_message_id": external_message_id,
            "full_payload": full_payload,
        }[key]
    )
    return row


class TestDrainReplayPending:
    """Tests for the shared drain_replay_pending helper function."""

    async def test_noop_when_no_rows(self) -> None:
        pool, conn, _ = _make_mock_pool_with_transaction([])
        submit_fn = AsyncMock()

        await drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", submit_fn)

        submit_fn.assert_not_awaited()
        conn.execute.assert_not_awaited()

    async def test_submits_envelope_with_schema_version(self) -> None:
        row = _make_row()
        pool, conn, _ = _make_mock_pool_with_transaction([row])
        submit_fn = AsyncMock()

        await drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", submit_fn)

        submit_fn.assert_awaited_once()
        envelope = submit_fn.call_args[0][0]
        assert envelope["schema_version"] == "ingest.v1"
        # Original payload fields are preserved
        assert "source" in envelope

    async def test_marks_row_replay_complete_on_success(self) -> None:
        row = _make_row(row_id=42)
        pool, conn, _ = _make_mock_pool_with_transaction([row])
        submit_fn = AsyncMock()

        await drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", submit_fn)

        conn.execute.assert_awaited_once()
        args = conn.execute.call_args[0]
        assert args[0] == _REPLAY_UPDATE_SQL
        assert args[1] == "replay_complete"
        assert args[2] is None  # error_detail
        assert args[3] == 42  # row_id

    async def test_marks_row_replay_failed_on_submit_error(self) -> None:
        row = _make_row(row_id=7, external_message_id="bad-msg")
        pool, conn, _ = _make_mock_pool_with_transaction([row])
        submit_fn = AsyncMock(side_effect=RuntimeError("ingest down"))

        await drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", submit_fn)

        conn.execute.assert_awaited_once()
        args = conn.execute.call_args[0]
        assert args[0] == _REPLAY_UPDATE_SQL
        assert args[1] == "replay_failed"
        assert "ingest down" in args[2]
        assert args[3] == 7

    async def test_marks_row_replay_failed_on_json_parse_error(self) -> None:
        row = _make_row(row_id=99, full_payload="not-valid-json{{{")
        pool, conn, _ = _make_mock_pool_with_transaction([row])
        submit_fn = AsyncMock()

        await drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", submit_fn)

        # JSON parse failure → replay_failed, submit never called
        submit_fn.assert_not_awaited()
        conn.execute.assert_awaited_once()
        args = conn.execute.call_args[0]
        assert args[1] == "replay_failed"

    async def test_processes_multiple_rows(self) -> None:
        rows = [_make_row(row_id=i, external_message_id=f"msg-{i}") for i in range(3)]
        pool, conn, _ = _make_mock_pool_with_transaction(rows)
        submit_fn = AsyncMock()

        await drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", submit_fn)

        assert submit_fn.await_count == 3
        assert conn.execute.await_count == 3

    async def test_continues_processing_after_one_row_failure(self) -> None:
        """A failure on one row must not abort processing of subsequent rows."""
        row_ok = _make_row(row_id=1, external_message_id="msg-ok")
        row_bad = _make_row(row_id=2, external_message_id="msg-bad", full_payload="bad{json")
        row_ok2 = _make_row(row_id=3, external_message_id="msg-ok2")
        pool, conn, _ = _make_mock_pool_with_transaction([row_ok, row_bad, row_ok2])
        submit_fn = AsyncMock()

        await drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", submit_fn)

        # submit called for the two valid rows only
        assert submit_fn.await_count == 2
        # execute called for all three rows (2 complete + 1 failed)
        assert conn.execute.await_count == 3

    async def test_uses_correct_select_sql_and_params(self) -> None:
        pool, conn, _ = _make_mock_pool_with_transaction([])

        await drain_replay_pending(pool, "telegram_bot", "tg:bot:123", AsyncMock())

        conn.fetch.assert_awaited_once_with(_REPLAY_SELECT_SQL, "telegram_bot", "tg:bot:123")

    async def test_outer_db_error_does_not_raise(self) -> None:
        mock_pool = MagicMock()
        mock_acquire_ctx = AsyncMock()
        mock_acquire_ctx.__aenter__ = AsyncMock(side_effect=OSError("DB unreachable"))
        mock_acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_pool.acquire = MagicMock(return_value=mock_acquire_ctx)
        submit_fn = AsyncMock()

        # Should not raise — outer exception is swallowed with a warning log
        await drain_replay_pending(mock_pool, "gmail", "gmail:user:x@example.com", submit_fn)

    async def test_outer_db_error_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        mock_pool = MagicMock()
        mock_acquire_ctx = AsyncMock()
        mock_acquire_ctx.__aenter__ = AsyncMock(side_effect=OSError("DB unreachable"))
        mock_acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_pool.acquire = MagicMock(return_value=mock_acquire_ctx)

        with caplog.at_level(logging.WARNING, logger="butlers.connectors.filtered_event_buffer"):
            await drain_replay_pending(mock_pool, "gmail", "x", AsyncMock())

        assert any("replay_pending" in rec.message for rec in caplog.records)
        assert any(rec.levelno == logging.WARNING for rec in caplog.records)

    async def test_accepts_dict_full_payload(self) -> None:
        """asyncpg may return JSONB as a dict — the helper handles both str and dict."""
        payload_dict = {
            "source": {"channel": "email", "provider": "gmail", "endpoint_identity": "x"},
            "event": {
                "external_event_id": "msg-1",
                "external_thread_id": None,
                "observed_at": "2026-03-11T10:00:00Z",
            },
            "sender": {"identity": "s@example.com"},
            "payload": {"raw": {}, "normalized_text": None},
            "control": {"policy_tier": None},
        }
        row = _make_row(full_payload=payload_dict)  # dict, not str
        pool, conn, _ = _make_mock_pool_with_transaction([row])
        submit_fn = AsyncMock()

        await drain_replay_pending(pool, "gmail", "gmail:user:alice@example.com", submit_fn)

        submit_fn.assert_awaited_once()
        envelope = submit_fn.call_args[0][0]
        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["source"] == payload_dict["source"]

    async def test_custom_logger_is_used(self, caplog: pytest.LogCaptureFixture) -> None:
        row = _make_row()
        pool, conn, _ = _make_mock_pool_with_transaction([row])
        submit_fn = AsyncMock()
        custom_logger = logging.getLogger("custom.connector.logger")

        with caplog.at_level(logging.DEBUG, logger="custom.connector.logger"):
            await drain_replay_pending(pool, "gmail", "x", submit_fn, custom_logger)

        assert any("custom.connector.logger" == rec.name for rec in caplog.records)
