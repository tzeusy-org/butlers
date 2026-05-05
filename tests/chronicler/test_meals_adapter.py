"""Tests for the health meals Chronicler projection adapter.

Covers:
- Single meal → single eating_event point event (no episode shape).
- Event field schema correctness and title format.
- Payload omits null nutrition/notes (schema cleanliness).
- Watermark advances to max eaten_at across batch.
- Missing evidence surface graceful degradation.
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
    """The meals adapter module must not import any LLM client packages."""
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
# Single meal → single eating_event point event
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
# Watermark advance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watermark_advances_to_max_eaten_at() -> None:
    t1 = _NOW
    t2 = _NOW + timedelta(hours=6)
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


# ---------------------------------------------------------------------------
# Missing evidence surface graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_evidence_table_returns_skipped_result() -> None:
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
