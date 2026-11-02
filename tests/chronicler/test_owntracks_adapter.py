"""Tests for the OwnTracks point Chronicler projection adapter.

Covers:
- Per-point event projection correctness (one location event per row).
- Movement episode rollup correctness (contiguous points collapse).
- Gap detection: points beyond the threshold start a new episode.
- Endpoint identity change starts a new episode.
- Nonfinite coordinate handling (skip + warn).
- Missing evidence surface graceful degradation.
- Checkpoint advance / resume: arrival-order (recorded_at, id) cursor, legacy
  ts-watermark cutover, settle window; relational watermark reports device ts.
- Clock-skew clamping: future-dated device timestamps clamped to recorded_at;
  late-delivered (buffered offline) points keep their device timestamp.
- Source-scan guardrail: no LLM imports in adapters/owntracks.py.
"""

from __future__ import annotations

import ast
import json
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
        "id": uuid5(NAMESPACE_URL, ikey),
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
    conn = AsyncMock(spec=asyncpg.Connection)
    conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    conn.fetchval = AsyncMock(return_value=True)  # table-exists check
    conn.fetch = AsyncMock(return_value=[_make_mock_row(r) for r in rows])
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _pool_table_missing() -> AsyncMock:
    """Build a pool whose table-existence check returns False."""
    conn = AsyncMock(spec=asyncpg.Connection)
    conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    conn.fetchval = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock(spec=asyncpg.Pool)
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
    conn = AsyncMock(spec=asyncpg.Connection)
    conn.transaction = MagicMock(return_value=_NullCtx())
    conn.fetchrow = AsyncMock(return_value=None)

    pool = AsyncMock(spec=asyncpg.Pool)
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
    conn = AsyncMock(spec=asyncpg.Connection)
    conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    conn.fetchval = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedTableError(
            'relation "connectors.owntracks_points" does not exist'
        )
    )
    pool = AsyncMock(spec=asyncpg.Pool)
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


def _arrival(row: dict) -> tuple[datetime, UUID]:
    return row["recorded_at"], row["id"]


class _ArrivalTable:
    """In-memory ``connectors.owntracks_points`` honouring the cursor SQL."""

    def __init__(self, *rows: dict, now: datetime) -> None:
        self.rows = list(rows)
        self.now = now

    def _settled(self, settle_seconds: float) -> list[dict]:
        cutoff = self.now - timedelta(seconds=settle_seconds)
        return sorted((r for r in self.rows if r["recorded_at"] <= cutoff), key=_arrival)

    async def fetch(self, query: str, *args: object) -> list[MagicMock]:
        assert "ORDER BY recorded_at ASC, id ASC" in query
        settle, *rest = args
        assert isinstance(settle, float)
        candidates = self._settled(settle)
        if len(rest) == 3:
            position = (rest[0], rest[1])
            if "(recorded_at, id) >=" in query:
                candidates = [r for r in candidates if _arrival(r) >= position]
            else:
                assert "(recorded_at, id) >" in query
                candidates = [r for r in candidates if _arrival(r) > position]
        limit = rest[-1]
        assert isinstance(limit, int)
        return [_make_mock_row(r) for r in candidates[:limit]]

    async def fetchrow(self, query: str, *args: object) -> dict | None:
        if "owntracks_points" not in query:
            return None  # owner-entity lookup: no owner
        if "WHERE ts > $1" in query:
            later = sorted((r for r in self.rows if r["ts"] > args[0]), key=_arrival)
            return later[0] if later else None
        assert "ORDER BY recorded_at DESC, id DESC" in query
        settled = self._settled(args[0])
        return settled[-1] if settled else None

    def pool(self) -> AsyncMock:
        conn = AsyncMock(spec=asyncpg.Connection)
        conn.transaction = MagicMock(return_value=_AsyncCtx(None))
        conn.fetchval = AsyncMock(return_value=True)
        conn.fetch = self.fetch
        conn.fetchrow = self.fetchrow
        pool = AsyncMock(spec=asyncpg.Pool)
        pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
        return pool


async def _project_run(
    table: _ArrivalTable,
    store: dict,
    *,
    since: datetime | None = None,
    batch_limit: int = 1000,
) -> tuple[object, list[str]]:
    """One run through a fresh adapter, persisting carryover in ``store``."""
    projected: list[str] = []

    async def _fake_pe(conn: object, event: PointEvent) -> PointEvent:
        projected.append(event.payload["idempotency_key"])
        return replace(event, id=_event_id(event.payload["idempotency_key"]))

    async def _save(conn: object, source_name: str, carryover: dict) -> None:
        store.clear()
        store.update(json.loads(json.dumps(carryover)))

    with (
        patch("butlers.chronicler.adapters.owntracks.get_owntracks_point", return_value=None),
        patch("butlers.chronicler.adapters.owntracks.retained_owntracks_rows", return_value=[]),
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event", side_effect=_fake_pe),
        patch("butlers.chronicler.adapters.owntracks.upsert_episode", side_effect=lambda c, e: e),
        patch(
            "butlers.chronicler.adapters.owntracks.get_carryover",
            AsyncMock(side_effect=lambda c, n: json.loads(json.dumps(store))),
        ),
        patch("butlers.chronicler.adapters.owntracks.save_carryover", side_effect=_save),
    ):
        result = await OwnTracksPointAdapter(batch_limit=batch_limit).project(
            table.pool(), chronicler_pool=_chronicler_pool(), since=since
        )
    return result, projected


def _arrived(ts: datetime, arrived_at: datetime, key: str) -> dict:
    return {**_make_row(ts=ts, idempotency_key=key), "recorded_at": arrived_at}


@pytest.mark.asyncio
async def test_buffered_fix_older_than_projected_fix_is_projected_once() -> None:
    """An offline-queued fix that arrives after a newer one still projects (bu-e6lrho)."""
    table = _ArrivalTable(_arrived(_NOW, _NOW, "fresh"), now=_NOW + timedelta(hours=1))
    store: dict = {}

    first, first_keys = await _project_run(table, store)
    table.rows.append(_arrived(_NOW - timedelta(hours=3), _NOW + timedelta(minutes=5), "queued"))
    second, second_keys = await _project_run(table, store, since=first.watermark)
    _, third_keys = await _project_run(table, store, since=second.watermark)

    assert (first_keys, second_keys, third_keys) == (["fresh"], ["queued"], [])
    # The relational watermark still reports device time, never regressing.
    assert second.watermark == _NOW


@pytest.mark.asyncio
async def test_shared_arrival_time_pages_without_loss_or_repeat_across_retry() -> None:
    arrived_at = _NOW
    rows = [_arrived(_NOW - timedelta(minutes=i), arrived_at, f"tie-{i}") for i in range(5)]
    table = _ArrivalTable(*rows, now=_NOW + timedelta(hours=1))
    store: dict = {}

    _, first = await _project_run(table, store, batch_limit=2)
    # A run whose transaction rolls back leaves the stored cursor untouched,
    # so the retry re-reads exactly the same page.
    snapshot = json.loads(json.dumps(store))
    _, lost = await _project_run(table, store, batch_limit=2)
    store.clear()
    store.update(snapshot)
    pages = [first]
    while page := (await _project_run(table, store, batch_limit=2))[1]:
        pages.append(page)

    # Pages follow arrival order (id breaks the tie); within a page points
    # project in device-time order, so compare each page as a set.
    assert pages[1] == lost
    arrival = [r["idempotency_key"] for r in sorted(rows, key=_arrival)]
    assert [set(page) for page in pages] == [set(arrival[i : i + 2]) for i in range(0, 5, 2)]


@pytest.mark.asyncio
async def test_rows_inside_settle_window_wait_for_a_later_run() -> None:
    table = _ArrivalTable(_arrived(_NOW, _NOW, "early"), now=_NOW + timedelta(seconds=10))
    store: dict = {}

    _, waiting = await _project_run(table, store)
    table.now = _NOW + timedelta(minutes=2)
    _, settled = await _project_run(table, store)

    assert (waiting, settled) == ([], ["early"])


@pytest.mark.asyncio
async def test_legacy_ts_watermark_cuts_over_without_rescanning_history() -> None:
    """Start at the earliest-arrived unprojected row; recover a late fix after it."""
    legacy = _NOW
    history = [
        _arrived(_NOW - timedelta(hours=h), _NOW - timedelta(hours=h), f"h{h}") for h in (1, 2)
    ]
    unprojected = _arrived(_NOW + timedelta(minutes=10), _NOW + timedelta(minutes=10), "new")
    late = _arrived(_NOW - timedelta(hours=5), _NOW + timedelta(minutes=20), "late")
    table = _ArrivalTable(*history, unprojected, late, now=_NOW + timedelta(hours=1))
    store: dict = {}

    _, cut = await _project_run(table, store, since=legacy)
    _, after = await _project_run(table, store, since=legacy)

    assert (cut, after) == (["late", "new"], [])


@pytest.mark.asyncio
async def test_covered_legacy_watermark_persists_cursor_then_catches_late_fix() -> None:
    history = [
        _arrived(_NOW - timedelta(hours=h), _NOW - timedelta(hours=h), f"h{h}") for h in (1, 2)
    ]
    table = _ArrivalTable(*history, now=_NOW + timedelta(hours=1))
    store: dict = {"_ingest_cursor": {"version": 99}}  # unreadable -> legacy cutover

    _, cut = await _project_run(table, store, since=_NOW)
    assert cut == []
    assert store["_ingest_cursor"]["id"] == str(history[0]["id"])

    table.rows.append(_arrived(_NOW - timedelta(hours=6), _NOW + timedelta(minutes=5), "late"))
    _, recovered = await _project_run(table, store, since=_NOW)
    assert recovered == ["late"]


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
        patch("butlers.chronicler.adapters.owntracks.get_owntracks_point", return_value=None),
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

    cold = [_make_row(ts=_NOW, lat=52.12, lon=22.06, idempotency_key="seed")]
    cold += [
        _make_row(ts=_NOW + timedelta(minutes=m), idempotency_key=f"good-{m}") for m in (5, 15, 25)
    ]
    _, projected, movements, _ = await _project_capturing(cold)
    assert {e.payload["idempotency_key"] for e in projected if "trail_outlier" in e.payload} == {
        "seed"
    }
    assert len(projected) == 4
    assert movements[0].start_at == _NOW + timedelta(minutes=5)
    assert movements[0].payload["point_count"] == 3

    # A fetched replay may overlap checkpoint-known identities after their
    # projections expired. Classification can reset/mutate that state, but
    # must not turn those historical revisions into fresh default inserts.
    from copy import deepcopy

    for known_kind in ("seed", "pending", "anchor"):
        known = (
            cold[1] if known_kind == "pending" else cold[-1] if known_kind == "anchor" else cold[0]
        )
        anchor = cold[-1] if known_kind == "anchor" else cold[0]
        entry = {
            "seed_id": None if known_kind == "anchor" else str(cold[0]["id"]),
            "anchor_id": str(anchor["id"]),
            "pending_ids": [str(known["id"])] if known_kind == "pending" else [],
        }
        historical = {value for value in (entry["seed_id"], entry["anchor_id"]) if value}
        historical.update(entry["pending_ids"])
        prior = {
            "_trail_anchors": {
                _ENDPOINT: {
                    "ts": anchor["ts"].isoformat(),
                    "lat": anchor["lat"],
                    "lon": anchor["lon"],
                }
            },
            "_trail_state": {"version": 1, "endpoints": {_ENDPOINT: entry}},
        }
        for overlap in (False, True):
            current = cold if overlap else cold[2:]
            for disposition in ("expired", "live", "tombstoned"):
                inserted = []
                revisions = []
                existing = PointEvent(
                    id=_event_id("retained"),
                    source_name=SOURCE_NAME,
                    source_ref=f"connectors.owntracks_points:{known['idempotency_key']}",
                    event_type="location",
                    occurred_at=known["ts"],
                    precision=Precision.EXACT,
                    title="Preserved title",
                    payload={"preserved": True},
                    privacy=Privacy.RESTRICTED,
                    retention_days=91,
                    tombstone_at=_NOW if disposition == "tombstoned" else None,
                )

                async def lookup(_conn, ref, existing=existing, disposition=disposition):
                    if disposition != "expired" and ref == existing.source_ref:
                        return existing
                    return None

                async def insert(_conn, event, inserted=inserted):
                    inserted.append(event)
                    return replace(event, id=_event_id(event.source_ref))

                async def revise(
                    _conn,
                    ref,
                    marker,
                    revisions=revisions,
                    existing=existing,
                    disposition=disposition,
                ):
                    # Model the actual marker-only SQL's live-row predicate;
                    # a missing/tombstoned row has no update/returned UUID.
                    if disposition != "live" or ref != existing.source_ref:
                        return None
                    revisions.append((ref, marker))
                    return existing.id

                with (
                    patch(
                        "butlers.chronicler.adapters.owntracks.get_carryover",
                        return_value=deepcopy(prior),
                    ),
                    patch(
                        "butlers.chronicler.adapters.owntracks.retained_owntracks_rows",
                        return_value=[cold[0], known],
                    ),
                    patch(
                        "butlers.chronicler.adapters.owntracks.get_owntracks_point",
                        side_effect=lookup,
                    ),
                    patch(
                        "butlers.chronicler.adapters.owntracks.upsert_point_event",
                        side_effect=insert,
                    ),
                    patch(
                        "butlers.chronicler.adapters.owntracks.revise_owntracks_point",
                        side_effect=revise,
                    ),
                    patch(
                        "butlers.chronicler.adapters.owntracks.upsert_episode",
                        side_effect=lambda _conn, ep: ep,
                    ),
                    patch("butlers.chronicler.adapters.owntracks.save_carryover"),
                ):
                    await OwnTracksPointAdapter().project(
                        _pool_returning(*current), chronicler_pool=_chronicler_pool(), since=None
                    )
                assert not any(
                    event.source_ref == f"connectors.owntracks_points:{row['idempotency_key']}"
                    for event in inserted
                    for row in cold
                    if str(row["id"]) in historical
                ), (known_kind, overlap, disposition, "historical projection resurrected")
                unseen = {
                    row["idempotency_key"] for row in current if str(row["id"]) not in historical
                }
                assert {event.payload["idempotency_key"] for event in inserted} == unseen
                if disposition == "live" and (overlap or known_kind == "pending"):
                    assert any(ref == existing.source_ref for ref, _ in revisions)
                if disposition == "tombstoned":
                    assert not any(ref == existing.source_ref for ref, _ in revisions)
                assert existing.payload == {"preserved": True}
                assert (existing.privacy, existing.retention_days, existing.title) == (
                    Privacy.RESTRICTED,
                    91,
                    "Preserved title",
                )


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
        "payload": {
            "start_lat": 1.0,
            "start_lon": 103.0,
            "point_count": 40,
            "end_lat": 2.0,
            "end_lon": 104.0,
        },
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
    assert (ep.payload["end_lat"], ep.payload["end_lon"]) == (2.0, 104.0)
    assert (saved[_ENDPOINT]["end_lat"], saved[_ENDPOINT]["end_lon"]) == (2.0, 104.0)
    # A genuinely later current end uses its own location; ties keep the stored pair.
    for end in (rows[-1]["ts"] - timedelta(minutes=1), rows[-1]["ts"]):
        _, _, values, _ = await _project_capturing(rows, stored={**stored, "end_at": end})
        expected = (1.2345, 103.8765) if end < rows[-1]["ts"] else (2.0, 104.0)
        assert (values[0].payload["end_lat"], values[0].payload["end_lon"]) == expected
    _, _, legacy, _ = await _project_capturing(rows, stored={**stored, "payload": {}})
    assert "end_lat" not in legacy[0].payload and "end_lon" not in legacy[0].payload


@pytest.mark.asyncio
async def test_replaying_a_merged_batch_keeps_the_point_count_stable() -> None:
    """point_count counts distinct linked evidence, so a replay never re-adds it."""
    others = [_event_id(f"earlier-{i}") for i in range(40)]
    stored = {
        "source_ref": "connectors.owntracks_points:movement:stored",
        "start_at": _NOW - timedelta(minutes=11),
        "end_at": _NOW + timedelta(hours=3),
        "payload": {
            "start_lat": 1.0,
            "start_lon": 103.0,
            "point_count": 40,
            "end_lat": 2.0,
            "end_lon": 104.0,
        },
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
    assert (replayed[0].payload["end_lat"], replayed[0].payload["end_lon"]) == (
        first[0].payload["end_lat"],
        first[0].payload["end_lon"],
    )


@pytest.mark.asyncio
async def test_tied_fixes_have_identical_order_and_verdicts_in_both_adapters() -> None:
    from butlers.chronicler.adapters.owntracks_place_cluster import OwnTracksPlaceClusterAdapter
    from butlers.chronicler.adapters.owntracks_trail import classify_trail

    early = {
        **_make_row(lat=40.08, lon=116.58, idempotency_key="early"),
        "id": UUID(int=1),
        "recorded_at": _NOW + timedelta(minutes=2),
    }
    late = {
        **_make_row(lat=52.12, lon=22.06, idempotency_key="late"),
        "id": UUID(int=2),
        "recorded_at": _NOW + timedelta(minutes=1),
    }
    other = {**_make_row(endpoint_identity="other", idempotency_key="other"), "id": UUID(int=3)}
    for rows in ([late, early, other], [other, early, late]):
        seen: list[tuple[list[str], set[str]]] = []

        def capture(rows, seen=seen, **kwargs):
            outliers, anchors = classify_trail(rows, **kwargs)
            seen.append(([str(r["id"]) for r in rows], {str(rows[i]["id"]) for i in outliers}))
            return outliers, anchors

        with patch("butlers.chronicler.adapters.owntracks.classify_trail", side_effect=capture):
            _, events, _, _ = await _project_capturing(rows)
        with (
            patch(
                "butlers.chronicler.adapters.owntracks_place_cluster.get_carryover", return_value={}
            ),
            patch("butlers.chronicler.adapters.owntracks_place_cluster.save_carryover"),
            patch(
                "butlers.chronicler.adapters.owntracks_place_cluster.classify_trail",
                side_effect=capture,
            ),
            patch(
                "butlers.chronicler.adapters.owntracks_place_cluster.resolve_owner_entity_id",
                return_value=None,
            ),
        ):
            await OwnTracksPlaceClusterAdapter().project(
                _pool_returning(*rows), chronicler_pool=_chronicler_pool(), since=None
            )
        assert seen[0] == seen[1]
        assert seen[0][0] == [str(UUID(int=i)) for i in (1, 2, 3)]
        assert seen[0][1] == {str(UUID(int=2))}
        assert len(events) == 3

    # Three globally interleaved endpoint trails remain three eligible places.
    # This is the native carrier's accepted 5/15/25-minute pattern after
    # isolated seeds are rejected; neither input order may fragment places.
    endpoints = ("owntracks:retained", "owntracks:expired", "owntracks:tombstoned")
    seeds = [
        _make_row(lat=52.12, lon=22.06, endpoint_identity=e, idempotency_key=f"{e}:seed")
        for e in endpoints
    ]
    candidates = [
        _make_row(
            ts=_NOW + timedelta(minutes=minute),
            endpoint_identity=e,
            idempotency_key=f"{e}:{minute}",
        )
        for minute in (5, 15, 25)
        for e in endpoints
    ]
    prior = {
        "_trail_anchors": {
            e: {"ts": _NOW.isoformat(), "lat": 52.12, "lon": 22.06} for e in endpoints
        },
        "_trail_state": {
            "version": 1,
            "endpoints": {
                e: {"seed_id": str(seed["id"]), "anchor_id": str(seed["id"]), "pending_ids": []}
                for e, seed in zip(endpoints, seeds, strict=True)
            },
        },
    }
    for ordered in (candidates, list(reversed(candidates))):
        places = []
        verdicts = []

        def capture_places(rows, verdicts=verdicts, **kwargs):
            outliers, anchors = classify_trail(rows, **kwargs)
            verdicts.append((len(rows) - len(outliers), set(kwargs["rejected_seeds"])))
            return outliers, anchors

        async def persist_place(_conn, episode, places=places):
            places.append(episode)
            return episode

        with (
            patch(
                "butlers.chronicler.adapters.owntracks_place_cluster.get_carryover",
                return_value=prior,
            ),
            patch(
                "butlers.chronicler.adapters.owntracks_place_cluster.retained_owntracks_rows",
                return_value=seeds,
            ),
            patch("butlers.chronicler.adapters.owntracks_place_cluster.save_carryover"),
            patch(
                "butlers.chronicler.adapters.owntracks_place_cluster.classify_trail",
                side_effect=capture_places,
            ),
            patch(
                "butlers.chronicler.adapters.owntracks_place_cluster.resolve_owner_entity_id",
                return_value=None,
            ),
            patch(
                "butlers.chronicler.adapters.owntracks_place_cluster.upsert_episode",
                side_effect=persist_place,
            ),
        ):
            result = await OwnTracksPlaceClusterAdapter().project(
                _pool_returning(*ordered), chronicler_pool=_chronicler_pool(), since=None
            )
        assert verdicts == [(9, set(endpoints))]
        assert len(places) == result.episodes_closed == 3
        assert {episode.payload["endpoint_identity"] for episode in places} == set(endpoints)
        assert all(episode.payload["point_count"] == 3 for episode in places)
        assert all(
            (episode.end_at - episode.start_at) == timedelta(minutes=20) for episode in places
        )
        assert all(episode.start_at == _NOW + timedelta(minutes=5) for episode in places)
