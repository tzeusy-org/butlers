"""Unit tests for the TriageRuleCache.

Tests cache loading, fail-open semantics, validation, and background refresh.
Uses an in-memory mock pool to avoid DB dependencies.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.tools.switchboard.triage.cache import TriageRuleCache, _validate_rule_row

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool_with_rows(rows: list[dict]) -> Any:
    """Create a mock pool where fetch returns proper dict-like records."""
    pool = AsyncMock()

    class FakeRecord(dict):
        pass

    pool.fetch = AsyncMock(return_value=[FakeRecord(r) for r in rows])
    return pool


def _valid_rule(
    *,
    id: str = "00000000-0000-0000-0000-000000000001",
    rule_type: str = "sender_domain",
    condition: dict | None = None,
    action: str = "route_to:finance",
    priority: int = 10,
) -> dict:
    return {
        "id": id,
        "rule_type": rule_type,
        "condition": condition or {"domain": "chase.com", "match": "exact"},
        "action": action,
        "priority": priority,
        "enabled": True,
        "created_by": "seed",
        "created_at": "2026-02-01T00:00:00Z",
        "updated_at": "2026-02-01T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# _validate_rule_row
# ---------------------------------------------------------------------------


class TestValidateRuleRow:
    def test_valid_row_returns_none(self) -> None:
        assert _validate_rule_row(_valid_rule()) is None

    def test_missing_id_returns_error(self) -> None:
        row = _valid_rule()
        del row["id"]
        error = _validate_rule_row(row)
        assert error is not None
        assert "id" in error

    def test_missing_action_returns_error(self) -> None:
        row = _valid_rule()
        row["action"] = None
        error = _validate_rule_row(row)
        assert error is not None

    def test_invalid_rule_type_returns_error(self) -> None:
        row = _valid_rule(rule_type="unknown_type")
        error = _validate_rule_row(row)
        assert error is not None
        assert "rule_type" in error

    def test_invalid_action_returns_error(self) -> None:
        row = _valid_rule(action="invalid_action")
        error = _validate_rule_row(row)
        assert error is not None
        assert "action" in error

    def test_route_to_action_is_valid(self) -> None:
        row = _valid_rule(action="route_to:finance")
        assert _validate_rule_row(row) is None

    def test_all_valid_simple_actions(self) -> None:
        for action in ("skip", "metadata_only", "low_priority_queue", "pass_through"):
            row = _valid_rule(action=action)
            assert _validate_rule_row(row) is None, f"Expected valid for action={action}"

    def test_non_dict_condition_returns_error(self) -> None:
        row = _valid_rule()
        row["condition"] = "not-a-dict"
        error = _validate_rule_row(row)
        assert error is not None
        assert "condition" in error


# ---------------------------------------------------------------------------
# TriageRuleCache — initial state
# ---------------------------------------------------------------------------


class TestTriageRuleCacheInitialState:
    def test_not_available_before_load(self) -> None:
        pool = _make_pool_with_rows([])
        cache = TriageRuleCache(pool)
        assert cache.available is False

    def test_empty_rules_before_load(self) -> None:
        pool = _make_pool_with_rows([])
        cache = TriageRuleCache(pool)
        assert cache.get_rules() == []

    def test_needs_refresh_when_not_loaded(self) -> None:
        pool = _make_pool_with_rows([])
        cache = TriageRuleCache(pool)
        assert cache.needs_refresh() is True


# ---------------------------------------------------------------------------
# TriageRuleCache.load()
# ---------------------------------------------------------------------------


class TestTriageRuleCacheLoad:
    async def test_load_populates_rules(self) -> None:
        rows = [_valid_rule(id="id-001", priority=10)]
        pool = _make_pool_with_rows(rows)
        cache = TriageRuleCache(pool)

        result = await cache.load()
        assert result is True
        assert cache.available is True
        rules = cache.get_rules()
        assert len(rules) == 1
        assert rules[0]["id"] == "id-001"

    async def test_load_skips_invalid_rows(self) -> None:
        rows = [
            _valid_rule(id="id-good", rule_type="sender_domain"),
            {**_valid_rule(id="id-bad"), "rule_type": "totally_invalid"},
        ]
        pool = _make_pool_with_rows(rows)
        cache = TriageRuleCache(pool)

        result = await cache.load()
        assert result is True
        rules = cache.get_rules()
        assert len(rules) == 1
        assert rules[0]["id"] == "id-good"

    async def test_load_fails_open_on_db_error(self) -> None:
        """On database error: stale rules preserved, returns False."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=Exception("DB connection lost"))
        cache = TriageRuleCache(pool)
        # Pre-populate with stale rules
        cache._rules = [_valid_rule(id="stale-id")]
        cache._loaded = True

        result = await cache.load()
        assert result is False
        assert cache.available is True
        # Stale rules still in place (fail-open)
        assert len(cache.get_rules()) == 1
        assert cache.get_rules()[0]["id"] == "stale-id"

    async def test_load_marks_as_available(self) -> None:
        pool = _make_pool_with_rows([])
        cache = TriageRuleCache(pool)
        assert cache.available is False

        await cache.load()
        assert cache.available is True

    async def test_load_returns_false_on_exception(self) -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("boom"))
        cache = TriageRuleCache(pool)

        result = await cache.load()
        assert result is False

    async def test_load_with_multiple_valid_rules(self) -> None:
        rows = [
            _valid_rule(id="id-001", priority=10),
            _valid_rule(
                id="id-002",
                priority=20,
                rule_type="sender_address",
                condition={"address": "x@y.com"},
                action="skip",
            ),
        ]
        pool = _make_pool_with_rows(rows)
        cache = TriageRuleCache(pool)

        await cache.load()
        rules = cache.get_rules()
        assert len(rules) == 2

    async def test_load_skips_non_dict_condition(self) -> None:
        row = _valid_rule(id="id-bad")
        row["condition"] = "not-a-dict"
        pool = _make_pool_with_rows([row])
        cache = TriageRuleCache(pool)

        await cache.load()
        assert len(cache.get_rules()) == 0


# ---------------------------------------------------------------------------
# TriageRuleCache.invalidate()
# ---------------------------------------------------------------------------


class TestTriageRuleCacheInvalidate:
    async def test_invalidate_sets_needs_refresh(self) -> None:
        rows = [_valid_rule()]
        pool = _make_pool_with_rows(rows)
        cache = TriageRuleCache(pool)
        await cache.load()

        assert cache.needs_refresh() is False
        cache.invalidate()
        assert cache.needs_refresh() is True

    async def test_invalidate_does_not_clear_rules(self) -> None:
        """Invalidation marks stale but does NOT clear cached rules."""
        rows = [_valid_rule(id="id-001")]
        pool = _make_pool_with_rows(rows)
        cache = TriageRuleCache(pool)
        await cache.load()

        cache.invalidate()
        # Rules still accessible until next load
        assert len(cache.get_rules()) == 1

    async def test_invalidate_preserves_available_flag(self) -> None:
        pool = _make_pool_with_rows([_valid_rule()])
        cache = TriageRuleCache(pool)
        await cache.load()
        assert cache.available is True

        cache.invalidate()
        assert cache.available is True


# ---------------------------------------------------------------------------
# TriageRuleCache — background refresh
# ---------------------------------------------------------------------------


class TestTriageRuleCacheBackgroundRefresh:
    async def test_start_and_stop_background_refresh(self) -> None:
        pool = _make_pool_with_rows([])
        cache = TriageRuleCache(pool, refresh_interval_s=1000)

        await cache.start_background_refresh()
        assert cache._refresh_task is not None
        assert not cache._refresh_task.done()

        await cache.stop_background_refresh()
        assert cache._refresh_task is None

    async def test_start_is_idempotent(self) -> None:
        pool = _make_pool_with_rows([])
        cache = TriageRuleCache(pool, refresh_interval_s=1000)

        await cache.start_background_refresh()
        task1 = cache._refresh_task

        await cache.start_background_refresh()
        task2 = cache._refresh_task

        # Same task — not a new one
        assert task1 is task2

        await cache.stop_background_refresh()

    async def test_stop_when_not_started_is_safe(self) -> None:
        pool = _make_pool_with_rows([])
        cache = TriageRuleCache(pool)
        # Should not raise
        await cache.stop_background_refresh()
