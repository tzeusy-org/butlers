"""Tests for the Google Health sleep-episode Chronicler projection adapter.

Covers:
- Empty evidence → empty episodes.
- Multi-stage sleep (light/deep/REM) → single sleep_episode row covering
  full session window.
- Same-day re-poll idempotency (no duplicate episodes — stable source_ref).
- Sleep epochs spanning midnight UTC handled correctly.
- Boundary case: same-ts rows (created_at) handled with correct watermark.
- Missing evidence surface (health.facts absent) → graceful degradation.
- No-LLM invariant (AST scan of adapter source).
- Contracts registration: google_health.measurements SUPPORTED.
- Tuple-watermark: since_id is ignored (UUID pk); watermark is single-column.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.chronicler.adapters.google_health import (
    DEFAULT_BATCH_LIMIT,
    EPISODE_TYPE_SLEEP,
    SOURCE_NAME,
    GoogleHealthSleepAdapter,
    _derive_end_at,
)
from butlers.chronicler.models import Episode, Precision, Privacy

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 25, 22, 0, 0, tzinfo=UTC)  # 22:00 UTC — spans midnight
_SESSION_START = _NOW
_SESSION_END = _NOW + timedelta(hours=7, minutes=32)
_SESSION_ID = "abc123session"
_DURATION_MS = 7 * 3_600_000 + 32 * 60_000  # 7h 32m

_STAGES_MULTI = {
    "deep": 95,
    "light": 220,
    "rem": 137,
    "wake": 40,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    *,
    row_id: str = "fact-uuid-001",
    idempotency_key: str = f"google_health:sleep:{_SESSION_ID}:session",
    valid_at: datetime = _SESSION_START,
    created_at: datetime = _NOW,
    metadata: dict | None = None,
    content: str = "Slept 7h 32m (91% efficiency)",
) -> dict:
    if metadata is None:
        metadata = {
            "session_id": _SESSION_ID,
            "end_time": _SESSION_END.isoformat(),
            "duration_ms": _DURATION_MS,
            "efficiency": 91,
            "minutes_asleep": 452,
            "minutes_awake": 40,
            "stages": _STAGES_MULTI,
        }
    return {
        "id": row_id,
        "subject": "owner",
        "predicate": "sleep_session",
        "content": content,
        "metadata": metadata,
        "valid_at": valid_at,
        "created_at": created_at,
        "idempotency_key": idempotency_key,
    }


def _make_mock_row(r: dict) -> MagicMock:
    """Build a MagicMock supporting dict-style access."""
    return MagicMock(**r, **{"__getitem__": lambda s, k, _r=r: _r[k]})


class _AsyncCtx:
    """Async context manager that yields ``obj``."""

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


def _pool_table_exists_no_rows() -> AsyncMock:
    """Build a pool where the table exists but fetch returns no rows."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _chronicler_pool() -> AsyncMock:
    """Build a minimal mock chronicler pool for upsert_episode calls."""
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_NullCtx())
    conn.fetchrow = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


# ---------------------------------------------------------------------------
# No-LLM AST scan
# ---------------------------------------------------------------------------


def test_no_llm_imports_in_google_health_adapter() -> None:
    """The google_health adapter module MUST NOT import any LLM client packages.

    Parses the source AST rather than inspecting the live module so
    transitive imports through other modules don't cause false negatives.
    """
    import butlers.chronicler.adapters.google_health as mod

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
                        f"LLM import detected in google_health adapter: {alias.name!r}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for prefix in forbidden_prefixes:
                    assert not node.module.startswith(prefix), (
                        f"LLM import detected in google_health adapter: {node.module!r}"
                    )


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


def test_source_name() -> None:
    assert SOURCE_NAME == "google_health.measurements"


def test_episode_type() -> None:
    assert EPISODE_TYPE_SLEEP == "sleep_episode"


def test_default_batch_limit() -> None:
    assert DEFAULT_BATCH_LIMIT == 500


# ---------------------------------------------------------------------------
# Empty evidence → empty episodes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_evidence_returns_zero_rows() -> None:
    """When health.facts exists but has no sleep_session rows, nothing is projected."""
    adapter = GoogleHealthSleepAdapter()
    pool = _pool_table_exists_no_rows()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.google_health.upsert_episode") as mock_upsert:
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 0
    assert result.episodes_closed == 0
    assert result.skipped is False
    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_empty_evidence_preserves_watermark() -> None:
    """When no new rows are returned, the watermark stays at ``since``."""
    pool = _pool_table_exists_no_rows()
    cp = _chronicler_pool()
    adapter = GoogleHealthSleepAdapter()
    prior = _NOW - timedelta(days=1)

    with patch("butlers.chronicler.adapters.google_health.upsert_episode"):
        result = await adapter.project(pool, chronicler_pool=cp, since=prior)

    assert result.watermark == prior
    assert result.rows_projected == 0


# ---------------------------------------------------------------------------
# Multi-stage sleep → single sleep_episode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_stage_sleep_produces_single_episode() -> None:
    """A sleep_session fact with light/deep/REM stages → one sleep_episode."""
    row = _make_row()
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert result.episodes_closed == 1
    assert len(upserted) == 1


@pytest.mark.asyncio
async def test_sleep_episode_covers_full_session_window() -> None:
    """start_at = valid_at; end_at = metadata.end_time."""
    row = _make_row()
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    ep = upserted[0]
    assert ep.start_at == _SESSION_START
    assert ep.end_at == _SESSION_END


@pytest.mark.asyncio
async def test_sleep_episode_contains_stage_breakdown() -> None:
    """The episode payload must include the stage breakdown from metadata."""
    row = _make_row()
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    payload = upserted[0].payload
    assert "stages" in payload
    assert payload["stages"]["deep"] == 95
    assert payload["stages"]["rem"] == 137


@pytest.mark.asyncio
async def test_sleep_episode_privacy_is_sensitive() -> None:
    row = _make_row()
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert upserted[0].privacy == Privacy.SENSITIVE


@pytest.mark.asyncio
async def test_sleep_episode_precision_is_minute() -> None:
    row = _make_row()
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert upserted[0].precision == Precision.MINUTE


@pytest.mark.asyncio
async def test_sleep_episode_type_is_sleep_episode() -> None:
    row = _make_row()
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert upserted[0].episode_type == EPISODE_TYPE_SLEEP


# ---------------------------------------------------------------------------
# Idempotency: stable source_ref on re-poll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_day_repoll_produces_same_source_ref() -> None:
    """Two runs against the same fact row must produce the same source_ref,
    so the upsert_episode ON CONFLICT path deduplicates them.
    """
    row = _make_row()
    adapter = GoogleHealthSleepAdapter()
    refs: list[str] = []

    async def _capture_ref(conn: object, episode: Episode) -> Episode:
        refs.append(episode.source_ref)
        return episode

    pool1 = _pool_returning(row)
    cp1 = _chronicler_pool()
    pool2 = _pool_returning(row)
    cp2 = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_capture_ref
    ):
        await adapter.project(pool1, chronicler_pool=cp1, since=None)
        await adapter.project(pool2, chronicler_pool=cp2, since=None)

    assert refs[0] == refs[1], "source_ref must be stable across runs (idempotency)"


@pytest.mark.asyncio
async def test_source_ref_contains_idempotency_key() -> None:
    """The source_ref must embed the idempotency_key for stable replay."""
    ikey = "google_health:sleep:session-xyz:session"
    row = _make_row(idempotency_key=ikey)
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert ikey in upserted[0].source_ref


# ---------------------------------------------------------------------------
# Sleep spanning midnight UTC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sleep_spanning_midnight_utc() -> None:
    """A session starting before midnight UTC and ending after must produce a
    correct episode window — both start_at and end_at are captured verbatim
    from the fact metadata.
    """
    # 23:00 UTC on day D, ending 06:30 UTC on day D+1
    start = datetime(2026, 4, 24, 23, 0, 0, tzinfo=UTC)
    end = datetime(2026, 4, 25, 6, 30, 0, tzinfo=UTC)
    dur_ms = int((end - start).total_seconds() * 1000)

    row = _make_row(
        valid_at=start,
        metadata={
            "session_id": "midnight-session",
            "end_time": end.isoformat(),
            "duration_ms": dur_ms,
            "efficiency": 88,
            "stages": {"deep": 80, "light": 200, "rem": 120, "wake": 50},
        },
    )
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    ep = upserted[0]
    assert ep.start_at == start
    assert ep.end_at == end
    # Spans two calendar days
    assert ep.start_at.date() != ep.end_at.date()


# ---------------------------------------------------------------------------
# Null valid_at: row must be skipped, not stored as sentinel epoch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_valid_at_row_is_skipped() -> None:
    """A fact with null valid_at must be skipped; rows_projected must not be incremented."""
    row = _make_row(valid_at=None)  # type: ignore[arg-type]
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    # Row must not be counted or stored
    assert result.rows_projected == 0
    assert len(upserted) == 0, "upsert_episode must NOT be called for null valid_at rows"


@pytest.mark.asyncio
async def test_null_valid_at_does_not_store_sentinel_epoch() -> None:
    """Null valid_at must not produce an episode with start_at=epoch (1970-01-01)."""
    row = _make_row(valid_at=None)  # type: ignore[arg-type]
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    for ep in upserted:
        assert ep.start_at.year != 1970, "Sentinel epoch must not be stored for null valid_at"


# ---------------------------------------------------------------------------
# Graceful degradation: missing evidence surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_health_facts_table_returns_skipped() -> None:
    """When health.facts is absent the adapter degrades to skipped=True."""
    adapter = GoogleHealthSleepAdapter()
    pool = _pool_table_missing()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.google_health.upsert_episode") as mock_upsert:
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.skipped is True
    assert result.skipped_reason is not None
    assert "not found" in result.skipped_reason
    assert result.rows_projected == 0
    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_postgres_error_on_table_check_returns_skipped() -> None:
    """An asyncpg.PostgresError during the table-exists check must degrade gracefully."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedTableError('relation "health.facts" does not exist')
    )
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = GoogleHealthSleepAdapter()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.google_health.upsert_episode") as mock_upsert:
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.skipped is True
    assert result.rows_projected == 0
    mock_upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Watermark advance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watermark_advances_to_latest_created_at() -> None:
    """After projecting multiple rows, watermark must equal max(created_at)."""
    t1 = _NOW
    t2 = _NOW + timedelta(hours=1)
    rows = [
        _make_row(
            row_id="fact-001", created_at=t1, valid_at=datetime(2026, 4, 23, 22, 0, tzinfo=UTC)
        ),
        _make_row(
            row_id="fact-002", created_at=t2, valid_at=datetime(2026, 4, 24, 22, 0, tzinfo=UTC)
        ),
    ]
    adapter = GoogleHealthSleepAdapter()

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        return episode

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.watermark == t2


@pytest.mark.asyncio
async def test_since_filter_passed_to_query() -> None:
    """When ``since`` is given, the SQL query must filter on created_at > $2."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = GoogleHealthSleepAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=2)

    with patch("butlers.chronicler.adapters.google_health.upsert_episode"):
        await adapter.project(pool, chronicler_pool=cp, since=since)

    assert conn.fetch.await_count == 1
    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "created_at > $2" in query
    assert call_args.args[2] == since


@pytest.mark.asyncio
async def test_no_since_filter_when_since_is_none() -> None:
    """When ``since`` is None the query must NOT include a created_at filter."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = GoogleHealthSleepAdapter()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.google_health.upsert_episode"):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "created_at > " not in query


# ---------------------------------------------------------------------------
# Tuple-watermark boundary (since_id is ignored — UUID pk)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_since_id_is_ignored_uuid_pk() -> None:
    """Passing since_id must not cause a tuple ``(created_at, id)`` filter.

    The facts table uses UUID PKs, so the adapter falls back to the
    single-column ``WHERE created_at > $1`` semantics regardless of since_id.
    """
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = GoogleHealthSleepAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=1)
    since_id = 99  # would be used by integer-pk adapters

    with patch("butlers.chronicler.adapters.google_health.upsert_episode"):
        await adapter.project(pool, chronicler_pool=cp, since=since, since_id=since_id)

    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    # Must NOT use tuple comparison
    assert "(created_at, id) > ($1, $2)" not in query
    # Must use single-column filter
    assert "created_at > " in query


@pytest.mark.asyncio
async def test_watermark_id_is_always_none() -> None:
    """The adapter never sets watermark_id (UUID pk — not an integer serial)."""
    row = _make_row()

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()
    adapter = GoogleHealthSleepAdapter()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.watermark_id is None


# ---------------------------------------------------------------------------
# _derive_end_at unit tests
# ---------------------------------------------------------------------------


def test_derive_end_at_uses_end_time_string() -> None:
    """end_time ISO string is preferred over duration_ms."""
    start = datetime(2026, 4, 25, 22, 0, 0, tzinfo=UTC)
    end_str = "2026-04-26T05:30:00+00:00"
    meta = {"end_time": end_str, "duration_ms": 1000}
    end = _derive_end_at(start, meta)
    assert end is not None
    expected = datetime(2026, 4, 26, 5, 30, 0, tzinfo=UTC)
    assert end == expected


def test_derive_end_at_falls_back_to_duration_ms() -> None:
    """When end_time is absent, derive end_at from duration_ms."""
    start = datetime(2026, 4, 25, 22, 0, 0, tzinfo=UTC)
    meta = {"duration_ms": 7 * 3_600_000}  # 7 hours
    end = _derive_end_at(start, meta)
    assert end == start + timedelta(hours=7)


def test_derive_end_at_returns_none_when_no_data() -> None:
    """Without end_time or duration_ms, return None."""
    start = datetime(2026, 4, 25, 22, 0, 0, tzinfo=UTC)
    end = _derive_end_at(start, {})
    assert end is None


def test_derive_end_at_handles_z_suffix() -> None:
    """Timestamps ending in 'Z' are parsed correctly as UTC."""
    start = datetime(2026, 4, 25, 22, 0, 0, tzinfo=UTC)
    meta = {"end_time": "2026-04-26T05:30:00Z"}
    end = _derive_end_at(start, meta)
    assert end is not None
    assert end == datetime(2026, 4, 26, 5, 30, 0, tzinfo=UTC)


def test_derive_end_at_handles_datetime_object() -> None:
    """If metadata.end_time is already a datetime, use it directly."""
    start = datetime(2026, 4, 25, 22, 0, 0, tzinfo=UTC)
    end_obj = datetime(2026, 4, 26, 5, 30, 0, tzinfo=UTC)
    end = _derive_end_at(start, {"end_time": end_obj})
    assert end == end_obj


def test_derive_end_at_bad_string_falls_back_to_duration() -> None:
    """An unparseable end_time falls back to duration_ms without crashing."""
    start = datetime(2026, 4, 25, 22, 0, 0, tzinfo=UTC)
    meta = {"end_time": "not-a-date", "duration_ms": 3_600_000}
    end = _derive_end_at(start, meta)
    assert end == start + timedelta(hours=1)


# ---------------------------------------------------------------------------
# Contracts registration
# ---------------------------------------------------------------------------


def test_google_health_adapter_exported_from_package() -> None:
    from butlers.chronicler.adapters import GoogleHealthSleepAdapter as _Cls

    assert _Cls is GoogleHealthSleepAdapter


def test_google_health_measurements_supported_in_contracts() -> None:
    from butlers.chronicler.contracts import find_source
    from butlers.chronicler.models import Compatibility

    source = find_source("google_health.measurements")
    assert source is not None
    assert source.chronicler_compatibility == Compatibility.SUPPORTED
    assert source.read_surface is not None
    assert "health.facts" in source.read_surface


def test_google_health_measurements_in_supported_names() -> None:
    from butlers.chronicler.contracts import supported_source_names

    assert "google_health.measurements" in supported_source_names()


def test_google_health_measurements_not_in_deferred_names() -> None:
    from butlers.chronicler.contracts import deferred_source_names

    assert "google_health.measurements" not in deferred_source_names()


# ---------------------------------------------------------------------------
# Title formatting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_title_includes_duration_and_efficiency() -> None:
    row = _make_row(
        metadata={
            "session_id": "s1",
            "end_time": _SESSION_END.isoformat(),
            "duration_ms": _DURATION_MS,
            "efficiency": 91,
        }
    )
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    title = upserted[0].title
    assert "7h" in title
    assert "91%" in title


@pytest.mark.asyncio
async def test_title_omits_efficiency_when_absent() -> None:
    row = _make_row(
        metadata={
            "session_id": "s2",
            "end_time": _SESSION_END.isoformat(),
            "duration_ms": 3_600_000,  # 1 hour
        }
    )
    adapter = GoogleHealthSleepAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode", side_effect=_fake_upsert
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    title = upserted[0].title
    assert "%" not in title
    assert "1h" in title
