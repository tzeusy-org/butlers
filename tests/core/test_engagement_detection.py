"""Tests for Switchboard ingress engagement detection — condensed.

Covers:
- check_and_update_engagement: importable; returns 0/count; correct SQL; window bounds; custom window
- Integration: within-window updated; outside-window skipped; already-engaged skipped;
  batch updates; boundary row included; empty table returns 0
- Pipeline.process(): engagement check called once per invocation when the sender
  resolves to the owner (bu-tdd4k.5); exception non-fatal; non-owner/connector
  ingress does NOT call the engagement check or the daily rollup writer.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.identity import ResolvedContact

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

# bu-tdd4k.5: the engagement gate resolves the ingress sender before deciding
# whether to touch insight_engagement / the daily rollup at all.
_OWNER_CONTACT = ResolvedContact(contact_id=None, name="Owner", roles=["owner"], entity_id=None)
_NON_OWNER_CONTACT = ResolvedContact(contact_id=None, name="Chloe", roles=[], entity_id=None)

# All pipeline.process() calls below pass an explicit channel + sender id so
# the engagement gate's resolve_contact_by_channel(source, sender_value) call
# actually fires (an "unknown"/"unknown" pair short-circuits before resolution).
_OWNER_TOOL_ARGS = {"source_channel": "telegram", "source_id": "1"}


class TestPipelineEngagementDetection:
    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_engagement_called_per_process_and_exception_nonfatal(self, _mock_load):
        """check_and_update_engagement called once per owner-authored process();
        exception doesn't block routing."""
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

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new_callable=AsyncMock,
                return_value=_OWNER_CONTACT,
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.check_and_update_engagement",
                side_effect=mock_engagement,
            ),
        ):
            pipeline = MessagePipeline(switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch)
            await pipeline.process("msg one", tool_args=dict(_OWNER_TOOL_ARGS))
            await pipeline.process("msg two", tool_args=dict(_OWNER_TOOL_ARGS))
        assert sum(counts) == 2

        # Exception non-fatal
        async def mock_raise(pool, **kwargs):
            raise RuntimeError("DB down")

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new_callable=AsyncMock,
                return_value=_OWNER_CONTACT,
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.check_and_update_engagement",
                side_effect=mock_raise,
            ),
        ):
            pipeline2 = MessagePipeline(switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch)
            result = await pipeline2.process("hello", tool_args=dict(_OWNER_TOOL_ARGS))
        assert result.target_butler == "general"

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_non_owner_ingress_does_not_call_engagement_check(self, _mock_load):
        """bu-tdd4k.5: connector/non-owner ingress must NOT count as engagement —
        only an owner-resolved sender may update insight_engagement or the
        daily rollup, or the disengagement ratchet can never fire."""
        from butlers.modules.pipeline import MessagePipeline
        from tests.modules.test_module_pipeline import FakeSpawnerResult

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

        engagement_mock = AsyncMock(return_value=0)
        rollup_mock = AsyncMock(return_value=None)

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new_callable=AsyncMock,
                return_value=_NON_OWNER_CONTACT,
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.check_and_update_engagement",
                engagement_mock,
            ),
            patch(
                "butlers.core.attention_ledger.record_owner_ingress_rollup",
                rollup_mock,
            ),
        ):
            pipeline = MessagePipeline(switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch)
            await pipeline.process("connector event", tool_args=dict(_OWNER_TOOL_ARGS))

        engagement_mock.assert_not_called()
        rollup_mock.assert_not_called()

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_unresolved_sender_does_not_call_engagement_check(self, _mock_load):
        """An unresolved/unknown sender (resolve_contact_by_channel -> None)
        must not be treated as owner-authored ingress."""
        from butlers.modules.pipeline import MessagePipeline
        from tests.modules.test_module_pipeline import FakeSpawnerResult

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

        engagement_mock = AsyncMock(return_value=0)

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.check_and_update_engagement",
                engagement_mock,
            ),
        ):
            pipeline = MessagePipeline(switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch)
            await pipeline.process("unknown sender", tool_args=dict(_OWNER_TOOL_ARGS))

        engagement_mock.assert_not_called()

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_owner_ingress_records_daily_rollup(self, _mock_load):
        """Owner-authored ingress records the daily rollup alongside the
        60-minute engagement sweep (bu-tdd4k.5)."""
        from butlers.modules.pipeline import MessagePipeline
        from tests.modules.test_module_pipeline import FakeSpawnerResult

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

        rollup_mock = AsyncMock(return_value=None)

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new_callable=AsyncMock,
                return_value=_OWNER_CONTACT,
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.check_and_update_engagement",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "butlers.core.attention_ledger.record_owner_ingress_rollup",
                rollup_mock,
            ),
        ):
            pipeline = MessagePipeline(switchboard_pool=MagicMock(), dispatch_fn=mock_dispatch)
            await pipeline.process("hi owner", tool_args=dict(_OWNER_TOOL_ARGS))

        rollup_mock.assert_called_once()
