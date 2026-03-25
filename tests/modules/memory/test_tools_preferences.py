"""Tests for the get_preferences MCP tool implementation."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.memory.tools.preferences import (
    _compute_effective_confidence,
    _resolve_owner_entity_id,
    get_preferences,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Return an AsyncMock asyncpg pool."""
    return AsyncMock()


@pytest.fixture()
def owner_entity_id() -> uuid.UUID:
    """Fixed owner entity UUID."""
    return uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


@pytest.fixture()
def now_utc() -> datetime:
    """Fixed 'now' for deterministic decay tests."""
    return datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)


def _make_fact_row(
    predicate: str = "preferences:travel_flight_seat",
    content: str = "window",
    scope: str = "travel",
    importance: float = 8.0,
    permanence: str = "stable",
    confidence: float = 1.0,
    decay_rate: float = 0.002,
    last_confirmed_at: datetime | None = None,
    created_at: datetime | None = None,
) -> dict:
    """Build a minimal fact row dict matching what asyncpg returns."""
    return {
        "predicate": predicate,
        "content": content,
        "scope": scope,
        "importance": importance,
        "permanence": permanence,
        "confidence": confidence,
        "decay_rate": decay_rate,
        "last_confirmed_at": last_confirmed_at,
        "created_at": created_at or datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
    }


# ---------------------------------------------------------------------------
# _compute_effective_confidence tests
# ---------------------------------------------------------------------------


class TestComputeEffectiveConfidence:
    """Unit tests for the standalone decay helper."""

    def test_permanent_returns_confidence_unchanged(self) -> None:
        row = _make_fact_row(confidence=0.9, decay_rate=0.0)
        assert _compute_effective_confidence(row) == 0.9

    def test_no_anchor_returns_zero(self) -> None:
        row = _make_fact_row(decay_rate=0.002, last_confirmed_at=None)
        row["created_at"] = None
        assert _compute_effective_confidence(row) == 0.0

    def test_prefers_last_confirmed_at_over_created_at(self, now_utc: datetime) -> None:
        # last_confirmed_at is recent (1 day ago), created_at is 100 days ago.
        recent = now_utc - timedelta(days=1)
        old = now_utc - timedelta(days=100)
        row = _make_fact_row(
            confidence=1.0,
            decay_rate=0.002,
            last_confirmed_at=recent,
            created_at=old,
        )
        with patch("butlers.modules.memory.tools.preferences.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            result = _compute_effective_confidence(row)

        expected = math.exp(-0.002 * 1.0)
        assert abs(result - expected) < 1e-9

    def test_falls_back_to_created_at(self, now_utc: datetime) -> None:
        anchor = now_utc - timedelta(days=10)
        row = _make_fact_row(
            confidence=1.0,
            decay_rate=0.01,
            last_confirmed_at=None,
            created_at=anchor,
        )
        with patch("butlers.modules.memory.tools.preferences.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            result = _compute_effective_confidence(row)

        expected = math.exp(-0.01 * 10.0)
        assert abs(result - expected) < 1e-9

    def test_zero_days_elapsed_returns_full_confidence(self, now_utc: datetime) -> None:
        row = _make_fact_row(
            confidence=0.8,
            decay_rate=0.5,
            last_confirmed_at=now_utc,
        )
        with patch("butlers.modules.memory.tools.preferences.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            result = _compute_effective_confidence(row)

        assert abs(result - 0.8) < 1e-9


# ---------------------------------------------------------------------------
# _resolve_owner_entity_id tests
# ---------------------------------------------------------------------------


class TestResolveOwnerEntityId:
    """Unit tests for owner entity resolution."""

    async def test_returns_entity_id_when_found(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        result = await _resolve_owner_entity_id(mock_pool)
        assert result == owner_entity_id

    async def test_returns_none_when_no_owner(self, mock_pool: AsyncMock) -> None:
        mock_pool.fetchrow = AsyncMock(return_value=None)
        result = await _resolve_owner_entity_id(mock_pool)
        assert result is None

    async def test_returns_none_on_exception(self, mock_pool: AsyncMock) -> None:
        mock_pool.fetchrow = AsyncMock(side_effect=Exception("DB error"))
        result = await _resolve_owner_entity_id(mock_pool)
        assert result is None

    async def test_queries_shared_entities_with_owner_role(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        await _resolve_owner_entity_id(mock_pool)
        call_args = mock_pool.fetchrow.call_args
        sql = call_args[0][0]
        assert "shared.entities" in sql
        assert "owner" in sql


# ---------------------------------------------------------------------------
# get_preferences tests
# ---------------------------------------------------------------------------


class TestGetPreferences:
    """Unit tests for the get_preferences function."""

    async def test_returns_empty_list_when_no_owner(self, mock_pool: AsyncMock) -> None:
        mock_pool.fetchrow = AsyncMock(return_value=None)
        result = await get_preferences(mock_pool)
        assert result == []

    async def test_returns_empty_list_on_db_error(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        mock_pool.fetch = AsyncMock(side_effect=Exception("connection reset"))
        result = await get_preferences(mock_pool)
        assert result == []

    async def test_returns_simplified_format(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID
    ) -> None:
        created = datetime(2026, 1, 10, tzinfo=UTC)
        row = _make_fact_row(
            predicate="preferences:travel_flight_seat",
            content="window",
            scope="travel",
            importance=8.0,
            permanence="stable",
            confidence=1.0,
            decay_rate=0.0,  # permanent-like for simplicity
            created_at=created,
        )
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        mock_pool.fetch = AsyncMock(return_value=[MagicMock(**row)])

        # Make dict() work on the mock row
        mock_row = MagicMock()
        mock_row.__iter__ = lambda self: iter(row.items())
        mock_row.keys = lambda: row.keys()
        mock_pool.fetch = AsyncMock(return_value=[mock_row])

        # Simpler: patch pool.fetch to return actual dict-like objects
        import asyncpg

        record = MagicMock(spec=asyncpg.Record)
        record.__iter__ = lambda self: iter(row.items())

        # Use real dicts instead
        mock_pool.fetch = AsyncMock(return_value=[row])

        result = await get_preferences(mock_pool)
        assert len(result) == 1
        pref = result[0]
        assert pref["predicate"] == "preferences:travel_flight_seat"
        assert pref["value"] == "window"
        assert pref["scope"] == "travel"
        assert pref["importance"] == 8.0
        assert pref["permanence"] == "stable"
        assert "effective_confidence" in pref
        assert "updated_at" in pref

    async def test_result_keys_match_spec(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID
    ) -> None:
        """Each result dict must contain exactly the spec-defined keys."""
        row = _make_fact_row(decay_rate=0.0)
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        mock_pool.fetch = AsyncMock(return_value=[row])

        result = await get_preferences(mock_pool)
        assert len(result) == 1
        keys = set(result[0].keys())
        expected_keys = {
            "predicate",
            "value",
            "scope",
            "importance",
            "permanence",
            "effective_confidence",
            "updated_at",
        }
        assert keys == expected_keys

    async def test_ordered_by_predicate_asc(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID
    ) -> None:
        """Results returned by DB are trusted to be ordered by predicate ASC."""
        rows = [
            _make_fact_row("preferences:general_language", "English", "global"),
            _make_fact_row("preferences:health_dietary_restriction", "no shellfish", "health"),
            _make_fact_row("preferences:travel_flight_seat", "window", "travel"),
        ]
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        mock_pool.fetch = AsyncMock(return_value=rows)

        result = await get_preferences(mock_pool)
        predicates = [r["predicate"] for r in result]
        assert predicates == sorted(predicates)

    async def test_scope_filter_passed_to_query(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        mock_pool.fetch = AsyncMock(return_value=[])

        await get_preferences(mock_pool, scope="travel")

        call_args = mock_pool.fetch.call_args
        sql = call_args[0][0]
        params = list(call_args[0][1:])
        assert "scope" in sql
        assert "travel" in params

    async def test_predicate_pattern_filter_passed_to_query(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        mock_pool.fetch = AsyncMock(return_value=[])

        await get_preferences(mock_pool, predicate_pattern="preferences:health_%")

        call_args = mock_pool.fetch.call_args
        sql = call_args[0][0]
        params = list(call_args[0][1:])
        assert "LIKE" in sql
        assert "preferences:health_%" in params

    async def test_default_predicate_pattern_is_preferences_wildcard(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        mock_pool.fetch = AsyncMock(return_value=[])

        await get_preferences(mock_pool)

        call_args = mock_pool.fetch.call_args
        params = list(call_args[0][1:])
        assert "preferences:%" in params

    async def test_effective_confidence_computed_for_decaying_fact(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID, now_utc: datetime
    ) -> None:
        anchor = now_utc - timedelta(days=100)
        row = _make_fact_row(
            confidence=1.0,
            decay_rate=0.002,
            last_confirmed_at=anchor,
        )
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        mock_pool.fetch = AsyncMock(return_value=[row])

        with patch("butlers.modules.memory.tools.preferences.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            result = await get_preferences(mock_pool)

        expected = round(math.exp(-0.002 * 100.0), 6)
        assert result[0]["effective_confidence"] == expected

    async def test_permanent_fact_has_full_effective_confidence(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID
    ) -> None:
        row = _make_fact_row(
            confidence=0.95,
            decay_rate=0.0,
            permanence="permanent",
        )
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        mock_pool.fetch = AsyncMock(return_value=[row])

        result = await get_preferences(mock_pool)
        assert result[0]["effective_confidence"] == round(0.95, 6)

    async def test_updated_at_is_iso8601_string(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID
    ) -> None:
        created = datetime(2026, 2, 14, 9, 30, 0, tzinfo=UTC)
        row = _make_fact_row(created_at=created, decay_rate=0.0)
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        mock_pool.fetch = AsyncMock(return_value=[row])

        result = await get_preferences(mock_pool)
        updated_at = result[0]["updated_at"]
        assert isinstance(updated_at, str)
        # Verify it's parseable as ISO-8601
        parsed = datetime.fromisoformat(updated_at)
        assert parsed.year == 2026

    async def test_both_filters_applied_together(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID
    ) -> None:
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        mock_pool.fetch = AsyncMock(return_value=[])

        await get_preferences(
            mock_pool,
            scope="health",
            predicate_pattern="preferences:health_%",
        )

        call_args = mock_pool.fetch.call_args
        sql = call_args[0][0]
        params = list(call_args[0][1:])
        assert "scope" in sql
        assert "LIKE" in sql
        assert "health" in params
        assert "preferences:health_%" in params

    async def test_value_comes_from_content_column(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID
    ) -> None:
        row = _make_fact_row(content="no shellfish", decay_rate=0.0)
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        mock_pool.fetch = AsyncMock(return_value=[row])

        result = await get_preferences(mock_pool)
        assert result[0]["value"] == "no shellfish"

    async def test_tenant_id_passed_to_query(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID
    ) -> None:
        """tenant_id must be included in query params to prevent cross-tenant leakage."""
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        mock_pool.fetch = AsyncMock(return_value=[])

        await get_preferences(mock_pool, tenant_id="owner")

        call_args = mock_pool.fetch.call_args
        sql = call_args[0][0]
        params = list(call_args[0][1:])
        assert "tenant_id" in sql
        assert "owner" in params

    async def test_invalid_predicate_pattern_coerced_to_default(
        self, mock_pool: AsyncMock, owner_entity_id: uuid.UUID
    ) -> None:
        """Patterns not starting with 'preferences:' must be coerced to 'preferences:%'."""
        mock_pool.fetchrow = AsyncMock(return_value={"entity_id": owner_entity_id})
        mock_pool.fetch = AsyncMock(return_value=[])

        await get_preferences(mock_pool, predicate_pattern="%")

        call_args = mock_pool.fetch.call_args
        params = list(call_args[0][1:])
        # The invalid pattern '%' must be replaced with 'preferences:%'
        assert "preferences:%" in params
        assert "%" not in [p for p in params if p != "preferences:%"]

    def test_zero_confidence_preserved_not_coerced_to_one(self) -> None:
        """confidence=0.0 must not be coerced to 1.0 (falsy value treated as missing was a bug)."""
        row = _make_fact_row(confidence=0.0, decay_rate=0.0)
        result = _compute_effective_confidence(row)
        assert result == 0.0
