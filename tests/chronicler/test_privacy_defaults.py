"""Tests for the privacy contract on Spotify and OwnTracks adapters.

Privacy contract (as of bu-6c5i6):
- Spotify session summaries: privacy=normal (track names and duration are
  not sensitive; blanket sensitive was causing opaque lanes on the dashboard).
- OwnTracks point events and movement episodes: privacy=sensitive (GPS
  coordinates ARE personally identifying).
- Restricted episodes remain fully hidden (escape hatch preserved).

Reproducer tests:
- test_spotify_default_privacy_is_normal — FAILS before fix, PASSES after.
- test_owntracks_point_event_privacy_is_sensitive — already correct, must stay.
- test_owntracks_movement_episode_privacy_is_sensitive — already correct, must stay.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.chronicler.adapters.owntracks import OwnTracksPointAdapter
from butlers.chronicler.adapters.spotify import SpotifySessionAdapter
from butlers.chronicler.models import Episode, PointEvent, Privacy

_NOW = datetime(2026, 3, 26, 10, 0, 0, tzinfo=UTC)
_SPOTIFY_ENDPOINT = "spotify_user_client:spotify:user123"
_OWNTRACKS_ENDPOINT = "owntracks:alice"


# ---------------------------------------------------------------------------
# Helpers (copied from per-adapter test modules to keep this file self-contained)
# ---------------------------------------------------------------------------


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


class _NullCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        pass


def _spotify_pool(row: dict) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(
        return_value=[MagicMock(**row, **{"__getitem__": lambda s, k, _r=row: _r[k]})]
    )
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _owntracks_pool(row: dict) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(
        return_value=[MagicMock(**row, **{"__getitem__": lambda s, k, _r=row: _r[k]})]
    )
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _chronicler_pool() -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_NullCtx())
    conn.fetchrow = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _make_spotify_row() -> dict:
    return {
        "id": 1,
        "idempotency_key": "spotify:ep:session:1711447200000",
        "endpoint_identity": _SPOTIFY_ENDPOINT,
        "spotify_user_id": "user123",
        "started_at": _NOW,
        "ended_at": _NOW + timedelta(minutes=30),
        "duration_seconds": 1800,
        "track_count": 5,
        "track_names": ["Song A", "Song B"],
        "context_uri": "spotify:playlist:abc",
        "context_name": "Deep Focus",
        "recorded_at": _NOW,
    }


def _make_owntracks_row(ts: datetime = _NOW) -> dict:
    ikey = f"owntracks:{_OWNTRACKS_ENDPOINT}:{int(ts.timestamp())}:location"
    return {
        "id": "some-uuid",
        "idempotency_key": ikey,
        "ts": ts,
        "lat": 1.2345,
        "lon": 103.8765,
        "accuracy": 10.0,
        "trigger": "p",
        "event": None,
        "endpoint_identity": _OWNTRACKS_ENDPOINT,
        "raw_payload": {"_type": "location", "lat": 1.2345, "lon": 103.8765},
        "recorded_at": ts,
    }


# ---------------------------------------------------------------------------
# Reproducer: Spotify privacy was sensitive (causing opaque lane)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spotify_default_privacy_is_normal() -> None:
    """Spotify session_summary episodes MUST use privacy=normal.

    The blanket sensitive default was causing the Music lane to render as
    opaque 'Private activity / sensitive' placeholders on the dashboard,
    hiding track names and duration that are not personally sensitive.

    This test reproduces the bug (fails before fix) and guards against
    regression after the fix.
    """
    row = _make_spotify_row()
    adapter = SpotifySessionAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _spotify_pool(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.spotify.upsert_episode", side_effect=_fake_upsert):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert len(upserted) == 1
    ep = upserted[0]
    # Track names and duration are not sensitive. Default must be normal.
    assert ep.privacy == Privacy.NORMAL, (
        f"Expected Privacy.NORMAL for Spotify session summary, got {ep.privacy!r}. "
        "Blanket sensitive on spotify.session_summary makes the Music lane opaque "
        "on the dashboard (bu-6c5i6)."
    )


# ---------------------------------------------------------------------------
# Invariant: OwnTracks point event privacy is normal (owner-view dashboard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owntracks_point_event_privacy_is_normal() -> None:
    """OwnTracks location point events default to privacy=normal.

    The Chronicles dashboard is the owner's view of their own location
    history; blanket sensitive masking hid the trail and made the Map
    widget useless. Per-recipient masking for shared/screenshot views
    should be reintroduced via an explicit toggle (core_086, bu-6c5i6).
    """
    row = _make_owntracks_row()
    adapter = OwnTracksPointAdapter()
    upserted_events: list[PointEvent] = []

    async def _fake_upsert_point(conn: object, event: PointEvent) -> PointEvent:
        upserted_events.append(event)
        return event

    pool = _owntracks_pool(row)
    cp = _chronicler_pool()

    with (
        patch(
            "butlers.chronicler.adapters.owntracks.upsert_point_event",
            side_effect=_fake_upsert_point,
        ),
        patch("butlers.chronicler.adapters.owntracks.upsert_episode", return_value=None),
        patch(
            "butlers.chronicler.adapters.owntracks.get_carryover",
            return_value={},
        ),
        patch("butlers.chronicler.adapters.owntracks.save_carryover"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert len(upserted_events) == 1
    ev = upserted_events[0]
    assert ev.privacy == Privacy.NORMAL, (
        f"Expected Privacy.NORMAL for OwnTracks point event under the owner-view "
        f"dashboard contract, got {ev.privacy!r}."
    )


# ---------------------------------------------------------------------------
# Invariant: OwnTracks movement episode privacy is normal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owntracks_movement_episode_privacy_is_normal() -> None:
    """OwnTracks movement_episode spans default to privacy=normal.

    Same rationale as the point-event invariant: the dashboard is the
    owner's view of their own travel trajectory.
    """
    row = _make_owntracks_row()
    adapter = OwnTracksPointAdapter()
    upserted_episodes: list[Episode] = []

    async def _fake_upsert_episode(conn: object, episode: Episode) -> Episode:
        upserted_episodes.append(episode)
        return episode

    pool = _owntracks_pool(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event", return_value=None),
        patch(
            "butlers.chronicler.adapters.owntracks.upsert_episode",
            side_effect=_fake_upsert_episode,
        ),
        patch(
            "butlers.chronicler.adapters.owntracks.get_carryover",
            return_value={},
        ),
        patch("butlers.chronicler.adapters.owntracks.save_carryover"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert len(upserted_episodes) == 1
    ep = upserted_episodes[0]
    assert ep.privacy == Privacy.NORMAL, (
        f"Expected Privacy.NORMAL for OwnTracks movement episode under the "
        f"owner-view dashboard contract, got {ep.privacy!r}."
    )
