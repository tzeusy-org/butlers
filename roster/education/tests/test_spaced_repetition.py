"""Unit tests for education butler SM-2 spaced repetition engine.

All tests mock the asyncpg pool/connection objects — no live database required.

Coverage:
- sm2_update: interval progression (rep0/rep1/rep2+), ease factor formula,
  ease factor floor, failed recall reset, quality examples
- spaced_repetition_record_response: happy path, batch cap, status transition,
  schedule naming, node not found
- spaced_repetition_pending_reviews: due nodes returned, overdue excluded
- spaced_repetition_schedule_cleanup: active map no-op, completed map deletes,
  abandoned map deletes, idempotent
- _datetime_to_cron: correct minute/hour/day/month
- _determine_sr_status: reviewing regression, mastered regression, no-op cases
"""

from __future__ import annotations

import uuid
import warnings
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers: mock asyncpg pool / connection builder
# (same pattern as test_mastery.py)
# ---------------------------------------------------------------------------


class _MockRecord:
    """Minimal asyncpg.Record-like object backed by a dict."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


def _make_row(data: dict[str, Any]) -> _MockRecord:
    return _MockRecord(data)


def _make_conn(
    *,
    fetchrow_returns: list[Any] | None = None,
    fetch_returns: list[Any] | None = None,
    fetchval_returns: list[Any] | None = None,
    execute_returns: list[str] | None = None,
) -> AsyncMock:
    """Build an AsyncMock behaving like an asyncpg connection."""
    conn = AsyncMock()

    if fetchrow_returns is not None:
        conn.fetchrow = AsyncMock(side_effect=list(fetchrow_returns))
    else:
        conn.fetchrow = AsyncMock(return_value=None)

    if fetch_returns is not None:
        conn.fetch = AsyncMock(side_effect=list(fetch_returns))
    else:
        conn.fetch = AsyncMock(return_value=[])

    if fetchval_returns is not None:
        conn.fetchval = AsyncMock(side_effect=list(fetchval_returns))
    else:
        conn.fetchval = AsyncMock(return_value=0)

    if execute_returns is not None:
        conn.execute = AsyncMock(side_effect=list(execute_returns))
    else:
        conn.execute = AsyncMock(return_value="UPDATE 1")

    # Wire transaction() as async context manager
    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)

    return conn


def _make_pool(
    *,
    fetchrow_returns: list[Any] | None = None,
    fetch_returns: list[Any] | None = None,
    fetchval_returns: list[Any] | None = None,
    execute_returns: list[str] | None = None,
) -> AsyncMock:
    """Build an AsyncMock behaving like an asyncpg.Pool (direct pool calls)."""
    pool = AsyncMock()

    if fetchrow_returns is not None:
        pool.fetchrow = AsyncMock(side_effect=list(fetchrow_returns))
    else:
        pool.fetchrow = AsyncMock(return_value=None)

    if fetch_returns is not None:
        pool.fetch = AsyncMock(side_effect=list(fetch_returns))
    else:
        pool.fetch = AsyncMock(return_value=[])

    if fetchval_returns is not None:
        pool.fetchval = AsyncMock(side_effect=list(fetchval_returns))
    else:
        pool.fetchval = AsyncMock(return_value=0)

    if execute_returns is not None:
        pool.execute = AsyncMock(side_effect=list(execute_returns))
    else:
        pool.execute = AsyncMock(return_value="UPDATE 1")

    return pool


def _make_pool_with_conn(conn: AsyncMock) -> MagicMock:
    """Build a pool whose acquire() context manager yields conn."""
    pool = MagicMock()
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)

    # Direct pool methods (used outside transaction in some tests)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock(return_value="UPDATE 1")

    return pool


def _node_row(
    node_id: str | None = None,
    mind_map_id: str | None = None,
    label: str = "Test Concept",
    ease_factor: float = 2.5,
    repetitions: int = 0,
    next_review_at: datetime | None = None,
    last_reviewed_at: datetime | None = None,
    mastery_status: str = "reviewing",
) -> _MockRecord:
    return _make_row(
        {
            "id": node_id or str(uuid.uuid4()),
            "label": label,
            "ease_factor": ease_factor,
            "repetitions": repetitions,
            "next_review_at": next_review_at,
            "last_reviewed_at": last_reviewed_at,
            "mastery_status": mastery_status,
        }
    )


# ---------------------------------------------------------------------------
# Tests: sm2_update — pure function
# ---------------------------------------------------------------------------


class TestSm2Update:
    """Tests for the SM-2 interval/ease computation."""

    def test_rep0_quality_pass_gives_1_day(self) -> None:
        from butlers.tools.education.spaced_repetition import sm2_update

        result = sm2_update(ease_factor=2.5, repetitions=0, quality=3)
        assert result["interval_days"] == 1.0
        assert result["new_repetitions"] == 1

    def test_rep1_quality_pass_gives_6_days(self) -> None:
        from butlers.tools.education.spaced_repetition import sm2_update

        result = sm2_update(ease_factor=2.5, repetitions=1, quality=4)
        assert result["interval_days"] == 6.0
        assert result["new_repetitions"] == 2

    def test_rep2_quality_pass_uses_last_interval_times_ef(self) -> None:
        from butlers.tools.education.spaced_repetition import sm2_update

        # last_interval=6, ef=2.5 → next interval = 6 * new_ef (quality=4 keeps ef=2.5)
        result = sm2_update(ease_factor=2.5, repetitions=2, quality=4, last_interval=6.0)
        expected_interval = 6.0 * result["new_ease_factor"]
        assert abs(result["interval_days"] - expected_interval) < 1e-9
        assert result["new_repetitions"] == 3

    def test_rep2_fallback_interval_when_last_interval_none(self) -> None:
        from butlers.tools.education.spaced_repetition import sm2_update

        # last_interval=None → uses 6.0 as fallback
        result = sm2_update(ease_factor=2.5, repetitions=2, quality=4, last_interval=None)
        expected_interval = 6.0 * result["new_ease_factor"]
        assert abs(result["interval_days"] - expected_interval) < 1e-9

    def test_quality_5_increases_ease_factor(self) -> None:
        """quality=5, ef=2.5 → new_ef=2.6"""
        from butlers.tools.education.spaced_repetition import sm2_update

        result = sm2_update(ease_factor=2.5, repetitions=0, quality=5)
        assert abs(result["new_ease_factor"] - 2.6) < 1e-9

    def test_quality_4_leaves_ease_factor_unchanged(self) -> None:
        """quality=4, ef=2.5 → new_ef=2.5 (delta=0)"""
        from butlers.tools.education.spaced_repetition import sm2_update

        result = sm2_update(ease_factor=2.5, repetitions=0, quality=4)
        assert abs(result["new_ease_factor"] - 2.5) < 1e-9

    def test_quality_0_decreases_ease_factor_to_1_7(self) -> None:
        """quality=0, ef=2.5 → new_ef=1.7"""
        from butlers.tools.education.spaced_repetition import sm2_update

        # delta = 0.1 - 5*(0.08 + 5*0.02) = 0.1 - 5*(0.18) = 0.1 - 0.9 = -0.8
        result = sm2_update(ease_factor=2.5, repetitions=0, quality=0)
        assert abs(result["new_ease_factor"] - 1.7) < 1e-9

    def test_ease_factor_floor_at_1_3(self) -> None:
        """EF cannot go below 1.3 even with repeated failures."""
        from butlers.tools.education.spaced_repetition import sm2_update

        result = sm2_update(ease_factor=1.3, repetitions=0, quality=0)
        assert result["new_ease_factor"] >= 1.3

    def test_ease_factor_floored_when_would_go_below_1_3(self) -> None:
        """EF=1.35 with quality=0 would go below 1.3; floor applies."""
        from butlers.tools.education.spaced_repetition import sm2_update

        result = sm2_update(ease_factor=1.35, repetitions=0, quality=0)
        assert result["new_ease_factor"] == 1.3

    def test_quality_below_3_resets_repetitions_to_0(self) -> None:
        """Failed recall (quality<3) resets repetitions to 0."""
        from butlers.tools.education.spaced_repetition import sm2_update

        result = sm2_update(ease_factor=2.5, repetitions=5, quality=2)
        assert result["new_repetitions"] == 0
        assert result["interval_days"] == 1.0

    def test_quality_below_3_still_adjusts_ease_factor(self) -> None:
        """Ease factor is penalized even on failure."""
        from butlers.tools.education.spaced_repetition import sm2_update

        result = sm2_update(ease_factor=2.5, repetitions=3, quality=1)
        # delta = 0.1 - 4*(0.08 + 4*0.02) = 0.1 - 4*0.16 = 0.1 - 0.64 = -0.54
        expected = max(1.3, 2.5 - 0.54)
        assert abs(result["new_ease_factor"] - expected) < 1e-9

    def test_quality_3_is_success_threshold(self) -> None:
        """quality=3 is the minimum passing quality (repetitions increment)."""
        from butlers.tools.education.spaced_repetition import sm2_update

        result = sm2_update(ease_factor=2.5, repetitions=0, quality=3)
        assert result["new_repetitions"] == 1
        assert result["interval_days"] == 1.0

    def test_quality_2_is_failure_threshold(self) -> None:
        """quality=2 resets repetitions."""
        from butlers.tools.education.spaced_repetition import sm2_update

        result = sm2_update(ease_factor=2.5, repetitions=3, quality=2)
        assert result["new_repetitions"] == 0

    def test_large_repetitions_use_last_interval_times_ef(self) -> None:
        """Rep 10 should multiply last_interval by new_ef."""
        from butlers.tools.education.spaced_repetition import sm2_update

        result = sm2_update(ease_factor=2.5, repetitions=10, quality=5, last_interval=30.0)
        expected = 30.0 * result["new_ease_factor"]
        assert abs(result["interval_days"] - expected) < 1e-9


# ---------------------------------------------------------------------------
# Tests: _datetime_to_cron
# ---------------------------------------------------------------------------


class TestDatetimeToCron:
    def test_known_datetime(self) -> None:
        from butlers.tools.education.spaced_repetition import _datetime_to_cron

        dt = datetime(2026, 3, 5, 14, 30, tzinfo=UTC)
        assert _datetime_to_cron(dt) == "30 14 5 3 *"

    def test_midnight(self) -> None:
        from butlers.tools.education.spaced_repetition import _datetime_to_cron

        dt = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        assert _datetime_to_cron(dt) == "0 0 1 1 *"

    def test_end_of_year(self) -> None:
        from butlers.tools.education.spaced_repetition import _datetime_to_cron

        dt = datetime(2026, 12, 31, 23, 59, tzinfo=UTC)
        assert _datetime_to_cron(dt) == "59 23 31 12 *"


# ---------------------------------------------------------------------------
# Tests: _determine_sr_status
# ---------------------------------------------------------------------------


class TestDetermineSrStatus:
    def test_reviewing_fail_returns_learning(self) -> None:
        from butlers.tools.education.spaced_repetition import _determine_sr_status

        assert _determine_sr_status("reviewing", 2) == "learning"

    def test_reviewing_pass_returns_none(self) -> None:
        from butlers.tools.education.spaced_repetition import _determine_sr_status

        assert _determine_sr_status("reviewing", 3) is None

    def test_mastered_fail_returns_reviewing(self) -> None:
        from butlers.tools.education.spaced_repetition import _determine_sr_status

        assert _determine_sr_status("mastered", 1) == "reviewing"

    def test_mastered_pass_returns_none(self) -> None:
        from butlers.tools.education.spaced_repetition import _determine_sr_status

        assert _determine_sr_status("mastered", 4) is None

    def test_learning_fail_returns_none(self) -> None:
        """Learning status is managed by mastery_record_response, not SR."""
        from butlers.tools.education.spaced_repetition import _determine_sr_status

        assert _determine_sr_status("learning", 0) is None

    def test_learning_pass_returns_none(self) -> None:
        from butlers.tools.education.spaced_repetition import _determine_sr_status

        assert _determine_sr_status("learning", 5) is None


# ---------------------------------------------------------------------------
# Tests: spaced_repetition_record_response
# ---------------------------------------------------------------------------


class TestSpacedRepetitionRecordResponse:
    """Tests for spaced_repetition_record_response."""

    async def test_happy_path_rep0_creates_individual_schedule(self) -> None:
        """rep=0, quality=4 → interval=1d, new rep=1, individual schedule."""
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_record_response,
        )

        node_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())

        conn = _make_conn(
            fetchrow_returns=[
                _node_row(
                    node_id=node_id,
                    mind_map_id=map_id,
                    ease_factor=2.5,
                    repetitions=0,
                    mastery_status="reviewing",
                )
            ],
            fetchval_returns=[0],  # schedule count = 0
        )
        pool = _make_pool_with_conn(conn)

        schedule_create = AsyncMock(return_value="sched-1")
        schedule_delete = AsyncMock()

        result = await spaced_repetition_record_response(
            pool,
            node_id=node_id,
            mind_map_id=map_id,
            quality=4,
            schedule_create=schedule_create,
            schedule_delete=schedule_delete,
        )

        assert result["repetitions"] == 1
        assert result["interval_days"] == 1.0
        assert abs(result["ease_factor"] - 2.5) < 1e-9
        assert "next_review_at" in result

        # schedule_create should be called with an individual schedule name
        assert schedule_create.call_count == 1
        call_kwargs = schedule_create.call_args.kwargs
        assert call_kwargs["name"] == f"review-{node_id}-rep1"
        assert call_kwargs["dispatch_mode"] == "prompt"
        assert "until_at" in call_kwargs

    async def test_happy_path_rep1_gives_6d_interval(self) -> None:
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_record_response,
        )

        node_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())

        conn = _make_conn(
            fetchrow_returns=[
                _node_row(
                    node_id=node_id,
                    mind_map_id=map_id,
                    ease_factor=2.5,
                    repetitions=1,
                    mastery_status="reviewing",
                )
            ],
            fetchval_returns=[0],
        )
        pool = _make_pool_with_conn(conn)
        schedule_create = AsyncMock(return_value="sched-2")
        schedule_delete = AsyncMock()

        result = await spaced_repetition_record_response(
            pool,
            node_id=node_id,
            mind_map_id=map_id,
            quality=3,
            schedule_create=schedule_create,
            schedule_delete=schedule_delete,
        )

        assert result["interval_days"] == 6.0
        assert result["repetitions"] == 2

    async def test_failed_recall_resets_repetitions(self) -> None:
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_record_response,
        )

        node_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())

        conn = _make_conn(
            fetchrow_returns=[
                _node_row(
                    node_id=node_id,
                    mind_map_id=map_id,
                    ease_factor=2.5,
                    repetitions=5,
                    mastery_status="reviewing",
                )
            ],
            fetchval_returns=[0],
        )
        pool = _make_pool_with_conn(conn)
        schedule_create = AsyncMock(return_value="sched-3")
        schedule_delete = AsyncMock()

        result = await spaced_repetition_record_response(
            pool,
            node_id=node_id,
            mind_map_id=map_id,
            quality=1,
            schedule_create=schedule_create,
            schedule_delete=schedule_delete,
        )

        assert result["repetitions"] == 0
        assert result["interval_days"] == 1.0

    async def test_invalid_quality_raises(self) -> None:
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_record_response,
        )

        pool = _make_pool_with_conn(_make_conn())
        with pytest.raises(ValueError, match="quality must be between 0 and 5"):
            await spaced_repetition_record_response(
                pool,
                node_id=str(uuid.uuid4()),
                mind_map_id=str(uuid.uuid4()),
                quality=6,
                schedule_create=AsyncMock(),
                schedule_delete=AsyncMock(),
            )

    async def test_node_not_found_raises(self) -> None:
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_record_response,
        )

        conn = _make_conn(fetchrow_returns=[None])
        pool = _make_pool_with_conn(conn)

        with pytest.raises(ValueError, match="not found"):
            await spaced_repetition_record_response(
                pool,
                node_id=str(uuid.uuid4()),
                mind_map_id=str(uuid.uuid4()),
                quality=4,
                schedule_create=AsyncMock(),
                schedule_delete=AsyncMock(),
            )

    async def test_batch_cap_creates_batch_schedule(self) -> None:
        """When >= 20 pending reviews exist, create a batch schedule instead."""
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_record_response,
        )

        node_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())

        conn = _make_conn(
            fetchrow_returns=[
                _node_row(
                    node_id=node_id,
                    mind_map_id=map_id,
                    ease_factor=2.5,
                    repetitions=0,
                    mastery_status="reviewing",
                )
            ],
            fetchval_returns=[20],  # 20 pending schedules → batch cap reached
        )
        pool = _make_pool_with_conn(conn)
        schedule_create = AsyncMock(return_value="sched-batch")
        schedule_delete = AsyncMock()

        await spaced_repetition_record_response(
            pool,
            node_id=node_id,
            mind_map_id=map_id,
            quality=4,
            schedule_create=schedule_create,
            schedule_delete=schedule_delete,
        )

        call_kwargs = schedule_create.call_args.kwargs
        assert call_kwargs["name"] == f"review-{map_id}-batch"

    async def test_status_transition_reviewing_fail_to_learning(self) -> None:
        """reviewing + quality<3 → mastery_status set to 'learning'."""
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_record_response,
        )

        node_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())

        conn = _make_conn(
            fetchrow_returns=[
                _node_row(
                    node_id=node_id,
                    mind_map_id=map_id,
                    ease_factor=2.5,
                    repetitions=3,
                    mastery_status="reviewing",
                )
            ],
            fetchval_returns=[0],
        )
        pool = _make_pool_with_conn(conn)
        schedule_create = AsyncMock(return_value="sched")
        schedule_delete = AsyncMock()

        await spaced_repetition_record_response(
            pool,
            node_id=node_id,
            mind_map_id=map_id,
            quality=2,
            schedule_create=schedule_create,
            schedule_delete=schedule_delete,
        )

        # Verify that conn.execute was called with a SQL containing mastery_status
        execute_calls = conn.execute.call_args_list
        assert len(execute_calls) >= 1
        first_call_sql = execute_calls[0].args[0]
        assert "mastery_status" in first_call_sql

    async def test_no_status_transition_when_reviewing_pass(self) -> None:
        """reviewing + quality>=3 → no mastery_status update in SQL."""
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_record_response,
        )

        node_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())

        conn = _make_conn(
            fetchrow_returns=[
                _node_row(
                    node_id=node_id,
                    mind_map_id=map_id,
                    ease_factor=2.5,
                    repetitions=1,
                    mastery_status="reviewing",
                )
            ],
            fetchval_returns=[0],
        )
        pool = _make_pool_with_conn(conn)
        schedule_create = AsyncMock(return_value="sched")
        schedule_delete = AsyncMock()

        await spaced_repetition_record_response(
            pool,
            node_id=node_id,
            mind_map_id=map_id,
            quality=4,
            schedule_create=schedule_create,
            schedule_delete=schedule_delete,
        )

        execute_calls = conn.execute.call_args_list
        assert len(execute_calls) >= 1
        first_call_sql = execute_calls[0].args[0]
        assert "mastery_status" not in first_call_sql

    async def test_schedule_name_includes_rep_number(self) -> None:
        """Schedule name must be review-{node_id}-rep{new_reps}."""
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_record_response,
        )

        node_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())

        conn = _make_conn(
            fetchrow_returns=[
                _node_row(
                    node_id=node_id,
                    mind_map_id=map_id,
                    repetitions=2,
                    ease_factor=2.5,
                )
            ],
            fetchval_returns=[0],
        )
        pool = _make_pool_with_conn(conn)
        schedule_create = AsyncMock(return_value="sched")
        schedule_delete = AsyncMock()

        result = await spaced_repetition_record_response(
            pool,
            node_id=node_id,
            mind_map_id=map_id,
            quality=5,
            schedule_create=schedule_create,
            schedule_delete=schedule_delete,
        )

        new_reps = result["repetitions"]
        call_kwargs = schedule_create.call_args.kwargs
        assert call_kwargs["name"] == f"review-{node_id}-rep{new_reps}"

    async def test_until_at_is_24h_after_next_review(self) -> None:
        """until_at must be next_review_at + 24 hours."""
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_record_response,
        )

        node_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())

        conn = _make_conn(
            fetchrow_returns=[
                _node_row(
                    node_id=node_id,
                    mid_map_id=map_id,
                    ease_factor=2.5,
                    repetitions=0,
                )
            ]
            if False
            else [
                _node_row(
                    node_id=node_id,
                    ease_factor=2.5,
                    repetitions=0,
                    mastery_status="reviewing",
                )
            ],
            fetchval_returns=[0],
        )
        pool = _make_pool_with_conn(conn)
        schedule_create = AsyncMock(return_value="sched")
        schedule_delete = AsyncMock()

        result = await spaced_repetition_record_response(
            pool,
            node_id=node_id,
            mind_map_id=map_id,
            quality=4,
            schedule_create=schedule_create,
            schedule_delete=schedule_delete,
        )

        next_review = datetime.fromisoformat(result["next_review_at"])
        call_kwargs = schedule_create.call_args.kwargs
        until_at = datetime.fromisoformat(call_kwargs["until_at"])
        delta = until_at - next_review
        assert abs(delta.total_seconds() - 86400) < 2  # within 2 seconds

    async def test_last_interval_computed_from_timestamps(self) -> None:
        """last_interval should be derived from next_review_at - last_reviewed_at."""
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_record_response,
        )

        node_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())

        now = datetime.now(tz=UTC)
        last_reviewed = now - timedelta(days=6)
        next_review = now  # so last_interval = 6 days

        conn = _make_conn(
            fetchrow_returns=[
                _node_row(
                    node_id=node_id,
                    ease_factor=2.5,
                    repetitions=2,
                    next_review_at=next_review,
                    last_reviewed_at=last_reviewed,
                    mastery_status="reviewing",
                )
            ],
            fetchval_returns=[0],
        )
        pool = _make_pool_with_conn(conn)
        schedule_create = AsyncMock(return_value="sched")
        schedule_delete = AsyncMock()

        result = await spaced_repetition_record_response(
            pool,
            node_id=node_id,
            mind_map_id=map_id,
            quality=4,
            schedule_create=schedule_create,
            schedule_delete=schedule_delete,
        )

        # last_interval=6, ef=2.5 (quality=4 → no change), new interval=6*2.5=15
        assert abs(result["interval_days"] - 15.0) < 1e-9


# ---------------------------------------------------------------------------
# Tests: spaced_repetition_pending_reviews
# ---------------------------------------------------------------------------


class TestSpacedRepetitionPendingReviews:
    async def test_returns_due_nodes(self) -> None:
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_pending_reviews,
        )

        map_id = str(uuid.uuid4())
        node_id_1 = str(uuid.uuid4())
        node_id_2 = str(uuid.uuid4())
        overdue = datetime.now(tz=UTC) - timedelta(hours=1)

        pool = _make_pool(
            fetch_returns=[
                [
                    _make_row(
                        {
                            "node_id": node_id_1,
                            "label": "Concept A",
                            "ease_factor": 2.5,
                            "repetitions": 2,
                            "next_review_at": overdue,
                            "mastery_status": "reviewing",
                        }
                    ),
                    _make_row(
                        {
                            "node_id": node_id_2,
                            "label": "Concept B",
                            "ease_factor": 2.3,
                            "repetitions": 1,
                            "next_review_at": overdue,
                            "mastery_status": "reviewing",
                        }
                    ),
                ]
            ]
        )

        result = await spaced_repetition_pending_reviews(pool, map_id)
        assert len(result) == 2
        assert result[0]["label"] == "Concept A"
        assert result[1]["label"] == "Concept B"

    async def test_returns_empty_when_no_due_nodes(self) -> None:
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_pending_reviews,
        )

        pool = _make_pool(fetch_returns=[[]])
        result = await spaced_repetition_pending_reviews(pool, str(uuid.uuid4()))
        assert result == []

    async def test_iso_formats_datetime_fields(self) -> None:
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_pending_reviews,
        )

        map_id = str(uuid.uuid4())
        overdue = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)

        pool = _make_pool(
            fetch_returns=[
                [
                    _make_row(
                        {
                            "node_id": str(uuid.uuid4()),
                            "label": "Concept",
                            "ease_factor": 2.5,
                            "repetitions": 1,
                            "next_review_at": overdue,
                            "mastery_status": "reviewing",
                        }
                    )
                ]
            ]
        )

        result = await spaced_repetition_pending_reviews(pool, map_id)
        assert len(result) == 1
        # next_review_at should be ISO string, not a datetime object
        assert isinstance(result[0]["next_review_at"], str)
        assert "2026-01-15" in result[0]["next_review_at"]


# ---------------------------------------------------------------------------
# Tests: spaced_repetition_schedule_cleanup
# ---------------------------------------------------------------------------


class TestSpacedRepetitionScheduleCleanup:
    async def test_active_map_returns_0_with_warning(self) -> None:
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_schedule_cleanup,
        )

        map_id = str(uuid.uuid4())
        pool = _make_pool(fetchrow_returns=[_make_row({"status": "active"})])

        schedule_delete = AsyncMock()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = await spaced_repetition_schedule_cleanup(
                pool, map_id, schedule_delete=schedule_delete
            )

        assert result == 0
        assert schedule_delete.call_count == 0
        assert any("active" in str(warning.message) for warning in w)

    async def test_completed_map_deletes_node_schedules(self) -> None:
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_schedule_cleanup,
        )

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        schedule_name = f"review-{node_id}-rep2"

        pool = _make_pool(
            fetchrow_returns=[_make_row({"status": "completed"})],
            fetch_returns=[
                # node list
                [_make_row({"id": node_id})],
                # node schedule names
                [_make_row({"name": schedule_name})],
                # batch schedule names
                [],
            ],
        )

        schedule_delete = AsyncMock()

        result = await spaced_repetition_schedule_cleanup(
            pool, map_id, schedule_delete=schedule_delete
        )

        assert result == 1
        schedule_delete.assert_called_once_with(schedule_name)

    async def test_abandoned_map_deletes_batch_schedule(self) -> None:
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_schedule_cleanup,
        )

        map_id = str(uuid.uuid4())
        batch_name = f"review-{map_id}-batch"

        pool = _make_pool(
            fetchrow_returns=[_make_row({"status": "abandoned"})],
            fetch_returns=[
                # node list — empty
                [],
                # batch schedule names
                [_make_row({"name": batch_name})],
            ],
        )

        schedule_delete = AsyncMock()

        result = await spaced_repetition_schedule_cleanup(
            pool, map_id, schedule_delete=schedule_delete
        )

        assert result == 1
        schedule_delete.assert_called_once_with(batch_name)

    async def test_map_not_found_returns_0_with_warning(self) -> None:
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_schedule_cleanup,
        )

        pool = _make_pool(fetchrow_returns=[None])
        schedule_delete = AsyncMock()

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = await spaced_repetition_schedule_cleanup(
                pool, str(uuid.uuid4()), schedule_delete=schedule_delete
            )

        assert result == 0
        assert schedule_delete.call_count == 0

    async def test_idempotent_no_schedules(self) -> None:
        """Calling cleanup when no schedules exist should return 0."""
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_schedule_cleanup,
        )

        map_id = str(uuid.uuid4())

        pool = _make_pool(
            fetchrow_returns=[_make_row({"status": "completed"})],
            fetch_returns=[
                [],  # nodes
                [],  # batch schedules
            ],
        )

        schedule_delete = AsyncMock()
        result = await spaced_repetition_schedule_cleanup(
            pool, map_id, schedule_delete=schedule_delete
        )

        assert result == 0
        assert schedule_delete.call_count == 0

    async def test_completed_map_deletes_both_node_and_batch_schedules(self) -> None:
        from butlers.tools.education.spaced_repetition import (
            spaced_repetition_schedule_cleanup,
        )

        map_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())
        node_sched = f"review-{node_id}-rep3"
        batch_sched = f"review-{map_id}-batch"

        pool = _make_pool(
            fetchrow_returns=[_make_row({"status": "completed"})],
            fetch_returns=[
                [_make_row({"id": node_id})],
                [_make_row({"name": node_sched})],
                [_make_row({"name": batch_sched})],
            ],
        )

        schedule_delete = AsyncMock()
        result = await spaced_repetition_schedule_cleanup(
            pool, map_id, schedule_delete=schedule_delete
        )

        assert result == 2
        deleted_names = {c.args[0] for c in schedule_delete.call_args_list}
        assert node_sched in deleted_names
        assert batch_sched in deleted_names
