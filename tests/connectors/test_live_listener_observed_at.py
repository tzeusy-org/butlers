"""bu-0wearu: live-listener envelopes carry a wall-clock ``observed_at``, not a monotonic reading.

The speech offset is a ``time.monotonic()`` value. Reading it as a Unix epoch dated every envelope
1970-01-01 plus host uptime. The wall time is now derived from the segment's age on the monotonic
clock, and the segment duration is untouched.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from butlers.connectors.live_listener.connector import (
    LiveListenerConnector,
    wall_clock_observed_at,
)


def test_observed_at_is_the_wall_clock_minus_the_segment_age() -> None:
    wall_now = datetime(2026, 10, 18, 12, 0, 0, tzinfo=UTC)

    observed = wall_clock_observed_at(95.0, wall_now=wall_now, monotonic_now=100.0)

    assert observed == wall_now - timedelta(seconds=5)
    assert observed > datetime(2000, 1, 1, tzinfo=UTC)


async def test_process_segment_submits_a_wall_clock_observed_at_and_the_same_duration() -> None:
    connector = object.__new__(LiveListenerConnector)
    connector._ll_metrics = {"kitchen": MagicMock()}
    connector._mic_states = {"kitchen": SimpleNamespace()}
    connector._transcription_clients = {
        "kitchen": SimpleNamespace(
            transcribe=AsyncMock(
                return_value=SimpleNamespace(
                    text="hello", confidence=0.9, language="en", healthy=True
                )
            ),
            healthy=True,
        )
    }
    connector._prefilters = {}
    connector._discretion_evaluators = {}
    connector._sessions = {
        "kitchen": SimpleNamespace(
            get_or_create_session=lambda _ts: "session-1",
            session_id="session-1",
            session_last_ts_ms=0,
        )
    }
    connector._mcp_client = SimpleNamespace(call_tool=AsyncMock(return_value={"status": "ok"}))
    connector._connector_metrics = MagicMock()
    connector._db_pool = None
    before = datetime.now(UTC)
    segment = SimpleNamespace(duration_ms=1500, audio_bytes=b"x", offset_ts=time.monotonic() - 2.0)

    with patch(
        "butlers.connectors.live_listener.connector.evaluate_voice_filter",
        return_value=SimpleNamespace(allowed=True),
    ):
        await connector._process_segment(SimpleNamespace(name="kitchen"), segment, object())

    after = datetime.now(UTC)
    envelope = connector._mcp_client.call_tool.await_args.args[1]
    observed = datetime.fromisoformat(envelope["event"]["observed_at"])
    assert observed > datetime(2000, 1, 1, tzinfo=UTC)
    assert before - timedelta(seconds=2.5) <= observed <= after - timedelta(seconds=1.5)
    assert envelope["payload"]["raw"]["duration_s"] == 1.5
