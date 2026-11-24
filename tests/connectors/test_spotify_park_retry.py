from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.connectors import spotify
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, ParkNotDurableError
from tests.connectors.test_spotify_connector import _ENDPOINT, _SPOTIFY_USER_ID


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


@pytest.mark.parametrize("kind", ["spoken", "music"])
async def test_spotify_poll_retries_after_submit_and_park_failure(
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

    music_connector._filtered_event_buffer = FilteredEventBuffer("spotify", _ENDPOINT)
    music_connector._db_pool = SimpleNamespace(
        execute=AsyncMock(side_effect=RuntimeError("synthetic park write failure"))
    )
    music_connector._mcp_client.call_tool.side_effect = RuntimeError("synthetic submit failure")
    try:
        await music_connector._execute_poll_cycle()
    except ParkNotDurableError:
        pass

    music_connector._cursor_pool.fetchval.assert_awaited_once()
    music_connector._db_pool.execute.assert_awaited_once()
    music_connector._mcp_client.call_tool.assert_awaited_once()

    music_connector._db_pool.execute.side_effect = None
    music_connector._mcp_client.call_tool.side_effect = None
    clock.advance(60)
    await music_connector._execute_poll_cycle()

    assert music_connector._mcp_client.call_tool.await_count == 2
