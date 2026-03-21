"""Tests for structured filters in search.py (_apply_filters, search, recall)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from butlers.modules.memory.search import _apply_filters

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _fact(
    *,
    id: str | None = None,
    scope: str = "global",
    entity_id: str | None = None,
    predicate: str = "likes",
    source_butler: str | None = None,
    created_at: datetime | None = None,
    retention_class: str = "operational",
    sensitivity: str = "normal",
) -> dict:
    return {
        "id": uuid.UUID(id) if id else uuid.uuid4(),
        "scope": scope,
        "entity_id": uuid.UUID(entity_id) if entity_id else None,
        "predicate": predicate,
        "source_butler": source_butler,
        "created_at": created_at or datetime(2026, 1, 15, tzinfo=UTC),
        "retention_class": retention_class,
        "sensitivity": sensitivity,
    }


# ---------------------------------------------------------------------------
# _apply_filters unit tests
# ---------------------------------------------------------------------------


class TestApplyFilters:
    """_apply_filters applies AND conditions from the filters dict."""

    def test_none_filters_returns_all(self):
        items = [_fact(), _fact()]
        assert _apply_filters(items, None) == items

    def test_empty_filters_returns_all(self):
        items = [_fact(), _fact()]
        assert _apply_filters(items, {}) == items

    def test_unknown_filter_keys_silently_ignored(self):
        items = [_fact(scope="health"), _fact(scope="global")]
        result = _apply_filters(items, {"totally_unknown_key": "value"})
        assert len(result) == 2  # nothing filtered

    def test_scope_filter(self):
        health = _fact(scope="health")
        global_ = _fact(scope="global")
        result = _apply_filters([health, global_], {"scope": "health"})
        assert len(result) == 1
        assert result[0]["scope"] == "health"

    def test_entity_id_filter(self):
        eid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        matching = _fact(entity_id=eid)
        non_matching = _fact(entity_id=None)
        result = _apply_filters([matching, non_matching], {"entity_id": eid})
        assert len(result) == 1
        assert str(result[0]["entity_id"]) == eid

    def test_entity_id_none_not_matched(self):
        eid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        none_entity = _fact(entity_id=None)
        result = _apply_filters([none_entity], {"entity_id": eid})
        assert result == []

    def test_predicate_filter(self):
        weight = _fact(predicate="weight")
        height = _fact(predicate="height")
        result = _apply_filters([weight, height], {"predicate": "weight"})
        assert len(result) == 1
        assert result[0]["predicate"] == "weight"

    def test_source_butler_filter(self):
        health_butler = _fact(source_butler="health")
        general = _fact(source_butler="general")
        result = _apply_filters([health_butler, general], {"source_butler": "health"})
        assert len(result) == 1
        assert result[0]["source_butler"] == "health"

    def test_time_from_filter(self):
        old = _fact(created_at=datetime(2025, 1, 1, tzinfo=UTC))
        recent = _fact(created_at=datetime(2026, 6, 1, tzinfo=UTC))
        result = _apply_filters([old, recent], {"time_from": "2026-01-01T00:00:00+00:00"})
        assert len(result) == 1
        assert result[0]["created_at"].year == 2026

    def test_time_to_filter(self):
        old = _fact(created_at=datetime(2025, 1, 1, tzinfo=UTC))
        recent = _fact(created_at=datetime(2026, 6, 1, tzinfo=UTC))
        result = _apply_filters([old, recent], {"time_to": "2025-12-31T23:59:59+00:00"})
        assert len(result) == 1
        assert result[0]["created_at"].year == 2025

    def test_time_from_z_suffix_handled(self):
        recent = _fact(created_at=datetime(2026, 3, 1, tzinfo=UTC))
        result = _apply_filters([recent], {"time_from": "2026-01-01T00:00:00Z"})
        assert len(result) == 1

    def test_invalid_time_from_silently_skipped(self):
        items = [_fact(), _fact()]
        # Invalid ISO string — should not crash, should not filter
        result = _apply_filters(items, {"time_from": "not-a-date"})
        assert len(result) == 2

    def test_retention_class_filter(self):
        health_log = _fact(retention_class="health_log")
        operational = _fact(retention_class="operational")
        result = _apply_filters([health_log, operational], {"retention_class": "health_log"})
        assert len(result) == 1
        assert result[0]["retention_class"] == "health_log"

    def test_sensitivity_filter(self):
        pii = _fact(sensitivity="pii")
        normal = _fact(sensitivity="normal")
        result = _apply_filters([pii, normal], {"sensitivity": "pii"})
        assert len(result) == 1
        assert result[0]["sensitivity"] == "pii"

    def test_multiple_filters_are_and_conditions(self):
        match = _fact(scope="health", predicate="weight", retention_class="health_log")
        wrong_scope = _fact(scope="global", predicate="weight", retention_class="health_log")
        wrong_pred = _fact(scope="health", predicate="mood", retention_class="health_log")

        result = _apply_filters(
            [match, wrong_scope, wrong_pred],
            {"scope": "health", "predicate": "weight"},
        )
        assert len(result) == 1
        assert result[0] is match

    def test_empty_results_with_no_matches(self):
        items = [_fact(scope="global"), _fact(scope="work")]
        result = _apply_filters(items, {"scope": "nonexistent"})
        assert result == []

    def test_created_at_none_excluded_by_time_from(self):
        item_with_no_date = _fact()
        item_with_no_date["created_at"] = None
        result = _apply_filters([item_with_no_date], {"time_from": "2020-01-01T00:00:00Z"})
        assert result == []

    def test_mixed_known_and_unknown_keys(self):
        matching = _fact(scope="health")
        non_matching = _fact(scope="global")
        # unknown key "future_feature" should be silently ignored
        result = _apply_filters(
            [matching, non_matching],
            {"scope": "health", "future_feature": "ignored"},
        )
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Tests for filters wiring through memory_search and memory_recall in reading.py
# ---------------------------------------------------------------------------


class TestFiltersWiredThroughReading:
    """Verify filters param flows from reading.py to search.py."""

    async def test_memory_search_passes_filters(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from butlers.modules.memory.tools import _helpers
        from butlers.modules.memory.tools.reading import memory_search

        pool = AsyncMock()
        engine = MagicMock()

        with patch.object(
            _helpers._search,
            "search",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_search:
            await memory_search(
                pool,
                engine,
                "query",
                filters={"scope": "health"},
            )
            call_kwargs = mock_search.call_args[1]
            assert call_kwargs.get("filters") == {"scope": "health"}

    async def test_memory_recall_passes_filters_and_tenant_id(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from butlers.modules.memory.tools import _helpers
        from butlers.modules.memory.tools.reading import memory_recall

        pool = AsyncMock()
        engine = MagicMock()

        with patch.object(
            _helpers._search,
            "recall",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_recall:
            await memory_recall(
                pool,
                engine,
                "topic",
                filters={"predicate": "weight"},
                request_context={"tenant_id": "custom"},
            )
            call_kwargs = mock_recall.call_args[1]
            assert call_kwargs.get("filters") == {"predicate": "weight"}
            assert call_kwargs.get("tenant_id") == "custom"

    async def test_memory_recall_defaults_to_shared_tenant(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from butlers.modules.memory.tools import _helpers
        from butlers.modules.memory.tools.reading import memory_recall

        pool = AsyncMock()
        engine = MagicMock()

        with patch.object(
            _helpers._search,
            "recall",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_recall:
            await memory_recall(pool, engine, "topic")
            call_kwargs = mock_recall.call_args[1]
            assert call_kwargs.get("tenant_id") == "shared"
