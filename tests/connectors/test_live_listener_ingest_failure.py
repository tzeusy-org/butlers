"""A forwarded utterance whose ingest fails is parked, never dropped (bu-dhloi1.224).

Live audio is never captured again, and the voice checkpoint only tracks
session continuity, so a forwarded utterance whose Switchboard submit failed
used to be gone with no record. It is now parked as a replayable error row
(the exact envelope, idempotency key included; the error detail is the
exception class only). The connector also drains replay requests, which it
never did, so a parked utterance can actually be replayed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.filtered_event_buffer import FilteredEventBuffer
from butlers.connectors.live_listener import connector as ll
from butlers.connectors.live_listener.connector import LiveListenerConnector
from butlers.connectors.live_listener.envelope import endpoint_identity

pytestmark = pytest.mark.unit

_MIC = "kitchen"
_SECRET_TEXT = "upstream echoed: order milk"


def _connector(call_tool: AsyncMock) -> tuple[Any, Any]:
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
    connector._db_pool = MagicMock()
    assert ll._FILTERED_EVENTS_CONNECTOR_TYPE is not None
    buffer: Any = FilteredEventBuffer(
        connector_type=ll._FILTERED_EVENTS_CONNECTOR_TYPE, endpoint_identity=endpoint_identity(_MIC)
    )
    buffer.flush = AsyncMock()
    connector._filtered_event_buffers = {_MIC: buffer}
    return connector, buffer


async def _segment(connector: Any) -> AsyncMock:
    segment: Any = SimpleNamespace(duration_ms=1000, audio_bytes=b"x", offset_ts=1_782_000_000.0)
    spec: Any = SimpleNamespace(name=_MIC)
    drain = AsyncMock()
    with (
        patch.object(ll, "evaluate_voice_filter", return_value=SimpleNamespace(allowed=True)),
        patch.object(ll, "save_voice_checkpoint", AsyncMock()),
        patch.object(ll, "drain_replay_pending", drain),
    ):
        await connector._process_segment(spec, segment, object())
    return drain


async def test_a_failed_utterance_is_parked_for_replay() -> None:
    call_tool = AsyncMock(side_effect=ConnectionError(_SECRET_TEXT))
    connector, buffer = _connector(call_tool)

    await _segment(connector)

    assert call_tool.await_args is not None
    envelope: dict[str, Any] = call_tool.await_args.args[1]
    (row,) = buffer._rows
    assert row[8] == "error"  # status
    stored = row[9]
    assert stored["control"]["idempotency_key"] == envelope["control"]["idempotency_key"]
    assert stored["payload"]["normalized_text"] == envelope["payload"]["normalized_text"]
    assert "schema_version" not in stored
    assert row[10] == "ConnectionError"  # the class, never the transport text
    assert _SECRET_TEXT not in repr(row)
    buffer.flush.assert_awaited_once_with(connector._db_pool)


async def test_a_delivered_utterance_drains_this_mics_replay_queue() -> None:
    call_tool = AsyncMock(return_value={"status": "accepted"})
    connector, buffer = _connector(call_tool)

    drain = await _segment(connector)

    assert buffer._rows == []
    drain.assert_awaited_once()
    assert drain.await_args is not None
    kwargs = drain.await_args.kwargs
    assert kwargs["pool"] is connector._db_pool
    assert kwargs["connector_type"] == ll._FILTERED_EVENTS_CONNECTOR_TYPE
    assert kwargs["endpoint_identity"] == endpoint_identity(_MIC)
    await kwargs["submit_fn"]({"replayed": True})
    call_tool.assert_awaited_with("ingest", {"replayed": True})
