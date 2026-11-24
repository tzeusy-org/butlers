"""Gap-fill row persistence: poison batches are visible, NUL is stripped, denied writes nothing."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
    runtime._gap_fill_play_already_observed = AsyncMock(return_value=False)
    return runtime


def _recent(name="Synthetic A"):
    return [
        {
            "track": {"id": "a", "uri": "spotify:track:a", "name": name},
            "played_at": _NOW.isoformat(),
        }
    ]


def _gauge(connector):
    return spotify.spotify_held_oldest_age_seconds.labels(
        endpoint_identity=connector._endpoint_identity
    )._value.get()


def _transaction_pool(connector, execute):
    conn = MagicMock()
    conn.execute = execute
    conn.transaction = MagicMock(
        return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
    )
    acquire = MagicMock(
        __aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False)
    )
    connector._cursor_pool.acquire = MagicMock(return_value=acquire)
    return conn


async def test_a_nul_in_a_track_name_is_stripped_before_the_row_write(connector):
    execute = AsyncMock()
    _transaction_pool(connector, execute)
    ok = await connector._persist_gap_fill_track_plays(
        [
            {
                "track_uri": "spotify:track:a",
                "track_name": "Synthetic\x00 A",
                "duration_ms": None,
                "played_at_ms": 1,
            }
        ]
    )
    assert ok is True
    written = [arg for arg in execute.await_args.args if isinstance(arg, str)]
    assert all("\x00" not in arg for arg in written)
    assert "Synthetic A" in written


async def test_a_poison_pending_batch_is_visible_in_the_age_gauge_and_logged(connector, caplog):
    connector._get_recently_played = AsyncMock(return_value=_recent())
    connector._persist_gap_fill_track_plays = AsyncMock(return_value=False)
    connector._get_currently_playing = AsyncMock(return_value=None)

    await connector._poll_recently_played(_NOW, _NOW.isoformat())
    assert connector._pending_gap_persist is not None
    connector._pending_gap_persist.first_failed -= 120

    await connector._execute_poll_cycle()
    assert _gauge(connector) >= 120


async def test_a_failed_row_write_logs_the_class_only(connector, caplog):
    secret = "Synthetic private track title"
    conn = _transaction_pool(connector, AsyncMock(side_effect=ValueError(secret)))
    del conn
    with caplog.at_level(logging.ERROR):
        assert not await connector._persist_gap_fill_track_plays(
            [{"track_uri": "u", "track_name": secret, "duration_ms": None, "played_at_ms": 1}]
        )
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and "failure_class=ValueError" in errors[0].getMessage()
    assert secret not in caplog.text


async def test_an_empty_poll_reports_zero_age(connector):
    connector._get_recently_played = AsyncMock(return_value=[])
    connector._get_currently_playing = AsyncMock(return_value=None)
    spotify.spotify_held_oldest_age_seconds.labels(endpoint_identity=_ENDPOINT).set(-0.1)
    await connector._execute_poll_cycle()
    assert _gauge(connector) == 0


def _denying_policy():
    decision = SimpleNamespace(
        allowed=False, action="drop", reason="synthetic", matched_rule_type="sender"
    )
    return SimpleNamespace(evaluate=lambda _env: decision, scope="connector:spotify:synthetic")


async def test_a_denied_gap_fill_writes_no_track_rows_and_advances_the_cursor(
    connector, monkeypatch
):
    journal = AsyncMock()
    monkeypatch.setattr(spotify, "commit_envelope", journal)
    connector._get_recently_played = AsyncMock(return_value=_recent())
    connector._ingestion_policy = _denying_policy()
    connector._filtered_event_buffer = MagicMock()
    persist = AsyncMock(return_value=True)
    connector._persist_gap_fill_track_plays = persist

    await connector._poll_recently_played(_NOW, _NOW.isoformat())

    persist.assert_not_awaited()
    connector._mcp_client.call_tool.assert_not_awaited()
    journal.assert_not_awaited()  # no evidence row either
    connector._filtered_event_buffer.record.assert_called_once()
    assert connector._last_recently_played_cursor == str(int(_NOW.timestamp() * 1000))
    assert connector._pending_gap_persist is None


async def test_an_allowed_gap_fill_writes_evidence_and_the_track_rows(connector, monkeypatch):
    journal = AsyncMock()
    monkeypatch.setattr(spotify, "commit_envelope", journal)
    connector._get_recently_played = AsyncMock(return_value=_recent())
    connector._ingestion_policy = None
    persist = AsyncMock(return_value=True)
    connector._persist_gap_fill_track_plays = persist

    await connector._poll_recently_played(_NOW, _NOW.isoformat())

    persist.assert_awaited_once()
    connector._mcp_client.call_tool.assert_awaited_once()
    journal.assert_awaited_once()
    assert connector._last_recently_played_cursor == str(int(_NOW.timestamp() * 1000))


async def test_a_null_name_and_a_nul_uri_are_normalized_where_they_are_read(connector):
    """A null name or a NUL in the uri must not make a batch that never persists."""
    connector._get_recently_played = AsyncMock(
        return_value=[
            {
                "track": {"id": "a", "uri": "spotify:track:\x00a", "name": None},
                "played_at": _NOW.isoformat(),
            }
        ]
    )
    connector._ingestion_policy = None
    seen = []

    async def observed(track_uri, played_at_ms):
        seen.append(track_uri)
        return False

    connector._gap_fill_play_already_observed = observed
    persist = AsyncMock(return_value=True)
    connector._persist_gap_fill_track_plays = persist
    journal = AsyncMock()
    spotify_commit = journal
    import butlers.connectors.spotify as module

    original = module.commit_envelope
    module.commit_envelope = spotify_commit
    try:
        await connector._poll_recently_played(_NOW, _NOW.isoformat())
    finally:
        module.commit_envelope = original

    assert seen == ["spotify:track:a"]  # the lookup never sees the NUL
    [row] = persist.await_args.args[0]
    assert row["track_uri"] == "spotify:track:a" and row["track_name"] == "unknown"
    assert connector._pending_gap_persist is None
    assert connector._last_recently_played_cursor == str(int(_NOW.timestamp() * 1000))
