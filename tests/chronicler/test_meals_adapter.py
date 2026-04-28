"""Tests for the health meals Chronicler projection adapter.

Covers:
- Empty input → empty events (no point events, watermark preserved).
- Single meal → single eating_event point event (no episode shape).
- Multiple meals → one point event per meal.
- Idempotent re-poll (same source_ref across runs).
- Midnight UTC handling (eating_event at midnight).
- Missing evidence surface graceful degradation (table not found / UndefinedTableError).
- UndefinedTableError graceful degradation.
- Watermark advances to max eaten_at across batch.
- Watermark preserved when no rows returned.
- since filter passed through to query (eaten_at > $1).
- Tuple-watermark: (eaten_at, seq) > ($1, $2) boundary precision.
- Single-column fallback when since_id is None.
- Watermark_id set in result.
- No-LLM AST scan.
- Contracts registration: health.meals SUPPORTED.
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.chronicler.adapters.meals import (
    DEFAULT_BATCH_LIMIT,
    EVENT_TYPE_EATING,
    SOURCE_NAME,
    MealsAdapter,
)
from butlers.chronicler.models import PointEvent, Precision, Privacy

_NOW = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)
_MEAL_ID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    row_id: str = _MEAL_ID,
    meal_type: str = "lunch",
    description: str = "Grilled chicken salad",
    nutrition: dict | None = None,
    eaten_at: datetime = _NOW,
    notes: str | None = None,
    seq: int = 1,
) -> dict:
    return {
        "id": row_id,
        "type": meal_type,
        "description": description,
        "nutrition": nutrition,
        "eaten_at": eaten_at,
        "notes": notes,
        "seq": seq,
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
    """Build a minimal mock chronicler pool for upsert_point_event calls."""
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


def test_no_llm_imports_in_meals_adapter() -> None:
    """The meals adapter module must not import any LLM client packages.

    Parses the source AST rather than inspecting the live module so that
    transitive imports through other modules don't cause false negatives.
    """
    import butlers.chronicler.adapters.meals as mod

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
                        f"LLM import detected in meals adapter: {alias.name!r}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for prefix in forbidden_prefixes:
                    assert not node.module.startswith(prefix), (
                        f"LLM import detected in meals adapter: {node.module!r}"
                    )


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


def test_source_name() -> None:
    assert SOURCE_NAME == "health.meals"


def test_event_type() -> None:
    assert EVENT_TYPE_EATING == "eating_event"


def test_default_batch_limit() -> None:
    assert DEFAULT_BATCH_LIMIT == 500


# ---------------------------------------------------------------------------
# Empty input → empty events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_input_returns_no_events() -> None:
    """When the evidence table exists but has no rows, return no events."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)  # table exists
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = MealsAdapter()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.meals.upsert_point_event") as mock_upsert:
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 0
    assert result.point_events == 0
    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_empty_input_preserves_watermark() -> None:
    """When no rows are returned, the watermark stays at the prior since value."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = MealsAdapter()
    cp = _chronicler_pool()
    prior_watermark = _NOW - timedelta(days=1)

    with patch("butlers.chronicler.adapters.meals.upsert_point_event"):
        result = await adapter.project(pool, chronicler_pool=cp, since=prior_watermark)

    assert result.watermark == prior_watermark
    assert result.rows_projected == 0


# ---------------------------------------------------------------------------
# Single meal → single eating_event point event (no episode shape)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_meal_produces_one_point_event() -> None:
    """A single meal row must produce exactly one eating_event point event."""
    row = _make_row()
    adapter = MealsAdapter()
    upserted: list[PointEvent] = []

    async def _fake_upsert(conn: object, event: PointEvent) -> PointEvent:
        upserted.append(event)
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.meals.upsert_point_event", side_effect=_fake_upsert):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert result.point_events == 1
    assert result.episodes_opened == 0
    assert result.episodes_closed == 0
    assert len(upserted) == 1


@pytest.mark.asyncio
async def test_single_meal_event_fields() -> None:
    """Point event fields must reflect the meal row correctly."""
    row_id = str(uuid.uuid4())
    eaten_at = datetime(2026, 4, 25, 13, 30, 0, tzinfo=UTC)
    row = _make_row(
        row_id=row_id,
        meal_type="lunch",
        description="Grilled chicken salad",
        eaten_at=eaten_at,
    )
    adapter = MealsAdapter()
    upserted: list[PointEvent] = []

    async def _fake_upsert(conn: object, event: PointEvent) -> PointEvent:
        upserted.append(event)
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.meals.upsert_point_event", side_effect=_fake_upsert):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    ev = upserted[0]
    assert ev.source_name == SOURCE_NAME
    assert ev.event_type == EVENT_TYPE_EATING
    assert ev.occurred_at == eaten_at
    assert ev.precision == Precision.EXACT
    assert ev.privacy == Privacy.SENSITIVE
    assert "Grilled chicken salad" in ev.title
    assert ev.source_ref == f"health.meals:{row_id}"


@pytest.mark.asyncio
async def test_meal_title_format() -> None:
    """Title should be '{type}: {description}' when both are present."""
    row = _make_row(meal_type="breakfast", description="Oatmeal with berries")
    adapter = MealsAdapter()
    upserted: list[PointEvent] = []

    async def _fake_upsert(conn: object, event: PointEvent) -> PointEvent:
        upserted.append(event)
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.meals.upsert_point_event", side_effect=_fake_upsert):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    assert upserted[0].title == "Breakfast: Oatmeal with berries"


@pytest.mark.asyncio
async def test_meal_payload_includes_key_fields() -> None:
    """Payload must include id, type, description."""
    row_id = str(uuid.uuid4())
    nutrition = {"calories": 450, "protein_g": 30}
    row = _make_row(
        row_id=row_id,
        meal_type="dinner",
        description="Steak",
        nutrition=nutrition,
        notes="Cooked medium rare",
    )
    adapter = MealsAdapter()
    upserted: list[PointEvent] = []

    async def _fake_upsert(conn: object, event: PointEvent) -> PointEvent:
        upserted.append(event)
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.meals.upsert_point_event", side_effect=_fake_upsert):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    payload = upserted[0].payload
    assert payload["id"] == row_id
    assert payload["type"] == "dinner"
    assert payload["description"] == "Steak"
    assert payload["nutrition"] == nutrition
    assert payload["notes"] == "Cooked medium rare"


@pytest.mark.asyncio
async def test_meal_payload_omits_null_nutrition_and_notes() -> None:
    """Payload must omit nutrition and notes keys when they are None."""
    row = _make_row(nutrition=None, notes=None)
    adapter = MealsAdapter()
    upserted: list[PointEvent] = []

    async def _fake_upsert(conn: object, event: PointEvent) -> PointEvent:
        upserted.append(event)
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.meals.upsert_point_event", side_effect=_fake_upsert):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    payload = upserted[0].payload
    assert "nutrition" not in payload
    assert "notes" not in payload


# ---------------------------------------------------------------------------
# Idempotent re-poll (no duplicates — same source_ref on replay)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_row_produces_same_source_ref_on_replay() -> None:
    """Projecting the same row twice yields identical source_ref values."""
    row = _make_row()
    adapter = MealsAdapter()
    refs: list[str] = []

    async def _fake_upsert(conn: object, event: PointEvent) -> PointEvent:
        refs.append(event.source_ref)
        return event

    pool1 = _pool_returning(row)
    cp1 = _chronicler_pool()
    pool2 = _pool_returning(row)
    cp2 = _chronicler_pool()

    with patch("butlers.chronicler.adapters.meals.upsert_point_event", side_effect=_fake_upsert):
        await adapter.project(pool1, chronicler_pool=cp1, since=None)
        await adapter.project(pool2, chronicler_pool=cp2, since=None)

    assert refs[0] == refs[1]


# ---------------------------------------------------------------------------
# Midnight UTC handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_midnight_utc_eating_event() -> None:
    """An eating_event at midnight UTC (00:00:00) must be projected correctly."""
    midnight = datetime(2026, 4, 26, 0, 0, 0, tzinfo=UTC)
    row = _make_row(eaten_at=midnight)
    adapter = MealsAdapter()
    upserted: list[PointEvent] = []

    async def _fake_upsert(conn: object, event: PointEvent) -> PointEvent:
        upserted.append(event)
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.meals.upsert_point_event", side_effect=_fake_upsert):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert upserted[0].occurred_at == midnight


# ---------------------------------------------------------------------------
# Missing evidence surface graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_evidence_table_returns_skipped_result() -> None:
    """When the health.meals table doesn't exist, return skipped=True."""
    adapter = MealsAdapter()
    pool = _pool_table_missing()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.meals.upsert_point_event") as mock_upsert:
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.skipped is True
    assert result.skipped_reason is not None
    assert "not found" in result.skipped_reason
    assert result.rows_projected == 0
    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_undefined_table_exception_returns_skipped_result() -> None:
    """When DB raises UndefinedTableError, adapter must degrade gracefully."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedTableError('relation "health.meals" does not exist')
    )
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = MealsAdapter()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.meals.upsert_point_event") as mock_upsert:
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.skipped is True
    assert result.rows_projected == 0
    assert result.watermark is None
    mock_upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Watermark advance / resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watermark_advances_to_max_eaten_at() -> None:
    """Watermark advances to the maximum eaten_at across all projected rows."""
    t1 = _NOW
    t2 = _NOW + timedelta(hours=4)
    rows = [
        _make_row(eaten_at=t1, seq=1),
        _make_row(eaten_at=t2, seq=2),
    ]
    adapter = MealsAdapter()

    async def _fake_upsert(conn: object, event: PointEvent) -> PointEvent:
        return event

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.meals.upsert_point_event", side_effect=_fake_upsert):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.watermark == t2


@pytest.mark.asyncio
async def test_watermark_id_set_in_result() -> None:
    """watermark_id must reflect the seq of the last-projected row."""
    row = {**_make_row(eaten_at=_NOW), "seq": 42}

    async def _fake_upsert(conn: object, event: PointEvent) -> PointEvent:
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()
    adapter = MealsAdapter()

    with patch("butlers.chronicler.adapters.meals.upsert_point_event", side_effect=_fake_upsert):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.watermark == _NOW
    assert result.watermark_id == 42


@pytest.mark.asyncio
async def test_watermark_id_tracks_max_seq_at_same_eaten_at() -> None:
    """When multiple rows share the same eaten_at, watermark_id is the max seq."""
    t = _NOW
    rows = [
        _make_row(eaten_at=t, seq=5),
        _make_row(eaten_at=t, seq=15),
        _make_row(eaten_at=t, seq=25),
    ]

    async def _fake_upsert(conn: object, event: PointEvent) -> PointEvent:
        return event

    pool = _pool_returning(*rows)
    cp = _chronicler_pool()
    adapter = MealsAdapter()

    with patch("butlers.chronicler.adapters.meals.upsert_point_event", side_effect=_fake_upsert):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.watermark == t
    assert result.watermark_id == 25


@pytest.mark.asyncio
async def test_watermark_id_preserved_when_no_rows() -> None:
    """When no rows returned, both watermark and watermark_id remain unchanged."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = MealsAdapter()
    cp = _chronicler_pool()
    prior_watermark = _NOW - timedelta(days=7)
    prior_watermark_id = 3

    with patch("butlers.chronicler.adapters.meals.upsert_point_event"):
        result = await adapter.project(
            pool,
            chronicler_pool=cp,
            since=prior_watermark,
            since_id=prior_watermark_id,
        )

    assert result.watermark == prior_watermark
    assert result.watermark_id == prior_watermark_id


# ---------------------------------------------------------------------------
# since filter / ORDER BY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_since_filter_passed_to_query() -> None:
    """When since is given, the fetch query filters on eaten_at > $1."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = MealsAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=2)

    with patch("butlers.chronicler.adapters.meals.upsert_point_event"):
        await adapter.project(pool, chronicler_pool=cp, since=since)

    assert conn.fetch.await_count == 1
    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "eaten_at > $1" in query
    assert call_args.args[1] == since


@pytest.mark.asyncio
async def test_order_by_includes_id_tiebreaker() -> None:
    """ORDER BY clause must include id ASC as a tie-breaker."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = MealsAdapter()
    cp = _chronicler_pool()

    with patch("butlers.chronicler.adapters.meals.upsert_point_event"):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    query: str = conn.fetch.call_args.args[0]
    assert "ORDER BY eaten_at ASC, id ASC" in query


# ---------------------------------------------------------------------------
# Tuple-watermark: (eaten_at, seq) > ($1, $2) boundary precision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tuple_filter_used_when_since_and_since_id_both_given() -> None:
    """When both since and since_id are provided, the query uses tuple comparison."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = MealsAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=1)
    since_id = 99

    with patch("butlers.chronicler.adapters.meals.upsert_point_event"):
        await adapter.project(pool, chronicler_pool=cp, since=since, since_id=since_id)

    assert conn.fetch.await_count == 1
    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "(eaten_at, seq) > ($1, $2)" in query
    assert call_args.args[1] == since
    assert call_args.args[2] == since_id


@pytest.mark.asyncio
async def test_single_column_fallback_when_since_id_is_none() -> None:
    """When since is given but since_id is None, the legacy WHERE eaten_at > $1 form is used."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))

    adapter = MealsAdapter()
    cp = _chronicler_pool()
    since = _NOW - timedelta(hours=1)

    with patch("butlers.chronicler.adapters.meals.upsert_point_event"):
        await adapter.project(pool, chronicler_pool=cp, since=since, since_id=None)

    call_args = conn.fetch.call_args
    query: str = call_args.args[0]
    assert "eaten_at > $1" in query
    assert "(eaten_at, seq) > ($1, $2)" not in query


# ---------------------------------------------------------------------------
# Contracts registration
# ---------------------------------------------------------------------------


def test_meals_adapter_exported_from_package() -> None:
    from butlers.chronicler.adapters import MealsAdapter as _Cls

    assert _Cls is MealsAdapter


def test_health_meals_supported_in_contracts() -> None:
    from butlers.chronicler.contracts import find_source
    from butlers.chronicler.models import Compatibility

    source = find_source("health.meals")
    assert source is not None
    assert source.chronicler_compatibility == Compatibility.SUPPORTED
    assert source.read_surface == "health.meals"


def test_health_meals_in_supported_names() -> None:
    from butlers.chronicler.contracts import supported_source_names

    assert "health.meals" in supported_source_names()


def test_health_meals_not_in_planned_names() -> None:
    from butlers.chronicler.contracts import planned_source_names

    assert "health.meals" not in planned_source_names()
