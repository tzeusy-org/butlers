"""Candidate-activity confidence + evidence_refs + inferred-exercise tests (bu-1sj3zn).

Covers the deterministic candidate-projection seam (tasks.md §5–§6a):

* Each existing activity adapter stamps a ``confidence`` derived from its
  per-rule evidence kinds — google_health (sleep/workout), sessions (work),
  spotify/steam (play), owntracks (travel).
* Adapters that link point events populate ``evidence_refs`` from them
  (sessions boundary events, owntracks GPS fixes).
* A genuinely-NEW inferred ``exercise_episode`` candidate is emitted from
  HR+GPS corroboration, guarded against duplicating an explicit workout.

Every assertion exercises a pure deterministic code path — no LLM is invoked
anywhere in projection (RFC 0014 §D5 / "Candidate emitted without LLM").
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from butlers.chronicler.adapters.exercise import ExerciseInferredAdapter
from butlers.chronicler.adapters.google_health import (
    GoogleHealthSleepAdapter,
    GoogleHealthWorkoutAdapter,
)
from butlers.chronicler.adapters.owntracks import OwnTracksPointAdapter
from butlers.chronicler.adapters.sessions import CoreSessionsAdapter
from butlers.chronicler.adapters.spotify import SpotifySessionAdapter
from butlers.chronicler.adapters.steam import SteamPlayAdapter
from butlers.chronicler.models import Confidence, Episode, Layer, PointEvent

_NOW = datetime(2026, 5, 8, 9, 0, tzinfo=UTC)


# ── shared mock scaffolding ────────────────────────────────────────────────


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


def _row(**values: object) -> MagicMock:
    return MagicMock(**values, **{"__getitem__": lambda s, k, _v=values: _v[k]})


def _chronicler_pool(fetch_rows: list[object] | None = None) -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_NullCtx())
    conn.fetch = AsyncMock(return_value=list(fetch_rows or []))
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


async def _noop_owner(*_a: object, **_k: object) -> None:
    return None


def _capture_upsert(sink: list[Episode]):
    async def _fake(_conn: object, episode: Episode) -> Episode:
        if episode.id is None:
            episode.id = uuid4()
        sink.append(episode)
        return episode

    return _fake


# ── google_health: sleep → medium ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_sleep_episode_confidence_medium() -> None:
    adapter = GoogleHealthSleepAdapter()
    captured: list[Episode] = []
    row = _row(
        id="fact-sleep-1",
        idempotency_key="google_health:sleep:s1",
        valid_at=_NOW,
        metadata={"duration_ms": 28_800_000, "efficiency": 91},
    )
    with (
        patch(
            "butlers.chronicler.adapters.google_health.upsert_episode",
            side_effect=_capture_upsert(captured),
        ),
        patch(
            "butlers.chronicler.adapters.google_health.upsert_owner_episode_entity",
            side_effect=_noop_owner,
        ),
    ):
        await adapter._project_row(_chronicler_pool(), row)

    assert captured[0].layer is Layer.ACTIVITY
    assert captured[0].confidence is Confidence.MEDIUM


# ── google_health: workout → medium / high (with HR) ────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"activity_type": "run", "duration_ms": 1_800_000}, Confidence.MEDIUM),
        (
            {"activity_type": "run", "duration_ms": 1_800_000, "average_heart_rate": 150},
            Confidence.HIGH,
        ),
    ],
)
async def test_workout_episode_confidence(metadata: dict, expected: Confidence) -> None:
    adapter = GoogleHealthWorkoutAdapter()
    captured: list[Episode] = []
    row = _row(
        id="fact-wk-1", idempotency_key="google_health:workout:w1", valid_at=_NOW, metadata=metadata
    )
    with (
        patch(
            "butlers.chronicler.adapters.google_health.upsert_episode",
            side_effect=_capture_upsert(captured),
        ),
        patch(
            "butlers.chronicler.adapters.google_health.upsert_owner_episode_entity",
            side_effect=_noop_owner,
        ),
    ):
        await adapter._project_row(_chronicler_pool(), row)

    assert captured[0].confidence is expected


# ── sessions: work → medium + evidence_refs from boundary events ────────────


@pytest.mark.asyncio
async def test_work_episode_confidence_and_evidence_refs() -> None:
    adapter = CoreSessionsAdapter(butler_schemas=("general",))
    captured: list[Episode] = []

    async def _fake_point_event(_conn: object, event: PointEvent) -> PointEvent:
        event.id = uuid4()
        return event

    row = _row(
        id=42,
        started_at=_NOW,
        completed_at=_NOW + timedelta(minutes=30),
        trigger_source="trigger",
        success=True,
        duration_ms=1_800_000,
        model="claude",
    )
    with (
        patch(
            "butlers.chronicler.adapters.sessions.upsert_episode",
            side_effect=_capture_upsert(captured),
        ),
        patch(
            "butlers.chronicler.adapters.sessions.upsert_point_event",
            side_effect=_fake_point_event,
        ),
        patch(
            "butlers.chronicler.adapters.sessions.link_event_to_episode", side_effect=_noop_owner
        ),
        patch(
            "butlers.chronicler.adapters.sessions.upsert_owner_episode_entity",
            side_effect=_noop_owner,
        ),
    ):
        await adapter._project_row(_chronicler_pool(), "general", row, contact_info=(None, None))

    ep = captured[0]
    assert ep.confidence is Confidence.MEDIUM
    # Two boundary events (started + completed) recorded as evidence refs.
    assert len(ep.evidence_refs) == 2


# ── spotify / steam: play → medium ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_listening_episode_confidence_medium() -> None:
    adapter = SpotifySessionAdapter()
    captured: list[Episode] = []
    row = _row(
        idempotency_key="spot-1",
        endpoint_identity="acct",
        spotify_user_id="u1",
        track_count=5,
        duration_seconds=900,
        context_uri="spotify:playlist:x",
        context_name="Focus",
        track_names=["a", "b"],
        started_at=_NOW,
        ended_at=_NOW + timedelta(minutes=15),
    )
    with (
        patch(
            "butlers.chronicler.adapters.spotify.upsert_episode",
            side_effect=_capture_upsert(captured),
        ),
        patch(
            "butlers.chronicler.adapters.spotify.upsert_owner_episode_entity",
            side_effect=_noop_owner,
        ),
    ):
        await adapter._project_row(_chronicler_pool(), row)

    assert captured[0].confidence is Confidence.MEDIUM


@pytest.mark.asyncio
async def test_play_episode_confidence_medium() -> None:
    adapter = SteamPlayAdapter()
    captured: list[Episode] = []
    row = _row(
        steam_id="76561",
        steam_account_id=None,
        app_id=440,
        app_name="TF2",
        date=date(2026, 5, 8),
        playtime_minutes=90,
        recorded_at=_NOW,
    )
    with (
        patch(
            "butlers.chronicler.adapters.steam.upsert_episode",
            side_effect=_capture_upsert(captured),
        ),
        patch(
            "butlers.chronicler.adapters.steam.upsert_owner_episode_entity",
            side_effect=_noop_owner,
        ),
    ):
        await adapter._project_row(_chronicler_pool(), row)

    assert captured[0].confidence is Confidence.MEDIUM


# ── owntracks: travel → low + evidence_refs from GPS fixes ──────────────────


def _ot_row(key: str, ts: datetime) -> dict:
    return {
        "id": uuid4(),
        "idempotency_key": key,
        "ts": ts,
        "lat": 1.0,
        "lon": 2.0,
        "accuracy": 5.0,
        "trigger": "u",
        "event": None,
        "endpoint_identity": "phone",
        "raw_payload": {},
        "recorded_at": ts,
    }


@pytest.mark.asyncio
async def test_movement_episode_confidence_low_with_evidence_refs() -> None:
    adapter = OwnTracksPointAdapter()
    captured: list[Episode] = []
    r1 = _ot_row("k1", _NOW)
    r2 = _ot_row("k2", _NOW + timedelta(minutes=5))
    id1, id2 = uuid4(), uuid4()
    event_id_by_key = {"k1": id1, "k2": id2}

    with (
        patch(
            "butlers.chronicler.adapters.owntracks.upsert_episode",
            side_effect=_capture_upsert(captured),
        ),
        patch(
            "butlers.chronicler.adapters.owntracks.link_event_to_episode",
            side_effect=_noop_owner,
        ),
        patch(
            "butlers.chronicler.adapters.owntracks.upsert_owner_episode_entity",
            side_effect=_noop_owner,
        ),
    ):
        count, _carry = await adapter._project_movement_episodes(
            _chronicler_pool(), [r1, r2], {}, event_id_by_key=event_id_by_key
        )

    assert count == 1
    ep = captured[0]
    assert ep.confidence is Confidence.LOW
    assert ep.evidence_refs == [str(id1), str(id2)]


# ── inferred exercise: HR+GPS → high; no-duplication + no-corroboration guards


def _movement_candidate(*, overlaps_workout: bool) -> MagicMock:
    return _row(
        id=uuid4(),
        source_ref="connectors.owntracks_points:movement:phone:1",
        start_at=_NOW,
        end_at=_NOW + timedelta(minutes=40),
        created_at=_NOW + timedelta(minutes=41),
        overlaps_workout=overlaps_workout,
    )


@pytest.mark.asyncio
async def test_inferred_exercise_high_confidence_with_evidence_refs() -> None:
    adapter = ExerciseInferredAdapter()
    captured: list[Episode] = []
    hr_id = uuid4()
    pool = _chronicler_pool(fetch_rows=[_row(id=hr_id)])

    with (
        patch(
            "butlers.chronicler.adapters.exercise.upsert_episode",
            side_effect=_capture_upsert(captured),
        ),
        patch(
            "butlers.chronicler.adapters.exercise.link_event_to_episode",
            side_effect=_noop_owner,
        ),
        patch(
            "butlers.chronicler.adapters.exercise.upsert_owner_episode_entity",
            side_effect=_noop_owner,
        ),
    ):
        ep = await adapter._maybe_project(pool, _movement_candidate(overlaps_workout=False))

    assert ep is not None
    assert captured[0].episode_type == "exercise_episode"
    assert captured[0].confidence is Confidence.HIGH
    assert captured[0].evidence_refs == [str(hr_id)]


@pytest.mark.asyncio
async def test_inferred_exercise_suppressed_when_workout_overlaps() -> None:
    """No-duplication guard: an explicit workout already represents the window."""
    adapter = ExerciseInferredAdapter()
    captured: list[Episode] = []
    pool = _chronicler_pool(fetch_rows=[_row(id=uuid4())])

    with patch(
        "butlers.chronicler.adapters.exercise.upsert_episode",
        side_effect=_capture_upsert(captured),
    ):
        ep = await adapter._maybe_project(pool, _movement_candidate(overlaps_workout=True))

    assert ep is None
    assert captured == []


@pytest.mark.asyncio
async def test_inferred_exercise_suppressed_without_elevated_hr() -> None:
    """GPS movement alone is travel, not exercise — nothing new to emit."""
    adapter = ExerciseInferredAdapter()
    captured: list[Episode] = []
    pool = _chronicler_pool(fetch_rows=[])  # no elevated-HR events in window

    with patch(
        "butlers.chronicler.adapters.exercise.upsert_episode",
        side_effect=_capture_upsert(captured),
    ):
        ep = await adapter._maybe_project(pool, _movement_candidate(overlaps_workout=False))

    assert ep is None
    assert captured == []


# ── no-LLM guarantee: full deterministic project() path emits a candidate ───


@pytest.mark.asyncio
async def test_exercise_project_is_pure_deterministic_no_llm() -> None:
    """End-to-end project() emits a candidate via DB mocks only — no LLM in path."""
    adapter = ExerciseInferredAdapter()
    captured: list[Episode] = []

    # chronicler_pool.fetch is called twice: once for movement candidates, once
    # per candidate for elevated-HR events. Sequence the return values.
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_NullCtx())
    conn.fetch = AsyncMock(
        side_effect=[
            [_movement_candidate(overlaps_workout=False)],  # candidate query
            [_row(id=uuid4())],  # elevated-HR query
        ]
    )
    chron_pool = AsyncMock()
    chron_pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    with (
        patch(
            "butlers.chronicler.adapters.exercise.upsert_episode",
            side_effect=_capture_upsert(captured),
        ),
        patch(
            "butlers.chronicler.adapters.exercise.link_event_to_episode",
            side_effect=_noop_owner,
        ),
        patch(
            "butlers.chronicler.adapters.exercise.upsert_owner_episode_entity",
            side_effect=_noop_owner,
        ),
        patch(
            "butlers.chronicler.adapters.exercise.resolve_owner_entity_id",
            side_effect=AsyncMock(return_value=None),
        ),
    ):
        result = await adapter.project(AsyncMock(), chronicler_pool=chron_pool, since=None)

    assert result.rows_projected == 1
    assert captured[0].confidence is Confidence.HIGH


def test_uuid_evidence_refs_are_strings() -> None:
    """Sanity: evidence_refs are stringified ids (matches storage column type)."""
    from butlers.chronicler.confidence import evidence_refs_from_event_ids

    a, b = uuid4(), uuid4()
    refs = evidence_refs_from_event_ids([a, b, a])
    assert refs == [str(a), str(b)]
    assert all(isinstance(r, str) for r in refs)
    assert UUID(refs[0])  # parseable
