"""Cross-batch stitching tests for the Google Health sleep adapter.

Covers:
- Sleep epoch crossing a batch boundary → single sleep_episode, not two.
- Sleep epoch fully within a batch → no carryover written (episode has end_at).
- Worker restart between batches preserves carryover state (empty batch does
  NOT erase prior carryover).
- Empty batch does NOT overwrite carryover.
- Carryover from a different sleep session is NOT reused (correct rejection).
- Carryover matched by session_id → source_ref reused.
- Carryover matched by temporal proximity → source_ref reused.
- Corrupt carryover is discarded and a fresh episode is started.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.chronicler.adapters.google_health import (
    SLEEP_STITCH_GAP_MINUTES,
    GoogleHealthSleepAdapter,
)
from butlers.chronicler.models import Episode

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 25, 22, 0, 0, tzinfo=UTC)
_SESSION_ID = "sleep-session-abc"
_IKEY = f"google_health:sleep:{_SESSION_ID}:session"
_SOURCE_REF = f"health.facts:sleep_session:{_IKEY}"


# ---------------------------------------------------------------------------
# Helpers
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


def _make_row(
    *,
    row_id: str = "fact-uuid-001",
    idempotency_key: str = _IKEY,
    valid_at: datetime = _NOW,
    created_at: datetime = _NOW,
    session_id: str = _SESSION_ID,
    end_time: datetime | None = None,
    duration_ms: int = 0,
) -> dict:
    metadata: dict = {"session_id": session_id}
    if end_time is not None:
        metadata["end_time"] = end_time.isoformat()
    if duration_ms:
        metadata["duration_ms"] = duration_ms
    return {
        "id": row_id,
        "subject": "owner",
        "predicate": "sleep_session",
        "content": "sleep",
        "metadata": metadata,
        "valid_at": valid_at,
        "created_at": created_at,
        "idempotency_key": idempotency_key,
    }


def _make_mock_row(r: dict) -> MagicMock:
    return MagicMock(**r, **{"__getitem__": lambda s, k, _r=r: _r[k]})


def _pool_returning(*rows: dict) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)  # table-exists check
    conn.fetch = AsyncMock(return_value=[_make_mock_row(r) for r in rows])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _pool_table_exists_no_rows() -> AsyncMock:
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
# Batch boundary crossing: open episode → second batch completes it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sleep_crossing_batch_boundary_single_episode() -> None:
    """Batch N: sleep starts, no end_time → open episode with carryover.
    Batch N+1: same session_id, end_time now present → reuses same source_ref.
    """
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []
    source_refs: list[str] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        source_refs.append(episode.source_ref)
        return episode

    # ----- Batch 1: open episode (no end_time) -----
    row1 = _make_row(valid_at=_NOW)  # no end_time → open
    pool1 = _pool_returning(row1)
    cp1 = _chronicler_pool()
    saved_carryovers: list[dict] = []

    async def _capture_save(conn_or_pool, source_name, carryover):
        saved_carryovers.append(carryover)

    with (
        patch("butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert),
        patch("butlers.chronicler.adapters.google_health.get_carryover", return_value={}),
        patch(
            "butlers.chronicler.adapters.google_health.save_carryover",
            side_effect=_capture_save,
        ),
    ):
        result1 = await adapter.project(pool1, chronicler_pool=cp1, since=None)

    assert result1.rows_projected == 1
    assert len(saved_carryovers) == 1
    carryover_after_batch1 = saved_carryovers[0]
    assert "open_episode" in carryover_after_batch1
    batch1_source_ref = carryover_after_batch1["open_episode"]["source_ref"]

    # ----- Batch 2: same session, now has end_time -----
    session_end = _NOW + timedelta(hours=7)
    row2 = _make_row(
        row_id="fact-uuid-002",
        valid_at=_NOW,
        created_at=_NOW + timedelta(hours=7),
        end_time=session_end,
        duration_ms=7 * 3_600_000,
    )
    pool2 = _pool_returning(row2)
    cp2 = _chronicler_pool()
    saved_carryovers2: list[dict] = []

    async def _capture_save2(conn_or_pool, source_name, carryover):
        saved_carryovers2.append(carryover)

    with (
        patch("butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.google_health.get_carryover",
            return_value=carryover_after_batch1,
        ),
        patch(
            "butlers.chronicler.adapters.google_health.save_carryover",
            side_effect=_capture_save2,
        ),
    ):
        result2 = await adapter.project(pool2, chronicler_pool=cp2, since=None)

    assert result2.rows_projected == 1

    # Batch 2 must reuse the SAME source_ref as batch 1.
    batch2_source_ref = source_refs[-1]
    assert batch2_source_ref == batch1_source_ref, (
        f"Expected batch 2 to reuse source_ref {batch1_source_ref!r}, but got {batch2_source_ref!r}"
    )

    # Batch 2 episode now has end_at → no new carryover.
    assert saved_carryovers2[0] == {}


@pytest.mark.asyncio
async def test_batch2_episode_has_correct_end_at_after_stitching() -> None:
    """After stitching, the episode produced in batch 2 must carry the end_at from the new row."""
    adapter = GoogleHealthSleepAdapter()
    upserted_batch2: list[Episode] = []
    session_end = _NOW + timedelta(hours=8)

    prior_carryover = {
        "open_episode": {
            "source_ref": _SOURCE_REF,
            "start_at": _NOW.isoformat(),
            "session_id": _SESSION_ID,
        }
    }

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted_batch2.append(episode)
        return episode

    row = _make_row(end_time=session_end, duration_ms=8 * 3_600_000)
    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.google_health.get_carryover", return_value=prior_carryover
        ),
        patch("butlers.chronicler.adapters.google_health.save_carryover"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert len(upserted_batch2) == 1
    ep = upserted_batch2[0]
    assert ep.source_ref == _SOURCE_REF
    assert ep.end_at == session_end


# ---------------------------------------------------------------------------
# Sleep fully within batch: no carryover written
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sleep_within_batch_no_carryover_written() -> None:
    """When a sleep session is complete within one batch (has end_at), no open-episode
    carryover should be saved for the sleep session.
    """
    adapter = GoogleHealthSleepAdapter()
    saved_carryovers: list[dict] = []

    async def _capture_save(conn_or_pool, source_name, carryover):
        saved_carryovers.append(carryover)

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        return episode

    session_end = _NOW + timedelta(hours=7)
    row = _make_row(end_time=session_end, duration_ms=7 * 3_600_000)
    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert),
        patch("butlers.chronicler.adapters.google_health.get_carryover", return_value={}),
        patch(
            "butlers.chronicler.adapters.google_health.save_carryover",
            side_effect=_capture_save,
        ),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    # save_carryover was called once, but with an empty dict (no open episode).
    assert len(saved_carryovers) == 1
    assert saved_carryovers[0] == {}


# ---------------------------------------------------------------------------
# Empty batch: carryover NOT overwritten
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_batch_does_not_erase_carryover() -> None:
    """If the batch produces zero valid rows, save_carryover must NOT be called.

    This preserves the prior open episode for the next batch (worker restart
    protection).
    """
    adapter = GoogleHealthSleepAdapter()
    pool = _pool_table_exists_no_rows()
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.google_health.upsert_episode"),
        patch("butlers.chronicler.adapters.google_health.get_carryover", return_value={}),
        patch("butlers.chronicler.adapters.google_health.save_carryover") as mock_save,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 0
    mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_all_null_valid_at_does_not_erase_carryover() -> None:
    """If all rows in the batch have null valid_at (all skipped), save_carryover
    must NOT be called.  Same invariant as empty batch.
    """
    adapter = GoogleHealthSleepAdapter()
    row = _make_row(valid_at=None)  # type: ignore[arg-type]
    pool = _pool_returning(row)
    cp = _chronicler_pool()

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        return episode

    with (
        patch("butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert),
        patch("butlers.chronicler.adapters.google_health.get_carryover", return_value={}),
        patch("butlers.chronicler.adapters.google_health.save_carryover") as mock_save,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 0
    mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# Carryover from different session: NOT reused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_different_session_carryover_not_reused() -> None:
    """A carryover belonging to a DIFFERENT session_id must NOT be applied to
    a new row, provided the new row also starts outside the temporal gap.
    """
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    different_session_id = "completely-different-session"
    different_source_ref = f"health.facts:sleep_session:google_health:sleep:{different_session_id}"

    # New row starts 6 hours after the prior session — well outside the gap.
    new_start = _NOW + timedelta(hours=6)
    prior_carryover = {
        "open_episode": {
            "source_ref": different_source_ref,
            "start_at": _NOW.isoformat(),
            "session_id": different_session_id,
        }
    }

    row = _make_row(
        row_id="fact-uuid-new",
        idempotency_key="google_health:sleep:new-session:session",
        session_id="new-session",
        valid_at=new_start,
        created_at=new_start,
    )
    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.google_health.get_carryover", return_value=prior_carryover
        ),
        patch("butlers.chronicler.adapters.google_health.save_carryover"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert len(upserted) == 1
    # The source_ref must NOT be the carryover's ref — it should be derived fresh.
    assert upserted[0].source_ref != different_source_ref


# ---------------------------------------------------------------------------
# Carryover matching by session_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_carryover_matched_by_session_id_reuses_source_ref() -> None:
    """When the row's session_id matches the carryover's session_id, the prior
    source_ref should be reused even if the idempotency_key changed.
    """
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    prior_source_ref = "health.facts:sleep_session:prior_ikey"
    prior_carryover = {
        "open_episode": {
            "source_ref": prior_source_ref,
            "start_at": _NOW.isoformat(),
            "session_id": _SESSION_ID,
        }
    }

    # Row has a DIFFERENT idempotency_key but the SAME session_id.
    session_end = _NOW + timedelta(hours=7)
    row = _make_row(
        row_id="fact-uuid-002",
        idempotency_key="google_health:sleep:different-ikey:session",
        session_id=_SESSION_ID,  # same session_id!
        end_time=session_end,
        duration_ms=7 * 3_600_000,
    )
    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.google_health.get_carryover", return_value=prior_carryover
        ),
        patch("butlers.chronicler.adapters.google_health.save_carryover"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert len(upserted) == 1
    assert upserted[0].source_ref == prior_source_ref


# ---------------------------------------------------------------------------
# Carryover matching by temporal proximity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_carryover_matched_by_temporal_proximity() -> None:
    """When session_id is absent but the new row starts within
    SLEEP_STITCH_GAP_MINUTES of the carryover start_at, the prior source_ref
    is reused.
    """
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    prior_source_ref = "health.facts:sleep_session:prior_ikey"
    prior_carryover = {
        "open_episode": {
            "source_ref": prior_source_ref,
            "start_at": _NOW.isoformat(),
            # No session_id → fall back to temporal proximity
        }
    }

    # Row starts 5 minutes after the carryover start_at — well within the gap.
    close_start = _NOW + timedelta(minutes=5)
    session_end = close_start + timedelta(hours=7)
    row = _make_row(
        row_id="fact-uuid-close",
        valid_at=close_start,
        created_at=close_start + timedelta(hours=7),
        end_time=session_end,
        duration_ms=7 * 3_600_000,
    )
    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.google_health.get_carryover", return_value=prior_carryover
        ),
        patch("butlers.chronicler.adapters.google_health.save_carryover"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert len(upserted) == 1
    assert upserted[0].source_ref == prior_source_ref


@pytest.mark.asyncio
async def test_carryover_not_matched_outside_temporal_gap() -> None:
    """When session_id is absent and the new row starts beyond
    SLEEP_STITCH_GAP_MINUTES, the carryover is NOT reused.
    """
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    prior_source_ref = "health.facts:sleep_session:prior_ikey"
    prior_carryover = {
        "open_episode": {
            "source_ref": prior_source_ref,
            "start_at": _NOW.isoformat(),
        }
    }

    # Row starts 2 hours after the carryover — well beyond SLEEP_STITCH_GAP_MINUTES.
    far_start = _NOW + timedelta(hours=2)
    session_end = far_start + timedelta(hours=7)
    new_ikey = "google_health:sleep:far-session:session"
    row = _make_row(
        row_id="fact-uuid-far",
        idempotency_key=new_ikey,
        valid_at=far_start,
        created_at=far_start + timedelta(hours=7),
        end_time=session_end,
        duration_ms=7 * 3_600_000,
    )
    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.google_health.get_carryover", return_value=prior_carryover
        ),
        patch("butlers.chronicler.adapters.google_health.save_carryover"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert len(upserted) == 1
    # Must use fresh source_ref derived from idempotency_key, NOT the carryover's ref.
    assert upserted[0].source_ref != prior_source_ref
    assert new_ikey in upserted[0].source_ref


# ---------------------------------------------------------------------------
# Corrupt carryover: discarded, fresh episode started
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corrupt_carryover_is_discarded() -> None:
    """Malformed carryover (missing required fields) must be discarded with a
    warning and the row projected with a fresh source_ref.
    """
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    # Carryover is malformed: missing 'source_ref'.
    corrupt_carryover = {
        "open_episode": {
            # Missing 'source_ref' and 'start_at' — should be discarded.
            "session_id": _SESSION_ID,
        }
    }

    session_end = _NOW + timedelta(hours=7)
    row = _make_row(end_time=session_end, duration_ms=7 * 3_600_000)
    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert),
        patch(
            "butlers.chronicler.adapters.google_health.get_carryover",
            return_value=corrupt_carryover,
        ),
        patch("butlers.chronicler.adapters.google_health.save_carryover"),
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert len(upserted) == 1
    # source_ref must be derived fresh from idempotency_key.
    assert _IKEY in upserted[0].source_ref


# ---------------------------------------------------------------------------
# Configurable gap threshold
# ---------------------------------------------------------------------------


def test_sleep_stitch_gap_minutes_default() -> None:
    """The default stitch gap must be the module-level constant."""
    adapter = GoogleHealthSleepAdapter()
    assert adapter.sleep_stitch_gap_minutes == SLEEP_STITCH_GAP_MINUTES


def test_sleep_stitch_gap_minutes_configurable() -> None:
    """The stitch gap must be overridable at construction time."""
    adapter = GoogleHealthSleepAdapter(sleep_stitch_gap_minutes=60)
    assert adapter.sleep_stitch_gap_minutes == 60
