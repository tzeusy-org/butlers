from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from butlers.connectors import spotify
from tests.connectors.test_spotify_connector import _ENDPOINT, _NOW, _SPOTIFY_USER_ID


@pytest.fixture
def connector():
    runtime = spotify.SpotifyConnector(
        spotify.SpotifyConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp")
    )
    runtime._endpoint_identity = _ENDPOINT
    runtime._spotify_user_id = _SPOTIFY_USER_ID
    runtime._cursor_pool = AsyncMock()
    runtime._cursor_pool.fetchval.return_value = 1
    runtime._mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
    return runtime


async def test_gap_fill_does_not_send_a_second_digest_when_track_write_retries(connector):
    later = _NOW + timedelta(seconds=60)
    connector._get_recently_played = AsyncMock(
        return_value=[
            {
                "track": {"id": "b", "uri": "spotify:track:b", "name": "Synthetic B"},
                "played_at": later.isoformat(),
            },
            {
                "track": {"id": "a", "uri": "spotify:track:a", "name": "Synthetic A"},
                "played_at": _NOW.isoformat(),
            },
        ]
    )
    held = set()
    reject_b = True

    async def already_observed(track_uri, played_at_ms):
        return (track_uri, played_at_ms) in held

    async def persist(rows):
        # All or none, like the one-transaction write it stands in for.
        nonlocal reject_b
        if reject_b:
            reject_b = False
            return False
        held.update((row["track_uri"], row["played_at_ms"]) for row in rows)
        return True

    connector._gap_fill_play_already_observed = already_observed
    connector._persist_gap_fill_track_plays = persist

    await connector._poll_recently_played(later, later.isoformat())
    assert connector._mcp_client.call_tool.await_count == 1
    await connector._poll_recently_played(later, later.isoformat())
    assert len(held) == 2
    keys = [
        call.args[1]["control"]["idempotency_key"]
        for call in connector._mcp_client.call_tool.await_args_list
    ]
    assert len(set(keys)) == 1, "A track-write retry rebuilt a different digest identity"


async def test_evidence_failure_keeps_all_spoken_starts_across_the_held_cap(monkeypatch, connector):
    monkeypatch.setattr(spotify, "_HELD_LIMIT", 2)
    connector._cursor_pool.fetchval.side_effect = RuntimeError("synthetic evidence outage")
    starts = {f"synthetic-spoken-{index}" for index in range(3)}

    for index in range(3):
        now = _NOW + timedelta(minutes=index)
        episode = {
            "type": "episode",
            "id": f"synthetic-spoken-{index}",
            "name": "Synthetic episode",
            "uri": f"spotify:episode:synthetic-spoken-{index}",
            "show": {"id": "synthetic-show", "name": "Synthetic show"},
        }
        await connector._handle_spoken_playback({"item": episode}, episode, now, now.isoformat())

    connector._mcp_client.call_tool.assert_not_awaited()
    connector._cursor_pool.fetchval.side_effect = None
    await connector._retry_held_notifications(_NOW + timedelta(minutes=4))
    delivered = {
        call.args[1]["event"]["external_event_id"].rsplit(":", 1)[1]
        for call in connector._mcp_client.call_tool.await_args_list
    }
    assert delivered == starts


async def test_a_track_arriving_after_a_failed_row_write_goes_in_a_later_digest(connector):
    """Native shape: A+B delivered, B's row write fails, C arrives, then recovery."""
    t0 = _NOW
    plays = {
        "a": t0,
        "b": t0 + timedelta(seconds=30),
        "c": t0 + timedelta(seconds=60),
    }

    def item(name):
        return {
            "track": {"id": name, "uri": f"spotify:track:{name}", "name": f"Synthetic {name}"},
            "played_at": plays[name].isoformat(),
        }

    connector._get_recently_played = AsyncMock(return_value=[item("b"), item("a")])
    connector._gap_fill_play_already_observed = AsyncMock(return_value=False)
    outcomes = iter([False, True, True])
    written: list[list[str]] = []

    async def persist(rows):
        ok = next(outcomes)
        if ok:
            written.append([row["track_uri"] for row in rows])
        return ok

    connector._persist_gap_fill_track_plays = persist
    stamp = (t0 + timedelta(minutes=2)).isoformat()

    await connector._poll_recently_played(t0, stamp)
    assert connector._mcp_client.call_tool.await_count == 1
    assert connector._last_recently_played_cursor is None  # the write failed; cursor holds

    connector._get_recently_played = AsyncMock(return_value=[item("c"), item("b"), item("a")])
    await connector._poll_recently_played(t0, stamp)

    sent = [
        call.args[1]["payload"]["raw"]["tracks"]
        for call in connector._mcp_client.call_tool.await_args_list
    ]
    assert sent == [["Synthetic a", "Synthetic b"], ["Synthetic c"]]
    assert written == [["spotify:track:a", "spotify:track:b"], ["spotify:track:c"]]
    assert connector._pending_gap_persist is None
