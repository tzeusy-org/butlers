from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.connectors import google_health, spotify
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer
from tests.connectors.test_google_health_connector import _make_connector, _privacy_envelope
from tests.connectors.test_spotify_connector import (
    _ENDPOINT,
    _OBSERVED,
    _SPOTIFY_USER_ID,
    _make_closed_session,
)


async def test_health_evidence_failure_does_not_park_the_sample():
    connector = _make_connector()
    connector._shared_pool = AsyncMock()
    connector._shared_pool.fetchval.side_effect = RuntimeError("synthetic evidence write failure")
    connector._filtered_event_buffer = FilteredEventBuffer("google_health", "ep")
    connector._ingestion_policy = None
    connector._mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})

    outcome = await connector._submit_envelope(_privacy_envelope(), resource="steps")

    assert outcome is google_health.SubmissionOutcome.FAILED
    connector._shared_pool.fetchval.assert_awaited_once()
    connector._mcp_client.call_tool.assert_not_awaited()
    assert len(connector._filtered_event_buffer) == 0


async def test_health_committed_sample_still_parks_failed_submit():
    connector = _make_connector()
    connector._shared_pool = AsyncMock()
    connector._shared_pool.fetchval.return_value = 1
    connector._filtered_event_buffer = FilteredEventBuffer("google_health", "ep")
    connector._ingestion_policy = None
    connector._mcp_client.call_tool = AsyncMock(
        side_effect=RuntimeError("synthetic submit failure")
    )

    outcome = await connector._submit_envelope(_privacy_envelope(), resource="steps")

    assert outcome is google_health.SubmissionOutcome.FAILED
    connector._shared_pool.fetchval.assert_awaited_once()
    connector._mcp_client.call_tool.assert_awaited_once()
    assert len(connector._filtered_event_buffer) == 1


@pytest.fixture
def music_connector():
    connector = spotify.SpotifyConnector(
        spotify.SpotifyConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp")
    )
    connector._cursor_pool = AsyncMock()
    connector._cursor_pool.fetchval.return_value = 1
    connector._endpoint_identity = _ENDPOINT
    connector._spotify_user_id = _SPOTIFY_USER_ID
    connector._ingestion_policy = None
    connector._mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
    return connector


async def test_spotify_session_summary_never_submits_when_evidence_write_fails(music_connector):
    music_connector._cursor_pool.fetchval.side_effect = RuntimeError(
        "synthetic evidence write failure"
    )
    try:
        await music_connector._emit_session_summary(_make_closed_session(), _OBSERVED)
    except (RuntimeError, ConnectionError):
        pass

    music_connector._cursor_pool.fetchval.assert_awaited_once()
    music_connector._mcp_client.call_tool.assert_not_awaited()


async def test_spotify_session_summary_never_persists_blocked_observation(music_connector):
    music_connector._ingestion_policy = SimpleNamespace(
        scope="connector_rule",
        evaluate=lambda _env: SimpleNamespace(
            allowed=False, action="block", matched_rule_type="sender_domain"
        ),
    )

    await music_connector._emit_session_summary(_make_closed_session(), _OBSERVED)

    music_connector._mcp_client.call_tool.assert_not_awaited()
    music_connector._cursor_pool.fetchval.assert_not_awaited()


@pytest.mark.parametrize("decision", ["allow_then_fail", "block"])
async def test_spotify_spoken_session_uses_the_same_evidence_boundary(music_connector, decision):
    from tests.connectors.test_spotify_connector import _NOW

    item = {
        "type": "episode",
        "id": "synthetic-1",
        "name": "Synthetic episode",
        "uri": "spotify:episode:synthetic-1",
        "duration_ms": 600_000,
        "show": {"id": "synthetic-show", "name": "Synthetic show"},
    }
    if decision == "allow_then_fail":
        music_connector._cursor_pool.fetchval.side_effect = RuntimeError("synthetic write failure")
    else:
        music_connector._ingestion_policy = SimpleNamespace(
            scope="connector_rule",
            evaluate=lambda _env: SimpleNamespace(
                allowed=False, action="block", matched_rule_type="sender_domain"
            ),
        )

    try:
        await music_connector._handle_spoken_playback({"item": item}, item, _NOW, _OBSERVED)
    except RuntimeError:
        pass

    music_connector._mcp_client.call_tool.assert_not_awaited()
    if decision == "allow_then_fail":
        music_connector._cursor_pool.fetchval.assert_awaited_once()
    else:
        music_connector._cursor_pool.fetchval.assert_not_awaited()


async def test_spotify_spoken_observation_survives_evidence_outage(music_connector):
    from datetime import timedelta

    from tests.connectors.test_spotify_connector import _NOW

    item = {
        "type": "episode",
        "id": "synthetic-resume",
        "name": "Synthetic episode",
        "uri": "spotify:episode:synthetic-resume",
        "duration_ms": 600_000,
        "show": {"id": "synthetic-show", "name": "Synthetic show"},
    }
    music_connector._cursor_pool.fetchval.side_effect = RuntimeError("synthetic write failure")
    try:
        await music_connector._handle_spoken_playback({"item": item}, item, _NOW, _OBSERVED)
    except RuntimeError:
        pass

    first_sends = music_connector._mcp_client.call_tool.await_count
    music_connector._cursor_pool.fetchval.side_effect = None
    next_tick = _NOW + timedelta(minutes=1)
    await music_connector._handle_spoken_playback(
        {"item": item}, item, next_tick, next_tick.isoformat()
    )

    total_sends = music_connector._mcp_client.call_tool.await_count
    assert (first_sends, total_sends - first_sends) == (0, 1)


async def test_spotify_closed_music_summary_survives_evidence_outage(music_connector):
    from datetime import timedelta

    from tests.connectors.test_spotify_connector import _NOW

    music_connector._reconcile_open_track_plays = AsyncMock(return_value=True)
    music_connector._retry_pending_closed_track_plays = AsyncMock()
    music_connector._session_tracker.process_playback(
        track_id="synthetic-song",
        track_name="Synthetic song",
        context_uri=None,
        context_name=None,
        now=_NOW,
    )
    drain = _NOW + timedelta(minutes=1)
    await music_connector._advance_music_for_no_playback(drain, drain.isoformat())
    close = drain + timedelta(seconds=music_connector._config.session_idle_timeout_s + 1)

    music_connector._cursor_pool.fetchval.side_effect = RuntimeError("synthetic write failure")
    try:
        await music_connector._advance_music_for_no_playback(close, close.isoformat())
    except RuntimeError:
        pass
    first_sends = music_connector._mcp_client.call_tool.await_count

    music_connector._cursor_pool.fetchval.side_effect = None
    next_tick = close + timedelta(minutes=1)
    await music_connector._advance_music_for_no_playback(next_tick, next_tick.isoformat())

    total_sends = music_connector._mcp_client.call_tool.await_count
    assert (first_sends, total_sends - first_sends) == (0, 1)


@pytest.mark.parametrize("kind", ["spoken", "music"])
async def test_spotify_poll_delivers_held_notification_after_database_recovers(
    music_connector, monkeypatch, kind
):
    from datetime import datetime

    from butlers.testing.clock import FakeClock
    from tests.connectors.test_spotify_connector import _NOW

    clock = FakeClock(_NOW)

    class SpotifyDateTime(datetime):
        @classmethod
        def now(cls, zone=None):
            return clock.now().astimezone(zone)

    monkeypatch.setattr(spotify, "datetime", SpotifyDateTime)
    music_connector._reconcile_open_track_plays = AsyncMock(return_value=True)
    music_connector._retry_pending_closed_track_plays = AsyncMock()
    music_connector._get_recently_played = AsyncMock(return_value=[])

    if kind == "spoken":
        music_connector._get_currently_playing = AsyncMock(
            return_value={
                "is_playing": True,
                "item": {
                    "type": "episode",
                    "id": "synthetic-poll-resume",
                    "name": "Synthetic episode",
                    "uri": "spotify:episode:synthetic-poll-resume",
                    "show": {"id": "synthetic-show", "name": "Synthetic show"},
                },
            }
        )
    else:
        music_connector._get_currently_playing = AsyncMock(return_value=None)
        music_connector._session_tracker.process_playback(
            track_id="synthetic-track",
            track_name="Synthetic track",
            context_uri=None,
            context_name=None,
            now=_NOW,
        )
        await music_connector._execute_poll_cycle()
        clock.advance(music_connector._config.session_idle_timeout_s + 1)

    music_connector._cursor_pool.fetchval.side_effect = RuntimeError("synthetic write failure")
    try:
        await music_connector._execute_poll_cycle()
    except RuntimeError:
        pass
    first_sends = music_connector._mcp_client.call_tool.await_count

    music_connector._cursor_pool.fetchval.side_effect = None
    clock.advance(60)
    await music_connector._execute_poll_cycle()
    total_sends = music_connector._mcp_client.call_tool.await_count

    assert (first_sends, total_sends - first_sends) == (0, 1)
