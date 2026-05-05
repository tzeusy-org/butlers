"""Tests for the OwnTracks point Chronicler projection adapter.

Covers:
- Per-point event projection correctness (one location event per row).
- Movement episode rollup correctness (contiguous points collapse).
- Gap detection: points beyond the threshold start a new episode.
- Endpoint identity change starts a new episode.
- Nonfinite coordinate handling (skip + warn).
- Missing evidence surface graceful degradation.
- Checkpoint advance / resume (watermark advances by ts).
- Clock-skew clamping: implausible device timestamps clamped to recorded_at.
- Source-scan guardrail: no LLM imports in adapters/owntracks.py.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

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
async def test_single_point_produces_one_movement_episode() -> None:
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
    ):
        mock_pe.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 1
    assert len(upserted_episodes) == 1
    ep = upserted_episodes[0]
    assert ep.episode_type == EPISODE_TYPE_MOVEMENT
    assert ep.start_at == _NOW
    assert ep.end_at == _NOW
    assert ep.precision == Precision.EXACT
    assert ep.privacy == Privacy.NORMAL


@pytest.mark.asyncio
async def test_gap_beyond_threshold_produces_two_episodes() -> None:
    t1 = _NOW
    t2 = _NOW + timedelta(minutes=MOVEMENT_GAP_MINUTES + 1)
    rows = [
        _make_row(ts=t1, idempotency_key="k1"),
        _make_row(ts=t2, idempotency_key="k2"),
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
        _make_row(ts=t2, endpoint_identity="owntracks:bob", idempotency_key="k2"),
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
    assert upserted_episodes[0].payload["endpoint_identity"] == "owntracks:alice"
    assert upserted_episodes[1].payload["endpoint_identity"] == "owntracks:bob"


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
    """A point whose device timestamp deviates from recorded_at by more than the
    threshold must be clamped to recorded_at BEFORE episode projection, preventing
    an inverted episode without ever invoking the swap-bounds guard.
    """
    server_time = _NOW + timedelta(hours=2)  # 12:00 UTC relative to _NOW baseline

    # Point A: device clock is skewed by 4h + 5min backward
    skewed_ts = server_time - timedelta(hours=4, minutes=5)  # 07:55
    recorded_at_a = server_time + timedelta(minutes=5)  # 12:05 (server time)

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

    # The clamped point A ts should be recorded_at_a (12:05), not skewed_ts (07:55).
    for ep in upserted_episodes:
        assert ep.start_at >= recorded_at_a, (
            f"Episode start_at {ep.start_at.isoformat()} predates clamped ts "
            f"{recorded_at_a.isoformat()} — skewed timestamp leaked into projection"
        )
