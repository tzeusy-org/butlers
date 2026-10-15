"""Listening digests report only new plays [bu-3e1wwi].

A session that stays active on one long track (overnight rain sounds) used to
emit "Listening digest: 0 tracks" every hour, and each empty digest cost a
routing session and a lifestyle session.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from butlers.connectors.spotify import ListeningSessionTracker, build_listening_digest_envelope

_START = datetime(2026, 10, 14, 18, 0, 0, tzinfo=UTC)
_HOUR = timedelta(hours=1)


def _play(tracker: ListeningSessionTracker, track_id: str, at: datetime) -> None:
    tracker.process_playback(
        track_id=track_id,
        track_name=f"Track {track_id}",
        context_uri="spotify:playlist:passive",
        context_name="Mah Passive",
        now=at,
    )


def _emit_digest(tracker: ListeningSessionTracker, at: datetime) -> dict:
    session = tracker.current_session
    assert session is not None
    envelope = build_listening_digest_envelope(
        endpoint_identity="spotify:user",
        spotify_user_id="user",
        session=session,
        observed_at=at.isoformat(),
    )
    tracker.mark_digest_emitted(at)
    return envelope


def test_no_digest_while_one_track_keeps_playing_after_the_last_digest() -> None:
    tracker = ListeningSessionTracker(digest_interval_s=3600)
    _play(tracker, "rain", _START)
    _play(tracker, "rain", _START + _HOUR)
    assert tracker.check_digest_due(_START + _HOUR)
    first = _emit_digest(tracker, _START + _HOUR)
    assert first["payload"]["raw"]["period_track_count"] == 1

    for hours in (2, 3, 8):
        at = _START + hours * _HOUR
        _play(tracker, "rain", at)
        assert tracker.state == "active"
        assert not tracker.check_digest_due(at), f"empty digest due after {hours}h"


def test_digest_resumes_with_only_the_new_tracks_once_playback_moves_on() -> None:
    tracker = ListeningSessionTracker(digest_interval_s=3600)
    _play(tracker, "rain", _START)
    _emit_digest(tracker, _START + _HOUR)
    _play(tracker, "rain", _START + 3 * _HOUR)
    assert not tracker.check_digest_due(_START + 3 * _HOUR)

    later = _START + 3 * _HOUR + timedelta(minutes=10)
    _play(tracker, "storm", later)
    assert tracker.check_digest_due(later)
    digest = _emit_digest(tracker, later)
    assert digest["payload"]["raw"]["period_track_count"] == 1
    assert "Track storm" in digest["payload"]["normalized_text"]
    assert "Track rain" not in digest["payload"]["normalized_text"]


def test_resuming_on_a_new_track_after_a_pause_makes_the_digest_due() -> None:
    tracker = ListeningSessionTracker(idle_timeout_s=600, digest_interval_s=3600)
    _play(tracker, "rain", _START)
    _emit_digest(tracker, _START + _HOUR)
    pause = _START + 2 * _HOUR
    tracker.process_no_playback(pause)
    assert tracker.state == "draining"
    assert not tracker.check_digest_due(pause)

    resumed = pause + timedelta(minutes=5)
    _play(tracker, "storm", resumed)
    assert tracker.state == "active"
    assert tracker.check_digest_due(resumed)
    assert _emit_digest(tracker, resumed)["payload"]["raw"]["period_tracks"] == ["Track storm"]


def test_new_tracks_still_wait_for_the_digest_interval() -> None:
    tracker = ListeningSessionTracker(digest_interval_s=3600)
    _play(tracker, "a", _START)
    _emit_digest(tracker, _START + _HOUR)
    _play(tracker, "b", _START + _HOUR + timedelta(minutes=5))
    assert not tracker.check_digest_due(_START + _HOUR + timedelta(minutes=5))
    assert tracker.check_digest_due(_START + 2 * _HOUR)
