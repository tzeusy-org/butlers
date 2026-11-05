"""Timed Steam session bounds, refresh and page-boundary regression coverage."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from butlers.chronicler.adapters.steam_status import SteamStatusAdapter, session_episode
from butlers.chronicler.models import Layer, Precision
from butlers.testing.fake_pool import AsyncCtx

pytestmark = pytest.mark.unit
_START = datetime(2026, 11, 1, 13, 15, tzinfo=UTC)


def _row(at=_START, game="Portal", persona=1, steam_id=42):
    return dict(
        steam_id=steam_id,
        steam_account_id=None,
        observed_at=at,
        persona_state=persona,
        game_id="400" if game else None,
        game_name=game,
        recorded_at=at,
    )


def test_open_session_is_bounded_evidence_and_later_close_keeps_identity():
    first = session_episode(_row(), end=None, now=_START + timedelta(hours=2))
    capped = session_episode(_row(), end=None, now=_START + timedelta(days=1))
    closed = session_episode(
        _row(), end=_START + timedelta(hours=3), now=_START + timedelta(days=1)
    )
    assert first.end_at == _START + timedelta(hours=2)
    assert capped.end_at == _START + timedelta(hours=12)
    assert closed.end_at == _START + timedelta(hours=3)
    assert first.source_ref == closed.source_ref == capped.source_ref
    assert first.layer == Layer.EVIDENCE and first.precision == Precision.MINUTE
    assert first.payload["provisional"] is True and closed.payload["provisional"] is False


async def test_pages_do_not_split_game_and_reprojection_refreshes_open_sessions():
    pool = AsyncMock()
    pool.fetchval.return_value = True
    write_pool = AsyncMock()
    conn = AsyncMock()
    write_pool.acquire = lambda: AsyncCtx(conn)
    episodes = []

    async def save(_conn, episode):
        episodes.append(episode)
        return episode

    adapter = SteamStatusAdapter(batch_limit=1)
    pages = [
        [_row()],
        [_row(_START + timedelta(minutes=5), persona=3)],
        [_row(_START + timedelta(hours=1), game=None)],
        [_row(_START + timedelta(hours=2), game="Half-Life")],
        [],
    ]
    with (
        patch("butlers.chronicler.adapters.steam_status.resolve_owner_entity_id", AsyncMock()),
        patch(
            "butlers.chronicler.adapters.steam_status.owner_episode_transaction",
            lambda c: AsyncCtx(c),
        ),
        patch("butlers.chronicler.adapters.steam_status.upsert_owner_episode_entity", AsyncMock()),
        patch("butlers.chronicler.adapters.steam_status.upsert_episode", save),
        patch(
            "butlers.chronicler.adapters.steam_status._now",
            return_value=_START + timedelta(hours=3),
        ),
    ):
        pool.fetch.side_effect = pages
        result = await adapter.project(pool, chronicler_pool=write_pool, since=None)
    assert result.rows_projected == 2
    assert [(e.start_at, e.end_at) for e in episodes] == [
        (_START, _START + timedelta(hours=1)),
        (_START + timedelta(hours=2), _START + timedelta(hours=3)),
    ]
    assert episodes[0].payload["provisional"] is False
    assert episodes[1].payload["provisional"] is True
    assert all(e.layer == Layer.EVIDENCE for e in episodes)
    # A checkpoint must not hide the open session on the next run.
    with (
        patch("butlers.chronicler.adapters.steam_status.resolve_owner_entity_id", AsyncMock()),
        patch(
            "butlers.chronicler.adapters.steam_status.owner_episode_transaction",
            lambda c: AsyncCtx(c),
        ),
        patch("butlers.chronicler.adapters.steam_status.upsert_owner_episode_entity", AsyncMock()),
        patch("butlers.chronicler.adapters.steam_status.upsert_episode", save),
        patch(
            "butlers.chronicler.adapters.steam_status._now",
            return_value=_START + timedelta(hours=4),
        ),
    ):
        pool.fetch.side_effect = pages
        refreshed = await adapter.project(pool, chronicler_pool=write_pool, since=result.watermark)
    assert refreshed.rows_projected == 1  # unchanged closed history is not rewritten
    assert episodes[-1].end_at == _START + timedelta(hours=4)
    assert episodes[-1].source_ref == episodes[1].source_ref

    with (
        patch("butlers.chronicler.adapters.steam_status.resolve_owner_entity_id", AsyncMock()),
        patch(
            "butlers.chronicler.adapters.steam_status._now",
            return_value=_START + timedelta(hours=30),
        ),
    ):
        pool.fetch.side_effect = pages
        stable = await adapter.project(
            pool, chronicler_pool=write_pool, since=_START + timedelta(hours=25)
        )
    assert stable.rows_projected == 0  # capped sessions also stop causing historical rewrites
