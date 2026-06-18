"""Tests for the insights_v1 versioned read-model boundary.

Verifies:
- ``row_to_delivery_state`` converts a raw aggregate record to the typed DTO
- ``query_insight_delivery_state`` returns a typed InsightDeliveryStateRow
- ``query_insight_delivery_state`` returns None when pool.fetchrow returns None
- ``query_insight_delivery_state`` raises exceptions (no error-handling — caller's job)
- SQL contract: queries ``public.insight_candidates`` with delivery_attempt_count >= 3
- Column constant is a non-empty string with expected column aliases
- Version marker is stable and matches the module name
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.api.read_models.insights_v1 import (
    INSIGHT_DELIVERY_COLUMNS,
    READ_MODEL_VERSION,
    InsightDeliveryStateRow,
    query_insight_delivery_state,
    row_to_delivery_state,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 18, 10, 0, 0, tzinfo=UTC)
_DELIVERED_AT = _NOW - timedelta(hours=3)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(d: dict):
    """Wrap a dict in a MagicMock that supports subscript access."""
    m = MagicMock()
    m.__getitem__ = lambda self, k: d[k]
    return m


def _aggregate_dict(**overrides) -> dict:
    base = {
        "queued": 2,
        "delivered": 8,
        "failed": 1,
        "last_delivery_at": _DELIVERED_AT,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Version marker
# ---------------------------------------------------------------------------


def test_version_marker_is_insights_v1():
    """READ_MODEL_VERSION must equal 'insights_v1' — change only on breaking update."""
    assert READ_MODEL_VERSION == "insights_v1"


# ---------------------------------------------------------------------------
# Column constant
# ---------------------------------------------------------------------------


def test_insight_delivery_columns_is_non_empty_string():
    assert isinstance(INSIGHT_DELIVERY_COLUMNS, str)
    assert len(INSIGHT_DELIVERY_COLUMNS) > 0


def test_insight_delivery_columns_has_expected_aliases():
    """Column constant must expose the four aliases the DTO expects."""
    assert "queued" in INSIGHT_DELIVERY_COLUMNS
    assert "delivered" in INSIGHT_DELIVERY_COLUMNS
    assert "failed" in INSIGHT_DELIVERY_COLUMNS
    assert "last_delivery_at" in INSIGHT_DELIVERY_COLUMNS


def test_insight_delivery_columns_has_delivery_attempt_count_filter():
    """The failed aggregate must use delivery_attempt_count >= 3."""
    assert "delivery_attempt_count >= 3" in INSIGHT_DELIVERY_COLUMNS


# ---------------------------------------------------------------------------
# row_to_delivery_state
# ---------------------------------------------------------------------------


def test_row_to_delivery_state_maps_all_fields():
    row = _make_record(_aggregate_dict())
    dto = row_to_delivery_state(row)

    assert isinstance(dto, InsightDeliveryStateRow)
    assert dto.queued == 2
    assert dto.delivered == 8
    assert dto.failed == 1
    assert dto.last_delivery_at == _DELIVERED_AT


def test_row_to_delivery_state_none_last_delivery_at():
    row = _make_record(_aggregate_dict(last_delivery_at=None))
    dto = row_to_delivery_state(row)
    assert dto.last_delivery_at is None


def test_row_to_delivery_state_zero_counts():
    row = _make_record(_aggregate_dict(queued=0, delivered=0, failed=0))
    dto = row_to_delivery_state(row)
    assert dto.queued == 0
    assert dto.delivered == 0
    assert dto.failed == 0


def test_row_to_delivery_state_coerces_none_counts_to_zero():
    """None values from the aggregate (empty table) are coerced to int 0."""
    row = _make_record(_aggregate_dict(queued=None, delivered=None, failed=None))
    dto = row_to_delivery_state(row)
    assert dto.queued == 0
    assert dto.delivered == 0
    assert dto.failed == 0


# ---------------------------------------------------------------------------
# query_insight_delivery_state
# ---------------------------------------------------------------------------


async def test_query_insight_delivery_state_returns_typed_dto():
    """Returns an InsightDeliveryStateRow when fetchrow succeeds."""
    mock_pool = AsyncMock()
    row = _make_record(_aggregate_dict())
    mock_pool.fetchrow = AsyncMock(return_value=row)

    result = await query_insight_delivery_state(mock_pool)

    assert isinstance(result, InsightDeliveryStateRow)
    assert result.queued == 2
    assert result.delivered == 8
    assert result.failed == 1
    assert result.last_delivery_at == _DELIVERED_AT


async def test_query_insight_delivery_state_returns_none_when_no_row():
    """Returns None when pool.fetchrow returns None (should not happen but guarded)."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=None)

    result = await query_insight_delivery_state(mock_pool)

    assert result is None


async def test_query_insight_delivery_state_propagates_exceptions():
    """Does NOT swallow exceptions — caller is responsible for graceful degrade."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(side_effect=RuntimeError("relation does not exist"))

    with pytest.raises(RuntimeError, match="relation does not exist"):
        await query_insight_delivery_state(mock_pool)


async def test_query_insight_delivery_state_queries_public_insight_candidates():
    """SQL must query public.insight_candidates."""
    mock_pool = AsyncMock()
    row = _make_record(_aggregate_dict())
    mock_pool.fetchrow = AsyncMock(return_value=row)

    await query_insight_delivery_state(mock_pool)

    sql = mock_pool.fetchrow.call_args[0][0]
    assert "public.insight_candidates" in sql


async def test_query_insight_delivery_state_sql_uses_column_constant():
    """SQL projection uses INSIGHT_DELIVERY_COLUMNS constant (no inline duplication)."""
    mock_pool = AsyncMock()
    row = _make_record(_aggregate_dict())
    mock_pool.fetchrow = AsyncMock(return_value=row)

    await query_insight_delivery_state(mock_pool)

    sql = mock_pool.fetchrow.call_args[0][0]
    assert INSIGHT_DELIVERY_COLUMNS in sql


async def test_query_insight_delivery_state_sql_has_delivery_attempt_count_filter():
    """SQL must filter failed by delivery_attempt_count >= 3."""
    mock_pool = AsyncMock()
    row = _make_record(_aggregate_dict())
    mock_pool.fetchrow = AsyncMock(return_value=row)

    await query_insight_delivery_state(mock_pool)

    sql = mock_pool.fetchrow.call_args[0][0]
    assert "delivery_attempt_count >= 3" in sql


async def test_query_insight_delivery_state_empty_table_returns_zeros():
    """When aggregate returns all-None counts (truly empty table), dto has zero counts."""
    mock_pool = AsyncMock()
    row = _make_record(
        _aggregate_dict(queued=None, delivered=None, failed=None, last_delivery_at=None)
    )
    mock_pool.fetchrow = AsyncMock(return_value=row)

    result = await query_insight_delivery_state(mock_pool)

    assert result is not None
    assert result.queued == 0
    assert result.delivered == 0
    assert result.failed == 0
    assert result.last_delivery_at is None
