"""FilteredEventBuffer.park writes a failed submission at once, or raises (bu-dhloi1.224).

A parked row is the only copy of an item whose source is about to be released
(a cursor committed, a webhook acknowledged). ``flush`` swallows write failures,
which is fine for visibility rows but not for that copy, so ``park`` writes the
row on its own and returns only once it is committed.
"""

from __future__ import annotations

from typing import Any

import pytest

from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, ParkNotDurableError
from tests.connectors.filtered_event_fakes import FilteredEventsPool

pytestmark = pytest.mark.unit

_ENVELOPE = {
    "source": {"channel": "owntracks", "provider": "owntracks", "endpoint_identity": "e"},
    "event": {"external_event_id": "ev-1", "observed_at": "2026-11-09T00:00:00+00:00"},
    "payload": {"raw": {}, "normalized_text": "point"},
    "control": {"idempotency_key": "k-1"},
}


def _buffer() -> FilteredEventBuffer:
    return FilteredEventBuffer(connector_type="owntracks", endpoint_identity="owntracks:ph")


async def _park(buffer: FilteredEventBuffer, pool: Any) -> None:
    await buffer.park(
        pool,
        external_message_id="ev-1",
        source_channel="owntracks",
        sender_identity="owntracks:ph",
        subject_or_preview=None,
        error_detail="ConnectionError",
        full_payload=FilteredEventBuffer.stored_envelope(_ENVELOPE),
    )


async def test_park_writes_one_error_row_at_once_and_leaves_the_buffer_alone() -> None:
    buffer = _buffer()
    buffer.record(
        external_message_id="filtered-1",
        source_channel="owntracks",
        sender_identity="owntracks:ph",
        subject_or_preview=None,
        filter_reason="policy",
        full_payload={},
    )
    pool = FilteredEventsPool()

    await _park(buffer, pool)

    (row,) = pool.written
    assert row[7] == FilteredEventBuffer.reason_submission_error()
    assert row[8] == "error"
    assert row[9]["control"]["idempotency_key"] == "k-1"
    assert row[10] == "ConnectionError"
    assert len(buffer) == 1  # the visibility row still waits for flush()


async def test_park_raises_when_the_row_is_not_written_and_keeps_nothing() -> None:
    buffer = _buffer()
    pool = FilteredEventsPool()
    pool.fail_writes = True

    with pytest.raises(ParkNotDurableError):
        await _park(buffer, pool)

    assert pool.written == []
    assert len(buffer) == 0  # the caller holds the source and parks again


async def test_park_raises_without_a_pool() -> None:
    with pytest.raises(ParkNotDurableError):
        await _park(_buffer(), None)
