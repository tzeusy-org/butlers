from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from butlers.connectors import spotify
from butlers.testing.clock import FakeClock
from tests.connectors.test_spotify_connector import _ENDPOINT, _NOW, _SPOTIFY_USER_ID


@pytest.mark.parametrize("notification", ["ctx", "digest"])
async def test_spotify_live_notification_holds_when_evidence_database_fails(
    monkeypatch, notification
):
    clock = FakeClock(_NOW)

    class SpotifyDateTime(datetime):
        @classmethod
        def now(cls, zone=None):
            return clock.now().astimezone(zone)

    monkeypatch.setattr(spotify, "datetime", SpotifyDateTime)
    connector = spotify.SpotifyConnector(
        spotify.SpotifyConnectorConfig(
            switchboard_mcp_url="http://switchboard.test/mcp", digest_interval_s=1
        )
    )
    connector._endpoint_identity = _ENDPOINT
    connector._spotify_user_id = _SPOTIFY_USER_ID
    connector._cursor_pool = AsyncMock()
    connector._cursor_pool.fetch.side_effect = RuntimeError("synthetic evidence database failure")
    connector._cursor_pool.fetchval.side_effect = RuntimeError(
        "synthetic evidence database failure"
    )
    connector._get_recently_played = AsyncMock(return_value=[])
    connector._get_currently_playing = AsyncMock(
        return_value={
            "is_playing": True,
            "item": {
                "type": "track",
                "id": "synthetic-song",
                "name": "Synthetic song",
                "uri": "spotify:track:synthetic-song",
            },
        }
    )
    connector._mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
    if notification == "digest":
        connector._session_tracker.process_playback(
            track_id="synthetic-first-song",
            track_name="Synthetic first song",
            context_uri=None,
            now=_NOW,
        )
        clock.advance(2)

    try:
        await connector._execute_poll_cycle()
    except RuntimeError:
        pass
    connector._cursor_pool.fetch.assert_awaited()
    sent_during_outage = [
        call.args[1]["event"]["external_event_id"].split(":")[1]
        for call in connector._mcp_client.call_tool.await_args_list
    ]
    assert notification not in sent_during_outage
