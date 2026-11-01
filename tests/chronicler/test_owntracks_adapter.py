"""Tests for the OwnTracks point Chronicler projection adapter.

Covers:
- Per-point event projection correctness (one location event per row).
- Movement episode rollup correctness (contiguous points collapse).
- Gap detection: points beyond the threshold start a new episode.
- Endpoint identity change starts a new episode.
- Nonfinite coordinate handling (skip + warn).
- Missing evidence surface graceful degradation.
- Checkpoint advance / resume (watermark advances by ts).
- Clock-skew clamping: future-dated device timestamps clamped to recorded_at;
  late-delivered (buffered offline) points keep their device timestamp.
- Source-scan guardrail: no LLM imports in adapters/owntracks.py.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import NAMESPACE_URL, UUID, uuid5

import asyncpg
import pytest

from butlers.chronicler.adapters.owntracks import (  # noqa: E402
    CLOCK_SKEW_THRESHOLD_HOURS,
    EPISODE_TYPE_MOVEMENT,
    MOVEMENT_GAP_MINUTES,
    SOURCE_NAME,
    OwnTracksPointAdapter,
)
from butlers.chronicler.models import Episode, PointEvent, Precision, Privacy

_NOW = datetime(2026, 3, 26, 10, 0, 0, tzinfo=UTC)
_ENDPOINT = "owntracks:alice"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    ts: datetime = _NOW,
    lat: float = 1.2345,
    lon: float = 103.8765,
    accuracy: float | None = 10.0,
    trigger: str | None = "p",
    endpoint_identity: str = _ENDPOINT,
    idempotency_key: str | None = None,
) -> dict:
    ikey = idempotency_key or f"owntracks:{endpoint_identity}:{int(ts.timestamp())}:location"
    return {
        "id": "some-uuid",
        "idempotency_key": ikey,
        "ts": ts,
        "lat": lat,
        "lon": lon,
        "accuracy": accuracy,
        "trigger": trigger,
        "event": None,
        "endpoint_identity": endpoint_identity,
        "raw_payload": {"_type": "location", "lat": lat, "lon": lon, "tst": int(ts.timestamp())},
        "recorded_at": ts,
    }


def _make_mock_row(r: dict) -> MagicMock:
    """Build a MagicMock that supports dict-style access via __getitem__."""
    return MagicMock(**r, **{"__getitem__": lambda s, k, _r=r: _r[k]})


def _pool_returning(*rows: dict) -> AsyncMock:
    """Build a mock asyncpg pool that returns the given row dicts for fetch()."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)  # table-exists check
    conn.fetch = AsyncMock(return_value=[_make_mock_row(r) for r in rows])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _tie_boundary_pool(*rows: dict) -> AsyncMock:
    """Build a read pool that applies the adapter's timestamp/limit SQL semantics."""

    async def _fetch(query: str, *args: object) -> list[MagicMock]:
        if "WHERE ts > $1" in query:
            since = args[0]
            assert isinstance(since, datetime)
            candidates = [row for row in rows if row["ts"] > since]
        else:
            candidates = list(rows)
        limit = args[-1]
        assert isinstance(limit, int)
        return [_make_mock_row(row) for row in candidates[:limit]]

    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = _fetch
    conn.fetchrow = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _pool_table_missing() -> AsyncMock:
    """Build a pool whose table-existence check returns False."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


class _AsyncCtx:
    """Async context manager that yields ``obj``."""

    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


def _chronicler_pool() -> AsyncMock:
    """Build a minimal mock chronicler pool for upsert calls."""
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_NullCtx())
    conn.fetchrow = AsyncMock(return_value=None)

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


class _NullCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        pass


# ---------------------------------------------------------------------------
# Source-scan guardrail: no LLM imports in adapters/owntracks.py
# ---------------------------------------------------------------------------


def test_no_llm_imports_in_owntracks_adapter() -> None:
    """The owntracks adapter module must not import any LLM client packages."""
    import butlers.chronicler.adapters.owntracks as mod

    source_path = mod.__file__
    assert source_path is not None

    with open(source_path) as fh:
        tree = ast.parse(fh.read(), filename=source_path)

    forbidden_prefixes = ("anthropic", "openai", "langchain", "litellm", "llm")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in forbidden_prefixes:
                    assert not alias.name.startswith(prefix), (
                        f"LLM import detected in owntracks adapter: {alias.name!r}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for prefix in forbidden_prefixes:
                    assert not node.module.startswith(prefix), (
                        f"LLM import detected in owntracks adapter: {node.module!r}"
                    )


# ---------------------------------------------------------------------------
# Per-point event projection correctness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_single_row_produces_one_point_event() -> None:
    row = _make_row()
    adapter = OwnTracksPointAdapter()

    upserted_events: list[PointEvent] = []

    async def _fake_upsert_event(conn: object, event: PointEvent) -> PointEvent:
        upserted_events.append(event)
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch(
            "butlers.chronicler.adapters.owntracks.upsert_point_event",
            side_effect=_fake_upsert_event,
        ),
        patch("butlers.chronicler.adapters.owntracks.upsert_episode") as mock_ep,
    ):
        mock_ep.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert result.point_events == 1
    assert len(upserted_events) == 1


@pytest.mark.asyncio
async def test_point_event_fields_from_row() -> None:
    row = _make_row(
        ts=_NOW,
        lat=1.2345,
        lon=103.8765,
        accuracy=15.0,
        trigger="p",
    )
    adapter = OwnTracksPointAdapter()
    upserted_events: list[PointEvent] = []

    async def _fake_upsert_event(conn: object, event: PointEvent) -> PointEvent:
        upserted_events.append(event)
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch(
            "butlers.chronicler.adapters.owntracks.upsert_point_event",
            side_effect=_fake_upsert_event,
        ),
        patch("butlers.chronicler.adapters.owntracks.upsert_episode") as mock_ep,
    ):
        mock_ep.return_value = MagicMock()
        await adapter.project(pool, chronicler_pool=cp, since=None)

    ev = upserted_events[0]
    assert ev.source_name == SOURCE_NAME
    assert ev.occurred_at == _NOW
    assert ev.precision == Precision.EXACT
    assert ev.privacy == Privacy.NORMAL
    assert ev.payload["lat"] == 1.2345
    assert ev.payload["lon"] == 103.8765
    assert ev.payload["accuracy"] == 15.0
    assert ev.payload["endpoint_identity"] == _ENDPOINT


@pytest.mark.asyncio
async def test_nonfinite_lat_skips_row_and_advances_watermark() -> None:
    row = _make_row(lat=float("nan"), idempotency_key="bad-lat")
    adapter = OwnTracksPointAdapter()
    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.owntracks.upsert_episode") as mock_ep,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 0
    assert result.point_events == 0
    assert result.episodes_closed == 0
    assert result.watermark == _NOW
    assert len(result.warnings) == 1
    assert "lat must be finite" in result.warnings[0]
    mock_pe.assert_not_called()
    mock_ep.assert_not_called()


# ---------------------------------------------------------------------------
# Movement episode rollup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_point_emits_no_movement_episode_but_carries_over() -> None:
    """One fix is a location, not a movement (bu-2tsgqb.6)."""
    row = _make_row()
    adapter = OwnTracksPointAdapter()
    upserted_episodes: list[Episode] = []

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        upserted_episodes.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch(
            "butlers.chronicler.adapters.owntracks.upsert_episode",
            side_effect=_fake_upsert_ep,
        ),
        patch("butlers.chronicler.adapters.owntracks.save_carryover") as mock_save,
    ):
        mock_pe.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 0
    assert upserted_episodes == []
    # The pending segment is still carried so the next batch can extend it.
    saved = mock_save.call_args.args[2]
    assert saved[_ENDPOINT]["point_count"] == 1
    assert saved[_ENDPOINT]["start_at"] == _NOW.isoformat()


@pytest.mark.asyncio
async def test_gap_beyond_threshold_produces_two_episodes() -> None:
    t1 = _NOW
    t2 = _NOW + timedelta(minutes=5 + MOVEMENT_GAP_MINUTES + 1)
    rows = [
        _make_row(ts=t1, idempotency_key="k1"),
        _make_row(ts=t1 + timedelta(minutes=5), idempotency_key="k1b"),
        _make_row(ts=t2, idempotency_key="k2"),
        _make_row(ts=t2 + timedelta(minutes=5), idempotency_key="k2b"),
    ]
    adapter = OwnTracksPointAdapter()
    upserted_episodes: list[Episode] = []

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        upserted_episodes.append(episode)
        return episode

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch(
            "butlers.chronicler.adapters.owntracks.upsert_episode",
            side_effect=_fake_upsert_ep,
        ),
    ):
        mock_pe.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 2
    assert len(upserted_episodes) == 2


@pytest.mark.asyncio
async def test_endpoint_identity_change_splits_episode() -> None:
    t1 = _NOW
    t2 = _NOW + timedelta(minutes=5)
    rows = [
        _make_row(ts=t1, endpoint_identity="owntracks:alice", idempotency_key="k1"),
        _make_row(ts=t1, endpoint_identity="owntracks:bob", idempotency_key="k1b"),
        _make_row(ts=t2, endpoint_identity="owntracks:alice", idempotency_key="k2"),
        _make_row(ts=t2, endpoint_identity="owntracks:bob", idempotency_key="k2b"),
    ]
    adapter = OwnTracksPointAdapter()
    upserted_episodes: list[Episode] = []

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        upserted_episodes.append(episode)
        return episode

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch(
            "butlers.chronicler.adapters.owntracks.upsert_episode",
            side_effect=_fake_upsert_ep,
        ),
    ):
        mock_pe.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    # Interleaved endpoints neither merge nor fragment each other's movement.
    assert result.episodes_closed == 2
    assert {ep.payload["endpoint_identity"] for ep in upserted_episodes} == {
        "owntracks:alice",
        "owntracks:bob",
    }


# ---------------------------------------------------------------------------
# Missing evidence surface graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_evidence_table_returns_skipped_result() -> None:
    adapter = OwnTracksPointAdapter()
    pool = _pool_table_missing()
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.owntracks.upsert_episode") as mock_ep,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.skipped is True
    assert result.skipped_reason is not None
    assert "not found" in result.skipped_reason
    assert result.rows_projected == 0
    mock_pe.assert_not_called()
    mock_ep.assert_not_called()


@pytest.mark.asyncio
async def test_undefined_table_exception_returns_skipped_result() -> None:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedTableError(
            'relation "connectors.owntracks_points" does not exist'
        )
    )
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = OwnTracksPointAdapter()
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.owntracks.upsert_episode") as mock_ep,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.skipped is True
    assert result.rows_projected == 0
    assert result.watermark is None
    mock_pe.assert_not_called()
    mock_ep.assert_not_called()


# ---------------------------------------------------------------------------
# Checkpoint advance / resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watermark_advances_to_latest_ts() -> None:
    t1 = _NOW
    t2 = _NOW + timedelta(hours=1)
    rows = [
        _make_row(ts=t1, idempotency_key="k1"),
        _make_row(ts=t2, idempotency_key="k2"),
    ]
    adapter = OwnTracksPointAdapter()

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.owntracks.upsert_episode") as mock_ep,
    ):
        mock_pe.return_value = MagicMock()
        mock_ep.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.watermark == t2


@pytest.mark.asyncio
async def test_single_column_ts_watermark_skips_equal_tie_after_split_batch() -> None:
    """Record the accepted UUID-source checkpoint trade-off at a batch boundary.

    ``owntracks_points.id`` cannot use the BIGINT ``watermark_id``
    tiebreaker, so the adapter deliberately resumes with ``ts > watermark``.
    When a batch limit splits equal ``ts`` values, the remaining tie is not
    replayed. This characterization keeps that residual risk visible; a
    composite watermark needs a separately scoped contract.
    """
    tied_at = _NOW
    rows = [
        _make_row(ts=tied_at, idempotency_key="tie-1"),
        _make_row(ts=tied_at, idempotency_key="tie-2"),
        _make_row(ts=tied_at, idempotency_key="tie-3"),
    ]
    adapter = OwnTracksPointAdapter(batch_limit=2)
    pool = _tie_boundary_pool(*rows)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_point_event,
        patch("butlers.chronicler.adapters.owntracks.upsert_episode") as mock_episode,
    ):
        mock_point_event.return_value = MagicMock()
        mock_episode.return_value = MagicMock()
        first = await adapter.project(pool, chronicler_pool=cp, since=None)
        second = await adapter.project(pool, chronicler_pool=cp, since=first.watermark)

    assert first.rows_projected == 2
    assert first.watermark == tied_at
    assert second.rows_projected == 0
    assert second.watermark == tied_at
    assert mock_point_event.await_count == 2


# ---------------------------------------------------------------------------
# Contracts registration
# ---------------------------------------------------------------------------


def test_owntracks_adapter_exported_from_package() -> None:
    from butlers.chronicler.adapters import OwnTracksPointAdapter as _Cls

    assert _Cls is OwnTracksPointAdapter


# ---------------------------------------------------------------------------
# Clock-skew clamping (bu-g3qyp)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_implausible_device_ts_clamped_to_recorded_at_no_inversion() -> None:
    """A point whose device timestamp runs ahead of recorded_at by more than the
    threshold must be clamped to recorded_at BEFORE episode projection, preventing
    an inverted episode without ever invoking the swap-bounds guard.
    """
    server_time = _NOW + timedelta(hours=2)  # 12:00 UTC relative to _NOW baseline

    # Point A: device clock is skewed by 4h + 5min forward (a fix cannot be
    # recorded before it was taken, so a future-dated ts is a broken clock).
    skewed_ts = server_time + timedelta(hours=4, minutes=5)  # 16:05
    recorded_at_a = server_time  # 12:00 (server time)

    # Point B: device clock is accurate
    good_ts = server_time + timedelta(minutes=10)  # 12:10
    recorded_at_b = good_ts

    row_a = {
        **_make_row(ts=skewed_ts, idempotency_key="skew-clamp-a"),
        "recorded_at": recorded_at_a,
    }
    row_b = {
        **_make_row(ts=good_ts, idempotency_key="skew-clamp-b"),
        "recorded_at": recorded_at_b,
    }

    # Confirm precondition: point A's device ts is > threshold from its recorded_at
    assert abs(skewed_ts - recorded_at_a) > timedelta(hours=CLOCK_SKEW_THRESHOLD_HOURS)

    adapter = OwnTracksPointAdapter()
    upserted_episodes: list[Episode] = []

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        upserted_episodes.append(episode)
        return episode

    pool = _pool_returning(row_a, row_b)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch(
            "butlers.chronicler.adapters.owntracks.upsert_episode",
            side_effect=_fake_upsert_ep,
        ),
    ):
        mock_pe.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    # The adapter must have warned about the skewed point
    assert any("implausible" in w for w in result.warnings), (
        f"Expected clamping warning in result.warnings, got: {result.warnings}"
    )

    # Every emitted episode must be non-inverted
    assert len(upserted_episodes) >= 1
    for ep in upserted_episodes:
        assert ep.end_at >= ep.start_at, (
            f"Inverted episode after clock-skew clamp: "
            f"start_at={ep.start_at.isoformat()}, end_at={ep.end_at.isoformat()}"
        )

    # The clamped point A ts should be recorded_at_a (12:00), not skewed_ts (16:05).
    for ep in upserted_episodes:
        assert ep.start_at >= recorded_at_a, (
            f"Episode start_at {ep.start_at.isoformat()} predates clamped ts "
            f"{recorded_at_a.isoformat()} — skewed timestamp leaked into projection"
        )
        assert ep.end_at < skewed_ts, (
            f"Episode end_at {ep.end_at.isoformat()} reaches the future-dated device ts "
            f"{skewed_ts.isoformat()} — skewed timestamp leaked into projection"
        )


@pytest.mark.asyncio
async def test_buffered_offline_points_keep_device_ts_and_split_by_ts() -> None:
    """Points buffered offline and uploaded hours later keep their device ts.

    Regression for bu-8r9q06: the phone was offline on a TPE -> NRT flight and
    uploaded its queued Taoyuan fixes together with the first Narita fix after
    landing.  One Taoyuan fix arrived 4h18m late; clamping it to recorded_at
    placed the owner back in Taiwan 39 minutes after the Narita fix.  Upload
    order also differed from device order, which merged both airports into one
    movement episode when segmenting by recorded_at.
    """
    upload = datetime(2026, 10, 26, 2, 43, 50, tzinfo=UTC)
    taoyuan_late = datetime(2026, 10, 25, 22, 25, 39, tzinfo=UTC)
    taoyuan_b = datetime(2026, 10, 25, 23, 3, 43, tzinfo=UTC)
    taoyuan_c = datetime(2026, 10, 25, 23, 8, 38, tzinfo=UTC)
    narita = datetime(2026, 10, 26, 2, 4, 34, tzinfo=UTC)
    assert upload - taoyuan_late > timedelta(hours=CLOCK_SKEW_THRESHOLD_HOURS)

    # Fetched in ts order (as the SQL does), but recorded_at order differs.
    rows = [
        {
            **_make_row(ts=taoyuan_late, lat=25.077, lon=121.236, idempotency_key="tpe-a"),
            "recorded_at": upload,
        },
        {
            **_make_row(ts=taoyuan_b, lat=25.071, lon=121.224, idempotency_key="tpe-b"),
            "recorded_at": upload + timedelta(seconds=2),
        },
        {
            **_make_row(ts=taoyuan_c, lat=25.053, lon=121.223, idempotency_key="tpe-c"),
            "recorded_at": upload + timedelta(seconds=1),
        },
        {
            **_make_row(ts=narita, lat=35.762, lon=140.385, idempotency_key="nrt-a"),
            "recorded_at": upload + timedelta(seconds=3),
        },
    ]

    adapter = OwnTracksPointAdapter()
    upserted_events: list[PointEvent] = []
    upserted_episodes: list[Episode] = []

    async def _fake_upsert_pe(conn: object, event: PointEvent) -> PointEvent:
        upserted_events.append(event)
        return event

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        upserted_episodes.append(episode)
        return episode

    with (
        patch(
            "butlers.chronicler.adapters.owntracks.upsert_point_event",
            side_effect=_fake_upsert_pe,
        ),
        patch(
            "butlers.chronicler.adapters.owntracks.upsert_episode",
            side_effect=_fake_upsert_ep,
        ),
    ):
        result = await adapter.project(
            _pool_returning(*rows), chronicler_pool=_chronicler_pool(), since=None
        )

    assert not any("implausible" in w for w in result.warnings), result.warnings
    occurred = {e.payload["idempotency_key"]: e.occurred_at for e in upserted_events}
    assert occurred == {
        "tpe-a": taoyuan_late,
        "tpe-b": taoyuan_b,
        "tpe-c": taoyuan_c,
        "nrt-a": narita,
    }

    # The lone Taoyuan and Narita fixes are locations, not movements.
    spans = sorted((ep.start_at, ep.end_at) for ep in upserted_episodes)
    assert spans == [(taoyuan_b, taoyuan_c)]


# ---------------------------------------------------------------------------
# Trail quality (bu-2tsgqb.6): outliers, minimum evidence, duplicate chunks
# ---------------------------------------------------------------------------


def _event_id(idempotency_key: str) -> UUID:
    return uuid5(NAMESPACE_URL, idempotency_key)


async def _project_capturing(
    rows: list[dict], *, carryover: dict | None = None, stored: dict | None = None
) -> tuple[object, list[PointEvent], list[Episode], dict]:
    """Run the adapter, capturing point events, episodes and saved carryover."""
    events: list[PointEvent] = []
    episodes: list[Episode] = []

    async def _fake_pe(conn: object, event: PointEvent) -> PointEvent:
        events.append(event)
        return replace(event, id=_event_id(event.payload["idempotency_key"]))

    async def _fake_ep(conn: object, episode: Episode) -> Episode:
        episodes.append(episode)
        return episode

    cp = _chronicler_pool()
    if stored is not None:
        conn = await cp.acquire().__aenter__()
        conn.fetchrow = AsyncMock(return_value=stored)

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event", side_effect=_fake_pe),
        patch("butlers.chronicler.adapters.owntracks.upsert_episode", side_effect=_fake_ep),
        patch(
            "butlers.chronicler.adapters.owntracks.get_carryover",
            AsyncMock(return_value=carryover or {}),
        ),
        patch("butlers.chronicler.adapters.owntracks.save_carryover") as mock_save,
    ):
        result = await OwnTracksPointAdapter().project(
            _pool_returning(*rows), chronicler_pool=cp, since=None
        )
    return result, events, episodes, mock_save.call_args.args[2]


@pytest.mark.asyncio
async def test_impossible_jump_kept_as_flagged_evidence_and_excluded_from_movement() -> None:
    """The Oct 16 landing-time eastern-Poland fix must not become a movement."""
    beijing = (40.08, 116.58)
    rows = [
        _make_row(ts=_NOW, lat=beijing[0], lon=beijing[1], idempotency_key="b1"),
        _make_row(ts=_NOW + timedelta(minutes=4), lat=52.12, lon=22.06, idempotency_key="poland"),
        _make_row(
            ts=_NOW + timedelta(minutes=10), lat=beijing[0], lon=beijing[1], idempotency_key="b2"
        ),
    ]
    result, events, episodes, saved = await _project_capturing(rows)

    flagged = {e.payload["idempotency_key"] for e in events if "trail_outlier" in e.payload}
    assert flagged == {"poland"}
    assert len(events) == 3  # the evidence is kept, only flagged
    assert any("impossible jump" in w for w in result.warnings)
    assert [(ep.episode_type, ep.start_at, ep.payload["point_count"]) for ep in episodes] == [
        (EPISODE_TYPE_MOVEMENT, _NOW, 2)
    ]
    assert saved["_trail_anchors"][_ENDPOINT]["ts"] == (_NOW + timedelta(minutes=10)).isoformat()


@pytest.mark.asyncio
async def test_carryover_extension_counts_the_whole_episode() -> None:
    """One new fix extending an open episode is not a "1 points" movement."""
    start = _NOW - timedelta(minutes=50)
    carry = {
        _ENDPOINT: {
            "source_ref": "connectors.owntracks_points:movement:x:1",
            "start_at": start.isoformat(),
            "end_at": (_NOW - timedelta(minutes=5)).isoformat(),
            "start_lat": 1.2345,
            "start_lon": 103.8765,
            "point_count": 6,
        }
    }
    _, _, episodes, saved = await _project_capturing([_make_row(ts=_NOW)], carryover=carry)

    assert len(episodes) == 1
    assert episodes[0].start_at == start
    assert episodes[0].title == "Movement (7 points)"
    assert saved[_ENDPOINT]["point_count"] == 7


@pytest.mark.asyncio
async def test_fresh_segment_overlapping_stored_episode_widens_it_not_duplicates() -> None:
    """Regression for the Oct 16 08:14-12:01 / 08:25-09:26 duplicate chunks."""
    stored = {
        "source_ref": "connectors.owntracks_points:movement:stored",
        "start_at": _NOW - timedelta(minutes=11),
        "end_at": _NOW + timedelta(hours=3),
        "payload": {"start_lat": 1.0, "start_lon": 103.0, "point_count": 40},
        "linked_event_ids": [],  # legacy episode: no linked evidence yet
    }
    rows = [
        _make_row(ts=_NOW, idempotency_key="a"),
        _make_row(ts=_NOW + timedelta(minutes=20), idempotency_key="b"),
    ]
    _, _, episodes, saved = await _project_capturing(rows, stored=stored)

    assert len(episodes) == 1
    ep = episodes[0]
    assert ep.source_ref == stored["source_ref"]
    assert (ep.start_at, ep.end_at) == (stored["start_at"], stored["end_at"])
    assert ep.payload["point_count"] == 42
    assert saved[_ENDPOINT]["source_ref"] == stored["source_ref"]


@pytest.mark.asyncio
async def test_replaying_a_merged_batch_keeps_the_point_count_stable() -> None:
    """point_count counts distinct linked evidence, so a replay never re-adds it."""
    others = [_event_id(f"earlier-{i}") for i in range(40)]
    stored = {
        "source_ref": "connectors.owntracks_points:movement:stored",
        "start_at": _NOW - timedelta(minutes=11),
        "end_at": _NOW + timedelta(hours=3),
        "payload": {"start_lat": 1.0, "start_lon": 103.0, "point_count": 40},
        "linked_event_ids": others,
    }
    rows = [
        _make_row(ts=_NOW, idempotency_key="a"),
        _make_row(ts=_NOW + timedelta(minutes=20), idempotency_key="b"),
    ]
    _, _, first, _ = await _project_capturing(rows, stored=stored)
    assert first[0].payload["point_count"] == 42

    # Replay: the stored episode now carries this batch's fixes as evidence.
    replay_stored = {
        **stored,
        "payload": {**stored["payload"], "point_count": 42},
        "linked_event_ids": [*others, _event_id("a"), _event_id("b")],
    }
    _, _, replayed, saved = await _project_capturing(rows, stored=replay_stored)
    assert replayed[0].payload["point_count"] == 42
    assert saved[_ENDPOINT]["point_count"] == 42
