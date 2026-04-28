"""Tests for the OwnTracks point Chronicler projection adapter.

Covers:
- Per-point event projection correctness (one location event per row).
- Movement episode rollup correctness (contiguous points collapse).
- Gap detection: points beyond the threshold start a new episode.
- Endpoint identity change starts a new episode even within gap.
- Replay / idempotency (same source_ref on repeated runs).
- Missing evidence surface graceful degradation.
- Checkpoint advance / resume (watermark advances by ts).
- UUID primary-key guard: since_id is ignored and watermark_id remains unset.
- Source-scan guardrail: no LLM imports in adapters/owntracks.py.
- Contracts registration: source upgraded from PLANNED to SUPPORTED.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.chronicler.adapters.owntracks import (  # noqa: E402
    DEFAULT_BATCH_LIMIT,
    EPISODE_TYPE_MOVEMENT,
    EVENT_TYPE_LOCATION,
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
    # Use a default argument to capture each `r` by value in the closure,
    # avoiding the classic loop-variable capture pitfall.
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
    """The owntracks adapter module must not import any LLM client packages.

    Parses the source AST rather than inspecting the live module so that
    transitive imports through other modules don't cause false negatives.
    """
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
# Module constants
# ---------------------------------------------------------------------------


def test_source_name() -> None:
    assert SOURCE_NAME == "owntracks.points"


def test_event_type() -> None:
    assert EVENT_TYPE_LOCATION == "location"


def test_episode_type() -> None:
    assert EPISODE_TYPE_MOVEMENT == "movement_episode"


def test_default_batch_limit() -> None:
    assert DEFAULT_BATCH_LIMIT == 1000


def test_movement_gap_minutes() -> None:
    assert MOVEMENT_GAP_MINUTES == 30


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
    assert ev.event_type == EVENT_TYPE_LOCATION
    assert ev.occurred_at == _NOW
    assert ev.precision == Precision.EXACT
    assert ev.privacy == Privacy.SENSITIVE
    assert ev.payload["lat"] == 1.2345
    assert ev.payload["lon"] == 103.8765
    assert ev.payload["accuracy"] == 15.0
    assert ev.payload["trigger"] == "p"
    assert ev.payload["endpoint_identity"] == _ENDPOINT


@pytest.mark.asyncio
async def test_point_event_title_includes_coordinates() -> None:
    row = _make_row(lat=51.5074, lon=-0.1278, accuracy=None)
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

    assert "51.50740" in upserted_events[0].title
    assert "-0.12780" in upserted_events[0].title


@pytest.mark.asyncio
async def test_point_event_omits_accuracy_from_payload_when_none() -> None:
    row = _make_row(accuracy=None)
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

    assert "accuracy" not in upserted_events[0].payload


@pytest.mark.asyncio
async def test_nonfinite_accuracy_is_omitted_without_failing_projection() -> None:
    row = _make_row(accuracy=float("nan"))
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
    assert len(result.warnings) == 1
    assert "accuracy" in result.warnings[0]
    assert "accuracy" not in upserted_events[0].payload


# ---------------------------------------------------------------------------
# Source_ref / idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_ref_uses_idempotency_key() -> None:
    ikey = "owntracks:owntracks:alice:1711447200:location"
    row = _make_row(idempotency_key=ikey)
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

    expected_ref = f"connectors.owntracks_points:{ikey}"
    assert upserted_events[0].source_ref == expected_ref


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
    assert ep.privacy == Privacy.SENSITIVE


@pytest.mark.asyncio
async def test_two_close_points_produce_one_episode() -> None:
    t1 = _NOW
    t2 = _NOW + timedelta(minutes=10)
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

    assert result.episodes_closed == 1
    assert len(upserted_episodes) == 1
    ep = upserted_episodes[0]
    assert ep.start_at == t1
    assert ep.end_at == t2
    assert ep.payload["point_count"] == 2


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


@pytest.mark.asyncio
async def test_movement_episode_source_ref_is_stable_on_replay() -> None:
    """The movement episode source_ref is keyed to (endpoint, start_tst) —
    stable even when the batch boundary shifts on replay."""
    row = _make_row(ts=_NOW)
    adapter = OwnTracksPointAdapter()
    refs: list[str] = []

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        refs.append(episode.source_ref)
        return episode

    pool1 = _pool_returning(row)
    cp1 = _chronicler_pool()
    pool2 = _pool_returning(row)
    cp2 = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch(
            "butlers.chronicler.adapters.owntracks.upsert_episode",
            side_effect=_fake_upsert_ep,
        ),
    ):
        mock_pe.return_value = MagicMock()
        await adapter.project(pool1, chronicler_pool=cp1, since=None)
        await adapter.project(pool2, chronicler_pool=cp2, since=None)

    assert refs[0] == refs[1]


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
async def test_watermark_preserved_when_no_rows() -> None:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = OwnTracksPointAdapter()
    cp = _chronicler_pool()
    prior_watermark = _NOW - timedelta(days=1)

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event"),
        patch("butlers.chronicler.adapters.owntracks.upsert_episode"),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=prior_watermark)

    assert result.watermark == prior_watermark
    assert result.rows_projected == 0


@pytest.mark.asyncio
async def test_since_filter_passed_to_query() -> None:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = OwnTracksPointAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=2)

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event"),
        patch("butlers.chronicler.adapters.owntracks.upsert_episode"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=since)

    assert conn.fetch.await_count == 1
    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "ts > $1" in query
    assert call_args.args[1] == since


# ---------------------------------------------------------------------------
# Deterministic ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_order_by_includes_id_tiebreaker_without_since() -> None:
    """ORDER BY clause must include id ASC as a tie-breaker when since=None.

    Same-timestamp rows in the evidence table have non-deterministic ordering
    without a secondary sort key, which can cause rows to be missed or
    duplicated at batch boundaries when paginating with a watermark.
    """
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = OwnTracksPointAdapter()
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event"),
        patch("butlers.chronicler.adapters.owntracks.upsert_episode"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    query: str = conn.fetch.call_args.args[0]
    assert "ORDER BY ts ASC, id ASC" in query


@pytest.mark.asyncio
async def test_order_by_includes_id_tiebreaker_with_since() -> None:
    """ORDER BY clause must include id ASC as a tie-breaker when since is given."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = OwnTracksPointAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=1)

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event"),
        patch("butlers.chronicler.adapters.owntracks.upsert_episode"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=since)

    query: str = conn.fetch.call_args.args[0]
    assert "ORDER BY ts ASC, id ASC" in query


# ---------------------------------------------------------------------------
# Contracts registration
# ---------------------------------------------------------------------------


def test_owntracks_adapter_exported_from_package() -> None:
    from butlers.chronicler.adapters import OwnTracksPointAdapter as _Cls

    assert _Cls is OwnTracksPointAdapter


def test_owntracks_points_supported_in_contracts() -> None:
    from butlers.chronicler.contracts import find_source
    from butlers.chronicler.models import Compatibility

    source = find_source("owntracks.points")
    assert source is not None
    assert source.chronicler_compatibility == Compatibility.SUPPORTED
    assert source.read_surface == "connectors.owntracks_points"


def test_owntracks_points_in_supported_names() -> None:
    from butlers.chronicler.contracts import supported_source_names

    assert "owntracks.points" in supported_source_names()


def test_owntracks_points_not_in_planned_names() -> None:
    from butlers.chronicler.contracts import planned_source_names

    assert "owntracks.points" not in planned_source_names()


# ---------------------------------------------------------------------------
# UUID primary-key checkpoint behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_since_id_is_ignored_uuid_pk() -> None:
    """OwnTracks rows use UUID primary keys, so integer ``since_id`` values
    must not be used in tuple comparisons against ``id``.
    """
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = OwnTracksPointAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=1)
    since_id = 42

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event"),
        patch("butlers.chronicler.adapters.owntracks.upsert_episode"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=since, since_id=since_id)

    assert conn.fetch.await_count == 1
    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "ts > $1" in query
    assert "(ts, id) > ($1, $2)" not in query
    assert call_args.args[1] == since


@pytest.mark.asyncio
async def test_single_column_fallback_when_since_id_is_none() -> None:
    """When ``since`` is given, the adapter uses ``WHERE ts > $1``."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = OwnTracksPointAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=1)

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event"),
        patch("butlers.chronicler.adapters.owntracks.upsert_episode"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=since, since_id=None)

    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "ts > $1" in query
    assert "(ts, id) > ($1, $2)" not in query


@pytest.mark.asyncio
async def test_watermark_id_is_always_none_for_uuid_pk() -> None:
    """The adapter never sets watermark_id because owntracks_points.id is UUID."""
    row = _make_row(ts=_NOW)
    row["id"] = "fd1822d6-b7a2-45b6-bcd8-bc18c73cb72d"

    adapter = OwnTracksPointAdapter()
    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.owntracks.upsert_episode") as mock_ep,
    ):
        mock_pe.return_value = MagicMock()
        mock_ep.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.watermark == _NOW
    assert result.watermark_id is None


@pytest.mark.asyncio
async def test_same_timestamp_uuid_rows_do_not_compare_ids_in_python() -> None:
    """Rows with equal timestamps should not compare UUID ids to since_id."""
    t = _NOW
    rows = [
        {**_make_row(ts=t, idempotency_key="k1"), "id": "fd1822d6-b7a2-45b6-bcd8-bc18c73cb72d"},
        {**_make_row(ts=t, idempotency_key="k2"), "id": "982ff78c-7224-43f7-94a8-6d6ad2120c36"},
        {**_make_row(ts=t, idempotency_key="k3"), "id": "d142af98-35d1-4857-8d00-691c1244d514"},
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

    assert result.watermark == t
    assert result.watermark_id is None
    assert result.rows_projected == 3


@pytest.mark.asyncio
async def test_stale_since_id_is_cleared_from_successful_result() -> None:
    """A stale integer checkpoint must not be carried forward for OwnTracks."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = OwnTracksPointAdapter()
    cp = _chronicler_pool()

    # Simulate previous batch ended at row id=10, timestamp _NOW.
    since = _NOW
    since_id = 10

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event"),
        patch("butlers.chronicler.adapters.owntracks.upsert_episode"),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=since, since_id=since_id)

    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "ts > $1" in query
    assert "(ts, id) > ($1, $2)" not in query
    assert call_args.args[1] == since
    assert result.watermark == since
    assert result.watermark_id is None


@pytest.mark.asyncio
async def test_watermark_id_cleared_when_no_rows() -> None:
    """When no rows are returned, stale ``since_id`` is not preserved."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = OwnTracksPointAdapter()
    cp = _chronicler_pool()
    prior_watermark = _NOW - timedelta(days=1)
    prior_watermark_id = 77

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event"),
        patch("butlers.chronicler.adapters.owntracks.upsert_episode"),
    ):
        result = await adapter.project(
            pool,
            chronicler_pool=cp,
            since=prior_watermark,
            since_id=prior_watermark_id,
        )

    assert result.watermark == prior_watermark
    assert result.watermark_id is None
