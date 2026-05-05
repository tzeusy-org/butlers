"""Tests for the Steam play-history Chronicler projection adapter.

Covers:
- Per-episode projection correctness (one play_episode per row).
- Episode start_at / end_at derivation from date + playtime_minutes.
- Negative playtime skipped with watermark advance (error path).
- Missing evidence surface graceful degradation (fetchval=False, UndefinedTableError).
- Watermark advances to max recorded_at across batch.
- No-LLM AST scan.
- Adapter export from package.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.chronicler.adapters.steam import (
    EPISODE_TYPE_PLAY,
    SOURCE_NAME,
    SteamPlayAdapter,
)
from butlers.chronicler.models import Episode, Precision, Privacy

_NOW = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)
_DATE = date(2026, 4, 25)
_STEAM_ID = 76561198000000001
_APP_ID = 730
_APP_NAME = "Counter-Strike 2"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    steam_id: int = _STEAM_ID,
    steam_account_id: str | None = None,
    app_id: int = _APP_ID,
    app_name: str | None = _APP_NAME,
    play_date: date = _DATE,
    playtime_minutes: int = 90,
    recorded_at: datetime = _NOW,
) -> dict:
    return {
        "id": "some-uuid",
        "steam_id": steam_id,
        "steam_account_id": steam_account_id,
        "app_id": app_id,
        "app_name": app_name,
        "date": play_date,
        "playtime_minutes": playtime_minutes,
        "recorded_at": recorded_at,
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
    """Build a minimal mock chronicler pool for upsert_episode calls."""
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
# No-LLM AST scan
# ---------------------------------------------------------------------------


def test_no_llm_imports_in_steam_adapter() -> None:
    """The steam adapter module must not import any LLM client packages."""
    import butlers.chronicler.adapters.steam as mod

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
                        f"LLM import detected in steam adapter: {alias.name!r}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for prefix in forbidden_prefixes:
                    assert not node.module.startswith(prefix), (
                        f"LLM import detected in steam adapter: {node.module!r}"
                    )


# ---------------------------------------------------------------------------
# Per-episode projection correctness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_single_row_produces_one_episode() -> None:
    row = _make_row()
    adapter = SteamPlayAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.steam.upsert_episode", side_effect=_fake_upsert):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert result.episodes_closed == 1
    assert len(upserted) == 1


@pytest.mark.asyncio
async def test_episode_fields_from_row() -> None:
    row = _make_row(play_date=_DATE, playtime_minutes=90, app_name="Counter-Strike 2")
    adapter = SteamPlayAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.steam.upsert_episode", side_effect=_fake_upsert):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    ep = upserted[0]
    assert ep.source_name == SOURCE_NAME
    assert ep.episode_type == EPISODE_TYPE_PLAY
    assert ep.precision == Precision.DAY
    assert ep.privacy == Privacy.NORMAL
    assert "Counter-Strike 2" in ep.title


@pytest.mark.asyncio
async def test_episode_anchored_to_recorded_at_when_within_day() -> None:
    """end_at = recorded_at; start_at = recorded_at - playtime_minutes."""
    play_date = date(2026, 4, 25)
    playtime = 90  # minutes
    recorded = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)
    row = _make_row(play_date=play_date, playtime_minutes=playtime, recorded_at=recorded)
    adapter = SteamPlayAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.steam.upsert_episode", side_effect=_fake_upsert):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    ep = upserted[0]
    assert ep.end_at == recorded
    assert ep.start_at == recorded - timedelta(minutes=playtime)


@pytest.mark.asyncio
async def test_episode_anchored_to_end_of_day_when_recorded_at_outside() -> None:
    """Backfilled day (recorded_at later than the date) anchors at end_of_day."""
    play_date = date(2026, 4, 23)
    playtime = 120  # minutes
    recorded = datetime(2026, 4, 25, 9, 0, 0, tzinfo=UTC)  # 2 days later
    row = _make_row(play_date=play_date, playtime_minutes=playtime, recorded_at=recorded)
    adapter = SteamPlayAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.steam.upsert_episode", side_effect=_fake_upsert):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    ep = upserted[0]
    end_of_day = datetime(2026, 4, 24, 0, 0, 0, tzinfo=UTC)
    assert ep.end_at == end_of_day
    assert ep.start_at == end_of_day - timedelta(minutes=playtime)


@pytest.mark.asyncio
async def test_negative_playtime_row_is_skipped_and_watermarked() -> None:
    row = _make_row(playtime_minutes=-15, recorded_at=_NOW)
    adapter = SteamPlayAdapter()
    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.steam.upsert_episode") as mock_upsert:
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.error is None
    assert result.rows_projected == 0
    assert result.episodes_closed == 0
    assert result.watermark == _NOW
    assert len(result.warnings) == 1
    assert "negative playtime_minutes" in result.warnings[0]
    mock_upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Missing evidence surface graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_evidence_table_returns_skipped_result() -> None:
    adapter = SteamPlayAdapter()
    pool = _pool_table_missing()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.steam.upsert_episode") as mock_upsert:
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.skipped is True
    assert result.skipped_reason is not None
    assert "not found" in result.skipped_reason
    assert result.rows_projected == 0
    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_undefined_table_exception_returns_skipped_result() -> None:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedTableError(
            'relation "connectors.steam_play_history" does not exist'
        )
    )
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = SteamPlayAdapter()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.steam.upsert_episode") as mock_upsert:
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.skipped is True
    assert result.rows_projected == 0
    assert result.watermark is None
    mock_upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Watermark advance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watermark_advances_to_max_recorded_at() -> None:
    t1 = _NOW
    t2 = _NOW + timedelta(hours=2)
    rows = [
        _make_row(recorded_at=t1, play_date=date(2026, 4, 24)),
        _make_row(recorded_at=t2, play_date=date(2026, 4, 25)),
    ]
    adapter = SteamPlayAdapter()

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        return episode

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.steam.upsert_episode", side_effect=_fake_upsert):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.watermark == t2


# ---------------------------------------------------------------------------
# Contracts registration
# ---------------------------------------------------------------------------


def test_steam_adapter_exported_from_package() -> None:
    from butlers.chronicler.adapters import SteamPlayAdapter as _Cls

    assert _Cls is SteamPlayAdapter
