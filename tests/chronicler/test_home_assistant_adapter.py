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
- Entity resolution via connectors.home_assistant_persons (bu-v7hen).
- entity_id = NULL graceful degradation when no mapping exists.
- episode_entities row written for each presence episode.
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

_ENTITY_ID_ALICE = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

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
    """Build a mock asyncpg pool that returns the given row dicts for fetch().

    fetchval is routed by the SQL string:
      - "home_assistant_persons" in SQL → False (no mapping table — graceful degradation)
      - anything else → True  (evidence table exists)

    Routing by SQL content rather than call order keeps the mock stable if the
    adapter gains additional fetchval checks in future.
    """

    async def _fetchval(*args: object, **kwargs: object) -> bool:
        sql = args[0] if args else ""
        if "home_assistant_persons" in sql:
            return False  # mapping table absent → all episodes degrade to entity_id=NULL
        return True  # evidence table exists

    conn = AsyncMock()
    conn.fetchval = _fetchval
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


# ---------------------------------------------------------------------------
# Entity resolution via connectors.home_assistant_persons (bu-v7hen)
# ---------------------------------------------------------------------------


def _pool_with_person_mapping(
    *rows: dict,
    mapping: dict[str, uuid.UUID] | None = None,
) -> AsyncMock:
    """Build a mock pool returning HA history rows AND an optional person-entity mapping.

    The pool serves two distinct query shapes:
    - table-existence fetchval → True
    - fetch for HA history rows → the given rows
    - fetch for home_assistant_persons mapping → rows derived from ``mapping``
    """
    mapping = mapping or {}

    def _make_mapping_row(ha_id: str, entity_id: uuid.UUID) -> MagicMock:
        d = {"ha_entity_id": ha_id, "entity_id": entity_id}
        return MagicMock(**d, **{"__getitem__": lambda s, k, _d=d: _d[k]})

    mapping_rows = [_make_mapping_row(k, v) for k, v in mapping.items()]
    history_rows = [_make_mock_row(r) for r in rows]

    fetch_calls: list[int] = [0]

    async def _fetch(*args: object, **kwargs: object) -> list:
        fetch_calls[0] += 1
        if fetch_calls[0] == 1:
            return history_rows
        # Second fetch call is the mapping query.
        return mapping_rows

    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = _fetch
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _capture_entity_writes() -> tuple[list[Episode], list[tuple], object, object]:
    """Build fake upsert_episode / upsert_owner_episode_entity that record writes.

    Returns (upserted_episodes, owner_entity_calls, fake_upsert, fake_upsert_owner).
    The resolved person entity lives in the episode_entities join table now
    (the derived episodes.entity_id column was dropped in bu-cfsgy), so the
    owner_id captured here is the per-person resolution under test.
    """
    upserted: list[Episode] = []
    owner_calls: list[tuple] = []
    counter = [0]

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        counter[0] += 1
        stored = Episode(
            source_name=episode.source_name,
            source_ref=episode.source_ref,
            episode_type=episode.episode_type,
            start_at=episode.start_at,
            end_at=episode.end_at,
            precision=episode.precision,
            title=episode.title,
            payload=episode.payload,
            privacy=episode.privacy,
            id=uuid.UUID(int=counter[0]),
        )
        upserted.append(stored)
        return stored

    async def _fake_upsert_owner(conn: object, episode_id: object, *, owner_id: object) -> None:
        owner_calls.append((episode_id, owner_id))

    return upserted, owner_calls, _fake_upsert, _fake_upsert_owner


@pytest.mark.asyncio
async def test_entity_id_resolved_per_entity_multi_person() -> None:
    """Multi-person household: each person's episode_entities owner row gets their entity."""
    _ENTITY_ID_BOB = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    t0 = _NOW
    t1 = _NOW + timedelta(hours=1)
    rows = [
        _make_row(entity_id="person.alice", state="home", recorded_at=t0, row_id=_UUID_1),
        _make_row(entity_id="person.bob", state="home", recorded_at=t1, row_id=_UUID_2),
    ]
    adapter = HomeAssistantHistoryAdapter()
    upserted, owner_calls, fake_upsert, fake_upsert_owner = _capture_entity_writes()

    pool = _pool_with_person_mapping(
        *rows,
        mapping={"person.alice": _ENTITY_ID_ALICE, "person.bob": _ENTITY_ID_BOB},
    )
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.home_assistant.upsert_episode", side_effect=fake_upsert),
        patch(
            "butlers.chronicler.adapters.home_assistant.upsert_owner_episode_entity",
            side_effect=fake_upsert_owner,
        ),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 2
    # Map each upserted episode's HA person (payload) to the owner_id written for it.
    owner_by_episode = dict(owner_calls)
    owner_by_person = {ep.payload["entity_id"]: owner_by_episode[ep.id] for ep in upserted}
    assert owner_by_person["person.alice"] == _ENTITY_ID_ALICE
    assert owner_by_person["person.bob"] == _ENTITY_ID_BOB


@pytest.mark.asyncio
async def test_entity_id_mixed_mapped_and_unmapped() -> None:
    """Mixed household: mapped person gets an entity owner_id, unmapped gets None."""
    t0 = _NOW
    t1 = _NOW + timedelta(hours=1)
    rows = [
        _make_row(entity_id="person.alice", state="home", recorded_at=t0, row_id=_UUID_1),
        _make_row(entity_id="person.guest", state="home", recorded_at=t1, row_id=_UUID_2),
    ]
    adapter = HomeAssistantHistoryAdapter()
    upserted, owner_calls, fake_upsert, fake_upsert_owner = _capture_entity_writes()

    # Only alice is mapped; guest is not.
    pool = _pool_with_person_mapping(*rows, mapping={"person.alice": _ENTITY_ID_ALICE})
    cp = _chronicler_pool()

    with (
        patch("butlers.chronicler.adapters.home_assistant.upsert_episode", side_effect=fake_upsert),
        patch(
            "butlers.chronicler.adapters.home_assistant.upsert_owner_episode_entity",
            side_effect=fake_upsert_owner,
        ),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 2
    owner_by_episode = dict(owner_calls)
    owner_by_person = {ep.payload["entity_id"]: owner_by_episode[ep.id] for ep in upserted}
    assert owner_by_person["person.alice"] == _ENTITY_ID_ALICE
    assert owner_by_person["person.guest"] is None


@pytest.mark.asyncio
async def test_episode_entities_row_written_when_entity_id_resolved() -> None:
    """When entity_id is resolved, upsert_owner_episode_entity is called for the episode."""
    row = _make_row(entity_id="person.alice", state="home", recorded_at=_NOW)
    adapter = HomeAssistantHistoryAdapter()

    upserted_episodes: list[Episode] = []
    upserted_episode_entity_calls: list[tuple] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        episode_with_id = Episode(
            source_name=episode.source_name,
            source_ref=episode.source_ref,
            episode_type=episode.episode_type,
            start_at=episode.start_at,
            end_at=episode.end_at,
            precision=episode.precision,
            title=episode.title,
            payload=episode.payload,
            privacy=episode.privacy,
            id=_UUID_3,
        )
        upserted_episodes.append(episode_with_id)
        return episode_with_id

    async def _fake_upsert_owner_entity(
        conn: object, episode_id: object, *, owner_id: object
    ) -> None:
        upserted_episode_entity_calls.append((episode_id, owner_id))

    pool = _pool_with_person_mapping(row, mapping={"person.alice": _ENTITY_ID_ALICE})
    cp = _chronicler_pool()

    with (
        patch(
            "butlers.chronicler.adapters.home_assistant.upsert_episode",
            side_effect=_fake_upsert,
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant.upsert_owner_episode_entity",
            side_effect=_fake_upsert_owner_entity,
        ),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_closed == 1
    assert len(upserted_episode_entity_calls) == 1
    episode_id, owner_id = upserted_episode_entity_calls[0]
    assert episode_id == _UUID_3
    assert owner_id == _ENTITY_ID_ALICE
