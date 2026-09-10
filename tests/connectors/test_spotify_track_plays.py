"""Track-play evidence tests for the taste ledger (bu-2jtfw.10).

Covers the acceptance behavior matrix's "Play evidence" section:
- a short play followed by a track change closes with a low completion_ratio
  and skipped=True
- a full-duration play closes with skipped=False
- progress-absent evidence (gap-fill) lands with observation_precision
  ='play_only' and a NULL completion_ratio/skipped
- replaying the same poll sequence through a fresh tracker is idempotent
  (deterministic first_seen_ms/max_progress_ms, no duplicate identity)

All persistence functions are tested against a mocked asyncpg pool (same
convention as ``test_spotify_connector.py``'s ``persist_session_summary``
tests) — the real SQL/grants are covered by
``tests/migrations/test_spotify_track_plays_migration.py``.

Issue: bu-2jtfw.10
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.connectors.spotify import (
    TrackObservation,
    TrackPlayTracker,
    close_track_play,
    record_gap_fill_track_play,
    upsert_open_track_play,
)

_ENDPOINT = "spotify_user_client:spotify:user123"
_SPOTIFY_USER_ID = "user123"


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


# ---------------------------------------------------------------------------
# close_track_play: completion_ratio / skipped derivation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_short_play_closes_with_low_completion_and_skipped_true() -> None:
    """A ~5s play of a 250s track closes with completion_ratio ~= 0.02, skipped=True."""
    tracker = TrackPlayTracker()
    tracker.observe(
        _obs(track_uri="spotify:track:a", duration_ms=250_000, progress_ms=5_000, timestamp_ms=0)
    )
    closed = tracker.observe(
        _obs(track_uri="spotify:track:b", duration_ms=200_000, progress_ms=0, timestamp_ms=5_000)
    )
    assert closed is not None

    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="UPDATE 1")
    await close_track_play(pool, endpoint_identity=_ENDPOINT, evidence=closed)

    pool.execute.assert_awaited_once()
    params = pool.execute.call_args.args[1:]
    completion_ratio = params[5]
    skipped = params[6]
    assert completion_ratio == pytest.approx(0.02, abs=0.001)
    assert skipped is True


@pytest.mark.asyncio
async def test_full_duration_play_closes_with_skipped_false() -> None:
    tracker = TrackPlayTracker()
    tracker.observe(
        _obs(track_uri="spotify:track:a", duration_ms=200_000, progress_ms=198_000, timestamp_ms=0)
    )
    closed = tracker.observe(
        _obs(track_uri="spotify:track:b", duration_ms=200_000, progress_ms=0, timestamp_ms=200_000)
    )
    assert closed is not None

    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="UPDATE 1")
    await close_track_play(pool, endpoint_identity=_ENDPOINT, evidence=closed)

    params = pool.execute.call_args.args[1:]
    completion_ratio = params[5]
    skipped = params[6]
    assert completion_ratio == pytest.approx(0.99, abs=0.001)
    assert skipped is False


@pytest.mark.asyncio
async def test_play_only_evidence_closes_with_null_completion_and_skipped() -> None:
    """Absent progress (gap-fill / 204 / omission) is honest, not fabricated."""
    tracker = TrackPlayTracker()
    tracker.observe(
        _obs(track_uri="spotify:track:a", duration_ms=200_000, progress_ms=None, timestamp_ms=0)
    )
    closed = tracker.observe(
        _obs(track_uri="spotify:track:b", duration_ms=200_000, progress_ms=0, timestamp_ms=1000)
    )
    assert closed is not None
    assert closed.observation_precision == "play_only"

    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="UPDATE 1")
    await close_track_play(pool, endpoint_identity=_ENDPOINT, evidence=closed)

    params = pool.execute.call_args.args[1:]
    completion_ratio = params[5]
    skipped = params[6]
    assert completion_ratio is None
    assert skipped is None


# ---------------------------------------------------------------------------
# upsert_open_track_play / record_gap_fill_track_play — SQL shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_open_track_play_is_conflict_safe_and_keyed_on_identity() -> None:
    tracker = TrackPlayTracker()
    tracker.observe(_obs(track_uri="spotify:track:a", progress_ms=1000, timestamp_ms=0))
    evidence = tracker.snapshot_open()
    assert evidence is not None

    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    await upsert_open_track_play(
        pool, endpoint_identity=_ENDPOINT, spotify_user_id=_SPOTIFY_USER_ID, evidence=evidence
    )

    query = pool.execute.call_args.args[0]
    assert "ON CONFLICT (endpoint_identity, track_uri, first_seen_ms)" in query
    assert "GREATEST" in query


@pytest.mark.asyncio
async def test_gap_fill_track_play_is_recorded_already_closed_as_play_only() -> None:
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    await record_gap_fill_track_play(
        pool,
        endpoint_identity=_ENDPOINT,
        spotify_user_id=_SPOTIFY_USER_ID,
        track_uri="spotify:track:gap",
        track_name="Gap Track",
        duration_ms=180_000,
        played_at_ms=123456,
    )

    query, *params = pool.execute.call_args.args
    assert "'play_only'" in query
    assert "closed_at" in query
    # played_at_ms is used for both first_seen_ms and last_seen_ms.
    assert params[4] == 123456
