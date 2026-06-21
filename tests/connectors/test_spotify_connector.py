"""Condensed Spotify connector tests — ingest.v1 contract and session state machine.

Replaces: test_spotify_connector.py, test_spotify_client.py,
test_spotify_session.py, test_spotify_metrics.py, test_spotify_integration.py

Verifies:
- ingest.v1 envelope production for context_start, listening_digest, session_summary
- ListeningSessionTracker state machine: key transitions
- Idempotency key determinism
- Playback-gap-only session boundaries (context changes never split sessions)

[bu-35fm7]
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.connectors.spotify import (
    ListeningSession,
    ListeningSessionTracker,
    SpotifyConnector,
    SpotifyConnectorConfig,
    build_context_start_envelope,
    build_listening_digest_envelope,
    build_session_summary_envelope,
    persist_session_summary,
)

_ENDPOINT = "spotify_user_client:spotify:user123"
_SPOTIFY_USER_ID = "user123"
_OBSERVED = "2026-03-26T10:00:00+00:00"
_NOW = datetime(2026, 3, 26, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def tracker() -> ListeningSessionTracker:
    return ListeningSessionTracker(idle_timeout_s=60)


# ---------------------------------------------------------------------------
# Envelope contract
# ---------------------------------------------------------------------------


@pytest.fixture
def context_start_env() -> dict[str, Any]:
    return build_context_start_envelope(
        endpoint_identity=_ENDPOINT,
        spotify_user_id=_SPOTIFY_USER_ID,
        track_id="track1",
        track_name="Song A",
        artist_names=["Artist"],
        album_name="Album",
        duration_ms=240000,
        context_uri="spotify:playlist:abc",
        device_name="Phone",
        timestamp_ms=1711447200000,
        raw_payload={"track_id": "track1"},
        observed_at=_OBSERVED,
    )


def test_context_start_envelope_fields(context_start_env: dict[str, Any]) -> None:
    """context_start carries ingest.v1 schema, spotify source fields, full tier."""
    assert context_start_env["schema_version"] == "ingest.v1"
    assert context_start_env["source"]["channel"] == "spotify_user_client"
    assert context_start_env["source"]["provider"] == "spotify"
    assert context_start_env["source"]["endpoint_identity"] == _ENDPOINT
    assert context_start_env["control"]["ingestion_tier"] == "full"


def test_context_start_idempotency_key_deterministic() -> None:
    e1 = build_context_start_envelope(
        endpoint_identity=_ENDPOINT,
        spotify_user_id=_SPOTIFY_USER_ID,
        track_id="t1",
        track_name="S",
        artist_names=[],
        album_name="A",
        duration_ms=0,
        context_uri="spotify:playlist:x",
        device_name=None,
        timestamp_ms=111,
        raw_payload={},
        observed_at=_OBSERVED,
    )
    e2 = build_context_start_envelope(
        endpoint_identity=_ENDPOINT,
        spotify_user_id=_SPOTIFY_USER_ID,
        track_id="t1",
        track_name="S",
        artist_names=[],
        album_name="A",
        duration_ms=0,
        context_uri="spotify:playlist:x",
        device_name=None,
        timestamp_ms=111,
        raw_payload={},
        observed_at=_OBSERVED,
    )
    assert e1["control"]["idempotency_key"] == e2["control"]["idempotency_key"]


def test_context_start_passes_parse_ingest_envelope(context_start_env: dict[str, Any]) -> None:
    """Envelope must validate against parse_ingest_envelope contract."""
    from pydantic import ValidationError

    from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

    try:
        parse_ingest_envelope(context_start_env)
    except ValidationError as exc:
        pytest.fail(f"parse_ingest_envelope raised ValidationError: {exc}")


def test_session_summary_schema_version() -> None:
    session = ListeningSession(
        started_at=_NOW,
        context_uri="spotify:playlist:abc",
        track_names=["Song A", "Song B"],
        last_digest_at=_NOW,
    )
    env = build_session_summary_envelope(
        endpoint_identity=_ENDPOINT,
        spotify_user_id=_SPOTIFY_USER_ID,
        session=session,
        observed_at=_OBSERVED,
    )
    assert env["schema_version"] == "ingest.v1"
    assert env["control"]["ingestion_tier"] == "full"


def test_context_start_prefers_explicit_context_name_from_raw_payload() -> None:
    env = build_context_start_envelope(
        endpoint_identity=_ENDPOINT,
        spotify_user_id=_SPOTIFY_USER_ID,
        track_id="track1",
        track_name="Song A",
        artist_names=["Artist"],
        album_name="Album",
        duration_ms=240000,
        context_uri="spotify:playlist:abc123",
        device_name="Phone",
        timestamp_ms=1711447200000,
        raw_payload={"context_name": "Deep Focus"},
        observed_at=_OBSERVED,
    )

    assert "Deep Focus" in env["payload"]["normalized_text"]
    assert "abc123" not in env["payload"]["normalized_text"]


def test_listening_digest_prefers_session_context_name() -> None:
    session = ListeningSession(
        started_at=_NOW,
        context_uri="spotify:playlist:abc123",
        track_names=["Song A", "Song B"],
        last_digest_at=_NOW,
    )
    session.context_name = "Deep Focus"

    env = build_listening_digest_envelope(
        endpoint_identity=_ENDPOINT,
        spotify_user_id=_SPOTIFY_USER_ID,
        session=session,
        observed_at=_OBSERVED,
    )

    assert "Deep Focus" in env["payload"]["normalized_text"]
    assert "abc123" not in env["payload"]["normalized_text"]


def test_session_summary_prefers_session_context_name() -> None:
    session = ListeningSession(
        started_at=_NOW,
        context_uri="spotify:playlist:abc123",
        track_names=["Song A", "Song B"],
        last_digest_at=_NOW,
    )
    session.context_name = "Deep Focus"

    env = build_session_summary_envelope(
        endpoint_identity=_ENDPOINT,
        spotify_user_id=_SPOTIFY_USER_ID,
        session=session,
        observed_at=_OBSERVED,
    )

    assert "Deep Focus" in env["payload"]["normalized_text"]
    assert "abc123" not in env["payload"]["normalized_text"]


@pytest.mark.asyncio
async def test_handle_active_playback_persists_in_progress_session() -> None:
    """Active polls upsert the open session so the Music lane shows live progress."""
    from unittest.mock import patch

    connector = SpotifyConnector(
        SpotifyConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp")
    )
    connector._endpoint_identity = _ENDPOINT
    connector._spotify_user_id = _SPOTIFY_USER_ID
    connector._submit_envelope = AsyncMock()
    connector._resolve_context_name = AsyncMock(return_value="Deep Focus")
    cursor_pool_mock = AsyncMock()
    connector._cursor_pool = cursor_pool_mock

    payload = {
        "timestamp": 1711447200000,
        "context": {"uri": "spotify:playlist:abc123"},
        "device": {"name": "Phone"},
        "item": {
            "id": "track1",
            "name": "Song A",
            "duration_ms": 240000,
            "artists": [{"name": "Artist"}],
            "album": {"name": "Album"},
        },
    }

    with patch(
        "butlers.connectors.spotify.persist_session_summary", new_callable=AsyncMock
    ) as mock_persist:
        mock_persist.return_value = True
        await connector._handle_active_playback(payload, payload["item"], _NOW, _OBSERVED)

    # The session-tracker opened a fresh session on the first active poll;
    # _persist_in_progress_session must upsert it so the Chronicler music
    # lane reflects current playback before the 5-minute idle-drain closes.
    mock_persist.assert_awaited_once()
    kwargs = mock_persist.await_args.kwargs
    assert kwargs["endpoint_identity"] == _ENDPOINT
    assert kwargs["spotify_user_id"] == _SPOTIFY_USER_ID
    assert kwargs["session"] is connector._session_tracker.current_session


@pytest.mark.asyncio
async def test_handle_active_playback_skips_persist_without_cursor_pool() -> None:
    """No persist attempt when the connector has no cursor pool wired up."""
    from unittest.mock import patch

    connector = SpotifyConnector(
        SpotifyConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp")
    )
    connector._endpoint_identity = _ENDPOINT
    connector._spotify_user_id = _SPOTIFY_USER_ID
    connector._submit_envelope = AsyncMock()
    connector._resolve_context_name = AsyncMock(return_value=None)
    connector._cursor_pool = None

    payload = {
        "timestamp": 1711447200000,
        "context": {"uri": "spotify:playlist:abc123"},
        "device": {"name": "Phone"},
        "item": {
            "id": "track1",
            "name": "Song A",
            "duration_ms": 240000,
            "artists": [{"name": "Artist"}],
            "album": {"name": "Album"},
        },
    }

    with patch(
        "butlers.connectors.spotify.persist_session_summary", new_callable=AsyncMock
    ) as mock_persist:
        await connector._handle_active_playback(payload, payload["item"], _NOW, _OBSERVED)

    mock_persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_active_playback_resolves_context_name() -> None:
    connector = SpotifyConnector(
        SpotifyConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp")
    )
    connector._endpoint_identity = _ENDPOINT
    connector._spotify_user_id = _SPOTIFY_USER_ID
    connector._submit_envelope = AsyncMock()
    connector._resolve_context_name = AsyncMock(return_value="Deep Focus")

    payload = {
        "timestamp": 1711447200000,
        "context": {"uri": "spotify:playlist:abc123"},
        "device": {"name": "Phone"},
        "item": {
            "id": "track1",
            "name": "Song A",
            "duration_ms": 240000,
            "artists": [{"name": "Artist"}],
            "album": {"name": "Album"},
        },
    }

    await connector._handle_active_playback(payload, payload["item"], _NOW, _OBSERVED)

    envelope = connector._submit_envelope.await_args.args[0]
    assert envelope["payload"]["raw"]["context_name"] == "Deep Focus"
    assert "Deep Focus" in envelope["payload"]["normalized_text"]
    assert "abc123" not in envelope["payload"]["normalized_text"]


# ---------------------------------------------------------------------------
# Session state machine — core transitions
# ---------------------------------------------------------------------------


def test_initial_state_is_idle(tracker: ListeningSessionTracker) -> None:
    assert tracker.state == "idle"
    assert tracker.current_session is None


def test_first_playback_transitions_to_active(tracker: ListeningSessionTracker) -> None:
    events, closed = tracker.process_playback(
        track_id="t1", track_name="Song A", context_uri="spotify:playlist:x", now=_NOW
    )
    assert tracker.state == "active"
    assert "context_start" in events
    assert closed == []


def test_no_playback_transitions_to_draining(tracker: ListeningSessionTracker) -> None:
    tracker.process_playback(track_id="t1", track_name="A", context_uri=None, now=_NOW)
    closed = tracker.process_no_playback(now=_NOW + timedelta(seconds=5))
    assert tracker.state == "draining"
    assert closed == []


def test_draining_timeout_closes_session(tracker: ListeningSessionTracker) -> None:
    """After idle timeout expires while draining, session is closed."""
    tracker.process_playback(track_id="t1", track_name="A", context_uri="ctx", now=_NOW)
    tracker.process_no_playback(now=_NOW + timedelta(seconds=5))
    # Timeout is 60s; advance past it
    closed = tracker.process_no_playback(now=_NOW + timedelta(seconds=70))
    assert tracker.state == "idle"
    assert len(closed) == 1


def test_draining_resume_returns_to_active(tracker: ListeningSessionTracker) -> None:
    tracker.process_playback(track_id="t1", track_name="A", context_uri="ctx", now=_NOW)
    tracker.process_no_playback(now=_NOW + timedelta(seconds=5))
    assert tracker.state == "draining"

    events, closed = tracker.process_playback(
        track_id="t1",
        track_name="A",
        context_uri="ctx",
        now=_NOW + timedelta(seconds=30),
    )
    assert tracker.state == "active"
    assert closed == []


# ---------------------------------------------------------------------------
# Playback-gap-only session boundaries
#
# Context changes during continuous playback (autoplay, radio, DJ) must NOT
# split sessions.  Sessions end only when playback stops (drain timeout).
# ---------------------------------------------------------------------------


def test_context_change_does_not_split_active_session(tracker: ListeningSessionTracker) -> None:
    """Different context_uri during active playback: accumulate, don't split."""
    tracker.process_playback(track_id="t1", track_name="A", context_uri="ctx:1", now=_NOW)
    events, closed = tracker.process_playback(
        track_id="t2",
        track_name="B",
        context_uri="ctx:2",
        now=_NOW + timedelta(minutes=5),
    )
    assert closed == [], "context change must not close session during continuous playback"
    assert events == [], "context change must not emit context_start during continuous playback"
    assert tracker.state == "active"
    assert tracker.current_session is not None
    assert tracker.current_session.track_count == 2


def test_null_context_does_not_split_session(tracker: ListeningSessionTracker) -> None:
    """When autoplay drops context_uri to None, session continues."""
    tracker.process_playback(
        track_id="t1", track_name="Rain 1", context_uri="spotify:playlist:rain", now=_NOW
    )
    events, closed = tracker.process_playback(
        track_id="t2",
        track_name="Rain 2",
        context_uri=None,
        now=_NOW + timedelta(minutes=4),
    )
    assert closed == []
    assert events == []
    assert tracker.current_session is not None
    assert tracker.current_session.context_uri == "spotify:playlist:rain"
    assert tracker.current_session.track_count == 2


def test_overnight_rain_sounds_single_session(tracker: ListeningSessionTracker) -> None:
    """Overnight scenario: playlist → many autoplay tracks with varying contexts.

    Should produce exactly ONE session, not one per track.
    """
    # Playlist starts
    tracker.process_playback(
        track_id="t1", track_name="Rain 1", context_uri="spotify:playlist:rain", now=_NOW
    )
    # Autoplay takes over with different contexts
    contexts = [None, "spotify:artist:abc", None, "spotify:album:xyz", "spotify:artist:def"]
    for i, ctx in enumerate(contexts, start=2):
        events, closed = tracker.process_playback(
            track_id=f"t{i}",
            track_name=f"Rain {i}",
            context_uri=ctx,
            now=_NOW + timedelta(minutes=4 * i),
        )
        assert closed == [], f"track {i} should not close session"
        assert events == [], f"track {i} should not emit events"

    assert tracker.current_session is not None
    assert tracker.current_session.track_count == 6
    assert tracker.state == "active"


def test_session_ends_on_playback_gap_not_context(tracker: ListeningSessionTracker) -> None:
    """Session ends when playback stops + drain timeout, not on context change."""
    tracker.process_playback(track_id="t1", track_name="A", context_uri="ctx:1", now=_NOW)
    tracker.process_playback(
        track_id="t2",
        track_name="B",
        context_uri="ctx:2",
        now=_NOW + timedelta(minutes=5),
    )
    # Playback stops
    tracker.process_no_playback(now=_NOW + timedelta(minutes=6))
    assert tracker.state == "draining"
    # Drain timeout expires
    closed = tracker.process_no_playback(now=_NOW + timedelta(minutes=8))
    assert tracker.state == "idle"
    assert len(closed) == 1
    assert closed[0].track_count == 2  # both tracks in one session


def test_draining_resume_with_different_context_continues(
    tracker: ListeningSessionTracker,
) -> None:
    """Resuming from draining with a different context continues the session."""
    tracker.process_playback(track_id="t1", track_name="A", context_uri="ctx:1", now=_NOW)
    tracker.process_no_playback(now=_NOW + timedelta(seconds=10))
    assert tracker.state == "draining"

    # Resume with different context (e.g. user switched playlists during brief pause)
    events, closed = tracker.process_playback(
        track_id="t2",
        track_name="B",
        context_uri="ctx:2",
        now=_NOW + timedelta(seconds=30),
    )
    assert tracker.state == "active"
    assert closed == [], "resume from draining should not close session"
    assert events == [], "resume from draining should not emit context_start"
    assert tracker.current_session is not None
    assert tracker.current_session.track_count == 2


def test_new_session_after_full_drain(tracker: ListeningSessionTracker) -> None:
    """After drain timeout completes, next playback starts a genuinely new session."""
    tracker.process_playback(track_id="t1", track_name="A", context_uri="ctx:1", now=_NOW)
    tracker.process_no_playback(now=_NOW + timedelta(seconds=5))
    closed = tracker.process_no_playback(now=_NOW + timedelta(seconds=70))
    assert tracker.state == "idle"
    assert len(closed) == 1

    # New session starts
    events, closed = tracker.process_playback(
        track_id="t2",
        track_name="B",
        context_uri="ctx:2",
        now=_NOW + timedelta(seconds=80),
    )
    assert tracker.state == "active"
    assert "context_start" in events
    assert closed == []
    assert tracker.current_session is not None
    assert tracker.current_session.track_count == 1


# ---------------------------------------------------------------------------
# Durable evidence persistence — persist_session_summary
# ---------------------------------------------------------------------------


def _make_closed_session(
    started_at: datetime = _NOW,
    drain_started_at: datetime | None = None,
    track_names: list[str] | None = None,
    context_uri: str | None = "spotify:playlist:abc",
    context_name: str | None = "Test Playlist",
) -> ListeningSession:
    """Build a closed ListeningSession for persist_session_summary tests."""
    s = ListeningSession(
        started_at=started_at,
        context_uri=context_uri,
        track_names=track_names or ["Song A", "Song B"],
        last_digest_at=started_at,
    )
    s.context_name = context_name
    s.drain_started_at = drain_started_at or (started_at + timedelta(minutes=30))
    return s


@pytest.mark.asyncio
async def test_persist_session_summary_upserts_row() -> None:
    """persist_session_summary executes the expected INSERT … ON CONFLICT DO UPDATE."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value="some-uuid")

    session = _make_closed_session()
    result = await persist_session_summary(
        pool,
        endpoint_identity=_ENDPOINT,
        spotify_user_id=_SPOTIFY_USER_ID,
        session=session,
    )

    assert result is True
    pool.fetchval.assert_awaited_once()
    call_args = pool.fetchval.call_args
    query: str = call_args.args[0]
    # Conflict-safe upsert so in-progress sessions extend the same row.
    assert "ON CONFLICT" in query
    assert "DO UPDATE" in query

    positional = call_args.args[1:]
    idempotency_key = positional[0]
    assert idempotency_key.startswith("spotify:")
    assert _ENDPOINT in idempotency_key

    # The asyncpg JSONB codec is registered on the pool, so track_names is
    # passed as a Python list directly (no json.dumps pre-serialization needed).
    track_names = positional[7]
    assert isinstance(track_names, list)
    assert track_names == ["Song A", "Song B"]


@pytest.mark.asyncio
async def test_persist_session_summary_in_progress_uses_last_activity() -> None:
    """In-progress sessions (no drain_started_at) write last_activity_at as ended_at."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value="row-id")

    started = _NOW
    last_activity = _NOW + timedelta(minutes=10)
    session = ListeningSession(
        started_at=started,
        context_uri="spotify:playlist:abc",
        track_names=["Song A"],
        last_digest_at=started,
        last_activity_at=last_activity,
    )
    # drain_started_at remains None: session is still active

    result = await persist_session_summary(
        pool,
        endpoint_identity=_ENDPOINT,
        spotify_user_id=_SPOTIFY_USER_ID,
        session=session,
    )

    assert result is True
    positional = pool.fetchval.call_args.args[1:]
    # ended_at is positional[4] (after key, endpoint, user_id, started_at)
    ended_at = positional[4]
    assert ended_at == last_activity, (
        "in-progress session must persist last_activity_at as the live ended_at cursor"
    )


@pytest.mark.asyncio
async def test_persist_session_summary_idempotency_key_deterministic() -> None:
    """Same session produces the same idempotency key on every call."""
    session = _make_closed_session(started_at=_NOW)

    pool1 = AsyncMock()
    pool1.fetchval = AsyncMock(return_value="uuid-1")
    pool2 = AsyncMock()
    pool2.fetchval = AsyncMock(return_value="uuid-2")

    await persist_session_summary(
        pool1,
        endpoint_identity=_ENDPOINT,
        spotify_user_id=_SPOTIFY_USER_ID,
        session=session,
    )
    await persist_session_summary(
        pool2,
        endpoint_identity=_ENDPOINT,
        spotify_user_id=_SPOTIFY_USER_ID,
        session=session,
    )

    key1 = pool1.fetchval.call_args.args[1]
    key2 = pool2.fetchval.call_args.args[1]
    assert key1 == key2


@pytest.mark.asyncio
async def test_emit_session_summary_persists_to_evidence_table() -> None:
    """_emit_session_summary calls persist_session_summary when cursor_pool is available."""
    from unittest.mock import patch

    connector = SpotifyConnector(
        SpotifyConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp")
    )
    connector._endpoint_identity = _ENDPOINT
    connector._spotify_user_id = _SPOTIFY_USER_ID
    cursor_pool_mock = AsyncMock()
    connector._cursor_pool = cursor_pool_mock
    connector._submit_envelope = AsyncMock()
    connector._ingestion_policy = None

    session = _make_closed_session()

    with patch(
        "butlers.connectors.spotify.persist_session_summary", new_callable=AsyncMock
    ) as mock_persist:
        mock_persist.return_value = True
        await connector._emit_session_summary(session, _OBSERVED)

    mock_persist.assert_awaited_once()
    assert mock_persist.await_args.kwargs["session"] is session
    connector._submit_envelope.assert_awaited_once()


@pytest.mark.asyncio
async def test_emit_session_summary_evidence_failure_is_non_fatal() -> None:
    """Persist failure must not abort ingest submission."""
    from unittest.mock import patch

    connector = SpotifyConnector(
        SpotifyConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp")
    )
    connector._endpoint_identity = _ENDPOINT
    connector._spotify_user_id = _SPOTIFY_USER_ID
    connector._cursor_pool = AsyncMock()
    connector._submit_envelope = AsyncMock()
    connector._ingestion_policy = None

    session = _make_closed_session()

    with patch(
        "butlers.connectors.spotify.persist_session_summary", new_callable=AsyncMock
    ) as mock_persist:
        mock_persist.side_effect = RuntimeError("DB is down")
        await connector._emit_session_summary(session, _OBSERVED)

    # Ingest submission must still happen despite the persist failure.
    connector._submit_envelope.assert_awaited_once()


@pytest.mark.asyncio
async def test_emit_session_summary_skips_persist_without_cursor_pool() -> None:
    """No persist attempt when cursor_pool is None (graceful degradation)."""
    from unittest.mock import patch

    connector = SpotifyConnector(
        SpotifyConnectorConfig(switchboard_mcp_url="http://switchboard.test/mcp")
    )
    connector._endpoint_identity = _ENDPOINT
    connector._spotify_user_id = _SPOTIFY_USER_ID
    connector._cursor_pool = None  # explicitly no pool
    connector._submit_envelope = AsyncMock()
    connector._ingestion_policy = None

    session = _make_closed_session()

    with patch(
        "butlers.connectors.spotify.persist_session_summary", new_callable=AsyncMock
    ) as mock_persist:
        await connector._emit_session_summary(session, _OBSERVED)

    mock_persist.assert_not_awaited()
    connector._submit_envelope.assert_awaited_once()
