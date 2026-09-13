"""Real-PostgreSQL recovery tests for RFC 0023's notification-only worker."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from butlers.core.approval_delivery_worker import (
    ApprovalDeliveryWorker,
    DeliveryClaim,
    HandoffResult,
)
from butlers.db import register_jsonb_codec
from butlers.modules.approvals.delivery_recovery import (
    ApprovalDeliveryRenderer,
    ApprovalDeliveryRepository,
)
from butlers.modules.approvals.park import ParkAdmission, park_pending_action
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "approvals"],
    )


@pytest.fixture
async def delivery_pool(migrated_db_url: str):
    pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=8,
        init=register_jsonb_codec,
    )
    await pool.execute(
        "TRUNCATE approval_delivery_attempts, approval_delivery_cohort_members, "
        "approval_delivery_presentations, approval_delivery_cohorts, "
        "approval_delivery_intents, approval_events, pending_actions CASCADE"
    )
    await pool.execute(
        "UPDATE public.approvals_policy SET quiet_start_hour = NULL, "
        "quiet_end_hour = NULL, timezone = 'UTC' WHERE id = 1"
    )
    yield pool
    await pool.close()


async def _park(pool: asyncpg.Pool, *, ordinal: int = 0) -> ParkAdmission:
    now = datetime.now(UTC) + timedelta(microseconds=ordinal)
    return await park_pending_action(
        pool,
        action_id=uuid.uuid4(),
        tool_name="relationship_assert_fact",
        tool_args={"ordinal": ordinal},
        agent_summary="Synthetic approval recovery fixture",
        requested_at=now,
        expires_at=now + timedelta(hours=72),
        why="Current synthetic reason",
        evidence=[],
        blast_radius="contact",
        reversibility="compensable",
        origin_butler="relationship",
    )


class _Runtime:
    def __init__(
        self,
        *,
        recipient: str | None = "owner-recipient-sentinel",
        secret: str | None = "callback-secret-sentinel",
        handoff_result: HandoffResult | Exception | None = None,
        reconcile_result: HandoffResult | Exception | None = None,
    ) -> None:
        self.recipient = recipient
        self.secret = secret
        self.handoff_result = handoff_result or HandoffResult("confirmed")
        self.reconcile_result = reconcile_result or HandoffResult(
            "ambiguous", "provider_outcome_unknown"
        )
        self.handoffs: list[tuple[DeliveryClaim, dict[str, Any]]] = []
        self.reconciliations: list[DeliveryClaim] = []

    async def resolve_verified_owner_recipient(self) -> str | None:
        return self.recipient

    async def resolve_callback_secret(self) -> str | None:
        return self.secret

    async def handoff(self, claim: DeliveryClaim, envelope: dict[str, Any]) -> HandoffResult:
        self.handoffs.append((claim, envelope))
        if isinstance(self.handoff_result, Exception):
            raise self.handoff_result
        return self.handoff_result

    async def reconcile(self, claim: DeliveryClaim) -> HandoffResult:
        self.reconciliations.append(claim)
        if isinstance(self.reconcile_result, Exception):
            raise self.reconcile_result
        return self.reconcile_result


class _SlowRuntime(_Runtime):
    """Hold one external operation beyond a test lease until explicitly released."""

    def __init__(self, operation: str) -> None:
        super().__init__()
        self.operation = operation
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def handoff(self, claim: DeliveryClaim, envelope: dict[str, Any]) -> HandoffResult:
        if self.operation != "handoff":
            return await super().handoff(claim, envelope)
        self.handoffs.append((claim, envelope))
        self.started.set()
        await self.release.wait()
        return HandoffResult("confirmed")

    async def reconcile(self, claim: DeliveryClaim) -> HandoffResult:
        if self.operation != "reconcile":
            return await super().reconcile(claim)
        self.reconciliations.append(claim)
        self.started.set()
        await self.release.wait()
        return HandoffResult("confirmed")


async def test_skip_locked_claims_are_distinct_and_stale_fences_cannot_write(
    delivery_pool: asyncpg.Pool,
) -> None:
    """Concurrent workers never share authority and an expired owner stays fenced."""
    await _park(delivery_pool, ordinal=1)
    await _park(delivery_pool, ordinal=2)
    first_repo = ApprovalDeliveryRepository(delivery_pool)
    second_repo = ApprovalDeliveryRepository(delivery_pool)

    first, second = await asyncio.gather(first_repo.claim_next(), second_repo.claim_next())

    assert first is not None and second is not None
    assert first.presentation_id != second.presentation_id
    assert first.claim_fence == second.claim_fence == 1
    await delivery_pool.execute(
        "UPDATE approval_delivery_presentations "
        "SET claim_expires_at = clock_timestamp() - interval '1 second' WHERE id = $1",
        first.presentation_id,
    )
    successor = await second_repo.claim_next()
    assert successor is not None
    assert successor.presentation_id == first.presentation_id
    assert successor.claim_fence == first.claim_fence + 1
    assert successor.claim_token != first.claim_token
    assert await first_repo.heartbeat(first) is False
    assert await first_repo.record_prestart_retry(first, "transport_unavailable") is False
    assert await second_repo.heartbeat(successor) is True
    assert await second_repo.mark_handoff_started(successor) is True
    assert await first_repo.complete_handoff(first, HandoffResult("confirmed")) is False
    assert await second_repo.complete_handoff(successor, HandoffResult("confirmed")) is True


async def test_missing_current_owner_is_prestart_retry_scheduled_from_database_time(
    delivery_pool: asyncpg.Pool,
) -> None:
    """Recipient resolution failure records safe evidence without a handoff."""
    admission = await _park(delivery_pool)
    runtime = _Runtime(recipient=None)
    worker = ApprovalDeliveryWorker(
        ApprovalDeliveryRepository(delivery_pool),
        ApprovalDeliveryRenderer(dashboard_base_url="https://dashboard.example.test"),
        runtime,
    )

    assert await worker.process_one() is True

    row = await delivery_pool.fetchrow(
        """
        SELECT state, last_reason_code, attempt_count,
               next_attempt_at > clock_timestamp() AS retry_is_future
          FROM approval_delivery_presentations WHERE presentation_key = $1
        """,
        admission.presentation_key,
    )
    assert dict(row) == {
        "state": "retry_wait",
        "last_reason_code": "owner_recipient_unavailable",
        "attempt_count": 1,
        "retry_is_future": True,
    }
    assert runtime.handoffs == []
    assert await delivery_pool.fetchval(
        "SELECT outcome = 'safe_retry' FROM approval_delivery_attempts"
    )


async def test_confirmed_delivery_renders_current_material_only_in_memory(
    delivery_pool: asyncpg.Pool,
) -> None:
    """The worker rereads the action and persists no recipient, token, or message."""
    admission = await _park(delivery_pool)
    await delivery_pool.execute(
        "UPDATE pending_actions SET why = 'updated-render-sentinel' WHERE id = $1",
        admission.action_id,
    )
    runtime = _Runtime(handoff_result=HandoffResult("confirmed", provider_reference="ref-1"))
    worker = ApprovalDeliveryWorker(
        ApprovalDeliveryRepository(delivery_pool),
        ApprovalDeliveryRenderer(dashboard_base_url="https://dashboard.example.test"),
        runtime,
    )

    assert await worker.process_one() is True

    assert len(runtime.handoffs) == 1
    _, envelope = runtime.handoffs[0]
    assert "updated-render-sentinel" in envelope["delivery"]["message"]
    assert envelope["delivery"]["recipient"] == "owner-recipient-sentinel"
    callback_token = envelope["actions"][0]["callback_token"]
    persisted = await delivery_pool.fetchval(
        """
        SELECT concat_ws(' ', i::text, p::text, string_agg(a::text, ' '))
          FROM approval_delivery_intents AS i
          JOIN approval_delivery_presentations AS p ON p.intent_id = i.id
          JOIN approval_delivery_attempts AS a ON a.presentation_id = p.id
         WHERE i.action_id = $1 GROUP BY i, p
        """,
        admission.action_id,
    )
    assert "owner-recipient-sentinel" not in persisted
    assert callback_token not in persisted
    assert "updated-render-sentinel" not in persisted
    assert (
        await delivery_pool.fetchval(
            "SELECT state FROM approval_delivery_presentations WHERE presentation_key = $1",
            admission.presentation_key,
        )
        == "delivered"
    )


async def test_expired_handoff_reconciles_once_then_ambiguous_never_resends(
    delivery_pool: asyncpg.Pool,
) -> None:
    """A crash after send start can reconcile but cannot issue another handoff."""
    await _park(delivery_pool)
    repository = ApprovalDeliveryRepository(delivery_pool)
    original = await repository.claim_next()
    assert original is not None
    assert await repository.mark_handoff_started(original) is True
    await delivery_pool.execute(
        "UPDATE approval_delivery_presentations "
        "SET claim_expires_at = clock_timestamp() - interval '1 second' WHERE id = $1",
        original.presentation_id,
    )
    runtime = _Runtime()
    worker = ApprovalDeliveryWorker(repository, ApprovalDeliveryRenderer(), runtime)

    assert await worker.process_one() is True
    assert await worker.process_one() is False

    assert runtime.handoffs == []
    assert len(runtime.reconciliations) == 1
    assert runtime.reconciliations[0].presentation_key == original.presentation_key
    assert (
        await delivery_pool.fetchval(
            "SELECT state FROM approval_delivery_presentations WHERE id = $1",
            original.presentation_id,
        )
        == "ambiguous"
    )
    snapshot = await repository.backlog_snapshot()
    assert snapshot.ambiguous_count == snapshot.stuck_count == 1


@pytest.mark.parametrize("operation", ["handoff", "reconcile"])
async def test_slow_external_await_renews_lease_and_prevents_claim_succession(
    delivery_pool: asyncpg.Pool,
    operation: str,
) -> None:
    """A live owner retains its fence beyond one lease and commits confirmed truth."""
    admission = await _park(delivery_pool)
    repository = ApprovalDeliveryRepository(delivery_pool, lease_seconds=1)
    if operation == "reconcile":
        original = await repository.claim_next()
        assert original is not None
        assert await repository.mark_handoff_started(original) is True
        await delivery_pool.execute(
            "UPDATE approval_delivery_presentations "
            "SET claim_expires_at = clock_timestamp() - interval '1 second' WHERE id = $1",
            original.presentation_id,
        )
    runtime = _SlowRuntime(operation)
    worker = ApprovalDeliveryWorker(
        repository,
        ApprovalDeliveryRenderer(),
        runtime,
        heartbeat_interval_s=0.2,
    )

    processing = asyncio.create_task(worker.process_one())
    await asyncio.wait_for(runtime.started.wait(), timeout=2)
    await asyncio.sleep(1.2)
    assert await ApprovalDeliveryRepository(delivery_pool, lease_seconds=1).claim_next() is None
    runtime.release.set()

    assert await asyncio.wait_for(processing, timeout=2) is True
    assert (
        await delivery_pool.fetchval(
            "SELECT state FROM approval_delivery_presentations WHERE presentation_key = $1",
            admission.presentation_key,
        )
        == "delivered"
    )


async def test_unknown_post_start_handoff_is_ambiguous_without_resend(
    delivery_pool: asyncpg.Pool,
) -> None:
    """A lost provider result is quarantined on the first attempt."""
    admission = await _park(delivery_pool)
    runtime = _Runtime(handoff_result=TimeoutError("synthetic lost result"))
    worker = ApprovalDeliveryWorker(
        ApprovalDeliveryRepository(delivery_pool), ApprovalDeliveryRenderer(), runtime
    )

    assert await worker.process_one() is True
    assert await worker.process_one() is False

    assert len(runtime.handoffs) == 1
    assert runtime.reconciliations == []
    assert (
        await delivery_pool.fetchval(
            "SELECT state FROM approval_delivery_presentations WHERE presentation_key = $1",
            admission.presentation_key,
        )
        == "ambiguous"
    )


async def test_worker_cancels_only_delivery_when_action_expired(
    delivery_pool: asyncpg.Pool,
) -> None:
    """Observed expiry stops notification recovery without expiring the action."""
    admission = await _park(delivery_pool)
    await delivery_pool.execute(
        "UPDATE pending_actions SET expires_at = clock_timestamp() - interval '1 second' "
        "WHERE id = $1",
        admission.action_id,
    )
    runtime = _Runtime()
    worker = ApprovalDeliveryWorker(
        ApprovalDeliveryRepository(delivery_pool), ApprovalDeliveryRenderer(), runtime
    )

    assert await worker.process_one() is False

    row = await delivery_pool.fetchrow(
        """
        SELECT pa.status, p.state, p.last_reason_code
          FROM pending_actions AS pa
          JOIN approval_delivery_intents AS i ON i.action_id = pa.id
          JOIN approval_delivery_presentations AS p ON p.intent_id = i.id
         WHERE pa.id = $1
        """,
        admission.action_id,
    )
    assert dict(row) == {
        "status": "pending",
        "state": "cancelled",
        "last_reason_code": "action_expired",
    }
    assert runtime.handoffs == []


async def test_late_handoff_result_is_evidence_only_after_expiry_cancellation(
    delivery_pool: asyncpg.Pool,
) -> None:
    """A result after cancellation is retained without reviving recovery or action state."""
    admission = await _park(delivery_pool)
    repository = ApprovalDeliveryRepository(delivery_pool)
    claim = await repository.claim_next()
    assert claim is not None
    assert await repository.mark_handoff_started(claim) is True
    await delivery_pool.execute(
        "UPDATE pending_actions SET expires_at = clock_timestamp() - interval '1 second' "
        "WHERE id = $1",
        admission.action_id,
    )
    assert await repository.cancel_ineligible_presentations() == 1

    assert await repository.complete_handoff(claim, HandoffResult("confirmed")) is True

    row = await delivery_pool.fetchrow(
        """
        SELECT pa.status, p.state,
               count(a.id) FILTER (WHERE a.outcome = 'confirmed')::integer AS confirmations
          FROM pending_actions AS pa
          JOIN approval_delivery_intents AS i ON i.action_id = pa.id
          JOIN approval_delivery_presentations AS p ON p.intent_id = i.id
          JOIN approval_delivery_attempts AS a ON a.presentation_id = p.id
         WHERE pa.id = $1 GROUP BY pa.status, p.state
        """,
        admission.action_id,
    )
    assert dict(row) == {
        "status": "pending",
        "state": "cancelled",
        "confirmations": 1,
    }


async def test_collapsed_action_never_handoffs_and_digest_uses_current_membership(
    delivery_pool: asyncpg.Pool,
) -> None:
    """The fourth/fifth roots share one cohort call; collapsed roots remain local."""
    admissions = [await _park(delivery_pool, ordinal=index) for index in range(5)]
    assert [item.admission_mode for item in admissions] == [
        "single",
        "single",
        "single",
        "cohort_anchor",
        "collapsed",
    ]
    await delivery_pool.execute(
        """
        UPDATE approval_delivery_presentations
           SET state = 'delivered', next_attempt_at = NULL, updated_at = clock_timestamp()
         WHERE presentation_mode = 'single'
        """
    )
    runtime = _Runtime()
    worker = ApprovalDeliveryWorker(
        ApprovalDeliveryRepository(delivery_pool), ApprovalDeliveryRenderer(), runtime
    )

    assert await worker.process_one() is True
    assert len(runtime.handoffs) == 1
    claim, envelope = runtime.handoffs[0]
    assert claim.presentation_mode == "burst_digest"
    assert envelope["delivery"]["message"].startswith("2 actions awaiting review.")
    collapsed_key = admissions[-1].presentation_key
    assert (
        await delivery_pool.fetchval(
            "SELECT state FROM approval_delivery_presentations WHERE presentation_key = $1",
            collapsed_key,
        )
        == "collapsed"
    )
    assert (
        await delivery_pool.fetchval(
            """
        SELECT count(*) FROM approval_delivery_attempts AS a
        JOIN approval_delivery_presentations AS p ON p.id = a.presentation_id
        WHERE p.presentation_key = $1
        """,
            collapsed_key,
        )
        == 0
    )


async def test_worker_processes_existing_successor_without_advancing_generation(
    delivery_pool: asyncpg.Pool,
) -> None:
    """A dashboard-authored later generation is due work, not worker scheduling authority."""
    admission = await _park(delivery_pool)
    await delivery_pool.execute(
        """
        UPDATE approval_delivery_presentations
           SET state = 'superseded', last_reason_code = 'defer_rescheduled',
               next_attempt_at = NULL, updated_at = clock_timestamp()
         WHERE presentation_key = $1
        """,
        admission.presentation_key,
    )
    await delivery_pool.execute(
        """
        INSERT INTO approval_delivery_presentations (
            intent_id, subject_key, subject_kind, presentation_mode,
            presentation_generation, presentation_key, state, not_before,
            next_attempt_at
        )
        SELECT id, action_key, 'action', 'single', 2,
               action_key || ':p:2', 'ready', clock_timestamp(), clock_timestamp()
          FROM approval_delivery_intents WHERE action_id = $1
        """,
        admission.action_id,
    )
    runtime = _Runtime()
    worker = ApprovalDeliveryWorker(
        ApprovalDeliveryRepository(delivery_pool), ApprovalDeliveryRenderer(), runtime
    )

    assert await worker.process_one() is True

    assert runtime.handoffs[0][0].presentation_generation == 2
    assert runtime.handoffs[0][0].presentation_key.endswith(":p:2")
    assert await delivery_pool.fetchval("SELECT count(*) FROM approval_delivery_presentations") == 2


async def test_stuck_age_starts_at_due_time_not_presentation_creation(
    delivery_pool: asyncpg.Pool,
) -> None:
    """Long-held quiet work is fresh when released and stuck only after 15 due minutes."""
    admission = await _park(delivery_pool)
    await delivery_pool.execute(
        """
        UPDATE approval_delivery_presentations
           SET created_at = clock_timestamp() - interval '2 hours',
               not_before = clock_timestamp() - interval '1 second',
               next_attempt_at = clock_timestamp() - interval '1 second'
         WHERE presentation_key = $1
        """,
        admission.presentation_key,
    )
    repository = ApprovalDeliveryRepository(delivery_pool)

    fresh = await repository.backlog_snapshot()
    assert fresh.due_count == 1
    assert fresh.stuck_count == 0
    assert fresh.oldest_due_age_seconds is not None
    assert fresh.oldest_due_age_seconds < 15 * 60

    await delivery_pool.execute(
        """
        UPDATE approval_delivery_presentations
           SET not_before = clock_timestamp() - interval '16 minutes',
               next_attempt_at = clock_timestamp() - interval '16 minutes'
         WHERE presentation_key = $1
        """,
        admission.presentation_key,
    )
    overdue = await repository.backlog_snapshot()
    assert overdue.due_count == 1
    assert overdue.stuck_count == 1
    assert overdue.oldest_due_age_seconds is not None
    assert overdue.oldest_due_age_seconds >= 16 * 60
