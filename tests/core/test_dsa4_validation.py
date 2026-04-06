"""End-to-end validation tests for the non-role 0bz3 rollout (butlers-dsa4.6) — condensed.

Validates the five dsa4 feature areas:
1. Deterministic pre-LLM triage (dsa4.1) — evaluator, rule types, thread affinity
2. Attachment handling (dsa4.2) — ATTACHMENT_POLICY map, MIME types, .ics eager routing
3. Backfill lifecycle (dsa4.3) — state machine via mock MCP tool calls
4. Dashboard /ingestion (dsa4.4) — existence of backend API modules (smoke)
5. Policy-tier queuing (dsa4.5) — DurableBuffer tier ordering, starvation guard

All tests are unit-level (no Docker / real DB required). DB calls are mocked.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

_WORKTREE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKTREE_ROOT))


# ===========================================================================
# Area 1: Triage evaluator + thread affinity integration (dsa4.1)
# ===========================================================================


class TestThreadAffinityIntegration:
    def test_affinity_outcome_properties(self) -> None:
        """AffinityResult outcome properties and HIT/MISS/FORCE_OVERRIDE behavior."""
        from butlers.tools.switchboard.triage.thread_affinity import (
            AffinityOutcome,
            AffinityResult,
        )

        hit = AffinityResult(outcome=AffinityOutcome.HIT, target_butler="finance")
        assert hit.outcome.produces_route is True and hit.target_butler == "finance"

        conflict = AffinityResult(outcome=AffinityOutcome.MISS_CONFLICT)
        assert conflict.outcome.is_miss is True and conflict.target_butler is None

        force = AffinityResult(outcome=AffinityOutcome.FORCE_OVERRIDE, target_butler="relationship")
        assert force.outcome.produces_route is True

    def test_affinity_overrides(self) -> None:
        """Disabled globally → None; force override → FORCE_OVERRIDE; thread disabled → MISS_DISABLED_THREAD."""
        from butlers.tools.switchboard.triage.thread_affinity import (
            AffinityOutcome,
            ThreadAffinitySettings,
            _check_override,
        )

        settings_disabled = ThreadAffinitySettings(enabled=False, ttl_days=30, thread_overrides={})
        assert _check_override("thread-001", settings_disabled) is None

        settings_force = ThreadAffinitySettings(
            enabled=True, ttl_days=30, thread_overrides={"thread-finance-001": "force:finance"}
        )
        result = _check_override("thread-finance-001", settings_force)
        assert result is not None and result.outcome == AffinityOutcome.FORCE_OVERRIDE
        assert result.target_butler == "finance"

        settings_disabled_thread = ThreadAffinitySettings(
            enabled=True, ttl_days=30, thread_overrides={"thread-disabled-001": "disabled"}
        )
        result2 = _check_override("thread-disabled-001", settings_disabled_thread)
        assert result2 is not None and result2.outcome == AffinityOutcome.MISS_DISABLED_THREAD


# ===========================================================================
# Area 2: Attachment policy enforcement (dsa4.2)
# ===========================================================================


class TestAttachmentPolicyEnforcement:
    def test_attachment_policy_structure(self) -> None:
        """ATTACHMENT_POLICY is non-empty; SUPPORTED_ATTACHMENT_TYPES matches keys; global cap 25MB."""
        from butlers.connectors.gmail import (
            ATTACHMENT_POLICY,
            GLOBAL_MAX_ATTACHMENT_SIZE_BYTES,
            SUPPORTED_ATTACHMENT_TYPES,
        )

        assert isinstance(ATTACHMENT_POLICY, dict) and len(ATTACHMENT_POLICY) > 0
        assert isinstance(SUPPORTED_ATTACHMENT_TYPES, frozenset)
        assert SUPPORTED_ATTACHMENT_TYPES == frozenset(ATTACHMENT_POLICY.keys())
        assert GLOBAL_MAX_ATTACHMENT_SIZE_BYTES == 25 * 1024 * 1024

    def test_attachment_policy_fetch_modes_and_limits(self) -> None:
        """.ics eager; PDFs lazy 15MB; images lazy 5MB; spreadsheets lazy 10MB."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY

        assert ATTACHMENT_POLICY["text/calendar"]["fetch_mode"] == "eager"
        assert ATTACHMENT_POLICY["application/pdf"]["fetch_mode"] == "lazy"
        assert ATTACHMENT_POLICY["application/pdf"]["max_size_bytes"] == 15 * 1024 * 1024

        for mime in ["image/jpeg", "image/png", "image/gif", "image/webp"]:
            assert ATTACHMENT_POLICY[mime]["fetch_mode"] == "lazy"
            assert ATTACHMENT_POLICY[mime]["max_size_bytes"] == 5 * 1024 * 1024

        for mime in [
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "text/csv",
        ]:
            assert ATTACHMENT_POLICY[mime]["max_size_bytes"] == 10 * 1024 * 1024

    def test_policy_tier_assign(self) -> None:
        """PolicyTierAssigner: known contact → high_priority; unknown → default; direct correspondence → high_priority."""
        from butlers.connectors.gmail_policy import (
            POLICY_TIER_DEFAULT,
            POLICY_TIER_HIGH_PRIORITY,
            PolicyTierAssigner,
        )

        assigner = PolicyTierAssigner(
            user_email="me@example.com",
            known_contacts=frozenset(["alice@trusted.com"]),
            sent_message_ids=frozenset(),
        )
        tier, _ = assigner.assign("alice@trusted.com", {})
        assert tier == POLICY_TIER_HIGH_PRIORITY

        tier2, _ = assigner.assign("unknown@bulk.com", {})
        assert tier2 == POLICY_TIER_DEFAULT

        tier3, _ = assigner.assign("colleague@work.com", {"To": "me@example.com"})
        assert tier3 == POLICY_TIER_HIGH_PRIORITY


# ===========================================================================
# Area 3: Backfill lifecycle state machine (dsa4.3)
# ===========================================================================


class TestBackfillLifecycleStateMachine:
    def _make_job_row(self, status: str = "pending", job_id: str = "00000000-0000-0000-0000-000000000001") -> MagicMock:
        import json
        from datetime import date
        row = MagicMock()
        data: dict[str, Any] = {
            "id": job_id, "connector_type": "gmail", "endpoint_identity": "user@example.com",
            "target_categories": json.dumps(["finance"]),
            "date_from": date(2023, 1, 1), "date_to": date(2023, 12, 31),
            "rate_limit_per_hour": 100, "daily_cost_cap_cents": 500,
            "status": status, "rows_processed": 0, "rows_skipped": 0,
            "cost_spent_cents": 0, "error": None, "created_at": None,
            "started_at": None, "completed_at": None, "updated_at": None, "cursor": None,
        }
        row.__getitem__ = lambda s, k: data[k]
        return row

    def _make_pool(self, fetchrow_return: Any = None, fetch_return: Any = None) -> AsyncMock:
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=fetchrow_return)
        pool.fetch = AsyncMock(return_value=fetch_return or [])
        pool.execute = AsyncMock(return_value="UPDATE 1")
        return pool

    @pytest.mark.asyncio
    async def test_create_and_poll(self) -> None:
        """create_backfill_job → pending; backfill_poll → active row; None when no pending."""
        from datetime import date
        from roster.switchboard.tools.backfill.controls import create_backfill_job
        from roster.switchboard.tools.backfill.connector import backfill_poll

        connector_row = MagicMock()
        connector_row.__getitem__ = lambda s, k: {
            "connector_type": "gmail", "endpoint_identity": "user@example.com"
        }[k]
        result_row = MagicMock()
        result_row.__getitem__ = lambda s, k: {
            "id": "00000000-0000-0000-0000-000000000005", "status": "pending"
        }[k]
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=[connector_row, result_row])

        result = await create_backfill_job(
            pool, connector_type="gmail", endpoint_identity="user@example.com",
            target_categories=["finance"], date_from=date(2023, 1, 1), date_to=date(2023, 12, 31),
        )
        assert result["status"] == "pending"

        # Poll: active row
        active_row = MagicMock()
        active_row.__getitem__ = lambda s, k: {
            "id": "00000000-0000-0000-0000-000000000005",
            "target_categories": '["finance"]',
            "date_from": date(2023, 1, 1), "date_to": date(2023, 12, 31),
            "rate_limit_per_hour": 100, "daily_cost_cap_cents": 500, "cursor": None,
        }[k]
        pool2 = self._make_pool(fetchrow_return=active_row)
        r = await backfill_poll(pool2, connector_type="gmail", endpoint_identity="user@example.com")
        assert r is not None

        # Poll: None when no pending
        pool3 = self._make_pool(fetchrow_return=None)
        r2 = await backfill_poll(pool3, connector_type="gmail", endpoint_identity="user@example.com")
        assert r2 is None

    @pytest.mark.asyncio
    async def test_state_transitions(self) -> None:
        """pause → paused; cancel (pending) → cancelled; resume (paused) → pending."""
        from roster.switchboard.tools.backfill.controls import (
            backfill_cancel,
            backfill_pause,
            backfill_resume,
        )

        # Pause
        paused_result = MagicMock()
        paused_result.__getitem__ = lambda s, k: {"id": "id-2", "status": "paused"}[k]
        pool_pause = AsyncMock()
        pool_pause.fetchrow = AsyncMock(side_effect=[self._make_job_row(status="active"), paused_result])
        r = await backfill_pause(pool_pause, job_id="00000000-0000-0000-0000-000000000002")
        assert r["status"] == "paused"

        # Cancel pending
        cancelled_result = MagicMock()
        cancelled_result.__getitem__ = lambda s, k: {"id": "id-3", "status": "cancelled"}[k]
        pool_cancel = AsyncMock()
        pool_cancel.fetchrow = AsyncMock(side_effect=[self._make_job_row(status="pending"), cancelled_result])
        r2 = await backfill_cancel(pool_cancel, job_id="00000000-0000-0000-0000-000000000003")
        assert r2["status"] == "cancelled"

        # Resume paused
        pending_result = MagicMock()
        pending_result.__getitem__ = lambda s, k: {"id": "id-4", "status": "pending"}[k]
        pool_resume = AsyncMock()
        pool_resume.fetchrow = AsyncMock(side_effect=[self._make_job_row(status="paused"), pending_result])
        r3 = await backfill_resume(pool_resume, job_id="00000000-0000-0000-0000-000000000004")
        assert r3["status"] == "pending"

    @pytest.mark.asyncio
    async def test_terminal_and_invalid_date(self) -> None:
        """Cancel terminal → ValueError; inverted date range → ValueError."""
        from datetime import date
        from roster.switchboard.tools.backfill.controls import backfill_cancel, create_backfill_job

        completed_row = self._make_job_row(status="completed", job_id="00000000-0000-0000-0000-000000000006")
        pool = self._make_pool(fetchrow_return=completed_row)
        with pytest.raises(ValueError, match="terminal"):
            await backfill_cancel(pool, job_id="00000000-0000-0000-0000-000000000006")

        pool2 = self._make_pool()
        with pytest.raises(ValueError, match="date_from"):
            await create_backfill_job(
                pool2, connector_type="gmail", endpoint_identity="user@example.com",
                target_categories=[], date_from=date(2024, 1, 1), date_to=date(2023, 1, 1),
            )


# ===========================================================================
# Area 4: /ingestion dashboard page components (dsa4.4) — smoke imports
# ===========================================================================


class TestIngestionPageSmoke:
    def test_all_backend_modules_importable(self) -> None:
        """All /ingestion backend API modules are importable with expected attributes."""
        import importlib
        from butlers.tools.switchboard.triage.thread_affinity import AffinityOutcome, lookup_thread_affinity

        mod = importlib.import_module("roster.switchboard.api.router")
        assert hasattr(mod, "router")

        from roster.switchboard.tools.backfill import controls, connector
        assert all(hasattr(controls, a) for a in ("create_backfill_job", "backfill_pause", "backfill_cancel", "backfill_resume", "backfill_list"))
        assert all(hasattr(connector, a) for a in ("backfill_poll", "backfill_progress"))

        assert lookup_thread_affinity is not None
        assert AffinityOutcome.HIT.produces_route is True


# ===========================================================================
# Area 5: Buffer tier ordering and starvation guard (dsa4.5)
# ===========================================================================


class TestBufferTierOrdering:
    def _make_config(self, *, queue_capacity: int = 20, max_consecutive_same_tier: int = 10):
        from butlers.config import BufferConfig
        return BufferConfig(
            queue_capacity=queue_capacity, worker_count=1,
            scanner_interval_s=3600, scanner_grace_s=10,
            scanner_batch_size=50, max_consecutive_same_tier=max_consecutive_same_tier,
        )

    def test_tier_order_and_enqueue_routing(self) -> None:
        """POLICY_TIER_ORDER is high_priority > interactive > default; enqueue routes to correct tier queue."""
        from butlers.core.buffer import (
            POLICY_TIER_DEFAULT, POLICY_TIER_HIGH_PRIORITY, POLICY_TIER_INTERACTIVE,
            POLICY_TIER_ORDER, DurableBuffer,
        )

        assert POLICY_TIER_ORDER[0] == POLICY_TIER_HIGH_PRIORITY
        assert POLICY_TIER_ORDER[1] == POLICY_TIER_INTERACTIVE
        assert POLICY_TIER_ORDER[2] == POLICY_TIER_DEFAULT

        buf = DurableBuffer(self._make_config(), pool=None, process_fn=AsyncMock())
        for tier in (POLICY_TIER_HIGH_PRIORITY, POLICY_TIER_INTERACTIVE, POLICY_TIER_DEFAULT):
            buf.enqueue(request_id=f"{tier}-1", message_inbox_id=f"{tier}-1",
                        message_text="test", source={}, event={}, sender={}, policy_tier=tier)
        assert buf.tier_depths[POLICY_TIER_HIGH_PRIORITY] == 1
        assert buf.tier_depths[POLICY_TIER_INTERACTIVE] == 1
        assert buf.tier_depths[POLICY_TIER_DEFAULT] == 1

    @pytest.mark.asyncio
    async def test_high_priority_drained_first_and_starvation_guard(self) -> None:
        """High-priority messages dequeued before default; starvation guard forces lower tier after threshold."""
        from butlers.core.buffer import POLICY_TIER_DEFAULT, POLICY_TIER_HIGH_PRIORITY, DurableBuffer

        # Priority ordering
        order1: list[str] = []
        async def process1(ref) -> None: order1.append(ref.policy_tier)
        buf1 = DurableBuffer(self._make_config(queue_capacity=10, max_consecutive_same_tier=100),
                             pool=None, process_fn=process1)
        for i in range(3):
            buf1.enqueue(request_id=f"df-{i}", message_inbox_id=f"df-{i}", message_text="default",
                         source={}, event={}, sender={}, policy_tier=POLICY_TIER_DEFAULT)
        for i in range(3):
            buf1.enqueue(request_id=f"hp-{i}", message_inbox_id=f"hp-{i}", message_text="high",
                         source={}, event={}, sender={}, policy_tier=POLICY_TIER_HIGH_PRIORITY)
        await buf1.start()
        for _ in range(30):
            if len(order1) >= 6: break
            await asyncio.sleep(0.05)
        await buf1.stop(drain_timeout_s=2.0)
        assert len(order1) == 6
        hp_pos = [i for i, t in enumerate(order1) if t == POLICY_TIER_HIGH_PRIORITY]
        df_pos = [i for i, t in enumerate(order1) if t == POLICY_TIER_DEFAULT]
        assert max(hp_pos) < min(df_pos)

        # Starvation guard
        order2: list[str] = []
        async def process2(ref) -> None: order2.append(ref.policy_tier)
        buf2 = DurableBuffer(self._make_config(queue_capacity=20, max_consecutive_same_tier=3),
                             pool=None, process_fn=process2)
        for i in range(10):
            buf2.enqueue(request_id=f"hp-{i}", message_inbox_id=f"hp-{i}", message_text="high",
                         source={}, event={}, sender={}, policy_tier=POLICY_TIER_HIGH_PRIORITY)
        for i in range(2):
            buf2.enqueue(request_id=f"df-{i}", message_inbox_id=f"df-{i}", message_text="default",
                         source={}, event={}, sender={}, policy_tier=POLICY_TIER_DEFAULT)
        await buf2.start()
        for _ in range(50):
            if len(order2) >= 12: break
            await asyncio.sleep(0.05)
        await buf2.stop(drain_timeout_s=2.0)
        assert len(order2) == 12
        df_pos2 = [i for i, t in enumerate(order2) if t == POLICY_TIER_DEFAULT]
        assert len(df_pos2) == 2 and min(df_pos2) <= 4

    def test_backpressure_and_unknown_tier_fallback(self) -> None:
        """Enqueue returns False (backpressure) on full queue; unknown tier falls back to default."""
        from butlers.core.buffer import POLICY_TIER_DEFAULT, DurableBuffer

        buf = DurableBuffer(self._make_config(queue_capacity=2), pool=None, process_fn=AsyncMock())
        results = [
            buf.enqueue(request_id=f"msg-{i}", message_inbox_id=f"msg-{i}", message_text="test",
                        source={}, event={}, sender={}, policy_tier=POLICY_TIER_DEFAULT)
            for i in range(5)
        ]
        assert results[:2] == [True, True] and results[2:] == [False, False, False]
        assert buf.stats["backpressure_total"] == 3

        buf2 = DurableBuffer(self._make_config(), pool=None, process_fn=AsyncMock())
        ok = buf2.enqueue(request_id="unk", message_inbox_id="unk", message_text="test",
                          source={}, event={}, sender={}, policy_tier="nonexistent_tier")
        assert ok is True and buf2.tier_depths[POLICY_TIER_DEFAULT] == 1


# ===========================================================================
# Cross-area integration: triage + policy tier + buffer tier chain
# ===========================================================================


class TestTriageToPolicyTierIntegration:
    def test_end_to_end_triage_to_buffer(self) -> None:
        """SPAM excluded (should_ingest=False); known contact → high_priority; buffer enqueue routes correctly."""
        from butlers.connectors.gmail_policy import (
            LabelFilterPolicy, PolicyTierAssigner, evaluate_message_policy,
        )
        from butlers.config import BufferConfig
        from butlers.core.buffer import POLICY_TIER_HIGH_PRIORITY, DurableBuffer

        label_filter = LabelFilterPolicy.default()
        tier_assigner = PolicyTierAssigner(
            user_email="user@example.com",
            known_contacts=frozenset(["alice@trusted.com"]),
            sent_message_ids=frozenset(),
        )

        # SPAM excluded
        spam_msg = {"id": "msg-skip", "threadId": "thread-skip", "labelIds": ["SPAM"],
                    "payload": {"headers": [{"name": "From", "value": "newsletter@bulk.com"},
                                             {"name": "To", "value": "user@example.com"}]}}
        r = evaluate_message_policy(spam_msg, label_filter=label_filter, tier_assigner=tier_assigner)
        assert r.should_ingest is False and r.ingestion_tier == 3

        # Known contact → high_priority
        known_msg = {"id": "msg-known", "threadId": "thread-known", "labelIds": ["INBOX"],
                     "payload": {"headers": [{"name": "From", "value": "alice@trusted.com"},
                                              {"name": "To", "value": "user@example.com"}]}}
        r2 = evaluate_message_policy(known_msg, label_filter=label_filter, tier_assigner=tier_assigner)
        assert r2.should_ingest is True and r2.policy_tier == POLICY_TIER_HIGH_PRIORITY

        # Buffer enqueue accepts triage-determined tier
        config = BufferConfig(queue_capacity=10, worker_count=1, scanner_interval_s=3600,
                               scanner_grace_s=10, scanner_batch_size=50, max_consecutive_same_tier=10)
        buf = DurableBuffer(config, pool=None, process_fn=AsyncMock())
        ok = buf.enqueue(request_id="triage-hp", message_inbox_id="triage-hp",
                         message_text="Urgent", source={"channel": "email"}, event={},
                         sender={"identity": "alice@trusted.com"}, policy_tier=POLICY_TIER_HIGH_PRIORITY)
        assert ok is True and buf.tier_depths[POLICY_TIER_HIGH_PRIORITY] == 1
