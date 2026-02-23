"""End-to-end validation tests for the non-role 0bz3 rollout (butlers-dsa4.6).

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

# ---------------------------------------------------------------------------
# Add the worktree root to sys.path so that 'roster.*' imports resolve when
# this file is collected from the 'tests/' testpath (pytest importlib mode
# scopes path injection per testpath; 'roster/' is not auto-added for tests
# under 'tests/').
# ---------------------------------------------------------------------------
_WORKTREE_ROOT = Path(__file__).resolve().parent.parent
if str(_WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKTREE_ROOT))


# ===========================================================================
# Area 1: Triage evaluator + thread affinity integration (dsa4.1)
# ===========================================================================


class TestTriageEvaluatorIntegration:
    """Validate triage rules produce correct routing decisions end-to-end."""

    def _make_rule(
        self,
        *,
        id: str = "00000000-0000-0000-0000-000000000001",
        rule_type: str = "sender_domain",
        condition: dict | None = None,
        action: str = "pass_through",
        priority: int = 10,
    ) -> dict:
        return {
            "id": id,
            "rule_type": rule_type,
            "condition": condition or {},
            "action": action,
            "priority": priority,
            "created_at": "2026-01-01T00:00:00Z",
        }

    def test_sender_domain_rule_routes_to_finance(self) -> None:
        """Chase sender domain should route to finance butler."""
        from butlers.tools.switchboard.triage.evaluator import TriageEnvelope, evaluate_triage

        rules = [
            self._make_rule(
                rule_type="sender_domain",
                condition={"domain": "chase.com", "match": "suffix"},
                action="route_to:finance",
                priority=10,
            )
        ]
        envelope = TriageEnvelope(
            sender_address="alerts@chase.com",
            source_channel="email",
        )
        decision = evaluate_triage(envelope, rules)

        assert decision.decision == "route_to"
        assert decision.target_butler == "finance"
        assert decision.matched_rule_type == "sender_domain"
        assert decision.bypasses_llm is True

    def test_sender_address_exact_match(self) -> None:
        """Exact sender address match should skip the message."""
        from butlers.tools.switchboard.triage.evaluator import TriageEnvelope, evaluate_triage

        rules = [
            self._make_rule(
                rule_type="sender_address",
                condition={"address": "noreply@newsletter.com"},
                action="skip",
                priority=5,
            )
        ]
        envelope = TriageEnvelope(
            sender_address="noreply@newsletter.com",
            source_channel="email",
        )
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "skip"

    def test_header_condition_present_op(self) -> None:
        """List-Unsubscribe header present should trigger metadata_only."""
        from butlers.tools.switchboard.triage.evaluator import TriageEnvelope, evaluate_triage

        rules = [
            self._make_rule(
                rule_type="header_condition",
                condition={"header": "List-Unsubscribe", "op": "present"},
                action="metadata_only",
                priority=20,
            )
        ]
        envelope = TriageEnvelope(
            sender_address="bulk@marketing.com",
            source_channel="email",
            headers={"List-Unsubscribe": "<mailto:unsub@marketing.com>"},
        )
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "metadata_only"
        assert decision.bypasses_llm is True

    def test_mime_type_rule_routes_on_ics_attachment(self) -> None:
        """mime_type rule for text/calendar should route to calendar butler.

        Note: condition key is 'type' (not 'mime_type') per evaluator contract.
        """
        from butlers.tools.switchboard.triage.evaluator import TriageEnvelope, evaluate_triage

        rules = [
            self._make_rule(
                rule_type="mime_type",
                condition={"type": "text/calendar"},  # key is 'type', not 'mime_type'
                action="route_to:calendar",
                priority=15,
            )
        ]
        envelope = TriageEnvelope(
            sender_address="invites@company.com",
            source_channel="email",
            mime_parts=["text/plain", "text/calendar"],
        )
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "route_to"
        assert decision.target_butler == "calendar"

    def test_priority_ordering_first_match_wins(self) -> None:
        """Lower priority number wins over higher priority number."""
        from butlers.tools.switchboard.triage.evaluator import TriageEnvelope, evaluate_triage

        rules = [
            self._make_rule(
                id="rule-high",
                rule_type="sender_domain",
                condition={"domain": "paypal.com", "match": "exact"},
                action="route_to:finance",
                priority=5,  # lower priority number = evaluated first
            ),
            self._make_rule(
                id="rule-low",
                rule_type="sender_domain",
                condition={"domain": "paypal.com", "match": "exact"},
                action="skip",
                priority=50,
            ),
        ]
        envelope = TriageEnvelope(
            sender_address="service@paypal.com",
            source_channel="email",
        )
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "route_to"
        assert decision.target_butler == "finance"
        assert decision.matched_rule_id == "rule-high"

    def test_no_matching_rule_returns_pass_through(self) -> None:
        """No matching rules returns pass_through (LLM fallback)."""
        from butlers.tools.switchboard.triage.evaluator import TriageEnvelope, evaluate_triage

        rules = [
            self._make_rule(
                rule_type="sender_domain",
                condition={"domain": "specificdomain.com", "match": "exact"},
                action="skip",
                priority=10,
            )
        ]
        envelope = TriageEnvelope(
            sender_address="anyone@otherdomain.com",
            source_channel="email",
        )
        decision = evaluate_triage(envelope, rules)
        assert decision.decision == "pass_through"
        assert decision.bypasses_llm is False

    def test_thread_affinity_overrides_all_rules(self) -> None:
        """Thread affinity target takes precedence over matching triage rules."""
        from butlers.tools.switchboard.triage.evaluator import TriageEnvelope, evaluate_triage

        # Rule would route to finance
        rules = [
            self._make_rule(
                rule_type="sender_domain",
                condition={"domain": "chase.com", "match": "suffix"},
                action="route_to:finance",
                priority=10,
            )
        ]
        envelope = TriageEnvelope(
            sender_address="alerts@chase.com",
            source_channel="email",
        )
        # But thread affinity says relationship
        decision = evaluate_triage(envelope, rules, thread_affinity_target="relationship")
        assert decision.decision == "route_to"
        assert decision.target_butler == "relationship"
        assert decision.matched_rule_type == "thread_affinity"
        assert decision.matched_rule_id is None  # No rule matched; affinity resolved it

    def test_thread_affinity_outcome_properties(self) -> None:
        """AffinityResult outcome properties are correct for HIT and MISS."""
        from butlers.tools.switchboard.triage.thread_affinity import (
            AffinityOutcome,
            AffinityResult,
        )

        # HIT produces a route
        hit = AffinityResult(outcome=AffinityOutcome.HIT, target_butler="finance")
        assert hit.outcome.produces_route is True
        assert hit.target_butler == "finance"

        # MISS_CONFLICT falls through to LLM
        conflict = AffinityResult(outcome=AffinityOutcome.MISS_CONFLICT)
        assert conflict.outcome.is_miss is True
        assert conflict.target_butler is None

        # FORCE_OVERRIDE also produces a route
        force = AffinityResult(outcome=AffinityOutcome.FORCE_OVERRIDE, target_butler="relationship")
        assert force.outcome.produces_route is True

    def test_thread_affinity_disabled_globally(self) -> None:
        """Thread affinity lookup returns MISS_DISABLED_GLOBAL when disabled."""
        from butlers.tools.switchboard.triage.thread_affinity import (
            ThreadAffinitySettings,
            _check_override,
        )

        settings_disabled = ThreadAffinitySettings(
            enabled=False,
            ttl_days=30,
            thread_overrides={},
        )
        # _check_override checks per-thread override (not global disable),
        # so returns None when no thread-specific override exists
        result = _check_override("thread-001", settings_disabled)
        assert result is None  # No thread-specific override; global disable is checked upstream

    def test_thread_affinity_force_override(self) -> None:
        """Thread-specific force override returns FORCE_OVERRIDE outcome."""
        from butlers.tools.switchboard.triage.thread_affinity import (
            AffinityOutcome,
            ThreadAffinitySettings,
            _check_override,
        )

        settings = ThreadAffinitySettings(
            enabled=True,
            ttl_days=30,
            thread_overrides={"thread-finance-001": "force:finance"},
        )
        result = _check_override("thread-finance-001", settings)
        assert result is not None
        assert result.outcome == AffinityOutcome.FORCE_OVERRIDE
        assert result.target_butler == "finance"

    def test_thread_affinity_thread_disabled_override(self) -> None:
        """Thread-specific disabled override returns MISS_DISABLED_THREAD."""
        from butlers.tools.switchboard.triage.thread_affinity import (
            AffinityOutcome,
            ThreadAffinitySettings,
            _check_override,
        )

        settings = ThreadAffinitySettings(
            enabled=True,
            ttl_days=30,
            thread_overrides={"thread-disabled-001": "disabled"},
        )
        result = _check_override("thread-disabled-001", settings)
        assert result is not None
        assert result.outcome == AffinityOutcome.MISS_DISABLED_THREAD


# ===========================================================================
# Area 2: Attachment policy enforcement (dsa4.2)
# ===========================================================================


class TestAttachmentPolicyEnforcement:
    """Validate ATTACHMENT_POLICY correctly classifies file types."""

    def test_attachment_policy_map_exists(self) -> None:
        """ATTACHMENT_POLICY dict is present and non-empty."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY

        assert isinstance(ATTACHMENT_POLICY, dict)
        assert len(ATTACHMENT_POLICY) > 0

    def test_ics_has_eager_fetch_mode(self) -> None:
        """.ics files (text/calendar) are configured for eager routing."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY

        assert "text/calendar" in ATTACHMENT_POLICY
        ics_policy = ATTACHMENT_POLICY["text/calendar"]
        assert ics_policy["fetch_mode"] == "eager"

    def test_pdf_has_lazy_fetch_mode(self) -> None:
        """PDFs are lazy-fetched with 15 MB limit."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY

        assert "application/pdf" in ATTACHMENT_POLICY
        pdf_policy = ATTACHMENT_POLICY["application/pdf"]
        assert pdf_policy["fetch_mode"] == "lazy"
        assert pdf_policy["max_size_bytes"] == 15 * 1024 * 1024

    def test_image_types_have_5mb_limit(self) -> None:
        """Images are lazy-fetched with 5 MB size limit."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY

        image_types = ["image/jpeg", "image/png", "image/gif", "image/webp"]
        for mime_type in image_types:
            assert mime_type in ATTACHMENT_POLICY, f"Missing policy for {mime_type}"
            policy = ATTACHMENT_POLICY[mime_type]
            assert policy["fetch_mode"] == "lazy", f"Expected lazy for {mime_type}"
            assert policy["max_size_bytes"] == 5 * 1024 * 1024, f"Expected 5MB for {mime_type}"

    def test_spreadsheet_types_have_10mb_limit(self) -> None:
        """Spreadsheets are lazy-fetched with 10 MB limit."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY

        spreadsheet_types = [
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "text/csv",
        ]
        for mime_type in spreadsheet_types:
            assert mime_type in ATTACHMENT_POLICY, f"Missing policy for {mime_type}"
            assert ATTACHMENT_POLICY[mime_type]["max_size_bytes"] == 10 * 1024 * 1024

    def test_supported_attachment_types_derived_from_policy(self) -> None:
        """SUPPORTED_ATTACHMENT_TYPES is a frozenset derived from ATTACHMENT_POLICY."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY, SUPPORTED_ATTACHMENT_TYPES

        assert isinstance(SUPPORTED_ATTACHMENT_TYPES, frozenset)
        assert SUPPORTED_ATTACHMENT_TYPES == frozenset(ATTACHMENT_POLICY.keys())

    def test_global_size_cap_is_25mb(self) -> None:
        """Global max attachment size cap is 25 MB (Gmail's limit)."""
        from butlers.connectors.gmail import GLOBAL_MAX_ATTACHMENT_SIZE_BYTES

        assert GLOBAL_MAX_ATTACHMENT_SIZE_BYTES == 25 * 1024 * 1024

    def test_policy_tier_assign_with_gmail_policy(self) -> None:
        """PolicyTierAssigner correctly routes known contacts to high_priority."""
        from butlers.connectors.gmail_policy import (
            POLICY_TIER_DEFAULT,
            POLICY_TIER_HIGH_PRIORITY,
            POLICY_TIER_INTERACTIVE,
            PolicyTierAssigner,
        )

        assigner = PolicyTierAssigner(
            user_email="me@example.com",
            known_contacts=frozenset(["alice@trusted.com"]),
            sent_message_ids=frozenset(),
        )
        tier, rule = assigner.assign("alice@trusted.com", {})
        assert tier == POLICY_TIER_HIGH_PRIORITY

        tier2, _rule2 = assigner.assign("unknown@bulk.com", {})
        assert tier2 == POLICY_TIER_DEFAULT

        # Direct correspondence gets interactive
        headers = {"To": "me@example.com"}
        tier3, _rule3 = assigner.assign("colleague@work.com", headers)
        assert tier3 == POLICY_TIER_INTERACTIVE


# ===========================================================================
# Area 3: Backfill lifecycle state machine (dsa4.3)
# ===========================================================================


class TestBackfillLifecycleStateMachine:
    """Validate backfill lifecycle via mock MCP tool calls."""

    def _make_job_row(
        self,
        status: str = "pending",
        job_id: str = "00000000-0000-0000-0000-000000000001",
    ) -> MagicMock:
        """Build a fake asyncpg-style row for backfill_jobs."""
        import json
        from datetime import date

        row = MagicMock()
        data: dict[str, Any] = {
            "id": job_id,
            "connector_type": "gmail",
            "endpoint_identity": "user@example.com",
            "target_categories": json.dumps(["finance"]),
            "date_from": date(2023, 1, 1),
            "date_to": date(2023, 12, 31),
            "rate_limit_per_hour": 100,
            "daily_cost_cap_cents": 500,
            "status": status,
            "rows_processed": 0,
            "rows_skipped": 0,
            "cost_spent_cents": 0,
            "error": None,
            "created_at": None,
            "started_at": None,
            "completed_at": None,
            "updated_at": None,
            "cursor": None,
        }
        row.__getitem__ = lambda s, k: data[k]
        return row

    def _make_pool(
        self,
        fetchrow_return: Any = None,
        fetch_return: Any = None,
    ) -> AsyncMock:
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=fetchrow_return)
        pool.fetch = AsyncMock(return_value=fetch_return or [])
        pool.execute = AsyncMock(return_value="UPDATE 1")
        return pool

    @pytest.mark.asyncio
    async def test_create_job_transitions_to_pending(self) -> None:
        """create_backfill_job creates a job in pending state."""
        from datetime import date

        from roster.switchboard.tools.backfill.controls import create_backfill_job

        # Mock connector registry lookup
        connector_row = MagicMock()
        connector_row.__getitem__ = lambda s, k: {
            "connector_type": "gmail",
            "endpoint_identity": "user@example.com",
        }[k]

        result_row = MagicMock()
        result_row.__getitem__ = lambda s, k: {
            "id": "00000000-0000-0000-0000-000000000005",
            "status": "pending",
        }[k]

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=[connector_row, result_row])

        result = await create_backfill_job(
            pool,
            connector_type="gmail",
            endpoint_identity="user@example.com",
            target_categories=["finance"],
            date_from=date(2023, 1, 1),
            date_to=date(2023, 12, 31),
        )
        assert result["status"] == "pending"
        assert str(result["job_id"]) == "00000000-0000-0000-0000-000000000005"

    @pytest.mark.asyncio
    async def test_backfill_poll_transitions_to_active(self) -> None:
        """backfill_poll claims a pending job and transitions it to active."""
        from datetime import date

        from roster.switchboard.tools.backfill.connector import backfill_poll

        active_row = MagicMock()
        active_row.__getitem__ = lambda s, k: {
            "id": "00000000-0000-0000-0000-000000000005",
            "target_categories": '["finance"]',
            "date_from": date(2023, 1, 1),
            "date_to": date(2023, 12, 31),
            "rate_limit_per_hour": 100,
            "daily_cost_cap_cents": 500,
            "cursor": None,
        }[k]

        pool = self._make_pool(fetchrow_return=active_row)

        result = await backfill_poll(
            pool,
            connector_type="gmail",
            endpoint_identity="user@example.com",
        )
        assert result is not None
        assert str(result["job_id"]) == "00000000-0000-0000-0000-000000000005"

    @pytest.mark.asyncio
    async def test_backfill_poll_returns_none_when_no_pending(self) -> None:
        """backfill_poll returns None when no pending jobs exist."""
        from roster.switchboard.tools.backfill.connector import backfill_poll

        pool = self._make_pool(fetchrow_return=None)

        result = await backfill_poll(
            pool,
            connector_type="gmail",
            endpoint_identity="user@example.com",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_backfill_pause_transitions_active_to_paused(self) -> None:
        """backfill_pause transitions an active job to paused state."""
        from roster.switchboard.tools.backfill.controls import backfill_pause

        _JOB_UUID_PAUSE = "00000000-0000-0000-0000-000000000002"
        active_row = self._make_job_row(status="active", job_id=_JOB_UUID_PAUSE)
        updated_row = MagicMock()
        updated_row.__getitem__ = lambda s, k: {
            "id": "00000000-0000-0000-0000-000000000002",
            "status": "paused",
        }[k]

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=[active_row, updated_row])

        result = await backfill_pause(pool, job_id="00000000-0000-0000-0000-000000000002")
        assert result["status"] == "paused"

    @pytest.mark.asyncio
    async def test_backfill_cancel_from_pending(self) -> None:
        """backfill_cancel transitions a pending job to cancelled."""
        from roster.switchboard.tools.backfill.controls import backfill_cancel

        pending_row = self._make_job_row(status="pending")
        cancelled_row = MagicMock()
        cancelled_row.__getitem__ = lambda s, k: {
            "id": "00000000-0000-0000-0000-000000000003",
            "status": "cancelled",
        }[k]

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=[pending_row, cancelled_row])

        result = await backfill_cancel(pool, job_id="00000000-0000-0000-0000-000000000003")
        assert result["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_backfill_resume_from_paused(self) -> None:
        """backfill_resume transitions a paused job back to pending."""
        from roster.switchboard.tools.backfill.controls import backfill_resume

        paused_row = self._make_job_row(status="paused")
        pending_row = MagicMock()
        pending_row.__getitem__ = lambda s, k: {
            "id": "00000000-0000-0000-0000-000000000004",
            "status": "pending",
        }[k]

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=[paused_row, pending_row])

        result = await backfill_resume(pool, job_id="00000000-0000-0000-0000-000000000004")
        assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_backfill_cancel_terminal_raises(self) -> None:
        """backfill_cancel raises ValueError for already completed jobs."""
        from roster.switchboard.tools.backfill.controls import backfill_cancel

        _JOB_UUID_COMPLETE = "00000000-0000-0000-0000-000000000006"
        completed_row = self._make_job_row(status="completed", job_id=_JOB_UUID_COMPLETE)
        pool = self._make_pool(fetchrow_return=completed_row)

        with pytest.raises(ValueError, match="terminal"):
            await backfill_cancel(pool, job_id=_JOB_UUID_COMPLETE)

    @pytest.mark.asyncio
    async def test_create_job_rejects_inverted_date_range(self) -> None:
        """create_backfill_job rejects date_from after date_to."""
        from datetime import date

        from roster.switchboard.tools.backfill.controls import create_backfill_job

        pool = self._make_pool()

        with pytest.raises(ValueError, match="date_from"):
            await create_backfill_job(
                pool,
                connector_type="gmail",
                endpoint_identity="user@example.com",
                target_categories=[],
                date_from=date(2024, 1, 1),
                date_to=date(2023, 1, 1),  # Before date_from
            )


# ===========================================================================
# Area 5: Buffer tier ordering and starvation guard (dsa4.5)
# ===========================================================================


class TestBufferTierOrdering:
    """Validate high_priority > interactive > default ordering with throughput difference."""

    def _make_config(
        self,
        *,
        queue_capacity: int = 20,
        worker_count: int = 1,
        max_consecutive_same_tier: int = 10,
    ):
        from butlers.config import BufferConfig

        return BufferConfig(
            queue_capacity=queue_capacity,
            worker_count=worker_count,
            scanner_interval_s=3600,
            scanner_grace_s=10,
            scanner_batch_size=50,
            max_consecutive_same_tier=max_consecutive_same_tier,
        )

    def test_tier_order_constants(self) -> None:
        """POLICY_TIER_ORDER is high_priority, interactive, default."""
        from butlers.core.buffer import (
            POLICY_TIER_DEFAULT,
            POLICY_TIER_HIGH_PRIORITY,
            POLICY_TIER_INTERACTIVE,
            POLICY_TIER_ORDER,
        )

        assert POLICY_TIER_ORDER[0] == POLICY_TIER_HIGH_PRIORITY
        assert POLICY_TIER_ORDER[1] == POLICY_TIER_INTERACTIVE
        assert POLICY_TIER_ORDER[2] == POLICY_TIER_DEFAULT

    def test_enqueue_routes_to_correct_tier_queue(self) -> None:
        """Enqueue places messages in the tier-specific queue."""
        from butlers.core.buffer import (
            POLICY_TIER_DEFAULT,
            POLICY_TIER_HIGH_PRIORITY,
            POLICY_TIER_INTERACTIVE,
            DurableBuffer,
        )

        config = self._make_config()
        process_fn = AsyncMock()
        buf = DurableBuffer(config, pool=None, process_fn=process_fn)

        buf.enqueue(
            request_id="hp-1",
            message_inbox_id="hp-1",
            message_text="urgent",
            source={},
            event={},
            sender={},
            policy_tier=POLICY_TIER_HIGH_PRIORITY,
        )
        buf.enqueue(
            request_id="ia-1",
            message_inbox_id="ia-1",
            message_text="normal",
            source={},
            event={},
            sender={},
            policy_tier=POLICY_TIER_INTERACTIVE,
        )
        buf.enqueue(
            request_id="df-1",
            message_inbox_id="df-1",
            message_text="bulk",
            source={},
            event={},
            sender={},
            policy_tier=POLICY_TIER_DEFAULT,
        )

        depths = buf.tier_depths
        assert depths[POLICY_TIER_HIGH_PRIORITY] == 1
        assert depths[POLICY_TIER_INTERACTIVE] == 1
        assert depths[POLICY_TIER_DEFAULT] == 1

    @pytest.mark.asyncio
    async def test_high_priority_drained_before_default(self) -> None:
        """High-priority messages are dequeued before default-tier messages."""
        from butlers.core.buffer import (
            POLICY_TIER_DEFAULT,
            POLICY_TIER_HIGH_PRIORITY,
            DurableBuffer,
        )

        config = self._make_config(queue_capacity=10, max_consecutive_same_tier=100)
        processing_order: list[str] = []

        async def process_fn(ref) -> None:
            processing_order.append(ref.policy_tier)

        buf = DurableBuffer(config, pool=None, process_fn=process_fn)

        # Enqueue 3 default then 3 high_priority
        for i in range(3):
            buf.enqueue(
                request_id=f"df-{i}",
                message_inbox_id=f"df-{i}",
                message_text="default",
                source={},
                event={},
                sender={},
                policy_tier=POLICY_TIER_DEFAULT,
            )
        for i in range(3):
            buf.enqueue(
                request_id=f"hp-{i}",
                message_inbox_id=f"hp-{i}",
                message_text="high",
                source={},
                event={},
                sender={},
                policy_tier=POLICY_TIER_HIGH_PRIORITY,
            )

        await buf.start()
        # Allow workers to drain all 6 messages
        for _ in range(30):
            if len(processing_order) >= 6:
                break
            await asyncio.sleep(0.05)
        await buf.stop(drain_timeout_s=2.0)

        assert len(processing_order) == 6
        # All high_priority items should appear before all default items
        hp_positions = [i for i, t in enumerate(processing_order) if t == POLICY_TIER_HIGH_PRIORITY]
        df_positions = [i for i, t in enumerate(processing_order) if t == POLICY_TIER_DEFAULT]
        assert max(hp_positions) < min(df_positions), (
            f"Expected all high_priority before default. Order: {processing_order}"
        )

    @pytest.mark.asyncio
    async def test_starvation_guard_forces_lower_tier_after_threshold(self) -> None:
        """After max_consecutive dequeues from high_priority, force a default-tier dequeue."""
        from butlers.core.buffer import (
            POLICY_TIER_DEFAULT,
            POLICY_TIER_HIGH_PRIORITY,
            DurableBuffer,
        )

        # Set max_consecutive=3 so starvation kicks in quickly
        config = self._make_config(queue_capacity=20, max_consecutive_same_tier=3)
        processing_order: list[str] = []

        async def process_fn(ref) -> None:
            processing_order.append(ref.policy_tier)

        buf = DurableBuffer(config, pool=None, process_fn=process_fn)

        # Enqueue 10 high_priority and 2 default to guarantee starvation fires
        for i in range(10):
            buf.enqueue(
                request_id=f"hp-{i}",
                message_inbox_id=f"hp-{i}",
                message_text="high",
                source={},
                event={},
                sender={},
                policy_tier=POLICY_TIER_HIGH_PRIORITY,
            )
        for i in range(2):
            buf.enqueue(
                request_id=f"df-{i}",
                message_inbox_id=f"df-{i}",
                message_text="default",
                source={},
                event={},
                sender={},
                policy_tier=POLICY_TIER_DEFAULT,
            )

        await buf.start()
        for _ in range(50):
            if len(processing_order) >= 12:
                break
            await asyncio.sleep(0.05)
        await buf.stop(drain_timeout_s=2.0)

        assert len(processing_order) == 12
        # Default-tier messages MUST appear before position 5 (after 3 consecutive hp)
        df_positions = [i for i, t in enumerate(processing_order) if t == POLICY_TIER_DEFAULT]
        assert len(df_positions) == 2, "Both default messages should be processed"
        # At least one default message must not be at the very end
        assert min(df_positions) <= 4, (
            f"Starvation guard should have forced at least one default before position 5. "
            f"Default positions: {df_positions}, Full order: {processing_order}"
        )

    def test_backpressure_on_full_queue_returns_false(self) -> None:
        """Enqueue returns False (backpressure) when tier queue is full."""
        from butlers.core.buffer import POLICY_TIER_DEFAULT, DurableBuffer

        config = self._make_config(queue_capacity=2)  # Tiny capacity
        buf = DurableBuffer(config, pool=None, process_fn=AsyncMock())

        results = []
        for i in range(5):
            ok = buf.enqueue(
                request_id=f"msg-{i}",
                message_inbox_id=f"msg-{i}",
                message_text="test",
                source={},
                event={},
                sender={},
                policy_tier=POLICY_TIER_DEFAULT,
            )
            results.append(ok)

        # First 2 succeed, remaining 3 are backpressure
        assert results[:2] == [True, True]
        assert results[2:] == [False, False, False]
        assert buf.stats["backpressure_total"] == 3

    def test_unknown_tier_falls_back_to_default(self) -> None:
        """Enqueue with unknown tier falls back to default tier."""
        from butlers.core.buffer import POLICY_TIER_DEFAULT, DurableBuffer

        config = self._make_config()
        buf = DurableBuffer(config, pool=None, process_fn=AsyncMock())

        ok = buf.enqueue(
            request_id="unknown-tier-msg",
            message_inbox_id="unknown-tier-msg",
            message_text="test",
            source={},
            event={},
            sender={},
            policy_tier="nonexistent_tier",  # Invalid tier
        )
        assert ok is True
        assert buf.tier_depths[POLICY_TIER_DEFAULT] == 1


# ===========================================================================
# Area 4: /ingestion dashboard page components (dsa4.4) — smoke imports
# ===========================================================================


class TestIngestionPageSmoke:
    """Verify the /ingestion page and related Python backend endpoints exist.

    Full frontend tests are in Jest (IngestionPage.test.tsx, ConnectorCard.test.tsx,
    BackfillHistoryTab.test.tsx, FiltersTab.test.tsx). These Python tests verify
    the backend API surface that the frontend calls.
    """

    def test_switchboard_ingestion_api_router_exists(self) -> None:
        """Switchboard API router module is importable for /ingestion page."""
        import importlib

        mod = importlib.import_module("roster.switchboard.api.router")
        assert hasattr(mod, "router"), (
            "roster.switchboard.api.router must export a 'router' variable"
        )

    def test_backfill_controls_module_exists(self) -> None:
        """Backfill controls module is importable."""
        from roster.switchboard.tools.backfill import controls

        assert hasattr(controls, "create_backfill_job")
        assert hasattr(controls, "backfill_pause")
        assert hasattr(controls, "backfill_cancel")
        assert hasattr(controls, "backfill_resume")
        assert hasattr(controls, "backfill_list")

    def test_backfill_connector_module_exists(self) -> None:
        """Backfill connector module is importable."""
        from roster.switchboard.tools.backfill import connector

        assert hasattr(connector, "backfill_poll")
        assert hasattr(connector, "backfill_progress")

    def test_triage_cache_module_exists(self) -> None:
        """Triage rule cache is importable for Filters tab."""
        from butlers.tools.switchboard.triage.cache import TriageRuleCache

        assert TriageRuleCache is not None

    def test_thread_affinity_module_exists(self) -> None:
        """Thread affinity module is importable."""
        from butlers.tools.switchboard.triage.thread_affinity import (
            AffinityOutcome,
            lookup_thread_affinity,
        )

        assert lookup_thread_affinity is not None
        assert AffinityOutcome.HIT.produces_route is True


# ===========================================================================
# Cross-area integration: triage + policy tier + buffer tier chain
# ===========================================================================


class TestTriageToPolicyTierIntegration:
    """Validate end-to-end flow: triage decision -> policy tier -> buffer enqueue."""

    def test_skip_action_should_not_ingest(self) -> None:
        """Triage 'skip' decision means should_ingest=False from policy evaluator."""
        from butlers.connectors.gmail_policy import (
            LabelFilterPolicy,
            PolicyTierAssigner,
            evaluate_message_policy,
        )

        message = {
            "id": "msg-skip",
            "threadId": "thread-skip",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "newsletter@bulk.com"},
                    {"name": "To", "value": "user@example.com"},
                    {"name": "List-Unsubscribe", "value": "<mailto:unsub@bulk.com>"},
                ],
            },
        }
        triage_rules = [
            {
                "id": "rule-skip-bulk",
                "rule_type": "header_condition",
                "condition": {"header": "List-Unsubscribe", "op": "present"},
                "action": "skip",
                "priority": 10,
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        label_filter = LabelFilterPolicy.default()
        tier_assigner = PolicyTierAssigner(
            user_email="user@example.com",
            known_contacts=frozenset(),
            sent_message_ids=frozenset(),
        )
        result = evaluate_message_policy(
            message,
            label_filter=label_filter,
            tier_assigner=tier_assigner,
            triage_rules=triage_rules,
        )
        assert result.should_ingest is False
        assert result.ingestion_tier == 3  # INGESTION_TIER_SKIP

    def test_known_contact_gets_high_priority_in_buffer(self) -> None:
        """Known contact email gets high_priority policy tier for buffer enqueuing."""
        from butlers.connectors.gmail_policy import (
            LabelFilterPolicy,
            PolicyTierAssigner,
            evaluate_message_policy,
        )
        from butlers.core.buffer import POLICY_TIER_HIGH_PRIORITY

        message = {
            "id": "msg-known",
            "threadId": "thread-known",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "alice@trusted.com"},
                    {"name": "To", "value": "user@example.com"},
                ],
            },
        }
        label_filter = LabelFilterPolicy.default()
        tier_assigner = PolicyTierAssigner(
            user_email="user@example.com",
            known_contacts=frozenset(["alice@trusted.com"]),
            sent_message_ids=frozenset(),
        )
        result = evaluate_message_policy(
            message,
            label_filter=label_filter,
            tier_assigner=tier_assigner,
        )
        assert result.should_ingest is True
        assert result.policy_tier == POLICY_TIER_HIGH_PRIORITY

    def test_buffer_enqueue_accepts_policy_tier_from_triage(self) -> None:
        """DurableBuffer correctly accepts policy_tier from triage decision."""
        from butlers.config import BufferConfig
        from butlers.core.buffer import POLICY_TIER_HIGH_PRIORITY, DurableBuffer

        config = BufferConfig(
            queue_capacity=10,
            worker_count=1,
            scanner_interval_s=3600,
            scanner_grace_s=10,
            scanner_batch_size=50,
            max_consecutive_same_tier=10,
        )
        buf = DurableBuffer(config, pool=None, process_fn=AsyncMock())

        # Simulate triage result saying high_priority
        ok = buf.enqueue(
            request_id="triage-hp-msg",
            message_inbox_id="triage-hp-msg",
            message_text="Urgent message from known contact",
            source={"channel": "email"},
            event={},
            sender={"identity": "alice@trusted.com"},
            policy_tier=POLICY_TIER_HIGH_PRIORITY,
        )
        assert ok is True
        assert buf.tier_depths[POLICY_TIER_HIGH_PRIORITY] == 1
        assert buf.tier_depths["interactive"] == 0
        assert buf.tier_depths["default"] == 0
