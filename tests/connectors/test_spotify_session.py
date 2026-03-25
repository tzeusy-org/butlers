"""Tests for ListeningSessionTracker state machine.

Covers tasks 6.1-6.7 from openspec/changes/connector-spotify/tasks.md:
- State machine: idle → active → draining → idle
- All transition paths
- Track change detection and events
- Context change detection and events
- Drain timeout and session_summary emission
- Session summary field correctness
- Edge cases: brief pauses, rapid track changes, context switches
"""

from __future__ import annotations

import pytest

from butlers.connectors.spotify_session import (
    DEFAULT_IDLE_TIMEOUT_S,
    ListeningSessionTracker,
    PollResult,
    SessionState,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Test fixtures and helpers
# ---------------------------------------------------------------------------

_T0 = 1_000_000.0  # arbitrary base Unix timestamp (seconds)
_T0_MS = int(_T0 * 1000)

TRACK_A = "track_id_A"
TRACK_B = "track_id_B"
TRACK_C = "track_id_C"
CTX_PLAYLIST_1 = "spotify:playlist:111"
CTX_PLAYLIST_2 = "spotify:playlist:222"
CTX_ALBUM_1 = "spotify:album:AAA"


def _playing(
    track_id: str = TRACK_A,
    track_name: str = "Song A",
    artist_names: list[str] | None = None,
    album_name: str = "Album A",
    duration_ms: int = 240_000,
    context_uri: str | None = CTX_PLAYLIST_1,
    context_name: str | None = "My Playlist",
    device_name: str = "My Device",
    played_at_ms: int | None = None,
    raw_payload: dict | None = None,
) -> PollResult:
    """Build a PollResult for an active track."""
    return PollResult(
        is_playing=True,
        track_id=track_id,
        track_name=track_name,
        artist_names=artist_names or ["Artist A"],
        album_name=album_name,
        duration_ms=duration_ms,
        context_uri=context_uri,
        context_name=context_name,
        device_name=device_name,
        played_at_ms=played_at_ms,
        raw_payload=raw_payload or {},
    )


def _stopped(played_at_ms: int | None = None) -> PollResult:
    """Build a PollResult for no active playback."""
    return PollResult(is_playing=False, played_at_ms=played_at_ms)


def _ms(offset_s: float) -> int:
    """Return a timestamp in ms offset from _T0."""
    return int((_T0 + offset_s) * 1000)


def _now(offset_s: float) -> float:
    """Return a wall-clock float offset from _T0."""
    return _T0 + offset_s


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


def test_initial_state_is_idle() -> None:
    tracker = ListeningSessionTracker()
    assert tracker.state == SessionState.IDLE


def test_initial_current_track_is_none() -> None:
    tracker = ListeningSessionTracker()
    assert tracker.current_track_id is None


# ---------------------------------------------------------------------------
# Idle state: no playback
# ---------------------------------------------------------------------------


def test_idle_no_playback_emits_no_events() -> None:
    tracker = ListeningSessionTracker()
    events = tracker.on_poll(_stopped(), now=_T0)
    assert events == []
    assert tracker.state == SessionState.IDLE


def test_idle_repeated_no_playback_stays_idle() -> None:
    tracker = ListeningSessionTracker()
    for _ in range(5):
        tracker.on_poll(_stopped(), now=_T0)
    assert tracker.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# Idle → Active transition
# ---------------------------------------------------------------------------


def test_idle_playback_detected_transitions_to_active() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(_playing(played_at_ms=_ms(0)), now=_now(0))
    assert tracker.state == SessionState.ACTIVE


def test_idle_to_active_emits_track_change() -> None:
    tracker = ListeningSessionTracker()
    events = tracker.on_poll(_playing(played_at_ms=_ms(0)), now=_now(0))
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "spotify.track_change"


def test_idle_to_active_track_change_fields() -> None:
    tracker = ListeningSessionTracker()
    raw = {"item": {"id": TRACK_A}}
    result = _playing(
        track_id=TRACK_A,
        track_name="Song A",
        artist_names=["Art X", "Art Y"],
        album_name="Alb Z",
        duration_ms=195_000,
        context_uri=CTX_PLAYLIST_1,
        context_name="My Playlist",
        device_name="Kitchen",
        played_at_ms=_ms(0),
        raw_payload=raw,
    )
    [ev] = tracker.on_poll(result, now=_now(0))
    assert ev.track_id == TRACK_A
    assert ev.track_name == "Song A"
    assert ev.artist_names == ["Art X", "Art Y"]
    assert ev.album_name == "Alb Z"
    assert ev.duration_ms == 195_000
    assert ev.context_uri == CTX_PLAYLIST_1
    assert ev.context_name == "My Playlist"
    assert ev.device_name == "Kitchen"
    assert ev.played_at_ms == _ms(0)
    assert ev.raw_payload == raw


def test_idle_to_active_sets_current_track_id() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(_playing(track_id=TRACK_A, played_at_ms=_ms(0)), now=_now(0))
    assert tracker.current_track_id == TRACK_A


# ---------------------------------------------------------------------------
# Active state: same track, same context
# ---------------------------------------------------------------------------


def test_active_same_track_no_event() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(_playing(track_id=TRACK_A, played_at_ms=_ms(0)), now=_now(0))
    events = tracker.on_poll(_playing(track_id=TRACK_A, played_at_ms=_ms(30)), now=_now(30))
    assert events == []
    assert tracker.state == SessionState.ACTIVE


def test_active_same_track_stays_active() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(_playing(track_id=TRACK_A, played_at_ms=_ms(0)), now=_now(0))
    for i in range(1, 5):
        tracker.on_poll(_playing(track_id=TRACK_A, played_at_ms=_ms(i * 30)), now=_now(i * 30))
    assert tracker.state == SessionState.ACTIVE


# ---------------------------------------------------------------------------
# Active state: track change (same context)
# ---------------------------------------------------------------------------


def test_active_track_change_emits_track_change_event() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(_playing(track_id=TRACK_A, played_at_ms=_ms(0)), now=_now(0))
    events = tracker.on_poll(
        _playing(track_id=TRACK_B, track_name="Song B", played_at_ms=_ms(60)),
        now=_now(60),
    )
    assert len(events) == 1
    assert events[0].event_type == "spotify.track_change"
    assert events[0].track_id == TRACK_B


def test_active_track_change_updates_current_track_id() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(_playing(track_id=TRACK_A, played_at_ms=_ms(0)), now=_now(0))
    tracker.on_poll(_playing(track_id=TRACK_B, played_at_ms=_ms(60)), now=_now(60))
    assert tracker.current_track_id == TRACK_B


def test_active_rapid_track_changes_each_emit_event() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(_playing(track_id=TRACK_A, played_at_ms=_ms(0)), now=_now(0))
    events_b = tracker.on_poll(_playing(track_id=TRACK_B, played_at_ms=_ms(1)), now=_now(1))
    events_c = tracker.on_poll(_playing(track_id=TRACK_C, played_at_ms=_ms(2)), now=_now(2))
    assert len(events_b) == 1
    assert events_b[0].track_id == TRACK_B
    assert len(events_c) == 1
    assert events_c[0].track_id == TRACK_C


# ---------------------------------------------------------------------------
# Active state: context change
# ---------------------------------------------------------------------------


def test_active_context_change_emits_session_summary() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(0)),
        now=_now(0),
    )
    # Play another track in same context
    tracker.on_poll(
        _playing(track_id=TRACK_B, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(180)),
        now=_now(180),
    )
    # Now switch context
    events = tracker.on_poll(
        _playing(track_id=TRACK_C, context_uri=CTX_PLAYLIST_2, played_at_ms=_ms(360)),
        now=_now(360),
    )
    summary_events = [e for e in events if e.event_type == "spotify.session_summary"]
    assert len(summary_events) == 1


def test_active_context_change_summary_track_count() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(0)),
        now=_now(0),
    )
    tracker.on_poll(
        _playing(track_id=TRACK_B, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(180)),
        now=_now(180),
    )
    events = tracker.on_poll(
        _playing(track_id=TRACK_C, context_uri=CTX_PLAYLIST_2, played_at_ms=_ms(360)),
        now=_now(360),
    )
    [summary] = [e for e in events if e.event_type == "spotify.session_summary"]
    assert summary.track_count == 2
    assert TRACK_A not in (summary.context_uri,)  # sanity
    assert summary.context_uri == CTX_PLAYLIST_1


def test_active_context_change_emits_track_change_for_new_context() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(0)),
        now=_now(0),
    )
    events = tracker.on_poll(
        _playing(track_id=TRACK_B, context_uri=CTX_PLAYLIST_2, played_at_ms=_ms(60)),
        now=_now(60),
    )
    track_change_events = [e for e in events if e.event_type == "spotify.track_change"]
    assert len(track_change_events) == 1
    assert track_change_events[0].track_id == TRACK_B
    assert track_change_events[0].context_uri == CTX_PLAYLIST_2


def test_active_context_change_events_order() -> None:
    """session_summary should be emitted before track_change for the new context."""
    tracker = ListeningSessionTracker()
    tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(0)),
        now=_now(0),
    )
    events = tracker.on_poll(
        _playing(track_id=TRACK_B, context_uri=CTX_PLAYLIST_2, played_at_ms=_ms(60)),
        now=_now(60),
    )
    assert len(events) == 2
    assert events[0].event_type == "spotify.session_summary"
    assert events[1].event_type == "spotify.track_change"


def test_active_context_change_starts_new_session() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(0)),
        now=_now(0),
    )
    tracker.on_poll(
        _playing(track_id=TRACK_B, context_uri=CTX_PLAYLIST_2, played_at_ms=_ms(60)),
        now=_now(60),
    )
    # One more track in CTX_PLAYLIST_2 — should be in the new session
    tracker.on_poll(
        _playing(track_id=TRACK_C, context_uri=CTX_PLAYLIST_2, played_at_ms=_ms(180)),
        now=_now(180),
    )
    # Context change again → emit summary for CTX_PLAYLIST_2 session
    events = tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(300)),
        now=_now(300),
    )
    [summary] = [e for e in events if e.event_type == "spotify.session_summary"]
    assert summary.context_uri == CTX_PLAYLIST_2
    assert summary.track_count == 2  # TRACK_B and TRACK_C


# ---------------------------------------------------------------------------
# Active → Draining transition
# ---------------------------------------------------------------------------


def test_active_playback_stopped_transitions_to_draining() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(_playing(played_at_ms=_ms(0)), now=_now(0))
    tracker.on_poll(_stopped(), now=_now(60))
    assert tracker.state == SessionState.DRAINING


def test_active_playback_stopped_no_event_yet() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(_playing(played_at_ms=_ms(0)), now=_now(0))
    events = tracker.on_poll(_stopped(), now=_now(60))
    assert events == []


# ---------------------------------------------------------------------------
# Draining state: timeout path
# ---------------------------------------------------------------------------


def test_draining_timeout_emits_session_summary() -> None:
    timeout_s = 300.0
    tracker = ListeningSessionTracker(idle_timeout_s=timeout_s)
    tracker.on_poll(_playing(played_at_ms=_ms(0)), now=_now(0))
    tracker.on_poll(_stopped(), now=_now(10))

    # Not yet timed out
    events = tracker.on_poll(_stopped(), now=_now(10 + timeout_s - 1))
    assert events == []
    assert tracker.state == SessionState.DRAINING

    # Now timed out
    events = tracker.on_poll(_stopped(), now=_now(10 + timeout_s + 1))
    assert len(events) == 1
    assert events[0].event_type == "spotify.session_summary"


def test_draining_timeout_transitions_to_idle() -> None:
    timeout_s = 60.0
    tracker = ListeningSessionTracker(idle_timeout_s=timeout_s)
    tracker.on_poll(_playing(played_at_ms=_ms(0)), now=_now(0))
    tracker.on_poll(_stopped(), now=_now(10))
    tracker.on_poll(_stopped(), now=_now(10 + timeout_s + 1))
    assert tracker.state == SessionState.IDLE


def test_draining_timeout_summary_includes_all_tracks() -> None:
    timeout_s = 300.0
    tracker = ListeningSessionTracker(idle_timeout_s=timeout_s)
    tracker.on_poll(
        _playing(track_id=TRACK_A, track_name="Song A", played_at_ms=_ms(0)), now=_now(0)
    )
    tracker.on_poll(
        _playing(track_id=TRACK_B, track_name="Song B", played_at_ms=_ms(60)), now=_now(60)
    )
    tracker.on_poll(_stopped(), now=_now(120))
    events = tracker.on_poll(_stopped(), now=_now(120 + timeout_s + 1))
    [summary] = events
    assert summary.track_count == 2
    assert "Song A" in summary.tracks_played
    assert "Song B" in summary.tracks_played


def test_draining_timeout_summary_duration() -> None:
    timeout_s = 10.0
    tracker = ListeningSessionTracker(idle_timeout_s=timeout_s)
    tracker.on_poll(_playing(played_at_ms=_ms(0)), now=_now(0))
    tracker.on_poll(_stopped(), now=_now(60))
    events = tracker.on_poll(_stopped(), now=_now(60 + timeout_s + 1))
    [summary] = events
    # Duration should be positive
    assert summary.session_duration_ms is not None
    assert summary.session_duration_ms > 0
    # Start should be at T0
    assert summary.session_start_ms == _ms(0)


def test_draining_timeout_resets_current_track_id() -> None:
    timeout_s = 10.0
    tracker = ListeningSessionTracker(idle_timeout_s=timeout_s)
    tracker.on_poll(_playing(played_at_ms=_ms(0)), now=_now(0))
    tracker.on_poll(_stopped(), now=_now(5))
    tracker.on_poll(_stopped(), now=_now(5 + timeout_s + 1))
    assert tracker.current_track_id is None


# ---------------------------------------------------------------------------
# Draining state: resume path (same context)
# ---------------------------------------------------------------------------


def test_draining_resume_same_context_transitions_to_active() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(0)),
        now=_now(0),
    )
    tracker.on_poll(_stopped(), now=_now(10))
    tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(20)),
        now=_now(20),
    )
    assert tracker.state == SessionState.ACTIVE


def test_draining_resume_same_context_no_session_summary() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(0)),
        now=_now(0),
    )
    tracker.on_poll(_stopped(), now=_now(10))
    events = tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(20)),
        now=_now(20),
    )
    summary_events = [e for e in events if e.event_type == "spotify.session_summary"]
    assert summary_events == []


def test_draining_resume_same_context_same_track_no_track_change() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(0)),
        now=_now(0),
    )
    tracker.on_poll(_stopped(), now=_now(10))
    events = tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(20)),
        now=_now(20),
    )
    track_changes = [e for e in events if e.event_type == "spotify.track_change"]
    assert track_changes == []


def test_draining_resume_same_context_new_track_emits_track_change() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(0)),
        now=_now(0),
    )
    tracker.on_poll(_stopped(), now=_now(10))
    events = tracker.on_poll(
        _playing(track_id=TRACK_B, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(20)),
        now=_now(20),
    )
    track_changes = [e for e in events if e.event_type == "spotify.track_change"]
    assert len(track_changes) == 1
    assert track_changes[0].track_id == TRACK_B


# ---------------------------------------------------------------------------
# Draining state: resume path (different context)
# ---------------------------------------------------------------------------


def test_draining_resume_different_context_emits_session_summary() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(0)),
        now=_now(0),
    )
    tracker.on_poll(_stopped(), now=_now(10))
    events = tracker.on_poll(
        _playing(track_id=TRACK_B, context_uri=CTX_PLAYLIST_2, played_at_ms=_ms(20)),
        now=_now(20),
    )
    summaries = [e for e in events if e.event_type == "spotify.session_summary"]
    assert len(summaries) == 1
    assert summaries[0].context_uri == CTX_PLAYLIST_1


def test_draining_resume_different_context_emits_track_change() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(0)),
        now=_now(0),
    )
    tracker.on_poll(_stopped(), now=_now(10))
    events = tracker.on_poll(
        _playing(track_id=TRACK_B, context_uri=CTX_PLAYLIST_2, played_at_ms=_ms(20)),
        now=_now(20),
    )
    changes = [e for e in events if e.event_type == "spotify.track_change"]
    assert len(changes) == 1
    assert changes[0].track_id == TRACK_B
    assert changes[0].context_uri == CTX_PLAYLIST_2


def test_draining_resume_different_context_transitions_to_active() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(0)),
        now=_now(0),
    )
    tracker.on_poll(_stopped(), now=_now(10))
    tracker.on_poll(
        _playing(track_id=TRACK_B, context_uri=CTX_PLAYLIST_2, played_at_ms=_ms(20)),
        now=_now(20),
    )
    assert tracker.state == SessionState.ACTIVE


# ---------------------------------------------------------------------------
# Session summary field correctness
# ---------------------------------------------------------------------------


def test_session_summary_context_uri() -> None:
    timeout_s = 10.0
    tracker = ListeningSessionTracker(idle_timeout_s=timeout_s)
    tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_ALBUM_1, played_at_ms=_ms(0)), now=_now(0)
    )
    tracker.on_poll(_stopped(), now=_now(5))
    [summary] = tracker.on_poll(_stopped(), now=_now(5 + timeout_s + 1))
    assert summary.context_uri == CTX_ALBUM_1


def test_session_summary_start_and_end_ms() -> None:
    timeout_s = 10.0
    tracker = ListeningSessionTracker(idle_timeout_s=timeout_s)
    tracker.on_poll(_playing(played_at_ms=_ms(0)), now=_now(0))
    drain_start = _now(60)
    tracker.on_poll(_stopped(), now=drain_start)
    [summary] = tracker.on_poll(_stopped(), now=_now(60 + timeout_s + 1))
    assert summary.session_start_ms == _ms(0)
    assert summary.session_end_ms is not None
    assert summary.session_end_ms > summary.session_start_ms


def test_session_summary_no_duplicate_track_in_track_count() -> None:
    """The same track_id should only be counted once in track_count."""
    timeout_s = 10.0
    tracker = ListeningSessionTracker(idle_timeout_s=timeout_s)
    # Same track polled three times (loop)
    for i in range(3):
        tracker.on_poll(_playing(track_id=TRACK_A, played_at_ms=_ms(i * 30)), now=_now(i * 30))
    tracker.on_poll(_stopped(), now=_now(90))
    [summary] = tracker.on_poll(_stopped(), now=_now(90 + timeout_s + 1))
    assert summary.track_count == 1


# ---------------------------------------------------------------------------
# Configurable idle timeout
# ---------------------------------------------------------------------------


def test_custom_idle_timeout_shorter() -> None:
    """A short timeout should end the session sooner."""
    tracker = ListeningSessionTracker(idle_timeout_s=5.0)
    tracker.on_poll(_playing(played_at_ms=_ms(0)), now=_now(0))
    tracker.on_poll(_stopped(), now=_now(10))
    events = tracker.on_poll(_stopped(), now=_now(16))  # 6s > 5s timeout
    assert len(events) == 1
    assert events[0].event_type == "spotify.session_summary"


def test_custom_idle_timeout_longer() -> None:
    """A long timeout should NOT end the session prematurely."""
    tracker = ListeningSessionTracker(idle_timeout_s=600.0)
    tracker.on_poll(_playing(played_at_ms=_ms(0)), now=_now(0))
    tracker.on_poll(_stopped(), now=_now(10))
    # Only 100s elapsed, well under 600s timeout
    events = tracker.on_poll(_stopped(), now=_now(110))
    assert events == []
    assert tracker.state == SessionState.DRAINING


def test_default_idle_timeout_constant() -> None:
    """The default idle timeout should match the spec (300 s)."""
    assert DEFAULT_IDLE_TIMEOUT_S == 300


# ---------------------------------------------------------------------------
# force_end_session
# ---------------------------------------------------------------------------


def test_force_end_session_from_active_emits_summary() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(_playing(played_at_ms=_ms(0)), now=_now(0))
    tracker.on_poll(_playing(track_id=TRACK_B, played_at_ms=_ms(60)), now=_now(60))
    events = tracker.force_end_session(now=_now(120))
    assert len(events) == 1
    assert events[0].event_type == "spotify.session_summary"
    assert events[0].track_count == 2


def test_force_end_session_from_active_transitions_to_idle() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(_playing(played_at_ms=_ms(0)), now=_now(0))
    tracker.force_end_session(now=_now(60))
    assert tracker.state == SessionState.IDLE


def test_force_end_session_from_draining_emits_summary() -> None:
    tracker = ListeningSessionTracker()
    tracker.on_poll(_playing(played_at_ms=_ms(0)), now=_now(0))
    tracker.on_poll(_stopped(), now=_now(10))
    events = tracker.force_end_session(now=_now(20))
    assert len(events) == 1
    assert events[0].event_type == "spotify.session_summary"


def test_force_end_session_from_idle_emits_nothing() -> None:
    tracker = ListeningSessionTracker()
    events = tracker.force_end_session(now=_now(0))
    assert events == []
    assert tracker.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_brief_pause_does_not_split_session() -> None:
    """A pause shorter than idle_timeout should not end the session."""
    timeout_s = 300.0
    tracker = ListeningSessionTracker(idle_timeout_s=timeout_s)
    tracker.on_poll(_playing(track_id=TRACK_A, played_at_ms=_ms(0)), now=_now(0))
    tracker.on_poll(_playing(track_id=TRACK_B, played_at_ms=_ms(60)), now=_now(60))
    # Short pause
    tracker.on_poll(_stopped(), now=_now(90))
    tracker.on_poll(_stopped(), now=_now(120))
    # Resumed — brief pause
    tracker.on_poll(
        _playing(track_id=TRACK_C, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(150)),
        now=_now(150),
    )
    # Context change to end session
    events = tracker.on_poll(
        _playing(track_id=TRACK_A, context_uri=CTX_PLAYLIST_2, played_at_ms=_ms(300)),
        now=_now(300),
    )
    [summary] = [e for e in events if e.event_type == "spotify.session_summary"]
    # All 3 tracks should be in the same session
    assert summary.track_count == 3


def test_context_none_does_not_trigger_context_change() -> None:
    """Two consecutive polls with context_uri=None should not trigger a context change."""
    tracker = ListeningSessionTracker()
    tracker.on_poll(_playing(track_id=TRACK_A, context_uri=None, played_at_ms=_ms(0)), now=_now(0))
    events = tracker.on_poll(
        _playing(track_id=TRACK_B, context_uri=None, played_at_ms=_ms(60)), now=_now(60)
    )
    # Only a track_change event, no session_summary
    summaries = [e for e in events if e.event_type == "spotify.session_summary"]
    assert summaries == []


def test_context_change_none_to_playlist() -> None:
    """Changing from no context to a playlist is a context change."""
    tracker = ListeningSessionTracker()
    tracker.on_poll(_playing(track_id=TRACK_A, context_uri=None, played_at_ms=_ms(0)), now=_now(0))
    events = tracker.on_poll(
        _playing(track_id=TRACK_B, context_uri=CTX_PLAYLIST_1, played_at_ms=_ms(60)),
        now=_now(60),
    )
    summaries = [e for e in events if e.event_type == "spotify.session_summary"]
    assert len(summaries) == 1


def test_played_at_ms_defaults_to_now() -> None:
    """When played_at_ms is None, the tracker should use now * 1000."""
    tracker = ListeningSessionTracker()
    result = PollResult(is_playing=True, track_id=TRACK_A)
    events = tracker.on_poll(result, now=_now(0))
    assert len(events) == 1
    assert events[0].played_at_ms == _ms(0)


def test_multiple_full_sessions_sequential() -> None:
    """Two complete sessions back-to-back should both produce summaries."""
    timeout_s = 10.0
    tracker = ListeningSessionTracker(idle_timeout_s=timeout_s)

    # Session 1
    tracker.on_poll(_playing(track_id=TRACK_A, played_at_ms=_ms(0)), now=_now(0))
    tracker.on_poll(_stopped(), now=_now(30))
    [s1] = tracker.on_poll(_stopped(), now=_now(30 + timeout_s + 1))
    assert s1.event_type == "spotify.session_summary"

    # Session 2
    tracker.on_poll(_playing(track_id=TRACK_B, played_at_ms=_ms(100)), now=_now(100))
    tracker.on_poll(_stopped(), now=_now(160))
    [s2] = tracker.on_poll(_stopped(), now=_now(160 + timeout_s + 1))
    assert s2.event_type == "spotify.session_summary"
    assert s2.track_count == 1
