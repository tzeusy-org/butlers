"""Priority-aware quiet-hours bypass + context-bus gating for delivery_cycle().

Covers bu-qvnce.8 slices 1-2 (the "One attention ledger" move, RFC 0011
Amendment 1): the insight-delivery-cycle side of the attention policy.

- A candidate at/above URGENT_PRIORITY_THRESHOLD (90) is delivered even
  during quiet hours or an active context-bus dnd/sleeping signal.
- Routine (sub-threshold) candidates are left untouched (still 'pending')
  rather than delivered or silently dropped — they get another chance on a
  later, non-suppressed cycle.
- When no urgent candidate is pending, a fully suppressed cycle behaves as
  before: ``skipped=True``, nothing delivered.

Ledger-row content assertions (the actual INSERT into
``public.attention_ledger``) live in
``tests/integration/test_attention_ledger_roundtrip.py`` against a fully
migrated database — the ``insight_pool`` fixture here only creates the four
insight tables (see ``create_insight_tables``), so ledger writes fail open
silently, which is itself part of the degraded-honesty contract this test
file does not need to re-verify.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

_docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
    pytest.mark.integration,
]


def _future(days: int = 7) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


@pytest.fixture
async def insight_pool(provisioned_postgres_pool):
    from butlers.tools.switchboard.insight.broker import create_insight_tables

    async with provisioned_postgres_pool() as pool:
        await create_insight_tables(pool)
        yield pool


async def _insert_candidate(pool, *, dedup_key: str, priority: int, origin_butler: str = "health"):
    await pool.execute(
        """
        INSERT INTO insight_candidates
            (origin_butler, priority, category, dedup_key, expires_at, message, status)
        VALUES ($1, $2, 'health', $3, $4, 'candidate message', 'pending')
        """,
        origin_butler,
        priority,
        dedup_key,
        _future(),
    )


class TestUrgentBypassesQuietHours:
    async def test_urgent_delivered_routine_stays_pending(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        # Quiet hours: entire day (0-23), so _is_quiet_hours is always True.
        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity, quiet_start, quiet_end)
            VALUES (1, 'normal', 0, 23)
            ON CONFLICT (id) DO UPDATE SET verbosity='normal', quiet_start=0, quiet_end=23
        """)
        await _insert_candidate(insight_pool, dedup_key="health:routine:1:2026", priority=70)
        await _insert_candidate(insight_pool, dedup_key="health:urgent:1:2026", priority=95)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        result = await delivery_cycle(
            insight_pool,
            notify_fn=notify_mock,
            now=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
        )

        assert result["skipped"] is False
        notify_mock.assert_awaited_once()
        assert len(result["delivered"]) == 1

        urgent_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:urgent:1:2026'"
        )
        routine_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:routine:1:2026'"
        )
        assert urgent_row["status"] == "delivered"
        assert routine_row["status"] == "pending"

    async def test_fully_suppressed_when_no_urgent_candidate_pending(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity, quiet_start, quiet_end)
            VALUES (1, 'normal', 0, 23)
            ON CONFLICT (id) DO UPDATE SET verbosity='normal', quiet_start=0, quiet_end=23
        """)
        await _insert_candidate(insight_pool, dedup_key="health:routine:2:2026", priority=70)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        result = await delivery_cycle(
            insight_pool,
            notify_fn=notify_mock,
            now=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
        )

        assert result["skipped"] is True
        notify_mock.assert_not_awaited()

        row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:routine:2:2026'"
        )
        assert row["status"] == "pending"


class TestContextBusGating:
    async def test_dnd_signal_suppresses_routine_when_no_quiet_hours(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        # No quiet-hours configured — only the context bus should suppress.
        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal', quiet_start=NULL, quiet_end=NULL
        """)
        await _insert_candidate(insight_pool, dedup_key="health:routine:3:2026", priority=70)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value="dnd"),
        ):
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock)

        assert result["skipped"] is True
        notify_mock.assert_not_awaited()

    async def test_dnd_signal_bypassed_for_urgent_candidate(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal', quiet_start=NULL, quiet_end=NULL
        """)
        await _insert_candidate(insight_pool, dedup_key="health:routine:4:2026", priority=70)
        await _insert_candidate(insight_pool, dedup_key="health:urgent:4:2026", priority=92)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value="sleeping"),
        ):
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock)

        assert result["skipped"] is False
        notify_mock.assert_awaited_once()
        urgent_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:urgent:4:2026'"
        )
        routine_row = await insight_pool.fetchrow(
            "SELECT status FROM insight_candidates WHERE dedup_key = 'health:routine:4:2026'"
        )
        assert urgent_row["status"] == "delivered"
        assert routine_row["status"] == "pending"

    async def test_no_context_signal_and_no_quiet_hours_delivers_normally(self, insight_pool):
        from butlers.tools.switchboard.insight.broker import delivery_cycle

        await insight_pool.execute("""
            INSERT INTO insight_settings (id, verbosity)
            VALUES (1, 'normal')
            ON CONFLICT (id) DO UPDATE SET verbosity='normal', quiet_start=NULL, quiet_end=NULL
        """)
        await _insert_candidate(insight_pool, dedup_key="health:routine:5:2026", priority=70)

        notify_mock = AsyncMock(return_value={"status": "ok"})
        with patch(
            "butlers.tools.switchboard.insight.broker.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ):
            result = await delivery_cycle(insight_pool, notify_fn=notify_mock)

        assert result["skipped"] is False
        notify_mock.assert_awaited_once()
