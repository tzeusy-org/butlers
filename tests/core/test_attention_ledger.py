"""Unit tests for butlers.core.attention_ledger (bu-qvnce.8 slice 1/2).

Covers the pure helpers and best-effort DB writer/reader in isolation. See
``tests/daemon/test_notify_attention_ledger.py`` and
``tests/modules/test_insight_attention_ledger.py`` for end-to-end wiring
tests at the notify()/delivery_cycle() boundaries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from butlers.core.attention_ledger import (
    URGENT_PRIORITY_THRESHOLD,
    count_attention_events_since,
    get_suppressing_context_signal,
    is_priority_urgent,
    normalize_priority,
    record_attention_event,
    record_owner_ingress_rollup,
)

pytestmark = pytest.mark.unit


class TestNormalizePriority:
    def test_label_high_maps_to_urgent_threshold(self):
        label, score = normalize_priority("high")
        assert label == "high"
        assert score == URGENT_PRIORITY_THRESHOLD

    def test_label_medium(self):
        label, score = normalize_priority("medium")
        assert label == "medium"
        assert score == 50

    def test_label_low(self):
        label, score = normalize_priority("low")
        assert label == "low"
        assert score == 20

    def test_int_in_range(self):
        label, score = normalize_priority(95)
        assert label == "95"
        assert score == 95

    def test_int_out_of_range_yields_none_score(self):
        label, score = normalize_priority(150)
        assert label == "150"
        assert score is None

        label, score = normalize_priority(0)
        assert label == "0"
        assert score is None

    def test_numeric_string(self):
        label, score = normalize_priority("42")
        assert label == "42"
        assert score == 42

    def test_non_numeric_string_degrades_gracefully(self):
        label, score = normalize_priority("urgent-ish")
        assert label == "urgent-ish"
        assert score is None

    def test_none_input(self):
        assert normalize_priority(None) == (None, None)

    def test_bool_is_not_treated_as_priority_int(self):
        # bool is an int subclass in Python; must not silently become score=1.
        label, score = normalize_priority(True)
        assert score is None
        assert label == "True"


class TestIsPriorityUrgent:
    def test_at_threshold_is_urgent(self):
        assert is_priority_urgent(URGENT_PRIORITY_THRESHOLD) is True

    def test_above_threshold_is_urgent(self):
        assert is_priority_urgent(100) is True

    def test_below_threshold_is_not_urgent(self):
        assert is_priority_urgent(URGENT_PRIORITY_THRESHOLD - 1) is False

    def test_none_is_not_urgent(self):
        assert is_priority_urgent(None) is False


class TestRecordAttentionEvent:
    async def test_none_pool_returns_none_without_error(self):
        result = await record_attention_event(
            None,
            origin_butler="health",
            source="notify",
            outcome="delivered",
        )
        assert result is None

    async def test_invalid_source_rejected(self):
        pool = AsyncMock()
        result = await record_attention_event(
            pool,
            origin_butler="health",
            source="bogus",  # type: ignore[arg-type]
            outcome="delivered",
        )
        assert result is None
        pool.fetchval.assert_not_awaited()

    async def test_invalid_outcome_rejected(self):
        pool = AsyncMock()
        result = await record_attention_event(
            pool,
            origin_butler="health",
            source="notify",
            outcome="bogus",  # type: ignore[arg-type]
        )
        assert result is None
        pool.fetchval.assert_not_awaited()

    async def test_successful_insert_returns_row_id(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value="row-id-123")

        result = await record_attention_event(
            pool,
            origin_butler="finance",
            source="insight",
            outcome="coalesced",
            channel="telegram",
            intent="insight",
            priority=80,
            dedup_key="finance:bill-due:abc:2026-01-01",
            reason=None,
            notification_ref="candidate-1",
            metadata={"foo": "bar"},
        )
        assert result == "row-id-123"

        pool.fetchval.assert_awaited_once()
        query, *params = pool.fetchval.await_args.args
        assert "INSERT INTO public.attention_ledger" in query
        assert params[0] == "finance"
        assert params[1] == "insight"
        assert params[7] == "coalesced"

    async def test_db_error_swallowed_and_logged(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(side_effect=Exception("relation does not exist"))

        result = await record_attention_event(
            pool,
            origin_butler="health",
            source="notify",
            outcome="delivered",
        )
        assert result is None


class TestCountAttentionEventsSince:
    async def test_none_pool_returns_zero_filled(self):
        counts = await count_attention_events_since(None, since=datetime.now(UTC))
        assert counts == {
            "coalesced": 0,
            "delivered": 0,
            "deferred": 0,
            "suppressed": 0,
        }

    async def test_zero_filled_even_when_some_outcomes_absent(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[
                {"outcome": "delivered", "n": 3},
                {"outcome": "suppressed", "n": 1},
            ]
        )
        counts = await count_attention_events_since(pool, since=datetime.now(UTC))
        assert counts == {
            "coalesced": 0,
            "delivered": 3,
            "deferred": 0,
            "suppressed": 1,
        }

    async def test_query_failure_fails_open_to_zero_filled(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=Exception("boom"))
        counts = await count_attention_events_since(pool, since=datetime.now(UTC))
        assert counts == {
            "coalesced": 0,
            "delivered": 0,
            "deferred": 0,
            "suppressed": 0,
        }


class TestGetSuppressingContextSignal:
    async def test_none_pool_returns_none(self):
        assert await get_suppressing_context_signal(None) is None

    async def test_no_active_signals_returns_none(self, monkeypatch):
        async def _fake_get_active_context(pool):
            return []

        monkeypatch.setattr("butlers.context_bus.get_active_context", _fake_get_active_context)
        assert await get_suppressing_context_signal(AsyncMock()) is None

    async def test_dnd_signal_detected(self, monkeypatch):
        from butlers.context_bus import ContextEntry

        async def _fake_get_active_context(pool):
            return [
                ContextEntry(
                    signal_type="dnd",
                    value=None,
                    set_by_butler="general",
                    set_at=datetime.now(UTC),
                    expires_at=datetime.now(UTC),
                    confidence=1.0,
                )
            ]

        monkeypatch.setattr("butlers.context_bus.get_active_context", _fake_get_active_context)
        assert await get_suppressing_context_signal(AsyncMock()) == "dnd"

    async def test_non_suppressing_signal_ignored(self, monkeypatch):
        from butlers.context_bus import ContextEntry

        async def _fake_get_active_context(pool):
            return [
                ContextEntry(
                    signal_type="traveling",
                    value=None,
                    set_by_butler="travel",
                    set_at=datetime.now(UTC),
                    expires_at=datetime.now(UTC),
                    confidence=1.0,
                )
            ]

        monkeypatch.setattr("butlers.context_bus.get_active_context", _fake_get_active_context)
        assert await get_suppressing_context_signal(AsyncMock()) is None

    async def test_exception_fails_open(self, monkeypatch):
        async def _raising(pool):
            raise RuntimeError("boom")

        monkeypatch.setattr("butlers.context_bus.get_active_context", _raising)
        assert await get_suppressing_context_signal(AsyncMock()) is None


class TestRecordOwnerIngressRollup:
    """bu-tdd4k.5: durable per-day owner-ingress counter."""

    async def test_none_pool_is_noop(self):
        # Must not raise — the pipeline's engagement gate is best-effort.
        await record_owner_ingress_rollup(None)

    async def test_upserts_current_utc_day_by_default(self):
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="INSERT 0 1")

        before = datetime.now(UTC).date()
        await record_owner_ingress_rollup(pool)
        after = datetime.now(UTC).date()

        pool.execute.assert_awaited_once()
        query, day = pool.execute.await_args.args
        assert "INSERT INTO public.attention_daily_rollup" in query
        assert "ON CONFLICT (day) DO UPDATE" in query
        assert before <= day <= after

    async def test_upserts_explicit_occurred_at_day(self):
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="INSERT 0 1")

        occurred_at = datetime(2026, 3, 4, 23, 30, tzinfo=UTC)
        await record_owner_ingress_rollup(pool, occurred_at=occurred_at)

        _, day = pool.execute.await_args.args
        assert day == occurred_at.date()

    async def test_db_error_swallowed_and_logged(self):
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=Exception("relation does not exist"))

        # Must not raise.
        await record_owner_ingress_rollup(pool)
