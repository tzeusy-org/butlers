"""Tests for FilteredEventBuffer — accumulation, flush, reason helpers, crash safety."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.filtered_event_buffer import (
    _INSERT_SQL,
    FilteredEventBuffer,
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
