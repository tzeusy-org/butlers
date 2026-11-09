"""A forwarded utterance whose ingest fails is parked, never dropped (bu-dhloi1.224).

Live audio is never captured again, and the voice checkpoint only tracks
session continuity, so a forwarded utterance whose Switchboard submit failed
used to be gone with no record. It is now parked durably as a replayable error
row (the exact envelope, idempotency key included; the error detail is the
exception class only), written at once rather than left in the buffer. The
connector also drains replay requests on a timer, which it never did, so a
parked utterance can actually be replayed even when the room is quiet.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.filtered_event_buffer import FilteredEventBuffer
from butlers.connectors.live_listener import connector as ll
from butlers.connectors.live_listener.connector import LiveListenerConnector
from butlers.connectors.live_listener.envelope import endpoint_identity
from tests.connectors.filtered_event_fakes import FilteredEventsPool

pytestmark = pytest.mark.unit

_MIC = "kitchen"
_SECRET_TEXT = "upstream echoed: order milk"


def _connector(call_tool: AsyncMock) -> tuple[Any, FilteredEventsPool]:
    # A bare instance with only what _process_segment reads.
    connector: Any = object.__new__(LiveListenerConnector)
    connector._ll_metrics = {_MIC: MagicMock()}
    connector._mic_states = {_MIC: SimpleNamespace()}
    connector._transcription_clients = {
        _MIC: SimpleNamespace(
            transcribe=AsyncMock(
                return_value=SimpleNamespace(
                    text="please order milk", confidence=0.9, language="en", healthy=True
                )
            ),
            healthy=True,
        )
    }
    connector._prefilters = {}
    connector._discretion_evaluators = {}
    connector._sessions = {
        _MIC: SimpleNamespace(
            get_or_create_session=lambda _ts: "session-1",
            session_id="session-1",
            session_last_ts_ms=0,
        )
    }
    connector._mcp_client = SimpleNamespace(call_tool=call_tool)
    connector._connector_metrics = MagicMock()
    pool = FilteredEventsPool()
    connector._db_pool = pool
    assert ll._FILTERED_EVENTS_CONNECTOR_TYPE is not None
    connector._filtered_event_buffers = {
        _MIC: FilteredEventBuffer(
            connector_type=ll._FILTERED_EVENTS_CONNECTOR_TYPE,
            endpoint_identity=endpoint_identity(_MIC),
        )
    }
    return connector, pool


async def _segment(connector: Any) -> None:
    segment: Any = SimpleNamespace(duration_ms=1000, audio_bytes=b"x", offset_ts=1_782_000_000.0)
    spec: Any = SimpleNamespace(name=_MIC)
    with (
        patch.object(ll, "evaluate_voice_filter", return_value=SimpleNamespace(allowed=True)),
        patch.object(ll, "save_voice_checkpoint", AsyncMock()),
    ):
        await connector._process_segment(spec, segment, object())


async def test_a_failed_utterance_is_parked_durably_for_replay() -> None:
    call_tool = AsyncMock(side_effect=ConnectionError(_SECRET_TEXT))
    connector, pool = _connector(call_tool)

    await _segment(connector)

    assert call_tool.await_args is not None
    envelope: dict[str, Any] = call_tool.await_args.args[1]
    assert connector._filtered_event_buffers[_MIC]._rows == []  # written, not buffered
    (row,) = pool.written
    assert row[8] == "error"  # status
    stored = row[9]
    assert stored["control"]["idempotency_key"] == envelope["control"]["idempotency_key"]
    assert stored["payload"]["normalized_text"] == envelope["payload"]["normalized_text"]
    assert "schema_version" not in stored
    assert row[10] == "ConnectionError"  # the class, never the transport text
    assert _SECRET_TEXT not in repr(row)


async def test_a_park_that_cannot_be_written_is_reported_as_lost(
    caplog: pytest.LogCaptureFixture,
) -> None:
    connector, pool = _connector(AsyncMock(side_effect=ConnectionError(_SECRET_TEXT)))
    pool.fail_writes = True

    await _segment(connector)

    assert pool.written == []
    assert any("could not be parked" in r.getMessage() for r in caplog.records)


async def test_replay_requests_are_drained_on_a_timer_without_any_utterance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_tool = AsyncMock(return_value={"status": "accepted"})
    connector, pool = _connector(call_tool)
    monkeypatch.setattr(ll, "_REPLAY_DRAIN_INTERVAL_S", 0.0)
    drained = asyncio.Event()
    calls: list[dict[str, Any]] = []

    async def drain(**kwargs: Any) -> None:
        calls.append(kwargs)
        drained.set()

    monkeypatch.setattr(ll, "drain_replay_pending", drain)
    task = asyncio.create_task(connector._replay_drain_loop())
    try:
        await asyncio.wait_for(drained.wait(), 1)
    finally:
        task.cancel()

    kwargs = calls[0]
    assert kwargs["pool"] is pool
    assert kwargs["connector_type"] == ll._FILTERED_EVENTS_CONNECTOR_TYPE
    assert kwargs["endpoint_identity"] == endpoint_identity(_MIC)
    await kwargs["submit_fn"]({"replayed": True})
    call_tool.assert_awaited_with("ingest", {"replayed": True})


async def test_a_delivered_utterance_no_longer_waits_on_a_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drain = AsyncMock()
    monkeypatch.setattr(ll, "drain_replay_pending", drain)
    connector, pool = _connector(AsyncMock(return_value={"status": "accepted"}))

    await _segment(connector)

    assert pool.written == []
    drain.assert_not_awaited()
