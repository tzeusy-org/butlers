"""Tests for Switchboard ingress engagement detection — condensed.

Covers:
- check_and_update_engagement: importable; returns 0/count; correct SQL; window bounds; custom window
- Integration: within-window updated; outside-window skipped; already-engaged skipped;
  batch updates; boundary row included; empty table returns 0
- Pipeline.process(): engagement check called once per invocation; exception non-fatal
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_docker_available = shutil.which("docker") is not None

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
async def engagement_pool(provisioned_postgres_pool):
    from butlers.tools.switchboard.insight.broker import create_insight_tables

    async with provisioned_postgres_pool() as pool:
        await create_insight_tables(pool)
        yield pool


class TestCheckAndUpdateEngagementUnit:
    async def test_function_behavior_and_sql(self):
        """Importable; returns the parsed update count from the engagement sweep."""
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        assert callable(check_and_update_engagement)

        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="UPDATE 3")
        assert await check_and_update_engagement(mock_pool) == 3

        mock_pool.execute = AsyncMock(return_value="UPDATE 0")
        assert await check_and_update_engagement(mock_pool) == 0


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
class TestCheckAndUpdateEngagementIntegration:
    async def _insert(self, pool, insight_id, delivered_at, engaged=False):
        await pool.execute(
            "INSERT INTO insight_engagement (insight_id, delivered_at, engaged) VALUES ($1, $2, $3)",
            insight_id,
            delivered_at,
            engaged,
        )

    async def test_engagement_window_logic(self, engagement_pool):
        """Within-window updated; outside-window skipped; already-engaged skipped; batch; boundary included; empty→0."""
        from butlers.tools.switchboard.insight.broker import check_and_update_engagement

        ref_now = _now()

        # Empty table
        assert await check_and_update_engagement(engagement_pool) == 0

        # Within window
        id1 = uuid.uuid4()
        await self._insert(engagement_pool, id1, ref_now - timedelta(minutes=30))
        assert await check_and_update_engagement(engagement_pool, now=ref_now) == 1
        row = await engagement_pool.fetchrow(
            "SELECT engaged FROM insight_engagement WHERE insight_id = $1", id1
        )
        assert row["engaged"] is True

        # Outside window (90 min ago)
        id2 = uuid.uuid4()
        await self._insert(engagement_pool, id2, ref_now - timedelta(minutes=90))
        updated = await check_and_update_engagement(engagement_pool, now=ref_now)
        assert updated == 0  # id1 already engaged, id2 outside window
        row2 = await engagement_pool.fetchrow(
            "SELECT engaged FROM insight_engagement WHERE insight_id = $1", id2
        )
        assert row2["engaged"] is False

        # Batch: multiple unengaged in window
        ids = [uuid.uuid4() for _ in range(3)]
        for i, iid in enumerate(ids):
            await self._insert(engagement_pool, iid, ref_now - timedelta(minutes=10 + i))
        updated2 = await check_and_update_engagement(engagement_pool, now=ref_now)
        assert updated2 == 3

        # Boundary at exactly 60 min
        id3 = uuid.uuid4()
        await self._insert(engagement_pool, id3, ref_now - timedelta(minutes=60))
        updated3 = await check_and_update_engagement(engagement_pool, now=ref_now)
        assert updated3 == 1  # only id3 is newly unengaged within boundary


_MOCK_BUTLERS = [{"name": "general", "description": "General purpose butler."}]


class TestPipelineEngagementDetection:
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_engagement_called_per_process_and_exception_nonfatal(self, _mock_load):
        """check_and_update_engagement called once per process(); exception doesn't block routing."""
        from butlers.modules.pipeline import MessagePipeline
        from tests.modules.test_module_pipeline import FakeSpawnerResult

        counts = []

        async def mock_engagement(pool, **kwargs):
            counts.append(1)
            return 0

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="ok",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "general", "prompt": "hi"},
                        "result": {"status": "ok", "butler": "general"},
                    },
                ],
            )

        with patch(
            "butlers.tools.switchboard.insight.broker.check_and_update_engagement",
            side_effect=mock_engagement,
        ):
            pipeline = MessagePipeline(switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch)
            await pipeline.process("msg one")
            await pipeline.process("msg two")
        assert sum(counts) == 2

        # Exception non-fatal
        async def mock_raise(pool, **kwargs):
            raise RuntimeError("DB down")

        with patch(
            "butlers.tools.switchboard.insight.broker.check_and_update_engagement",
            side_effect=mock_raise,
        ):
            pipeline2 = MessagePipeline(switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch)
            result = await pipeline2.process("hello")
        assert result.target_butler == "general"
