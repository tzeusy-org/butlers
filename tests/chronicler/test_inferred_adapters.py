"""Tests for Chronicler inferred focus and reading adapters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.chronicler.adapters.focus import FocusInferredAdapter
from butlers.chronicler.adapters.reading import ReadingInferredAdapter, _title_matches_reading
from butlers.chronicler.models import Episode, Precision, Privacy


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


def _row(**values: object) -> MagicMock:
    return MagicMock(**values, **{"__getitem__": lambda s, k, _values=values: _values[k]})


def _chronicler_pool() -> AsyncMock:
    conn = AsyncMock()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


@pytest.mark.asyncio
async def test_focus_adapter_projects_long_task_session() -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=UTC)
    row = _row(
        id="episode-1",
        source_name="core.sessions",
        source_ref="general.sessions:abc",
        episode_type="work",
        start_at=start,
        end_at=start + timedelta(minutes=72),
        title="General manual task",
        payload={"trigger_source": "trigger"},
        created_at=start + timedelta(minutes=73),
    )
    adapter = FocusInferredAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    with patch("butlers.chronicler.adapters.focus.upsert_episode", side_effect=_fake_upsert):
        episode = await adapter._maybe_project(_chronicler_pool(), row)

    assert episode is not None
    assert upserted[0].source_name == "chronicler.focus_inferred"
    assert upserted[0].source_ref == "chronicler.episodes:episode-1:long_task_session"
    assert upserted[0].episode_type == "focus_block"
    assert upserted[0].precision == Precision.MINUTE
    assert upserted[0].privacy == Privacy.NORMAL


@pytest.mark.asyncio
async def test_focus_adapter_skips_route_conversation_session() -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=UTC)
    row = _row(
        id="episode-2",
        source_name="core.sessions",
        source_ref="general.sessions:def",
        episode_type="work",
        start_at=start,
        end_at=start + timedelta(minutes=72),
        title="Conversation with Anna",
        payload={"trigger_source": "route"},
        created_at=start + timedelta(minutes=73),
    )
    adapter = FocusInferredAdapter()

    with patch("butlers.chronicler.adapters.focus.upsert_episode") as mock_upsert:
        episode = await adapter._maybe_project(_chronicler_pool(), row)

    assert episode is None
    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_focus_adapter_skips_long_task_session_with_overlapping_route() -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=UTC)
    row = _row(
        id="episode-overlap",
        source_name="core.sessions",
        source_ref="general.sessions:ghi",
        episode_type="work",
        start_at=start,
        end_at=start + timedelta(minutes=72),
        title="General manual task",
        payload={"trigger_source": "trigger"},
        created_at=start + timedelta(minutes=73),
        overlaps_route=True,
    )
    adapter = FocusInferredAdapter()

    with patch("butlers.chronicler.adapters.focus.upsert_episode") as mock_upsert:
        episode = await adapter._maybe_project(_chronicler_pool(), row)

    assert episode is None
    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_reading_adapter_projects_calendar_reading_block() -> None:
    start = datetime(2026, 5, 8, 20, 0, tzinfo=UTC)
    row = _row(
        id="episode-3",
        source_name="google_calendar.completed",
        source_ref="calendar:event-1",
        episode_type="scheduled_block",
        start_at=start,
        end_at=start + timedelta(minutes=50),
        title="Read book: The Dispossessed",
        payload={},
        created_at=start + timedelta(minutes=51),
    )
    adapter = ReadingInferredAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    with patch("butlers.chronicler.adapters.reading.upsert_episode", side_effect=_fake_upsert):
        episode = await adapter._project_calendar_row(_chronicler_pool(), row)

    assert episode is not None
    assert upserted[0].source_name == "chronicler.reading_inferred"
    assert upserted[0].source_ref == "chronicler.episodes:episode-3:reading"
    assert upserted[0].episode_type == "reading_block"
    assert upserted[0].payload["signal"] == "calendar_titled"


@pytest.mark.parametrize(
    "title", ["Book: The Dispossessed", "Article: local-first", "Paper: CRDTs"]
)
def test_reading_title_matches_colon_keywords(title: str) -> None:
    assert _title_matches_reading(title) is True
