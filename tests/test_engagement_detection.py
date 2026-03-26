"""Tests for Switchboard ingress engagement detection.

Covers bu-qr5o:
- check_and_update_engagement() marks unengaged rows within 60-minute window
- Rows outside the window are not updated
- Rows already marked engaged=TRUE are not double-counted
- Batch updates: multiple unengaged rows in window all get updated
- Pipeline.process() calls engagement detection on each ingress
- Pipeline.process() does not fail if engagement detection raises an exception

Issue: bu-qr5o
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_docker_available = shutil.which("docker") is not None

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _past_minutes(minutes: int) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


def _future_minutes(minutes: int) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engagement_pool(provisioned_postgres_pool):
    """Provision a fresh database with insight tables for one test."""
    from butlers.tools.switchboard.insight.broker import create_insight_tables

    async with provisioned_postgres_pool() as pool:
        await create_insight_tables(pool)
        yield pool


# ---------------------------------------------------------------------------
# Unit tests: check_and_update_engagement (no Docker required)
# ---------------------------------------------------------------------------


class TestCheckAndUpdateEngagementUnit:
    """Unit tests for check_and_update_engagement using mock pool."""

    async def test_function_is_importable(self):
        """check_and_update_engagement is importable from broker module."""
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        assert callable(check_and_update_engagement)

    async def test_returns_zero_on_no_matching_rows(self):
        """Returns 0 when pool.execute returns UPDATE 0."""
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="UPDATE 0")

        result = await check_and_update_engagement(mock_pool)
        assert result == 0

    async def test_returns_updated_count(self):
        """Returns count of updated rows from pool.execute result."""
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="UPDATE 3")

        result = await check_and_update_engagement(mock_pool)
        assert result == 3

    async def test_execute_called_with_correct_query(self):
        """pool.execute is called with the expected UPDATE statement."""
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="UPDATE 0")

        ref_now = _now()
        await check_and_update_engagement(mock_pool, window_minutes=60, now=ref_now)

        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args
        # First arg is the SQL string
        sql = call_args[0][0]
        assert "UPDATE insight_engagement" in sql
        assert "engaged = TRUE" in sql
        assert "engaged = FALSE" in sql
        assert "delivered_at" in sql

    async def test_window_bounds_are_correct(self):
        """SQL is called with window_start = now - window_minutes."""
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="UPDATE 0")

        ref_now = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        expected_start = datetime(2026, 3, 1, 11, 0, 0, tzinfo=UTC)

        await check_and_update_engagement(mock_pool, window_minutes=60, now=ref_now)

        call_args = mock_pool.execute.call_args[0]
        # Positional args: sql, window_start, now
        assert call_args[1] == expected_start
        assert call_args[2] == ref_now

    async def test_custom_window_minutes(self):
        """Custom window_minutes parameter is respected."""
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="UPDATE 0")

        ref_now = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        expected_start = datetime(2026, 3, 1, 11, 30, 0, tzinfo=UTC)

        await check_and_update_engagement(mock_pool, window_minutes=30, now=ref_now)

        call_args = mock_pool.execute.call_args[0]
        assert call_args[1] == expected_start


# ---------------------------------------------------------------------------
# Integration tests: check_and_update_engagement (requires Docker)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
class TestCheckAndUpdateEngagementIntegration:
    """Integration tests using a real Postgres container."""

    async def _insert_engagement_row(
        self,
        pool,
        insight_id: uuid.UUID,
        delivered_at: datetime,
        engaged: bool = False,
    ) -> None:
        """Helper: insert an engagement row directly."""
        await pool.execute(
            """
            INSERT INTO insight_engagement (insight_id, delivered_at, engaged)
            VALUES ($1, $2, $3)
            """,
            insight_id,
            delivered_at,
            engaged,
        )

    async def test_within_window_unengaged_row_updated(self, engagement_pool):
        """Row with delivered_at within 60 minutes and engaged=FALSE is updated to TRUE."""
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        ref_now = _now()
        insight_id = uuid.uuid4()
        # Delivered 30 minutes ago — within window
        await self._insert_engagement_row(
            engagement_pool,
            insight_id,
            delivered_at=ref_now - timedelta(minutes=30),
            engaged=False,
        )

        updated = await check_and_update_engagement(engagement_pool, now=ref_now)
        assert updated == 1

        row = await engagement_pool.fetchrow(
            "SELECT engaged FROM insight_engagement WHERE insight_id = $1",
            insight_id,
        )
        assert row["engaged"] is True

    async def test_outside_window_row_not_updated(self, engagement_pool):
        """Row with delivered_at > 60 minutes ago is NOT updated."""
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        ref_now = _now()
        insight_id = uuid.uuid4()
        # Delivered 90 minutes ago — outside window
        await self._insert_engagement_row(
            engagement_pool,
            insight_id,
            delivered_at=ref_now - timedelta(minutes=90),
            engaged=False,
        )

        updated = await check_and_update_engagement(engagement_pool, now=ref_now)
        assert updated == 0

        row = await engagement_pool.fetchrow(
            "SELECT engaged FROM insight_engagement WHERE insight_id = $1",
            insight_id,
        )
        assert row["engaged"] is False

    async def test_already_engaged_row_not_double_counted(self, engagement_pool):
        """Row already marked engaged=TRUE within window is not re-counted."""
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        ref_now = _now()
        insight_id = uuid.uuid4()
        # Delivered 10 minutes ago, already engaged
        await self._insert_engagement_row(
            engagement_pool,
            insight_id,
            delivered_at=ref_now - timedelta(minutes=10),
            engaged=True,
        )

        updated = await check_and_update_engagement(engagement_pool, now=ref_now)
        assert updated == 0

    async def test_batch_update_multiple_rows_within_window(self, engagement_pool):
        """Multiple unengaged rows within window are all updated in one call."""
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        ref_now = _now()
        ids = [uuid.uuid4() for _ in range(3)]
        for i, insight_id in enumerate(ids):
            await self._insert_engagement_row(
                engagement_pool,
                insight_id,
                delivered_at=ref_now - timedelta(minutes=10 + i),
                engaged=False,
            )

        updated = await check_and_update_engagement(engagement_pool, now=ref_now)
        assert updated == 3

        for insight_id in ids:
            row = await engagement_pool.fetchrow(
                "SELECT engaged FROM insight_engagement WHERE insight_id = $1",
                insight_id,
            )
            assert row["engaged"] is True

    async def test_mixed_window_only_in_window_updated(self, engagement_pool):
        """Only rows within the 60-minute window are updated; older rows stay."""
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        ref_now = _now()

        inside_id = uuid.uuid4()
        outside_id = uuid.uuid4()

        # Within window: 20 minutes ago
        await self._insert_engagement_row(
            engagement_pool,
            inside_id,
            delivered_at=ref_now - timedelta(minutes=20),
            engaged=False,
        )
        # Outside window: 70 minutes ago
        await self._insert_engagement_row(
            engagement_pool,
            outside_id,
            delivered_at=ref_now - timedelta(minutes=70),
            engaged=False,
        )

        updated = await check_and_update_engagement(engagement_pool, now=ref_now)
        assert updated == 1

        inside_row = await engagement_pool.fetchrow(
            "SELECT engaged FROM insight_engagement WHERE insight_id = $1",
            inside_id,
        )
        outside_row = await engagement_pool.fetchrow(
            "SELECT engaged FROM insight_engagement WHERE insight_id = $1",
            outside_id,
        )
        assert inside_row["engaged"] is True
        assert outside_row["engaged"] is False

    async def test_at_exactly_window_boundary_included(self, engagement_pool):
        """Row at exactly the window boundary (delivered_at = now - 60 min) is included."""
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        ref_now = _now()
        insight_id = uuid.uuid4()
        # Delivered exactly 60 minutes ago — at boundary (>= window_start)
        await self._insert_engagement_row(
            engagement_pool,
            insight_id,
            delivered_at=ref_now - timedelta(minutes=60),
            engaged=False,
        )

        updated = await check_and_update_engagement(engagement_pool, now=ref_now)
        assert updated == 1

    async def test_empty_engagement_table_returns_zero(self, engagement_pool):
        """Returns 0 when no engagement rows exist."""
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        updated = await check_and_update_engagement(engagement_pool)
        assert updated == 0


# ---------------------------------------------------------------------------
# Unit tests: Pipeline.process() engagement hook
# ---------------------------------------------------------------------------


_MOCK_BUTLERS = [
    {"name": "general", "description": "General purpose butler."},
]


class TestPipelineEngagementDetection:
    """Verify that pipeline.process() calls check_and_update_engagement on ingress."""

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_engagement_check_called_on_process(self, _mock_load):
        """check_and_update_engagement is called on each pipeline.process() invocation."""
        from butlers.modules.pipeline import MessagePipeline

        engagement_call_count = 0

        async def mock_check_engagement(pool, **kwargs):
            nonlocal engagement_call_count
            engagement_call_count += 1
            return 0

        async def mock_dispatch(**kwargs):
            from tests.modules.test_module_pipeline import FakeSpawnerResult

            return FakeSpawnerResult(
                output="Routed to general.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "general", "prompt": "hello"},
                        "result": {"status": "ok", "butler": "general"},
                    }
                ],
            )

        with patch(
            "butlers.tools.switchboard.insight.broker.check_and_update_engagement",
            side_effect=mock_check_engagement,
        ):
            pipeline = MessagePipeline(
                switchboard_pool=MagicMock(),
                dispatch_fn=mock_dispatch,
            )
            await pipeline.process("hello")

        assert engagement_call_count == 1

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_engagement_check_called_on_each_process_invocation(self, _mock_load):
        """Engagement check is called once per pipeline.process() call."""
        from butlers.modules.pipeline import MessagePipeline

        engagement_call_count = 0

        async def mock_check_engagement(pool, **kwargs):
            nonlocal engagement_call_count
            engagement_call_count += 1
            return 0

        async def mock_dispatch(**kwargs):
            from tests.modules.test_module_pipeline import FakeSpawnerResult

            return FakeSpawnerResult(
                output="ok",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "general", "prompt": "msg"},
                        "result": {"status": "ok", "butler": "general"},
                    }
                ],
            )

        with patch(
            "butlers.tools.switchboard.insight.broker.check_and_update_engagement",
            side_effect=mock_check_engagement,
        ):
            pipeline = MessagePipeline(
                switchboard_pool=MagicMock(),
                dispatch_fn=mock_dispatch,
            )
            await pipeline.process("message one")
            await pipeline.process("message two")

        assert engagement_call_count == 2

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_engagement_check_exception_does_not_block_routing(self, _mock_load):
        """If check_and_update_engagement raises, routing still succeeds."""
        from butlers.modules.pipeline import MessagePipeline

        async def mock_check_engagement_raises(pool, **kwargs):
            raise RuntimeError("DB connection lost")

        async def mock_dispatch(**kwargs):
            from tests.modules.test_module_pipeline import FakeSpawnerResult

            return FakeSpawnerResult(
                output="Routed to general.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "general", "prompt": "hello"},
                        "result": {"status": "ok", "butler": "general"},
                    }
                ],
            )

        with patch(
            "butlers.tools.switchboard.insight.broker.check_and_update_engagement",
            side_effect=mock_check_engagement_raises,
        ):
            pipeline = MessagePipeline(
                switchboard_pool=MagicMock(),
                dispatch_fn=mock_dispatch,
            )
            result = await pipeline.process("hello")

        # Routing should still succeed despite engagement check failure
        assert result.target_butler == "general"
        assert result.classification_error is None
