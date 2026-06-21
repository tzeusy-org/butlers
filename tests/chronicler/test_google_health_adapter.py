"""Tests for the Google Health sleep-episode Chronicler projection adapter.

Covers:
- Empty evidence → empty episodes.
- Multi-stage sleep (light/deep/REM) → single sleep_episode row covering
  full session window.
- Same-day re-poll idempotency (no duplicate episodes — stable source_ref).
- Sleep epochs spanning midnight UTC handled correctly.
- Null valid_at rows are skipped safely.
- Missing evidence surface (health.facts absent) → graceful degradation.
- No-LLM invariant (AST scan of adapter source).
- _derive_end_at end-time parsing (ISO strings, Z suffix, duration fallback).
- Adapter export from package.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.chronicler.adapters.google_health import (
    GoogleHealthHeartRateAdapter,
    GoogleHealthSleepAdapter,
    GoogleHealthStepsAdapter,
    GoogleHealthWorkoutAdapter,
    _derive_end_at,
)
from butlers.chronicler.models import Episode, PointEvent, Precision, Privacy

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
    """The google_health adapter module MUST NOT import any LLM client packages."""
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


# ---------------------------------------------------------------------------
# Multi-stage sleep → single sleep_episode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_stage_sleep_projects_single_sensitive_episode() -> None:
    """A multi-stage (light/deep/REM) sleep_session fact projects exactly ONE
    sleep_episode covering the full session window [valid_at, end_time), carrying
    the stage breakdown, with privacy=SENSITIVE (health-data invariant) and
    minute precision."""
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
    ep = upserted[0]
    # Full-session window
    assert ep.start_at == _SESSION_START
    assert ep.end_at == _SESSION_END
    # Stage breakdown preserved in payload
    assert ep.payload["stages"]["deep"] == 95
    assert ep.payload["stages"]["rem"] == 137
    # Privacy + precision contract
    assert ep.privacy == Privacy.SENSITIVE
    assert ep.precision == Precision.MINUTE


# ---------------------------------------------------------------------------
# Idempotency: stable source_ref on re-poll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_day_repoll_produces_same_source_ref() -> None:
    """Two runs against the same fact row must produce the same source_ref."""
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


# ---------------------------------------------------------------------------
# Sleep spanning midnight UTC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sleep_spanning_midnight_utc() -> None:
    """A session starting before midnight UTC and ending after must produce a
    correct episode window."""
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

    assert result.rows_projected == 0
    assert len(upserted) == 0, "upsert_episode must NOT be called for null valid_at rows"


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


# ---------------------------------------------------------------------------
# _derive_end_at unit tests (sleep stitching edge cases)
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


def test_derive_end_at_handles_z_suffix() -> None:
    """Timestamps ending in 'Z' are parsed correctly as UTC."""
    start = datetime(2026, 4, 25, 22, 0, 0, tzinfo=UTC)
    meta = {"end_time": "2026-04-26T05:30:00Z"}
    end = _derive_end_at(start, meta)
    assert end is not None
    assert end == datetime(2026, 4, 26, 5, 30, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Contracts registration
# ---------------------------------------------------------------------------


def test_google_health_adapter_exported_from_package() -> None:
    from butlers.chronicler.adapters import (
        GoogleHealthHeartRateAdapter as _HeartRateCls,
    )
    from butlers.chronicler.adapters import (
        GoogleHealthSleepAdapter as _SleepCls,
    )
    from butlers.chronicler.adapters import (
        GoogleHealthStepsAdapter as _StepsCls,
    )
    from butlers.chronicler.adapters import (
        GoogleHealthWorkoutAdapter as _WorkoutCls,
    )

    assert _SleepCls is GoogleHealthSleepAdapter
    assert _WorkoutCls is GoogleHealthWorkoutAdapter
    assert _StepsCls is GoogleHealthStepsAdapter
    assert _HeartRateCls is GoogleHealthHeartRateAdapter


@pytest.mark.asyncio
async def test_workout_fact_projects_episode() -> None:
    start = datetime(2026, 4, 25, 8, 0, tzinfo=UTC)
    row = _make_row(
        row_id="workout-001",
        idempotency_key="google_health:workout:run-1",
        valid_at=start,
        metadata={
            "activity_type": "run",
            "duration_ms": 45 * 60_000,
            "distance_m": 7200,
            "calories": 410,
        },
    )
    row["predicate"] = "workout_session"
    row["content"] = "Run (45m)"

    adapter = GoogleHealthWorkoutAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode",
        side_effect=_fake_upsert,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert result.episodes_closed == 1
    episode = upserted[0]
    assert episode.source_name == "google_health.measurements"
    assert episode.source_ref == "health.facts:workout_session:google_health:workout:run-1"
    assert episode.episode_type == "workout_episode"
    assert episode.privacy == Privacy.NORMAL
    assert episode.payload["activity_type"] == "run"
    assert episode.payload["distance_m"] == 7200


@pytest.mark.asyncio
async def test_workout_fact_with_heart_rate_metadata_is_sensitive() -> None:
    start = datetime(2026, 4, 25, 8, 0, tzinfo=UTC)
    row = _make_row(
        row_id="workout-hr-001",
        idempotency_key="google_health:workout:run-hr-1",
        valid_at=start,
        metadata={
            "activity_type": "run",
            "duration_ms": 45 * 60_000,
            "average_heart_rate": 151,
            "max_heart_rate": 177,
        },
    )
    row["predicate"] = "workout_session"
    row["content"] = "Run (45m)"

    adapter = GoogleHealthWorkoutAdapter()
    upserted: list[Episode] = []

    async def _fake_upsert(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        return episode

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_episode",
        side_effect=_fake_upsert,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert upserted[0].privacy == Privacy.SENSITIVE
    assert upserted[0].payload["average_heart_rate"] == 151


@pytest.mark.asyncio
async def test_steps_fact_projects_point_event() -> None:
    row = _make_row(
        row_id="steps-001",
        idempotency_key="google_health:steps:2026-04-25",
        valid_at=datetime(2026, 4, 25, 0, 0, tzinfo=UTC),
        metadata={"value": 9342, "distance_km": 6.8, "floors": 12},
    )
    row["predicate"] = "measurement_steps"
    row["content"] = "Steps: 9342"

    adapter = GoogleHealthStepsAdapter()
    upserted: list[PointEvent] = []

    async def _fake_upsert(conn: object, event: PointEvent) -> PointEvent:
        upserted.append(event)
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_point_event",
        side_effect=_fake_upsert,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert result.point_events == 1
    event = upserted[0]
    assert event.source_name == "health.steps"
    assert event.source_ref == "health.facts:measurement_steps:google_health:steps:2026-04-25"
    assert event.event_type == "daily_steps"
    assert event.precision == Precision.DAY
    assert event.privacy == Privacy.NORMAL
    assert event.payload["steps"] == 9342
    assert event.payload["distance_km"] == 6.8


@pytest.mark.asyncio
async def test_heart_rate_fact_projects_sensitive_point_event() -> None:
    row = _make_row(
        row_id="hr-001",
        idempotency_key="google_health:resting_hr:2026-04-25",
        valid_at=datetime(2026, 4, 25, 0, 0, tzinfo=UTC),
        metadata={
            "value": 62,
            "heart_rate_zones": {"fat_burn": 35, "cardio": 4},
        },
    )
    row["predicate"] = "measurement_resting_hr"
    row["content"] = "Resting HR: 62 bpm"

    adapter = GoogleHealthHeartRateAdapter()
    upserted: list[PointEvent] = []

    async def _fake_upsert(conn: object, event: PointEvent) -> PointEvent:
        upserted.append(event)
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.google_health.upsert_point_event",
        side_effect=_fake_upsert,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert result.point_events == 1
    event = upserted[0]
    assert event.source_name == "health.heart_rate"
    assert event.event_type == "heart_rate_summary"
    assert event.precision == Precision.DAY
    assert event.privacy == Privacy.SENSITIVE
    assert event.payload["bpm"] == 62
    assert event.payload["heart_rate_zones"] == {"fat_burn": 35, "cardio": 4}
