"""Tests for the Steam play-history Chronicler projection adapter.

Covers:
- Per-episode projection correctness (one play_episode per row).
- Episode start_at / end_at derivation from date + playtime_minutes.
- Stable source_ref on replay (keyed to steam_id:app_id:date).
- Missing evidence surface graceful degradation (fetchval=False).
- UndefinedTableError graceful degradation.
- Watermark advances to max recorded_at across batch.
- Watermark preserved when no rows returned.
- since filter passed through to query (recorded_at > $1).
- UUID-backed source id does not populate integer checkpoint watermark_id.
- No-LLM AST scan.
- Contracts registration: steam.play_history SUPPORTED.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.chronicler.adapters.steam import (
    DEFAULT_BATCH_LIMIT,
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
    """The steam adapter module must not import any LLM client packages.

    Parses the source AST rather than inspecting the live module so that
    transitive imports through other modules don't cause false negatives.
    """
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
# Module constants
# ---------------------------------------------------------------------------


def test_source_name() -> None:
    assert SOURCE_NAME == "steam.play_history"


def test_episode_type() -> None:
    assert EPISODE_TYPE_PLAY == "play_episode"


def test_default_batch_limit() -> None:
    assert DEFAULT_BATCH_LIMIT == 500


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
    row = _make_row(
        play_date=_DATE,
        playtime_minutes=90,
        app_name="Counter-Strike 2",
    )
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
async def test_episode_clamps_to_day_when_playtime_exceeds_anchor_offset() -> None:
    """Playtime longer than the time elapsed before anchor_end clamps start_at to midnight."""
    play_date = date(2026, 4, 25)
    playtime = 120  # minutes
    recorded = datetime(2026, 4, 25, 1, 0, 0, tzinfo=UTC)  # 60 min into day
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
    start_of_day = datetime(2026, 4, 25, 0, 0, 0, tzinfo=UTC)
    assert ep.start_at == start_of_day
    # End must equal start + playtime (post-clamp), guaranteed by adapter logic.
    assert ep.end_at == start_of_day + timedelta(minutes=playtime)


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
    assert result.warnings == [
        f"connectors.steam_play_history:{_STEAM_ID}:{_APP_ID}:{_DATE} "
        "has negative playtime_minutes"
    ]
    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_episode_title_falls_back_when_no_app_name() -> None:
    row = _make_row(app_id=440, app_name=None)
    adapter = SteamPlayAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.steam.upsert_episode", side_effect=_fake_upsert):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert "440" in upserted[0].title


@pytest.mark.asyncio
async def test_episode_payload_includes_key_fields() -> None:
    row = _make_row(
        steam_id=_STEAM_ID,
        app_id=_APP_ID,
        app_name=_APP_NAME,
        play_date=_DATE,
        playtime_minutes=120,
    )
    adapter = SteamPlayAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.steam.upsert_episode", side_effect=_fake_upsert):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    payload = upserted[0].payload
    assert payload["steam_id"] == _STEAM_ID
    assert payload["app_id"] == _APP_ID
    assert payload["app_name"] == _APP_NAME
    assert payload["playtime_minutes"] == 120
    assert payload["date"] == str(_DATE)


# ---------------------------------------------------------------------------
# Stable source_ref / idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_ref_keyed_to_steam_id_app_id_date() -> None:
    row = _make_row(steam_id=_STEAM_ID, app_id=_APP_ID, play_date=_DATE)
    adapter = SteamPlayAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.steam.upsert_episode", side_effect=_fake_upsert):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    expected_ref = f"connectors.steam_play_history:{_STEAM_ID}:{_APP_ID}:{_DATE}"
    assert upserted[0].source_ref == expected_ref


@pytest.mark.asyncio
async def test_same_row_produces_same_source_ref_on_replay() -> None:
    row = _make_row()
    adapter = SteamPlayAdapter()
    refs: list[str] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        refs.append(episode.source_ref)
        return episode

    pool1 = _pool_returning(row)
    cp1 = _chronicler_pool()
    pool2 = _pool_returning(row)
    cp2 = _chronicler_pool()

    with patch("butlers.chronicler.adapters.steam.upsert_episode", side_effect=_fake_upsert):
        await adapter.project(pool1, chronicler_pool=cp1, since=None)
        await adapter.project(pool2, chronicler_pool=cp2, since=None)

    assert refs[0] == refs[1]


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
    """When the DB raises UndefinedTableError (asyncpg.PostgresError subclass),
    the adapter must not crash — it returns skipped=True without advancing the
    watermark or upserting any episode.
    """
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
# Watermark advance / resume
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


@pytest.mark.asyncio
async def test_watermark_preserved_when_no_rows() -> None:
    """When the evidence table exists but no new rows, watermark stays at since."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)  # table exists
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = SteamPlayAdapter()
    cp = _chronicler_pool()
    prior_watermark = _NOW - timedelta(days=1)

    with patch("butlers.chronicler.adapters.steam.upsert_episode"):
        result = await adapter.project(pool, chronicler_pool=cp, since=prior_watermark)

    assert result.watermark == prior_watermark
    assert result.rows_projected == 0


@pytest.mark.asyncio
async def test_since_filter_passed_to_query() -> None:
    """When ``since`` is given, the fetch query filters on recorded_at > $1."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = SteamPlayAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=2)

    with patch("butlers.chronicler.adapters.steam.upsert_episode"):
        await adapter.project(pool, chronicler_pool=cp, since=since)

    assert conn.fetch.await_count == 1
    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "recorded_at > $1" in query
    assert call_args.args[1] == since


# ---------------------------------------------------------------------------
# Deterministic ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_order_by_uses_natural_tiebreaker_without_since() -> None:
    """ORDER BY clause must avoid UUID id while staying deterministic.

    Same recorded_at values in the evidence table have non-deterministic ordering
    without a secondary sort key, which can cause rows to be missed or duplicated
    at batch boundaries when paginating with a watermark.
    """
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = SteamPlayAdapter()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.steam.upsert_episode"):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    query: str = conn.fetch.call_args.args[0]
    assert "ORDER BY recorded_at ASC, steam_id ASC, app_id ASC, date ASC" in query


@pytest.mark.asyncio
async def test_order_by_uses_natural_tiebreaker_with_since() -> None:
    """ORDER BY clause must avoid UUID id while staying deterministic."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = SteamPlayAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=1)

    with patch("butlers.chronicler.adapters.steam.upsert_episode"):
        await adapter.project(pool, chronicler_pool=cp, since=since)

    query: str = conn.fetch.call_args.args[0]
    assert "ORDER BY recorded_at ASC, steam_id ASC, app_id ASC, date ASC" in query


# ---------------------------------------------------------------------------
# Contracts registration
# ---------------------------------------------------------------------------


def test_steam_adapter_exported_from_package() -> None:
    from butlers.chronicler.adapters import SteamPlayAdapter as _Cls

    assert _Cls is SteamPlayAdapter


def test_steam_play_history_supported_in_contracts() -> None:
    from butlers.chronicler.contracts import find_source
    from butlers.chronicler.models import Compatibility

    source = find_source("steam.play_history")
    assert source is not None
    assert source.chronicler_compatibility == Compatibility.SUPPORTED
    assert source.read_surface == "connectors.steam_play_history"


def test_steam_play_history_in_supported_names() -> None:
    from butlers.chronicler.contracts import supported_source_names

    assert "steam.play_history" in supported_source_names()


def test_steam_play_history_not_in_planned_names() -> None:
    from butlers.chronicler.contracts import planned_source_names

    assert "steam.play_history" not in planned_source_names()


# ---------------------------------------------------------------------------
# UUID-backed watermark semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_since_id_is_ignored_uuid_pk() -> None:
    """The Steam evidence table uses UUID primary keys, so ``since_id`` must
    not select the tuple ``(recorded_at, id)`` path backed by BIGINT checkpoints.
    """
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = SteamPlayAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=1)
    since_id = 99

    with patch("butlers.chronicler.adapters.steam.upsert_episode"):
        await adapter.project(pool, chronicler_pool=cp, since=since, since_id=since_id)

    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "(recorded_at, id) > ($1, $2)" not in query
    assert "recorded_at > $1" in query
    assert call_args.args[1] == since


@pytest.mark.asyncio
async def test_watermark_id_is_always_none_for_uuid_pk() -> None:
    """``watermark_id`` stays None because projection_checkpoints stores BIGINT ids."""
    row = _make_row(recorded_at=_NOW)

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()
    adapter = SteamPlayAdapter()

    with patch("butlers.chronicler.adapters.steam.upsert_episode", side_effect=_fake_upsert):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.watermark == _NOW
    assert result.watermark_id is None


@pytest.mark.asyncio
async def test_run_persists_checkpoint_without_uuid_watermark_id() -> None:
    """A successful run must not bind the UUID source id into BIGINT watermark_id."""
    row = _make_row(recorded_at=_NOW)
    pool = _pool_returning(row)
    cp = _chronicler_pool()
    adapter = SteamPlayAdapter()

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        return episode

    with (
        patch("butlers.chronicler.adapters.steam.upsert_episode", side_effect=_fake_upsert),
        patch("butlers.chronicler.adapters.base.get_checkpoint", AsyncMock(return_value=None)),
        patch("butlers.chronicler.adapters.base.mark_source_active", AsyncMock()),
        patch("butlers.chronicler.adapters.base.upsert_checkpoint", AsyncMock()) as checkpoint,
    ):
        result = await adapter.run(pool=pool, chronicler_pool=cp)

    assert result.error is None
    checkpoint.assert_awaited_once()
    assert checkpoint.await_args.kwargs["watermark"] == _NOW
    assert checkpoint.await_args.kwargs["watermark_id"] is None


@pytest.mark.asyncio
async def test_watermark_id_not_populated_for_rows_sharing_recorded_at() -> None:
    """Rows sharing a timestamp must not push UUID ids into BIGINT checkpoints."""
    t = _NOW
    rows = [
        {**_make_row(recorded_at=t, play_date=date(2026, 4, 23)), "id": "uuid-1"},
        {**_make_row(recorded_at=t, play_date=date(2026, 4, 24)), "id": "uuid-2"},
        {**_make_row(recorded_at=t, play_date=date(2026, 4, 25)), "id": "uuid-3"},
    ]

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        return episode

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()
    adapter = SteamPlayAdapter()

    with patch("butlers.chronicler.adapters.steam.upsert_episode", side_effect=_fake_upsert):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.watermark == t
    assert result.watermark_id is None


@pytest.mark.asyncio
async def test_initial_query_uses_deterministic_order_without_uuid_watermark_id() -> None:
    """The initial batch keeps deterministic ordering without selecting UUID ids."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = SteamPlayAdapter()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.steam.upsert_episode"):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "SELECT steam_id, steam_account_id" in query
    assert "SELECT id," not in query
    assert "ORDER BY recorded_at ASC, steam_id ASC, app_id ASC, date ASC" in query


@pytest.mark.asyncio
async def test_watermark_id_cleared_when_no_rows() -> None:
    """Even when passed, ``since_id`` is ignored for the UUID-backed source."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = SteamPlayAdapter()
    cp = _chronicler_pool()
    prior_watermark = _NOW - timedelta(days=7)
    prior_watermark_id = 3

    with patch("butlers.chronicler.adapters.steam.upsert_episode"):
        result = await adapter.project(
            pool,
            chronicler_pool=cp,
            since=prior_watermark,
            since_id=prior_watermark_id,
        )

    assert result.watermark == prior_watermark
    assert result.watermark_id is None
