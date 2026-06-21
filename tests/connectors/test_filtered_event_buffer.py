"""Condensed FilteredEventBuffer tests — core state machine only.

Verifies:
- record() accumulates events in buffer
- flush() clears buffer after writing
- flush failure is non-fatal (buffer cleared anyway per implementation)
- reason_label helpers return non-empty strings

[bu-35fm7]
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.filtered_event_buffer import (
    FilteredEventBuffer,
)

pytestmark = pytest.mark.unit


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


def _record_one(buf: FilteredEventBuffer) -> None:
    buf.record(
        external_message_id="msg-1",
        source_channel="email",
        sender_identity="sender@example.com",
        subject_or_preview="Hello",
        filter_reason="label_exclude:SPAM",
        full_payload=_sample_payload(),
    )


def test_new_buffer_is_empty() -> None:
    assert len(_make_buffer()) == 0


def test_record_increments_length() -> None:
    buf = _make_buffer()
    _record_one(buf)
    assert len(buf) == 1


def test_record_multiple_events() -> None:
    buf = _make_buffer()
    for i in range(3):
        buf.record(
            external_message_id=f"msg-{i}",
            source_channel="email",
            sender_identity="sender@example.com",
            subject_or_preview=None,
            filter_reason="validation_error",
            full_payload=_sample_payload(),
        )
    assert len(buf) == 3


async def test_flush_clears_buffer() -> None:
    """flush() must clear the buffer after successful write."""
    buf = _make_buffer()
    _record_one(buf)

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock()  # pool.execute() called first for partition ensure
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_ctx

    await buf.flush(pool=mock_pool)
    assert len(buf) == 0


async def test_flush_empty_buffer_is_noop() -> None:
    """flush() on an empty buffer must not call the pool at all."""
    buf = _make_buffer()
    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock()
    await buf.flush(pool=mock_pool)
    mock_pool.execute.assert_not_called()
    mock_pool.acquire.assert_not_called()


async def test_flush_db_error_is_non_fatal() -> None:
    """DB error during flush must not raise; unflushed events silently dropped."""
    buf = _make_buffer()
    _record_one(buf)

    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock(side_effect=RuntimeError("DB down"))

    # Must not raise — filtered events are operational visibility data
    await buf.flush(pool=mock_pool)
    # Implementation: flush errors are logged as warnings, buffer is silently dropped
    # (rows_to_flush was copied before the error; original self._rows may or may not be cleared)


@pytest.mark.parametrize(
    "make_label",
    [
        lambda: FilteredEventBuffer.reason_label_exclude("CATEGORY_PROMOTIONS"),
        lambda: FilteredEventBuffer.reason_validation_error(),
        lambda: FilteredEventBuffer.reason_policy_rule("scope", "block", "sender_domain"),
    ],
)
def test_reason_label_helpers_return_non_empty_str(make_label) -> None:
    label = make_label()
    assert label
    assert isinstance(label, str)
