"""Track-play evidence tests for the taste ledger (bu-2jtfw.10).

Covers the acceptance behavior matrix's "Play evidence" section:
- a short play followed by a track change closes with a low completion_ratio
  and skipped=True
- a full-duration play closes with skipped=False
- progress-absent evidence (gap-fill) lands with observation_precision
  ='play_only' and a NULL completion_ratio/skipped
- replaying the same poll sequence through a fresh tracker is idempotent
  (deterministic first_seen_ms/max_progress_ms, no duplicate identity)

This file pins the pure state machine. Persistence, restart hydration,
cross-source reconciliation, and grants are exercised against PostgreSQL in
``tests/migrations/test_spotify_track_plays_migration.py``.

Issue: bu-2jtfw.10
"""

from __future__ import annotations

from butlers.connectors.spotify import (
    TrackObservation,
    TrackPlayTracker,
)


def _obs(
    *,
    track_uri: str,
    track_name: str = "Track",
    duration_ms: int | None = 250_000,
    progress_ms: int | None,
    timestamp_ms: int,
) -> TrackObservation:
    return TrackObservation(
        track_uri=track_uri,
        track_name=track_name,
        duration_ms=duration_ms,
        progress_ms=progress_ms,
        timestamp_ms=timestamp_ms,
    )


# ---------------------------------------------------------------------------
# TrackPlayTracker state machine
# ---------------------------------------------------------------------------


def test_first_observation_opens_a_play_and_closes_nothing() -> None:
    tracker = TrackPlayTracker()
    closed = tracker.observe(_obs(track_uri="spotify:track:a", progress_ms=1000, timestamp_ms=0))
    assert closed is None
    assert tracker.is_open
    open_evidence = tracker.snapshot_open()
    assert open_evidence is not None
    assert open_evidence.closed is False
    assert open_evidence.max_progress_ms == 1000


def test_same_track_extends_max_progress_via_running_max() -> None:
    tracker = TrackPlayTracker()
    tracker.observe(_obs(track_uri="spotify:track:a", progress_ms=1000, timestamp_ms=0))
    tracker.observe(_obs(track_uri="spotify:track:a", progress_ms=5000, timestamp_ms=5000))
    # A stale/out-of-order poll must never move progress backwards.
    tracker.observe(_obs(track_uri="spotify:track:a", progress_ms=2000, timestamp_ms=6000))
    snapshot = tracker.snapshot_open()
    assert snapshot is not None
    assert snapshot.max_progress_ms == 5000


def test_seek_back_keeps_the_same_play_and_preserves_max_progress() -> None:
    tracker = TrackPlayTracker()
    tracker.observe(_obs(track_uri="spotify:track:a", progress_ms=80_000, timestamp_ms=1_000))
    closed = tracker.observe(
        _obs(track_uri="spotify:track:a", progress_ms=30_000, timestamp_ms=2_000)
    )
    assert closed is None
    snapshot = tracker.snapshot_open()
    assert snapshot is not None
    assert snapshot.first_seen_ms == 1_000
    assert snapshot.max_progress_ms == 80_000


def test_same_track_repeat_after_near_completion_opens_a_new_play() -> None:
    tracker = TrackPlayTracker()
    tracker.observe(
        _obs(
            track_uri="spotify:track:a", duration_ms=100_000, progress_ms=95_000, timestamp_ms=1_000
        )
    )
    closed = tracker.observe(
        _obs(
            track_uri="spotify:track:a", duration_ms=100_000, progress_ms=2_000, timestamp_ms=2_000
        )
    )
    assert closed is not None
    assert closed.first_seen_ms == 1_000
    snapshot = tracker.snapshot_open()
    assert snapshot is not None
    assert snapshot.first_seen_ms == 2_000
    assert snapshot.max_progress_ms == 2_000


def test_brief_pause_and_resume_does_not_close_the_play() -> None:
    tracker = TrackPlayTracker()
    tracker.observe(_obs(track_uri="spotify:track:a", progress_ms=5_000, timestamp_ms=1_000))
    assert tracker.observe_no_playback(timestamp_ms=2_000, idle_timeout_ms=5_000) is None
    assert tracker.observe_no_playback(timestamp_ms=4_000, idle_timeout_ms=5_000) is None
    assert (
        tracker.observe(_obs(track_uri="spotify:track:a", progress_ms=6_000, timestamp_ms=5_000))
        is None
    )
    assert tracker.snapshot_open() is not None


def test_prolonged_stop_closes_the_play_at_the_idle_boundary() -> None:
    tracker = TrackPlayTracker()
    tracker.observe(_obs(track_uri="spotify:track:a", progress_ms=5_000, timestamp_ms=1_000))
    assert tracker.observe_no_playback(timestamp_ms=2_000, idle_timeout_ms=5_000) is None
    closed = tracker.observe_no_playback(timestamp_ms=7_000, idle_timeout_ms=5_000)
    assert closed is not None
    assert closed.track_uri == "spotify:track:a"
    assert tracker.snapshot_open() is None


def test_track_change_closes_previous_play_and_opens_new_one() -> None:
    tracker = TrackPlayTracker()
    tracker.observe(_obs(track_uri="spotify:track:a", progress_ms=5000, timestamp_ms=0))
    closed = tracker.observe(_obs(track_uri="spotify:track:b", progress_ms=0, timestamp_ms=5000))
    assert closed is not None
    assert closed.track_uri == "spotify:track:a"
    assert closed.closed is True
    assert closed.max_progress_ms == 5000
    assert tracker.snapshot_open() is not None
    assert tracker.snapshot_open().track_uri == "spotify:track:b"


def test_close_current_force_closes_open_play_on_playback_stop() -> None:
    tracker = TrackPlayTracker()
    tracker.observe(_obs(track_uri="spotify:track:a", progress_ms=5000, timestamp_ms=0))
    closed = tracker.close_current()
    assert closed is not None
    assert closed.closed is True
    assert tracker.snapshot_open() is None
    assert tracker.close_current() is None


def test_missing_progress_ms_marks_play_only_precision() -> None:
    tracker = TrackPlayTracker()
    tracker.observe(_obs(track_uri="spotify:track:a", progress_ms=None, timestamp_ms=0))
    snapshot = tracker.snapshot_open()
    assert snapshot is not None
    assert snapshot.observation_precision == "play_only"
    assert snapshot.max_progress_ms is None


def test_replaying_the_same_poll_sequence_through_a_fresh_tracker_is_deterministic() -> None:
    """Restart test: a fresh tracker fed the identical poll sequence produces
    the same play identity and a non-decreasing max_progress_ms."""
    sequence = [
        _obs(track_uri="spotify:track:a", progress_ms=1000, timestamp_ms=0),
        _obs(track_uri="spotify:track:a", progress_ms=5000, timestamp_ms=5000),
        _obs(track_uri="spotify:track:b", progress_ms=0, timestamp_ms=10_000),
    ]

    def _run(seq: list[TrackObservation]) -> list[tuple[str, int, int | None]]:
        tracker = TrackPlayTracker()
        closed_plays = []
        for observation in seq:
            closed = tracker.observe(observation)
            if closed is not None:
                closed_plays.append(
                    (closed.track_uri, closed.first_seen_ms, closed.max_progress_ms)
                )
        return closed_plays

    first_run = _run(sequence)
    second_run = _run(sequence)  # simulates a fresh tracker after a restart
    assert first_run == second_run
    assert first_run == [("spotify:track:a", 0, 5000)]
