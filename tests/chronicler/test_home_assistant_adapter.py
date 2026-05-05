"""Tests for the Home Assistant history Chronicler projection adapter.

Covers:
- Presence detection from state changes (person.user: away → home → away).
- Non-person entities are filtered.
- Watermark advances across all rows (not just presence rows).
- Missing evidence surface graceful degradation (fetchval=False, UndefinedTableError).
- Episode title and taxonomy contract tests.
- No-LLM AST scan.
- Adapter export from package.
- Regression: UUID id rows never set watermark_id (asyncpg DataError guard).
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.chronicler.adapters.home_assistant import (
    EPISODE_TYPE_PRESENCE,
    SOURCE_NAME,
    HomeAssistantHistoryAdapter,
)
from butlers.chronicler.models import Episode, Precision, Privacy

_NOW = datetime(2026, 4, 25, 8, 0, 0, tzinfo=UTC)
_PERSON = "person.alice"

_UUID_1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
_UUID_2 = uuid.UUID("22222222-2222-2222-2222-222222222222")
_UUID_3 = uuid.UUID("33333333-3333-3333-3333-333333333333")
_UUID_4 = uuid.UUID("44444444-4444-4444-4444-444444444444")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    entity_id: str = _PERSON,
    state: str = "home",
    recorded_at: datetime = _NOW,
    row_id: uuid.UUID = _UUID_1,
    attributes: dict | None = None,
) -> dict:
    return {
        "id": row_id,
        "entity_id": entity_id,
        "state": state,
        "attributes": attributes or {},
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


def test_no_llm_imports_in_home_assistant_adapter() -> None:
    """The home_assistant adapter module must not import any LLM client packages."""
    import butlers.chronicler.adapters.home_assistant as mod

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
                        f"LLM import detected in home_assistant adapter: {alias.name!r}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for prefix in forbidden_prefixes:
                    assert not node.module.startswith(prefix), (
                        f"LLM import detected in home_assistant adapter: {node.module!r}"
                    )


# ---------------------------------------------------------------------------
# Presence detection from state changes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_home_row_produces_one_presence_episode() -> None:
    """A single 'home' state row collapses into one presence episode."""
    row = _make_row(state="home", recorded_at=_NOW)
    adapter = HomeAssistantHistoryAdapter()

    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.home_assistant.upsert_episode", side_effect=_fake_upsert
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert result.episodes_closed == 1
    assert len(upserted) == 1

    ep = upserted[0]
    assert ep.source_name == SOURCE_NAME
    assert ep.episode_type == EPISODE_TYPE_PRESENCE
    assert ep.start_at == _NOW
    assert ep.end_at == _NOW
    assert ep.precision == Precision.EXACT
    assert ep.privacy == Privacy.SENSITIVE
    assert ep.payload["entity_id"] == _PERSON


@pytest.mark.asyncio
async def test_away_home_away_produces_one_episode_for_home_span() -> None:
    """away → home → away transition produces one presence_episode for the home span."""
    t0 = _NOW
    t1 = _NOW + timedelta(hours=2)
    t2 = _NOW + timedelta(hours=5)
    rows = [
        _make_row(state="away", recorded_at=t0, row_id=_UUID_1),
        _make_row(state="home", recorded_at=t1, row_id=_UUID_2),
        _make_row(state="away", recorded_at=t2, row_id=_UUID_3),
    ]
    adapter = HomeAssistantHistoryAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.home_assistant.upsert_episode", side_effect=_fake_upsert
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 1
    assert len(upserted) == 1
    ep = upserted[0]
    assert ep.start_at == t1
    assert ep.end_at == t1  # single home row before leaving


@pytest.mark.asyncio
async def test_home_away_home_produces_two_episodes() -> None:
    """Two separate 'home' spans produce two presence episodes."""
    t0 = _NOW
    t1 = _NOW + timedelta(hours=2)
    t2 = _NOW + timedelta(hours=4)
    t3 = _NOW + timedelta(hours=6)
    rows = [
        _make_row(state="home", recorded_at=t0, row_id=_UUID_1),
        _make_row(state="away", recorded_at=t1, row_id=_UUID_2),
        _make_row(state="home", recorded_at=t2, row_id=_UUID_3),
        _make_row(state="away", recorded_at=t3, row_id=_UUID_4),
    ]
    adapter = HomeAssistantHistoryAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.home_assistant.upsert_episode", side_effect=_fake_upsert
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 2
    assert upserted[0].start_at == t0
    assert upserted[1].start_at == t2


@pytest.mark.asyncio
async def test_non_person_entities_are_ignored() -> None:
    """Rows for non-person entities (e.g. light.*) produce no episodes."""
    rows = [
        _make_row(entity_id="light.kitchen", state="on", recorded_at=_NOW, row_id=_UUID_1),
        _make_row(entity_id="sensor.temperature", state="22.5", recorded_at=_NOW, row_id=_UUID_2),
    ]
    adapter = HomeAssistantHistoryAdapter()

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.home_assistant.upsert_episode") as mock_upsert:
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 0
    assert result.episodes_closed == 0
    mock_upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Missing evidence surface graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_evidence_table_returns_skipped_result() -> None:
    adapter = HomeAssistantHistoryAdapter()
    pool = _pool_table_missing()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.home_assistant.upsert_episode") as mock_upsert:
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.skipped is True
    assert result.skipped_reason is not None
    assert "not found" in result.skipped_reason
    assert result.rows_projected == 0
    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_undefined_table_exception_returns_skipped_result() -> None:
    """When the DB raises UndefinedTableError, adapter degrades gracefully."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedTableError(
            'relation "connectors.home_assistant_history" does not exist'
        )
    )
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = HomeAssistantHistoryAdapter()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.home_assistant.upsert_episode") as mock_upsert:
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
    """Watermark advances to the latest recorded_at across all rows (not just presence)."""
    t1 = _NOW
    t2 = _NOW + timedelta(hours=4)
    rows = [
        _make_row(state="home", recorded_at=t1, row_id=_UUID_1),
        _make_row(entity_id="light.kitchen", state="on", recorded_at=t2, row_id=_UUID_2),
    ]
    adapter = HomeAssistantHistoryAdapter()

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        return episode

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.home_assistant.upsert_episode", side_effect=_fake_upsert
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.watermark == t2


# ---------------------------------------------------------------------------
# Contracts registration
# ---------------------------------------------------------------------------


def test_home_assistant_adapter_exported_from_package() -> None:
    from butlers.chronicler.adapters import HomeAssistantHistoryAdapter as _Cls

    assert _Cls is HomeAssistantHistoryAdapter


# ---------------------------------------------------------------------------
# Episode field correctness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_episode_title_derived_from_entity_id() -> None:
    """Episode title is derived from the entity_id short name."""
    row = _make_row(entity_id="person.alice_smith", state="home", recorded_at=_NOW)
    adapter = HomeAssistantHistoryAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.home_assistant.upsert_episode", side_effect=_fake_upsert
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert "Alice Smith" in upserted[0].title
    assert "home" in upserted[0].title.lower()


@pytest.mark.asyncio
async def test_home_lane_taxonomy_path_source_name_and_episode_type() -> None:
    """Mock connector rows produce source_name='home_assistant.history'
    and episode_type='presence_episode', which the frontend SOURCE_CATEGORY_MAP
    and backend _CATEGORY_MAP both map to the 'home' lane category.

    Uses string literals (not constants) so a rename of the constants without
    updating the taxonomy contract fails loudly here.
    """
    row = _make_row(entity_id="person.alice", state="home", recorded_at=_NOW)
    adapter = HomeAssistantHistoryAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.home_assistant.upsert_episode", side_effect=_fake_upsert
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert len(upserted) == 1
    ep = upserted[0]

    assert ep.source_name == "home_assistant.history", (
        f"source_name mismatch: got {ep.source_name!r}; "
        "taxonomy expects 'home_assistant.history' for the Home lane"
    )
    assert ep.episode_type == "presence_episode", (
        f"episode_type mismatch: got {ep.episode_type!r}; "
        "taxonomy expects 'presence_episode' for the Home lane"
    )

    from butlers.chronicler.aggregations import category_for

    assert category_for(ep.source_name, ep.episode_type) == "home", (
        f"category_for({ep.source_name!r}, {ep.episode_type!r}) returned "
        f"{category_for(ep.source_name, ep.episode_type)!r}; expected 'home'"
    )


@pytest.mark.asyncio
async def test_projection_with_uuid_id_rows_does_not_set_watermark_id() -> None:
    """Regression for bu-usgm4: UUID id rows must never set watermark_id.

    home_assistant_history.id is UUID; projection_checkpoints.watermark_id is BIGINT.
    Binding UUID to BIGINT raises asyncpg DataError at checkpoint-write time.
    """
    t0 = _NOW
    t1 = _NOW + timedelta(hours=1)
    t2 = _NOW + timedelta(hours=3)

    rows = [
        _make_row(state="home", recorded_at=t0, row_id=_UUID_1),
        _make_row(state="home", recorded_at=t1, row_id=_UUID_2),
        _make_row(state="away", recorded_at=t2, row_id=_UUID_3),
    ]

    adapter = HomeAssistantHistoryAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.home_assistant.upsert_episode", side_effect=_fake_upsert
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.error is None
    assert not result.skipped
    assert result.episodes_closed == 1
    assert result.watermark == t2
    assert result.watermark_id is None, (
        f"watermark_id must be None for UUID-keyed source, got {result.watermark_id!r}. "
        "This would cause asyncpg DataError when binding UUID to BIGINT checkpoint column."
    )
