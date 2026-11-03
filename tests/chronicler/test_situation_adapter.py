"""Wiring of the situational adapter (bu-r3fmja, REQ-butler-chronicler-016).

The precedence rules are covered in ``test_situation.py``; these tests check the
adapter's day loop, upserts, stale-block tombstoning and coordinate-free payloads
against a fake connection that answers by query shape.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from butlers.chronicler.adapters import situation as adapter_mod
from butlers.chronicler.adapters.situation import (
    TOMBSTONE_REASON,
    SituationInferredAdapter,
)
from butlers.chronicler.anchors import KIND_HOME, KIND_WORK, SOURCE_GRAPH, AnchorPlace
from butlers.chronicler.models import Layer, Precision

_NOW = datetime(2026, 11, 3, 1, 0, tzinfo=UTC)  # 09:00 SGT, Nov 3
_HOME = (1.2760, 103.8460)
_WORK = (1.2840, 103.8510)
_ANCHORS = (
    AnchorPlace(KIND_HOME, "home", *_HOME, 150, SOURCE_GRAPH),
    AnchorPlace(KIND_WORK, "Acme", *_WORK, 200, SOURCE_GRAPH),
)


def _place(start: datetime, hours: float, lat_lon: tuple[float, float]) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "start_at": start,
        "end_at": start + timedelta(hours=hours),
        "payload": {"centroid_lat": lat_lon[0], "centroid_lon": lat_lon[1], "bridged": True},
    }


class _Conn:
    def __init__(self, places: list[dict[str, Any]]) -> None:
        self.places = places
        self.executed: list[tuple[str, tuple]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "place_episode" in query and "start_at < $2" in query:
            start, end = args
            return [p for p in self.places if p["start_at"] < end and p["end_at"] > start]
        return []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        # The latest-ending place/movement episode, for the tail hold.
        last = max(self.places, key=lambda p: p["end_at"], default=None)
        if last is None:
            return None
        return {**last, "episode_type": "place_episode"}

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "UPDATE 0"


class _Ctx:
    def __init__(self, obj: Any) -> None:
        self.obj = obj

    async def __aenter__(self) -> Any:
        return self.obj

    async def __aexit__(self, *_: Any) -> None:
        return None


def _pool(conn: _Conn) -> MagicMock:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Ctx(conn))
    pool.fetch = conn.fetch
    pool.fetchrow = conn.fetchrow
    return pool


async def _run(
    places: list[dict[str, Any]], steam_start: datetime | None = None
) -> tuple[list[Any], _Conn]:
    conn = _Conn(places)
    pool = _pool(conn)
    pool.fetchval = AsyncMock(return_value=steam_start)
    upserted: list[Any] = []

    async def fake_upsert(_conn: Any, episode: Any) -> Any:
        upserted.append(episode)
        episode.id = uuid4()
        return episode

    with (
        patch.object(adapter_mod, "_now", return_value=_NOW),
        patch.object(adapter_mod, "load_anchors", AsyncMock(return_value=_ANCHORS)),
        patch.object(adapter_mod, "resolve_owner_entity_id", AsyncMock(return_value=None)),
        patch.object(adapter_mod, "upsert_episode", fake_upsert),
        patch.object(adapter_mod, "upsert_owner_episode_entity", AsyncMock()),
        patch.object(adapter_mod, "owner_episode_transaction", lambda _c: contextlib.nullcontext()),
    ):
        adapter = SituationInferredAdapter(
            lookback_days=2, timezone="Asia/Singapore", references=()
        )
        result = await adapter.project(pool, chronicler_pool=pool, since=None)
    assert result.error is None
    assert result.watermark == _NOW
    return upserted, conn


async def test_a_work_day_projects_work_and_home_blocks() -> None:
    nov2 = datetime(2026, 11, 2, tzinfo=UTC)  # 08:00 SGT
    places = [
        _place(nov2 - timedelta(hours=8), 9, _HOME),  # 00:00-09:00 SGT
        _place(nov2 + timedelta(hours=1.5), 9, _WORK),  # 09:30-18:30 SGT
    ]

    upserted, _ = await _run(places)

    by_type = {e.episode_type for e in upserted}
    assert by_type == {"home_block", "work_presence_block"}
    work = next(e for e in upserted if e.episode_type == "work_presence_block")
    assert (work.layer, work.precision, work.title) == (
        Layer.ACTIVITY,
        Precision.MINUTE,
        "At work (Acme)",
    )
    assert work.source_ref.startswith("chronicler.situation:2026-11-02:work_presence:")
    assert work.payload["local_date"] == "2026-11-02"
    assert work.evidence_refs == [str(places[1]["id"])]


async def test_payloads_never_carry_coordinates() -> None:
    nov2 = datetime(2026, 11, 2, tzinfo=UTC)
    upserted, _ = await _run([_place(nov2, 6, _WORK)])

    for episode in upserted:
        flat = repr(episode.payload) + (episode.title or "")
        assert "lat" not in episode.payload and "lon" not in episode.payload
        assert str(_WORK[0]) not in flat and str(_WORK[1]) not in flat


async def test_every_day_in_the_window_is_rederived_and_stale_rows_tombstoned() -> None:
    nov2 = datetime(2026, 11, 2, tzinfo=UTC)
    upserted, conn = await _run([_place(nov2, 6, _WORK)])

    tombstoned_days = [args[1] for _q, args in conn.executed]
    assert tombstoned_days == ["2026-11-02", "2026-11-03"]
    for query, args in conn.executed:
        assert "overrides" in query
        assert args[2] == TOMBSTONE_REASON
    nov2_refs = next(args[3] for _q, args in conn.executed if args[1] == "2026-11-02")
    assert nov2_refs == [e.source_ref for e in upserted if e.payload["local_date"] == "2026-11-02"]


async def test_status_sessions_suppress_daily_guess_but_missing_sessions_use_fallback():
    start = _NOW - timedelta(hours=2)
    daily = dict(
        source_name="steam.play_history",
        episode_type="play_episode",
        start_at=start,
        end_at=_NOW,
        layer="evidence",
        payload={},
    )
    session = dict(
        source_name="steam.status_history",
        episode_type="play_session",
        start_at=start,
        end_at=_NOW - timedelta(hours=1),
        layer="evidence",
        payload={},
    )
    conn = AsyncMock()
    conn.fetch.return_value = [daily, session]
    adapter = SituationInferredAdapter()
    spans = await adapter._activities(conn, start, _NOW)
    assert [(a.source_name, a.start_at, a.end_at) for a in spans] == [
        ("steam.status_history", start, session["end_at"])
    ]
    # Positive fallback companion: the same daily window is kept when no session exists.
    conn.fetch.return_value = [daily]
    spans = await adapter._activities(conn, start, _NOW)
    assert [(a.source_name, a.start_at, a.end_at) for a in spans] == [
        ("steam.play_history", start, _NOW)
    ]


async def test_changed_historical_steam_date_extends_trailing_rederivation():
    start = _NOW - timedelta(days=8)
    episodes, _ = await _run([_place(start, 2, _HOME)], steam_start=start)
    assert any(e.start_at == start and e.end_at == start + timedelta(hours=2) for e in episodes)
