"""bu-ly3lv5.14: a Switchboard ``status: error`` reply fails a connector's submit.

Spotify used to log a warning and count it as success, and its replay submitter ignored the reply,
so a rejected buffered row was marked ``replay_complete``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.activitywatch import ActivityWatchConnector
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, drain_replay_pending
from butlers.connectors.google_health import GoogleHealthConnector
from butlers.connectors.ingest_submit import IngestRejected
from butlers.connectors.owntracks import OwnTracksConnector
from butlers.connectors.spotify import (
    SpotifyConnector,
    SpotifyConnectorConfig,
    build_context_start_envelope,
)

pytestmark = pytest.mark.unit

_ENDPOINT = "spotify_user_client:spotify:user123"
_ERROR = {"status": "error", "error": "Validation failed"}
_ACCEPTED = {"status": "accepted", "request_id": "r1"}


def _spotify(reply: Any) -> SpotifyConnector:
    connector = SpotifyConnector(
        SpotifyConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp")
    )
    connector._mcp_client = AsyncMock()
    connector._mcp_client.call_tool = AsyncMock(return_value=reply)
    return connector


def _envelope() -> dict[str, Any]:
    return build_context_start_envelope(
        endpoint_identity=_ENDPOINT,
        spotify_user_id="user123",
        track_id="track1",
        track_name="Song A",
        artist_names=["Artist"],
        album_name="Album",
        duration_ms=240000,
        context_uri="spotify:playlist:abc",
        device_name="Phone",
        timestamp_ms=1711447200000,
        raw_payload={"track_id": "track1"},
        observed_at="2026-03-26T10:00:00+00:00",
    )


async def test_spotify_live_path_counts_an_error_reply_as_a_failed_submission() -> None:
    connector = _spotify(_ERROR)
    connector._metrics = MagicMock()
    connector._filtered_event_buffer = FilteredEventBuffer(
        connector_type="spotify_user_client", endpoint_identity=_ENDPOINT
    )

    await connector._submit_envelope(_envelope())

    assert connector._metrics.record_ingest_submission.call_args.args[0] == "error"
    connector._metrics.record_error.assert_called_once_with("ingest_error", "submit")
    (row,) = connector._filtered_event_buffer._rows
    assert row[8] == "error"  # buffered for replay as a submission error
    assert not getattr(connector, "_last_ingest_submit", None)


@pytest.mark.parametrize(
    ("reply", "label"), [(_ACCEPTED, "success"), ({"status": "duplicate"}, "duplicate")]
)
async def test_spotify_live_path_still_records_accepted_outcomes(reply, label) -> None:
    connector = _spotify(reply)
    connector._metrics = MagicMock()

    await connector._submit_envelope(_envelope())

    assert connector._metrics.record_ingest_submission.call_args.args[0] == label
    connector._metrics.record_error.assert_not_called()


def _replay_pool(connector_type: str, endpoint: str):
    buffer = FilteredEventBuffer(connector_type=connector_type, endpoint_identity=endpoint)
    buffer.record(
        external_message_id="evt-1",
        source_channel="spotify",
        sender_identity="user123",
        subject_or_preview="Song A",
        filter_reason="submission_error",
        full_payload=FilteredEventBuffer.full_payload(
            channel="spotify",
            provider="spotify",
            endpoint_identity=endpoint,
            external_event_id="evt-1",
            external_thread_id=None,
            observed_at=datetime(2026, 3, 26, tzinfo=UTC).isoformat(),
            sender_identity="user123",
            raw={},
            normalized_text="Song A",
            policy_tier="full",
        ),
    )
    row = buffer._rows[0]
    conn = AsyncMock()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": 1,
                "received_at": row[0],
                "external_message_id": "evt-1",
                "full_payload": row[9],
            }
        ]
    )
    conn.transaction = MagicMock(return_value=AsyncMock())
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value = ctx
    return pool, conn


@pytest.mark.parametrize(
    ("reply", "final_status"),
    [
        (_ERROR, "replay_failed"),
        ({"status": "wat"}, "replay_failed"),
        (_ACCEPTED, "replay_complete"),
    ],
)
async def test_spotify_replay_does_not_complete_a_row_switchboard_rejected(
    reply, final_status
) -> None:
    connector = _spotify(reply)
    pool, conn = _replay_pool("spotify_user_client", _ENDPOINT)

    await drain_replay_pending(
        pool, "spotify_user_client", _ENDPOINT, connector._submit_to_ingest_direct
    )

    assert conn.execute.await_args.args[1] == final_status


def _bare(cls, method):
    connector = object.__new__(cls)
    connector._mcp_client = AsyncMock()
    return connector, getattr(connector, method)


@pytest.mark.parametrize(
    ("cls", "method"),
    [
        (ActivityWatchConnector, "_submit_envelope"),
        (OwnTracksConnector, "_submit_envelope"),
        (GoogleHealthConnector, "_submit_to_ingest_direct"),
        (SpotifyConnector, "_submit_to_ingest_direct"),
    ],
)
async def test_replay_submitters_raise_on_a_rejected_reply(cls, method) -> None:
    connector, submit = _bare(cls, method)
    connector._mcp_client.call_tool = AsyncMock(return_value=_ERROR)

    with pytest.raises(IngestRejected, match="Validation failed"):
        await submit(_envelope())

    connector._mcp_client.call_tool = AsyncMock(return_value=_ACCEPTED)
    await submit(_envelope())  # accepted does not raise
