"""Tests for cross-batch episode stitching in Chronicler projection adapters.

Covers:
- home_assistant: batch boundary crossed mid-home-span → single episode
- home_assistant: span ends within batch (no carryover written)
- home_assistant: worker restart between batches (carryover DB survives)
- home_assistant: no carryover when entity is not home at batch end
- owntracks: batch boundary crossed mid-movement → single episode
- owntracks: gap between last batch point and first new point → two episodes
- owntracks: span ends within batch (no carryover needed) → unchanged
- owntracks: no valid rows → carryover not overwritten

Cross-adapter:
- Storage helpers get_carryover / save_carryover round-trip
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.chronicler.adapters.home_assistant import (
    HomeAssistantHistoryAdapter,
)
from butlers.chronicler.adapters.owntracks import (
    MOVEMENT_GAP_MINUTES,
    OwnTracksPointAdapter,
)
from butlers.chronicler.models import Episode

_NOW = datetime(2026, 4, 25, 10, 0, 0, tzinfo=UTC)
_PERSON = "person.alice"
_ENDPOINT = "owntracks:alice"


# ---------------------------------------------------------------------------
# Shared helpers
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


def _make_ha_row(
    entity_id: str = _PERSON,
    state: str = "home",
    recorded_at: datetime = _NOW,
    row_id: int = 1,
) -> dict:
    return {
        "id": row_id,
        "entity_id": entity_id,
        "state": state,
        "attributes": {},
        "recorded_at": recorded_at,
    }


def _make_ot_row(
    ts: datetime = _NOW,
    lat: float = 1.2345,
    lon: float = 103.8765,
    accuracy: float | None = 10.0,
    trigger: str | None = "p",
    endpoint_identity: str = _ENDPOINT,
    idempotency_key: str | None = None,
    row_id: int = 1,
) -> dict:
    ikey = idempotency_key or f"owntracks:{endpoint_identity}:{int(ts.timestamp())}:location"
    return {
        "id": row_id,
        "idempotency_key": ikey,
        "ts": ts,
        "lat": lat,
        "lon": lon,
        "accuracy": accuracy,
        "trigger": trigger,
        "event": None,
        "endpoint_identity": endpoint_identity,
        "raw_payload": {},
        "recorded_at": ts,
    }


def _make_mock_row(r: dict) -> MagicMock:
    return MagicMock(**r, **{"__getitem__": lambda s, k, _r=r: _r[k]})


def _pool_returning(*rows: dict) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[_make_mock_row(r) for r in rows])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _pool_empty() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _chronicler_pool() -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_NullCtx())
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)  # carryover fetch returns None
    conn.execute = AsyncMock()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


# ---------------------------------------------------------------------------
# Storage: get_carryover / save_carryover round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_carryover_returns_empty_when_null() -> None:
    from butlers.chronicler.storage import get_carryover

    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)

    result = await get_carryover(conn, "some.source")
    assert result == {}


@pytest.mark.asyncio
async def test_get_carryover_returns_empty_on_db_error() -> None:
    import asyncpg

    from butlers.chronicler.storage import get_carryover

    conn = AsyncMock()
    conn.fetchval = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedColumnError("column does not exist")
    )

    result = await get_carryover(conn, "some.source")
    assert result == {}


@pytest.mark.asyncio
async def test_save_carryover_ignores_missing_carryover_column() -> None:
    import asyncpg

    from butlers.chronicler.storage import save_carryover

    conn = AsyncMock()
    conn.execute = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedColumnError("column carryover does not exist")
    )

    await save_carryover(conn, "owntracks.points", {"open": {"source_ref": "ref:1"}})

    conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_carryover_parses_dict_from_string() -> None:
    import json

    from butlers.chronicler.storage import get_carryover

    payload = {
        "person.alice": {
            "source_ref": "ref:1",
            "start_at": "2026-01-01T00:00:00+00:00",
            "end_at": "2026-01-01T01:00:00+00:00",
        }
    }
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=json.dumps(payload))

    result = await get_carryover(conn, "home_assistant.history")
    assert result == payload


@pytest.mark.asyncio
async def test_get_carryover_returns_dict_directly() -> None:
    from butlers.chronicler.storage import get_carryover

    payload = {"person.alice": {"source_ref": "ref:1"}}
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=payload)

    result = await get_carryover(conn, "home_assistant.history")
    assert result == payload


# ---------------------------------------------------------------------------
# home_assistant: cross-batch stitching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ha_span_crossing_batch_boundary_produces_single_episode() -> None:
    """When a home span is open at the end of batch N and the entity continues
    home in batch N+1, the two batches should extend the same episode
    (same source_ref) rather than creating two separate ones.
    """
    t_batch1 = _NOW
    t_batch2 = _NOW + timedelta(hours=3)

    adapter = HomeAssistantHistoryAdapter()
    upserted: list[Episode] = []
    source_refs: list[str] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        source_refs.append(episode.source_ref)
        return episode

    # Batch 1: entity is home, open at batch end.
    batch1_row = _make_ha_row(state="home", recorded_at=t_batch1, row_id=1)
    pool1 = _pool_returning(batch1_row)
    cp1 = _chronicler_pool()

    # Capture what carryover was saved.
    saved_carryovers: list[dict] = []

    async def _capture_save(conn_or_pool, source_name, carryover):
        saved_carryovers.append(carryover)

    with (
        patch(
            "butlers.chronicler.adapters.home_assistant.upsert_episode", side_effect=_fake_upsert
        ),
        patch("butlers.chronicler.adapters.home_assistant.get_carryover", return_value={}),
        patch(
            "butlers.chronicler.adapters.home_assistant.save_carryover", side_effect=_capture_save
        ),
    ):
        result1 = await adapter.project(pool1, chronicler_pool=cp1, since=None)

    assert result1.episodes_closed == 1
    assert len(saved_carryovers) == 1
    carryover_after_batch1 = saved_carryovers[0]
    assert _PERSON in carryover_after_batch1

    batch1_episode_ref = carryover_after_batch1[_PERSON]["source_ref"]

    # Batch 2: entity is still home, then goes away.
    batch2_home_row = _make_ha_row(state="home", recorded_at=t_batch2, row_id=2)
    batch2_away_row = _make_ha_row(
        state="away", recorded_at=t_batch2 + timedelta(hours=1), row_id=3
    )
    pool2 = _pool_returning(batch2_home_row, batch2_away_row)
    cp2 = _chronicler_pool()
    saved_carryovers2: list[dict] = []

    async def _capture_save2(conn_or_pool, source_name, carryover):
        saved_carryovers2.append(carryover)

    with (
        patch(
            "butlers.chronicler.adapters.home_assistant.upsert_episode", side_effect=_fake_upsert
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant.get_carryover",
            return_value=carryover_after_batch1,
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant.save_carryover", side_effect=_capture_save2
        ),
    ):
        result2 = await adapter.project(pool2, chronicler_pool=cp2, since=None)

    assert result2.episodes_closed == 1

    # The episode upserted in batch 2 must use the SAME source_ref as batch 1.
    batch2_episode_refs = source_refs[1:]
    assert batch1_episode_ref in batch2_episode_refs, (
        f"Expected batch 2 to reuse source_ref {batch1_episode_ref!r}, "
        f"but got {batch2_episode_refs!r}"
    )

    # Carryover should be cleared (entity ended at home, then went away).
    assert saved_carryovers2[0] == {}


@pytest.mark.asyncio
async def test_ha_episode_start_at_preserved_from_prior_batch() -> None:
    """The extended episode keeps the start_at from the prior batch, not the current batch."""
    t_batch1 = _NOW
    t_batch2 = _NOW + timedelta(hours=2)

    adapter = HomeAssistantHistoryAdapter()
    upserted_batch2: list[Episode] = []

    # Simulate prior batch carryover.
    start_tst = int(t_batch1.timestamp())
    prior_source_ref = f"connectors.home_assistant_history:presence:{_PERSON}:{start_tst}"
    prior_carryover = {
        _PERSON: {
            "source_ref": prior_source_ref,
            "start_at": t_batch1.isoformat(),
            "end_at": t_batch1.isoformat(),
        }
    }

    async def _fake_upsert_batch2(conn: object, episode: Episode) -> Episode:
        upserted_batch2.append(episode)
        return episode

    batch2_rows = [
        _make_ha_row(state="home", recorded_at=t_batch2, row_id=2),
        _make_ha_row(state="away", recorded_at=t_batch2 + timedelta(minutes=30), row_id=3),
    ]
    pool2 = _pool_returning(*batch2_rows)
    cp2 = _chronicler_pool()

    with (
        patch(
            "butlers.chronicler.adapters.home_assistant.upsert_episode",
            side_effect=_fake_upsert_batch2,
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant.get_carryover", return_value=prior_carryover
        ),
        patch("butlers.chronicler.adapters.home_assistant.save_carryover"),
    ):
        await adapter.project(pool2, chronicler_pool=cp2, since=None)

    assert len(upserted_batch2) == 1
    ep = upserted_batch2[0]
    assert ep.source_ref == prior_source_ref
    assert ep.start_at == t_batch1  # preserved from prior batch
    assert ep.end_at == t_batch2  # updated to latest home row


@pytest.mark.asyncio
async def test_ha_no_carryover_when_span_ends_within_batch() -> None:
    """When a home span opens and closes within one batch, no carryover is saved."""
    t0 = _NOW
    t1 = _NOW + timedelta(hours=1)
    t2 = _NOW + timedelta(hours=2)

    rows = [
        _make_ha_row(state="home", recorded_at=t0, row_id=1),
        _make_ha_row(state="home", recorded_at=t1, row_id=2),
        _make_ha_row(state="away", recorded_at=t2, row_id=3),
    ]

    adapter = HomeAssistantHistoryAdapter()
    saved_carryovers: list[dict] = []

    async def _capture_save(conn_or_pool, source_name, carryover):
        saved_carryovers.append(carryover)

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.home_assistant.upsert_episode"),
        patch("butlers.chronicler.adapters.home_assistant.get_carryover", return_value={}),
        patch(
            "butlers.chronicler.adapters.home_assistant.save_carryover", side_effect=_capture_save
        ),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 1
    assert len(saved_carryovers) == 1
    # Span closed within batch → no carryover for this entity.
    assert saved_carryovers[0] == {}


@pytest.mark.asyncio
async def test_ha_carryover_written_for_open_span_at_batch_end() -> None:
    """When entity is still home at batch end, carryover is saved."""
    t0 = _NOW
    t1 = _NOW + timedelta(hours=2)

    rows = [
        _make_ha_row(state="home", recorded_at=t0, row_id=1),
        _make_ha_row(state="home", recorded_at=t1, row_id=2),
    ]

    adapter = HomeAssistantHistoryAdapter()
    saved_carryovers: list[dict] = []
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    async def _capture_save(conn_or_pool, source_name, carryover):
        saved_carryovers.append(carryover)

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with (
        patch(
            "butlers.chronicler.adapters.home_assistant.upsert_episode", side_effect=_fake_upsert
        ),
        patch("butlers.chronicler.adapters.home_assistant.get_carryover", return_value={}),
        patch(
            "butlers.chronicler.adapters.home_assistant.save_carryover", side_effect=_capture_save
        ),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 1
    assert len(saved_carryovers) == 1
    co = saved_carryovers[0]
    assert _PERSON in co
    assert co[_PERSON]["source_ref"] == upserted[0].source_ref
    assert co[_PERSON]["start_at"] == t0.isoformat()
    assert co[_PERSON]["end_at"] == t1.isoformat()


@pytest.mark.asyncio
async def test_ha_no_carryover_when_entity_not_home_at_batch_end() -> None:
    """If the last row for an entity is away, no carryover entry is written."""
    row = _make_ha_row(state="away", recorded_at=_NOW, row_id=1)

    adapter = HomeAssistantHistoryAdapter()
    saved_carryovers: list[dict] = []

    async def _capture_save(conn_or_pool, source_name, carryover):
        saved_carryovers.append(carryover)

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.home_assistant.upsert_episode"),
        patch("butlers.chronicler.adapters.home_assistant.get_carryover", return_value={}),
        patch(
            "butlers.chronicler.adapters.home_assistant.save_carryover", side_effect=_capture_save
        ),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert len(saved_carryovers) == 1
    assert saved_carryovers[0] == {}


@pytest.mark.asyncio
async def test_ha_corrupt_carryover_discarded_and_new_episode_started() -> None:
    """If the carryover dict has missing/invalid keys it is discarded silently."""
    bad_carryover = {_PERSON: {"source_ref": "ref:x"}}  # missing start_at / end_at

    row = _make_ha_row(state="home", recorded_at=_NOW, row_id=1)
    adapter = HomeAssistantHistoryAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch(
            "butlers.chronicler.adapters.home_assistant.upsert_episode", side_effect=_fake_upsert
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant.get_carryover", return_value=bad_carryover
        ),
        patch("butlers.chronicler.adapters.home_assistant.save_carryover"),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 1
    # New source_ref is generated (not the bad one).
    assert upserted[0].source_ref != "ref:x"


@pytest.mark.asyncio
async def test_ha_no_presence_rows_does_not_call_save_carryover() -> None:
    """When there are no presence rows, save_carryover is not called (carryover preserved)."""
    row = _make_ha_row(entity_id="light.kitchen", state="on", recorded_at=_NOW, row_id=1)

    adapter = HomeAssistantHistoryAdapter()

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.home_assistant.upsert_episode"),
        patch("butlers.chronicler.adapters.home_assistant.get_carryover") as mock_get,
        patch("butlers.chronicler.adapters.home_assistant.save_carryover") as mock_save,
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    # Non-presence rows: no carryover interaction expected.
    mock_get.assert_not_called()
    mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# owntracks: cross-batch stitching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ot_movement_span_crossing_batch_boundary_is_single_episode() -> None:
    """A movement sequence split across a batch boundary should produce one episode."""
    t1 = _NOW
    t2 = _NOW + timedelta(minutes=10)

    # Batch 1: single point, episode is open at batch end.
    batch1_row = _make_ot_row(ts=t1, idempotency_key="k1", row_id=1)

    adapter = OwnTracksPointAdapter()
    upserted: list[Episode] = []
    source_refs_per_call: list[str] = []
    saved_carryovers: list[dict] = []

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        source_refs_per_call.append(episode.source_ref)
        return episode

    async def _capture_save(conn_or_pool, source_name, carryover):
        saved_carryovers.append(carryover)

    pool1 = _pool_returning(batch1_row)
    cp1 = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.owntracks.upsert_episode", side_effect=_fake_upsert_ep),
        patch("butlers.chronicler.adapters.owntracks.get_carryover", return_value={}),
        patch("butlers.chronicler.adapters.owntracks.save_carryover", side_effect=_capture_save),
    ):
        mock_pe.return_value = MagicMock()
        result1 = await adapter.project(pool1, chronicler_pool=cp1, since=None)

    assert result1.episodes_closed == 1
    assert len(saved_carryovers) == 1
    co = saved_carryovers[0]
    assert _ENDPOINT in co
    batch1_source_ref = co[_ENDPOINT]["source_ref"]

    # Batch 2: another point within gap of batch1's last point.
    batch2_row = _make_ot_row(ts=t2, idempotency_key="k2", row_id=2)
    pool2 = _pool_returning(batch2_row)
    cp2 = _chronicler_pool()
    saved_carryovers2: list[dict] = []

    async def _capture_save2(conn_or_pool, source_name, carryover):
        saved_carryovers2.append(carryover)

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe2,
        patch("butlers.chronicler.adapters.owntracks.upsert_episode", side_effect=_fake_upsert_ep),
        patch("butlers.chronicler.adapters.owntracks.get_carryover", return_value=co),
        patch("butlers.chronicler.adapters.owntracks.save_carryover", side_effect=_capture_save2),
    ):
        mock_pe2.return_value = MagicMock()
        result2 = await adapter.project(pool2, chronicler_pool=cp2, since=None)

    assert result2.episodes_closed == 1

    # The second batch's episode must use the same source_ref as the first.
    batch2_episode_refs = source_refs_per_call[1:]
    assert batch1_source_ref in batch2_episode_refs, (
        f"Expected batch 2 to reuse source_ref {batch1_source_ref!r}, got {batch2_episode_refs!r}"
    )

    # end_at of the stitched episode should span from t1 to t2.
    stitched_ep = upserted[-1]
    assert stitched_ep.start_at == t1
    assert stitched_ep.end_at == t2


@pytest.mark.asyncio
async def test_ot_gap_beyond_threshold_between_batches_produces_two_episodes() -> None:
    """When the first point of batch 2 is beyond the gap threshold from the last
    point of batch 1, two separate episodes should be produced."""
    t1 = _NOW
    t2 = _NOW + timedelta(minutes=MOVEMENT_GAP_MINUTES + 5)  # beyond threshold

    batch1_row = _make_ot_row(ts=t1, idempotency_key="k1", row_id=1)
    batch2_row = _make_ot_row(ts=t2, idempotency_key="k2", row_id=2)

    adapter = OwnTracksPointAdapter()
    source_refs: list[str] = []

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        source_refs.append(episode.source_ref)
        return episode

    saved_carryovers1: list[dict] = []

    async def _capture_save1(conn_or_pool, source_name, carryover):
        saved_carryovers1.append(carryover)

    pool1 = _pool_returning(batch1_row)
    cp1 = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.owntracks.upsert_episode", side_effect=_fake_upsert_ep),
        patch("butlers.chronicler.adapters.owntracks.get_carryover", return_value={}),
        patch("butlers.chronicler.adapters.owntracks.save_carryover", side_effect=_capture_save1),
    ):
        mock_pe.return_value = MagicMock()
        await adapter.project(pool1, chronicler_pool=cp1, since=None)

    co_after_batch1 = saved_carryovers1[0]

    pool2 = _pool_returning(batch2_row)
    cp2 = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe2,
        patch("butlers.chronicler.adapters.owntracks.upsert_episode", side_effect=_fake_upsert_ep),
        patch("butlers.chronicler.adapters.owntracks.get_carryover", return_value=co_after_batch1),
        patch("butlers.chronicler.adapters.owntracks.save_carryover"),
    ):
        mock_pe2.return_value = MagicMock()
        await adapter.project(pool2, chronicler_pool=cp2, since=None)

    # Two distinct source_refs → two distinct episodes.
    assert len(source_refs) == 2
    assert source_refs[0] != source_refs[1]


@pytest.mark.asyncio
async def test_ot_future_carryover_is_discarded_for_replayed_older_batch() -> None:
    """A retried older batch must not extend carryover from a newer point."""
    row1_ts = _NOW
    row2_ts = _NOW + timedelta(minutes=10)
    future_ts = _NOW + timedelta(hours=4)
    future_source_ref = (
        f"connectors.owntracks_points:movement:{_ENDPOINT}:{int(future_ts.timestamp())}"
    )
    future_carryover = {
        _ENDPOINT: {
            "source_ref": future_source_ref,
            "start_at": future_ts.isoformat(),
            "end_at": future_ts.isoformat(),
            "start_lat": 1.0,
            "start_lon": 2.0,
        }
    }
    rows = [
        _make_ot_row(ts=row1_ts, idempotency_key="k1", row_id=1),
        _make_ot_row(ts=row2_ts, idempotency_key="k2", row_id=2),
    ]

    adapter = OwnTracksPointAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.owntracks.upsert_episode", side_effect=_fake_upsert_ep),
        patch("butlers.chronicler.adapters.owntracks.get_carryover", return_value=future_carryover),
        patch("butlers.chronicler.adapters.owntracks.save_carryover"),
    ):
        mock_pe.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 1
    assert len(upserted) == 1
    assert upserted[0].source_ref != future_source_ref
    assert upserted[0].start_at == row1_ts
    assert upserted[0].end_at == row2_ts
    assert upserted[0].end_at >= upserted[0].start_at


@pytest.mark.asyncio
async def test_ot_span_ends_within_batch_unchanged_behavior() -> None:
    """A movement sequence that starts and ends within a single batch still
    produces exactly one episode and the existing behavior is preserved."""
    t1 = _NOW
    t2 = _NOW + timedelta(minutes=10)
    t3 = _NOW + timedelta(minutes=MOVEMENT_GAP_MINUTES + 15)  # gap > threshold

    rows = [
        _make_ot_row(ts=t1, idempotency_key="k1", row_id=1),
        _make_ot_row(ts=t2, idempotency_key="k2", row_id=2),
        _make_ot_row(ts=t3, idempotency_key="k3", row_id=3),  # starts new episode
    ]

    adapter = OwnTracksPointAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.owntracks.upsert_episode", side_effect=_fake_upsert_ep),
        patch("butlers.chronicler.adapters.owntracks.get_carryover", return_value={}),
        patch("butlers.chronicler.adapters.owntracks.save_carryover"),
    ):
        mock_pe.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 2
    assert upserted[0].start_at == t1
    assert upserted[0].end_at == t2
    assert upserted[1].start_at == t3


@pytest.mark.asyncio
async def test_ot_no_valid_rows_does_not_overwrite_carryover() -> None:
    """When all rows are filtered as invalid (malformed), save_carryover is not called."""
    # Row with NaN lat — will be filtered by _normalize_row.
    bad_row = _make_ot_row(lat=float("nan"), idempotency_key="bad", row_id=1)

    adapter = OwnTracksPointAdapter()
    pool = _pool_returning(bad_row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event"),
        patch("butlers.chronicler.adapters.owntracks.upsert_episode"),
        patch("butlers.chronicler.adapters.owntracks.get_carryover") as mock_get,
        patch("butlers.chronicler.adapters.owntracks.save_carryover") as mock_save,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 0
    mock_get.assert_not_called()
    mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_ot_carryover_written_for_open_movement_episode() -> None:
    """A movement episode open at end of batch has carryover saved with correct fields."""
    row = _make_ot_row(ts=_NOW, lat=1.0, lon=2.0, idempotency_key="k1", row_id=1)

    adapter = OwnTracksPointAdapter()
    upserted: list[Episode] = []
    saved_carryovers: list[dict] = []

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    async def _capture_save(conn_or_pool, source_name, carryover):
        saved_carryovers.append(carryover)

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.owntracks.upsert_episode", side_effect=_fake_upsert_ep),
        patch("butlers.chronicler.adapters.owntracks.get_carryover", return_value={}),
        patch("butlers.chronicler.adapters.owntracks.save_carryover", side_effect=_capture_save),
    ):
        mock_pe.return_value = MagicMock()
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert len(saved_carryovers) == 1
    co = saved_carryovers[0]
    assert _ENDPOINT in co
    entry = co[_ENDPOINT]
    assert "source_ref" in entry
    assert "start_at" in entry
    assert "end_at" in entry
    assert "start_lat" in entry
    assert "start_lon" in entry
    assert entry["source_ref"] == upserted[0].source_ref


@pytest.mark.asyncio
async def test_ot_corrupt_carryover_discarded_and_new_episode_started() -> None:
    """Corrupt carryover (missing end_at) is discarded; a new episode starts cleanly."""
    bad_carryover = {
        _ENDPOINT: {"source_ref": "ref:x", "start_at": _NOW.isoformat()}
    }  # missing end_at

    row = _make_ot_row(ts=_NOW + timedelta(minutes=5), idempotency_key="k1", row_id=1)
    adapter = OwnTracksPointAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert_ep(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.owntracks.upsert_point_event") as mock_pe,
        patch("butlers.chronicler.adapters.owntracks.upsert_episode", side_effect=_fake_upsert_ep),
        patch("butlers.chronicler.adapters.owntracks.get_carryover", return_value=bad_carryover),
        patch("butlers.chronicler.adapters.owntracks.save_carryover"),
    ):
        mock_pe.return_value = MagicMock()
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 1
    assert upserted[0].source_ref != "ref:x"
