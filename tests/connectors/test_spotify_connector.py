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

import pytest

from butlers.connectors.spotify import (
    ListeningSession,
    ListeningSessionTracker,
    build_context_start_envelope,
    build_session_summary_envelope,
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


def test_context_start_schema_version(context_start_env: dict[str, Any]) -> None:
    assert context_start_env["schema_version"] == "ingest.v1"


def test_context_start_source_fields(context_start_env: dict[str, Any]) -> None:
    assert context_start_env["source"]["channel"] == "spotify_user_client"
    assert context_start_env["source"]["provider"] == "spotify"
    assert context_start_env["source"]["endpoint_identity"] == _ENDPOINT


def test_context_start_ingestion_tier_full(context_start_env: dict[str, Any]) -> None:
    assert context_start_env["control"]["ingestion_tier"] == "full"


def test_context_start_idempotency_key_deterministic() -> None:
    e1 = build_context_start_envelope(
        endpoint_identity=_ENDPOINT, spotify_user_id=_SPOTIFY_USER_ID,
        track_id="t1", track_name="S", artist_names=[], album_name="A",
        duration_ms=0, context_uri="spotify:playlist:x", device_name=None,
        timestamp_ms=111, raw_payload={}, observed_at=_OBSERVED,
    )
    e2 = build_context_start_envelope(
        endpoint_identity=_ENDPOINT, spotify_user_id=_SPOTIFY_USER_ID,
        track_id="t1", track_name="S", artist_names=[], album_name="A",
        duration_ms=0, context_uri="spotify:playlist:x", device_name=None,
        timestamp_ms=111, raw_payload={}, observed_at=_OBSERVED,
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
        track_id="t1", track_name="A", context_uri="ctx",
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
        track_id="t2", track_name="B", context_uri="ctx:2",
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
        track_id="t2", track_name="Rain 2", context_uri=None,
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
            track_id=f"t{i}", track_name=f"Rain {i}", context_uri=ctx,
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
        track_id="t2", track_name="B", context_uri="ctx:2",
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
        track_id="t2", track_name="B", context_uri="ctx:2",
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
        track_id="t2", track_name="B", context_uri="ctx:2",
        now=_NOW + timedelta(seconds=80),
    )
    assert tracker.state == "active"
    assert "context_start" in events
    assert closed == []
    assert tracker.current_session is not None
    assert tracker.current_session.track_count == 1
