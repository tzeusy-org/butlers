"""Tests for the Home Assistant history Chronicler projection adapter.

Covers:
- Empty input → empty episodes.
- Presence detection from state changes (person.user: away → home → away).
- Idempotent re-poll (same source_ref on replay).
- Multi-room / multi-entity transitions (two person entities simultaneously).
- Missing evidence surface graceful degradation (fetchval=False).
- UndefinedTableError graceful degradation.
- Watermark advances across all rows (not just presence rows).
- Watermark preserved when no rows returned.
- since / since_id filter passed through to query.
- No-LLM AST scan.
- Contracts registration: home_assistant.history SUPPORTED.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.chronicler.adapters.home_assistant import (
    DEFAULT_BATCH_LIMIT,
    EPISODE_TYPE_PRESENCE,
    SOURCE_NAME,
    HomeAssistantHistoryAdapter,
)
from butlers.chronicler.models import Episode, Precision, Privacy

_NOW = datetime(2026, 4, 25, 8, 0, 0, tzinfo=UTC)
_PERSON = "person.alice"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    entity_id: str = _PERSON,
    state: str = "home",
    recorded_at: datetime = _NOW,
    row_id: int = 1,
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
    """The home_assistant adapter module must not import any LLM client packages.

    Parses the source AST rather than inspecting the live module so that
    transitive imports through other modules don't cause false negatives.
    """
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
# Module constants
# ---------------------------------------------------------------------------


def test_source_name() -> None:
    assert SOURCE_NAME == "home_assistant.history"


def test_episode_type() -> None:
    assert EPISODE_TYPE_PRESENCE == "presence_episode"


def test_default_batch_limit() -> None:
    assert DEFAULT_BATCH_LIMIT == 1000


# ---------------------------------------------------------------------------
# Empty input → empty episodes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_rows_produce_no_episodes() -> None:
    """When the evidence table exists but has no rows, no episodes are upserted."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = HomeAssistantHistoryAdapter()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.home_assistant.upsert_episode") as mock_upsert:
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 0
    assert result.episodes_closed == 0
    mock_upsert.assert_not_called()


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
        _make_row(state="away", recorded_at=t0, row_id=1),
        _make_row(state="home", recorded_at=t1, row_id=2),
        _make_row(state="away", recorded_at=t2, row_id=3),
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
async def test_multiple_home_rows_collapse_into_one_episode() -> None:
    """Consecutive 'home' rows form a single episode spanning first to last."""
    t0 = _NOW
    t1 = _NOW + timedelta(hours=1)
    t2 = _NOW + timedelta(hours=3)
    t3 = _NOW + timedelta(hours=4)
    rows = [
        _make_row(state="home", recorded_at=t0, row_id=1),
        _make_row(state="home", recorded_at=t1, row_id=2),
        _make_row(state="home", recorded_at=t2, row_id=3),
        _make_row(state="away", recorded_at=t3, row_id=4),
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
    ep = upserted[0]
    assert ep.start_at == t0
    assert ep.end_at == t2


@pytest.mark.asyncio
async def test_home_away_home_produces_two_episodes() -> None:
    """Two separate 'home' spans produce two presence episodes."""
    t0 = _NOW
    t1 = _NOW + timedelta(hours=2)
    t2 = _NOW + timedelta(hours=4)
    t3 = _NOW + timedelta(hours=6)
    rows = [
        _make_row(state="home", recorded_at=t0, row_id=1),
        _make_row(state="away", recorded_at=t1, row_id=2),
        _make_row(state="home", recorded_at=t2, row_id=3),
        _make_row(state="away", recorded_at=t3, row_id=4),
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
    assert upserted[0].end_at == t0
    assert upserted[1].start_at == t2
    assert upserted[1].end_at == t2


@pytest.mark.asyncio
async def test_open_home_span_at_end_of_batch_is_emitted() -> None:
    """If the entity is still home at the end of the batch, the open span is emitted."""
    t0 = _NOW
    t1 = _NOW + timedelta(hours=2)
    rows = [
        _make_row(state="away", recorded_at=t0, row_id=1),
        _make_row(state="home", recorded_at=t1, row_id=2),
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
    assert upserted[0].start_at == t1
    assert upserted[0].end_at == t1


# ---------------------------------------------------------------------------
# Multi-entity (multi-room) transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_person_entities_produce_independent_episodes() -> None:
    """Each person entity tracks its own presence independently."""
    t0 = _NOW
    t1 = _NOW + timedelta(hours=1)
    t2 = _NOW + timedelta(hours=3)
    rows = [
        _make_row(entity_id="person.alice", state="home", recorded_at=t0, row_id=1),
        _make_row(entity_id="person.bob", state="home", recorded_at=t1, row_id=2),
        _make_row(entity_id="person.alice", state="away", recorded_at=t2, row_id=3),
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

    # alice closes her episode; bob still home → also emitted.
    assert result.episodes_closed == 2
    entity_ids = {ep.payload["entity_id"] for ep in upserted}
    assert "person.alice" in entity_ids
    assert "person.bob" in entity_ids


@pytest.mark.asyncio
async def test_non_person_entities_are_ignored() -> None:
    """Rows for non-person entities (e.g. light.*) produce no episodes."""
    rows = [
        _make_row(entity_id="light.kitchen", state="on", recorded_at=_NOW, row_id=1),
        _make_row(entity_id="sensor.temperature", state="22.5", recorded_at=_NOW, row_id=2),
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
# Idempotent re-poll (stable source_ref)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_row_produces_same_source_ref_on_replay() -> None:
    """The presence episode source_ref is keyed to (entity, start_tst) — stable on replay."""
    row = _make_row(state="home", recorded_at=_NOW)
    adapter = HomeAssistantHistoryAdapter()
    refs: list[str] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        refs.append(episode.source_ref)
        return episode

    pool1 = _pool_returning(row)
    cp1 = _chronicler_pool()
    pool2 = _pool_returning(row)
    cp2 = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.home_assistant.upsert_episode", side_effect=_fake_upsert
    ):
        await adapter.project(pool1, chronicler_pool=cp1, since=None)
        await adapter.project(pool2, chronicler_pool=cp2, since=None)

    assert refs[0] == refs[1]
    assert "person.alice" in refs[0]


@pytest.mark.asyncio
async def test_source_ref_format() -> None:
    """source_ref should be: connectors.home_assistant_history:presence:{entity}:{start_tst}"""
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

    start_tst = int(_NOW.timestamp())
    expected = f"connectors.home_assistant_history:presence:person.alice:{start_tst}"
    assert upserted[0].source_ref == expected


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
# Watermark advance / resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watermark_advances_to_max_recorded_at() -> None:
    """Watermark advances to the latest recorded_at across all rows (not just presence)."""
    t1 = _NOW
    t2 = _NOW + timedelta(hours=4)
    rows = [
        _make_row(state="home", recorded_at=t1, row_id=1),
        _make_row(entity_id="light.kitchen", state="on", recorded_at=t2, row_id=2),
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


@pytest.mark.asyncio
async def test_watermark_preserved_when_no_rows() -> None:
    """When no rows are returned, watermark stays at since."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = HomeAssistantHistoryAdapter()
    cp = _chronicler_pool()
    prior_watermark = _NOW - timedelta(days=1)

    with patch("butlers.chronicler.adapters.home_assistant.upsert_episode"):
        result = await adapter.project(pool, chronicler_pool=cp, since=prior_watermark)

    assert result.watermark == prior_watermark
    assert result.rows_projected == 0


@pytest.mark.asyncio
async def test_watermark_id_set_in_result() -> None:
    """watermark_id tracks the id of the latest-recorded_at row."""
    row = {**_make_row(state="home", recorded_at=_NOW), "id": 42}
    adapter = HomeAssistantHistoryAdapter()

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.home_assistant.upsert_episode", side_effect=_fake_upsert
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.watermark == _NOW
    assert result.watermark_id == 42


@pytest.mark.asyncio
async def test_watermark_id_preserved_when_no_rows() -> None:
    """When no rows are returned, watermark_id stays at since_id."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = HomeAssistantHistoryAdapter()
    cp = _chronicler_pool()
    prior_watermark = _NOW - timedelta(days=7)
    prior_watermark_id = 5

    with patch("butlers.chronicler.adapters.home_assistant.upsert_episode"):
        result = await adapter.project(
            pool,
            chronicler_pool=cp,
            since=prior_watermark,
            since_id=prior_watermark_id,
        )

    assert result.watermark == prior_watermark
    assert result.watermark_id == prior_watermark_id


# ---------------------------------------------------------------------------
# Since / since_id filter passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_since_filter_passed_to_query() -> None:
    """When since is given, the fetch query filters on recorded_at > $1."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = HomeAssistantHistoryAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=2)

    with patch("butlers.chronicler.adapters.home_assistant.upsert_episode"):
        await adapter.project(pool, chronicler_pool=cp, since=since)

    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "recorded_at > $1" in query
    assert call_args.args[1] == since


@pytest.mark.asyncio
async def test_tuple_filter_used_when_since_and_since_id_both_given() -> None:
    """When both since and since_id are given, uses (recorded_at, id) > ($1, $2)."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = HomeAssistantHistoryAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=1)
    since_id = 17

    with patch("butlers.chronicler.adapters.home_assistant.upsert_episode"):
        await adapter.project(pool, chronicler_pool=cp, since=since, since_id=since_id)

    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "(recorded_at, id) > ($1, $2)" in query
    assert call_args.args[1] == since
    assert call_args.args[2] == since_id


@pytest.mark.asyncio
async def test_single_column_fallback_when_since_id_is_none() -> None:
    """When since is given but since_id is None, uses legacy WHERE recorded_at > $1 form."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = HomeAssistantHistoryAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=1)

    with patch("butlers.chronicler.adapters.home_assistant.upsert_episode"):
        await adapter.project(pool, chronicler_pool=cp, since=since, since_id=None)

    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "recorded_at > $1" in query
    assert "(recorded_at, id) > ($1, $2)" not in query


@pytest.mark.asyncio
async def test_order_by_includes_id_tiebreaker() -> None:
    """ORDER BY clause includes id ASC as a tie-breaker for deterministic ordering."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = HomeAssistantHistoryAdapter()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.home_assistant.upsert_episode"):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    query: str = conn.fetch.call_args.args[0]
    assert "ORDER BY recorded_at ASC, id ASC" in query


# ---------------------------------------------------------------------------
# Contracts registration
# ---------------------------------------------------------------------------


def test_home_assistant_adapter_exported_from_package() -> None:
    from butlers.chronicler.adapters import HomeAssistantHistoryAdapter as _Cls

    assert _Cls is HomeAssistantHistoryAdapter


def test_home_assistant_history_supported_in_contracts() -> None:
    from butlers.chronicler.contracts import find_source
    from butlers.chronicler.models import Compatibility

    source = find_source("home_assistant.history")
    assert source is not None
    assert source.chronicler_compatibility == Compatibility.SUPPORTED
    assert source.read_surface == "connectors.home_assistant_history"


def test_home_assistant_history_in_supported_names() -> None:
    from butlers.chronicler.contracts import supported_source_names

    assert "home_assistant.history" in supported_source_names()


def test_home_assistant_history_not_in_planned_names() -> None:
    from butlers.chronicler.contracts import planned_source_names

    assert "home_assistant.history" not in planned_source_names()


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

    # "alice_smith" → "Alice Smith at home"
    assert "Alice Smith" in upserted[0].title
    assert "home" in upserted[0].title.lower()


# ---------------------------------------------------------------------------
# Home lane taxonomy path (bu-ykm2a)
#
# Explicit assertion that mock connector rows produce source_name and
# episode_type values matching the taxonomy contract for the "home" lane.
# Uses string literals (not constants) so that a rename of the constants
# without updating the taxonomy contract fails loudly here.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_home_lane_taxonomy_path_source_name_and_episode_type() -> None:
    """Mock connector rows produce source_name='home_assistant.history'
    and episode_type='presence_episode', which is the (source_name, episode_type)
    pair the frontend SOURCE_CATEGORY_MAP and backend _CATEGORY_MAP both map to
    the 'home' lane category.

    This test pins the full adapter → taxonomy contract so that any rename of
    the emitted pair immediately fails CI, forcing a matching taxonomy update.
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

    assert len(upserted) == 1, "Expected exactly one presence episode from one home row"
    ep = upserted[0]

    # Use string literals — not SOURCE_NAME / EPISODE_TYPE_PRESENCE constants —
    # so that renaming the constants without updating the taxonomy fails here.
    assert ep.source_name == "home_assistant.history", (
        f"source_name mismatch: got {ep.source_name!r}; "
        "taxonomy expects 'home_assistant.history' for the Home lane"
    )
    assert ep.episode_type == "presence_episode", (
        f"episode_type mismatch: got {ep.episode_type!r}; "
        "taxonomy expects 'presence_episode' for the Home lane"
    )

    # Confirm the (source_name, episode_type) pair maps to 'home' via the backend
    # aggregation function — this is the category the frontend Gantt lane uses.
    from butlers.chronicler.aggregations import category_for

    assert category_for(ep.source_name, ep.episode_type) == "home", (
        f"category_for({ep.source_name!r}, {ep.episode_type!r}) returned "
        f"{category_for(ep.source_name, ep.episode_type)!r}; expected 'home'"
    )
