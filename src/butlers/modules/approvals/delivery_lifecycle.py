"""Atomic domain-action to approval-delivery lifecycle transitions.

This is the sole bridge from canonical approval decisions/expiry/defer into
the notification-only recovery state.  It locks the action first, then its
intent and presentations/cohort membership, and never performs provider work.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from butlers.metrics_registry import get_or_create_counter
from butlers.modules.approvals.events import ApprovalEventType, record_approval_event
from butlers.modules.approvals.executor import _approval_write_transaction
from butlers.modules.approvals.models import ActionStatus, PendingAction

logger = logging.getLogger(__name__)

approval_delivery_lifecycle_total = get_or_create_counter(
    "approval_delivery_lifecycle_total",
    "Approval delivery lifecycle transitions by safe operation and reason.",
    labelnames=["operation", "reason"],
)

DeliveryTerminalReason = Literal[
    "action_approved",
    "action_rejected",
    "action_expired",
    "action_abandoned",
]

_EVENT_FOR_STATUS = {
    ActionStatus.APPROVED: ApprovalEventType.ACTION_APPROVED,
    ActionStatus.REJECTED: ApprovalEventType.ACTION_REJECTED,
    ActionStatus.EXPIRED: ApprovalEventType.ACTION_EXPIRED,
}
_REASON_FOR_STATUS: dict[ActionStatus, DeliveryTerminalReason] = {
    ActionStatus.APPROVED: "action_approved",
    ActionStatus.REJECTED: "action_rejected",
    ActionStatus.EXPIRED: "action_expired",
}
_PRESTART_STATES = ("ready", "claimed", "retry_wait")


def _affected_rows(command_tag: object) -> int:
    """Parse asyncpg's command tag; non-asyncpg test doubles mean no rows."""
    if not isinstance(command_tag, str):
        return 0
    try:
        return int(command_tag.rsplit(" ", 1)[-1])
    except ValueError:
        return 0


@dataclass(frozen=True, slots=True)
class PendingTransition:
    """Result of one serialized pending-action transition."""

    action: PendingAction | None
    changed: bool
    expired_instead: bool = False
    success_observation: LifecycleSuccessObservation | None = None


@dataclass(frozen=True, slots=True)
class DeferTransition:
    """Result of one serialized authenticated defer."""

    action: PendingAction | None
    changed: bool
    expired_instead: bool = False
    delivery_missing: bool = False
    success_observation: LifecycleSuccessObservation | None = None


@dataclass(frozen=True, slots=True)
class LifecycleSuccessObservation:
    """Content-blind success data emitted only after transaction commit."""

    operation: Literal["terminal", "defer"]
    reason: str
    terminal_status: str | None = None
    cancelled_count: int = 0
    successor_generation: int | None = None
    superseded_count: int = 0
    cohort_membership_changed: bool = False


def emit_lifecycle_success(observation: LifecycleSuccessObservation | None) -> None:
    """Emit a committed lifecycle transition's bounded metric and log."""
    if observation is None:
        return
    approval_delivery_lifecycle_total.labels(
        operation=observation.operation,
        reason=observation.reason,
    ).inc()
    if observation.cohort_membership_changed:
        approval_delivery_lifecycle_total.labels(
            operation="cohort_replacement",
            reason=observation.reason,
        ).inc()
    if observation.operation == "terminal":
        logger.info(
            "approval delivery terminalized operation=%s reason=%s cancelled=%d",
            observation.terminal_status,
            observation.reason,
            observation.cancelled_count,
        )
        return
    logger.info(
        "approval delivery deferred generation=%d superseded=%d cohort_member=%s",
        observation.successor_generation,
        observation.superseded_count,
        observation.cohort_membership_changed,
    )


@asynccontextmanager
async def _write_scope(
    source: Any,
    *,
    already_in_transaction: bool,
    success_observations: list[LifecycleSuccessObservation],
):
    if already_in_transaction:
        yield source
        return
    async with _approval_write_transaction(source) as connection:
        yield connection
    for observation in success_observations:
        emit_lifecycle_success(observation)


async def _lock_delivery_root(connection: Any, action_id: uuid.UUID) -> Any | None:
    available = await connection.fetchval(
        "SELECT to_regclass('approval_delivery_intents') IS NOT NULL"
    )
    if not available:
        return None
    return await connection.fetchrow(
        """
        SELECT id, action_key, admission_mode
          FROM approval_delivery_intents
         WHERE action_id = $1
         FOR UPDATE
        """,
        action_id,
    )


async def _mark_cohort_membership_ineligible(
    connection: Any,
    *,
    intent_id: uuid.UUID,
) -> tuple[uuid.UUID | None, bool]:
    member = await connection.fetchrow(
        """
        SELECT cohort_id, eligible
          FROM approval_delivery_cohort_members
         WHERE intent_id = $1
         FOR UPDATE
        """,
        intent_id,
    )
    if member is None:
        return None, False
    cohort_id = member["cohort_id"]
    await connection.fetchval(
        "SELECT id FROM approval_delivery_cohorts WHERE id = $1 FOR UPDATE",
        cohort_id,
    )
    changed = bool(member["eligible"])
    if changed:
        await connection.execute(
            "UPDATE approval_delivery_cohort_members SET eligible = false "
            "WHERE cohort_id = $1 AND intent_id = $2",
            cohort_id,
            intent_id,
        )
    return cohort_id, changed


async def _cancel_empty_cohort_before_handoff(connection: Any, cohort_id: uuid.UUID) -> int:
    eligible = await connection.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
              FROM approval_delivery_cohort_members AS m
              JOIN approval_delivery_intents AS i ON i.id = m.intent_id
              JOIN pending_actions AS pa ON pa.id = i.action_id
             WHERE m.cohort_id = $1 AND m.eligible AND pa.status = 'pending'
               AND (pa.expires_at IS NULL OR pa.expires_at > clock_timestamp())
        )
        """,
        cohort_id,
    )
    if eligible:
        return 0
    tag = await connection.execute(
        """
        UPDATE approval_delivery_presentations
           SET state = 'cancelled', last_reason_code = 'cohort_empty',
               next_attempt_at = NULL, claim_token = NULL,
               claim_expires_at = NULL, updated_at = clock_timestamp()
         WHERE cohort_id = $1 AND state = ANY($2::text[])
        """,
        cohort_id,
        list(_PRESTART_STATES),
    )
    return _affected_rows(tag)


async def _record_delivery_summary(
    connection: Any,
    *,
    action_id: uuid.UUID,
    actor: str,
    reason_code: str,
    cancelled_count: int = 0,
    superseded_count: int = 0,
    successor_generation: int | None = None,
    cohort_membership_changed: bool = False,
    ambiguous_count: int = 0,
    delivered_count: int = 0,
    attempt_count: int = 0,
    occurred_at: Any,
) -> None:
    await record_approval_event(
        connection,
        ApprovalEventType.APPROVAL_DELIVERY_TERMINAL,
        actor=actor,
        action_id=action_id,
        reason=reason_code,
        metadata={
            "reason_code": reason_code,
            "cancelled_count": cancelled_count,
            "superseded_count": superseded_count,
            "successor_generation": successor_generation,
            "cohort_membership_changed": cohort_membership_changed,
            "ambiguous_count": ambiguous_count,
            "delivered_count": delivered_count,
            "attempt_count": attempt_count,
        },
        occurred_at=occurred_at,
    )


async def _delivery_evidence_counts(connection: Any, intent_id: uuid.UUID) -> tuple[int, int, int]:
    row = await connection.fetchrow(
        """
        SELECT count(*) FILTER (WHERE state = 'ambiguous')::integer AS ambiguous_count,
               count(*) FILTER (WHERE state = 'delivered')::integer AS delivered_count,
               COALESCE(sum(attempt_count), 0)::integer AS attempt_count
          FROM approval_delivery_presentations
         WHERE intent_id = $1
        """,
        intent_id,
    )
    return (
        int(row["ambiguous_count"]),
        int(row["delivered_count"]),
        int(row["attempt_count"]),
    )


async def transition_pending_action(
    source: Any,
    *,
    action_id: uuid.UUID,
    target_status: Literal[ActionStatus.APPROVED, ActionStatus.REJECTED, ActionStatus.EXPIRED],
    decided_by: str,
    event_actor: str,
    event_reason: str,
    event_metadata: dict[str, Any] | None = None,
    now: Any | None = None,
    _already_in_transaction: bool = False,
) -> PendingTransition:
    """Atomically leave pending state and fence that action's delivery work."""
    success_observations: list[LifecycleSuccessObservation] = []
    async with _write_scope(
        source,
        already_in_transaction=_already_in_transaction,
        success_observations=success_observations,
    ) as connection:
        row = await connection.fetchrow(
            "SELECT * FROM pending_actions WHERE id = $1 FOR UPDATE",
            action_id,
        )
        if row is None:
            return PendingTransition(None, False)
        action = PendingAction.from_row(row)
        if action.status != ActionStatus.PENDING:
            return PendingTransition(action, False)

        database_now = now or await connection.fetchval("SELECT clock_timestamp()")
        # Lightweight unit-test databases can omit the scalar result. Real
        # PostgreSQL always supplies it; keep those doubles deterministic
        # without weakening the database-time production boundary.
        if not isinstance(database_now, datetime):
            database_now = datetime.now(UTC)
        effective_status = target_status
        expired_instead = False
        if (
            target_status != ActionStatus.EXPIRED
            and action.expires_at is not None
            and action.expires_at < database_now
        ):
            effective_status = ActionStatus.EXPIRED
            decided_by = "system:expiry"
            event_actor = "system:expiry"
            event_reason = "approval window elapsed"
            event_metadata = {"tool_name": action.tool_name}
            expired_instead = True

        updated = await connection.fetchrow(
            "UPDATE pending_actions SET status = $1, decided_by = $2, decided_at = $3 "
            "WHERE id = $4 AND status = 'pending' RETURNING *",
            effective_status.value,
            decided_by,
            database_now,
            action_id,
        )
        if updated is None:
            latest = await connection.fetchrow(
                "SELECT * FROM pending_actions WHERE id = $1",
                action_id,
            )
            return PendingTransition(PendingAction.from_row(latest) if latest else None, False)

        intent = await _lock_delivery_root(connection, action_id)
        cancelled_count = 0
        membership_changed = False
        if intent is not None:
            tag = await connection.execute(
                """
                UPDATE approval_delivery_presentations
                   SET state = 'cancelled', last_reason_code = $2,
                       next_attempt_at = NULL, claim_token = NULL,
                       claim_expires_at = NULL, updated_at = $3
                 WHERE intent_id = $1
                   AND state IN ('ready', 'claimed', 'handoff_started', 'retry_wait')
                """,
                intent["id"],
                _REASON_FOR_STATUS[effective_status],
                database_now,
            )
            cancelled_count += _affected_rows(tag)
            cohort_id, membership_changed = await _mark_cohort_membership_ineligible(
                connection,
                intent_id=intent["id"],
            )
            if cohort_id is not None:
                cancelled_count += await _cancel_empty_cohort_before_handoff(connection, cohort_id)
            ambiguous_count, delivered_count, attempt_count = await _delivery_evidence_counts(
                connection, intent["id"]
            )

        await record_approval_event(
            connection,
            _EVENT_FOR_STATUS[effective_status],
            actor=event_actor,
            action_id=action_id,
            reason=event_reason,
            metadata=event_metadata or {"tool_name": action.tool_name},
            occurred_at=database_now,
        )
        if intent is not None:
            await _record_delivery_summary(
                connection,
                action_id=action_id,
                actor=event_actor,
                reason_code=_REASON_FOR_STATUS[effective_status],
                cancelled_count=cancelled_count,
                cohort_membership_changed=membership_changed,
                ambiguous_count=ambiguous_count,
                delivered_count=delivered_count,
                attempt_count=attempt_count,
                occurred_at=database_now,
            )
        observation = LifecycleSuccessObservation(
            operation="terminal",
            reason=_REASON_FOR_STATUS[effective_status],
            terminal_status=effective_status.value,
            cancelled_count=cancelled_count,
        )
        success_observations.append(observation)
        return PendingTransition(
            PendingAction.from_row(updated),
            True,
            expired_instead=expired_instead,
            success_observation=observation,
        )


async def defer_pending_action(
    source: Any,
    *,
    action_id: uuid.UUID,
    hours: int,
    actor: str,
    now: Any | None = None,
    _already_in_transaction: bool = False,
) -> DeferTransition:
    """Append exactly one direct successor for an authenticated defer."""
    if not 1 <= hours <= 168:
        raise ValueError("hours must be between 1 and 168")
    success_observations: list[LifecycleSuccessObservation] = []
    async with _write_scope(
        source,
        already_in_transaction=_already_in_transaction,
        success_observations=success_observations,
    ) as connection:
        row = await connection.fetchrow(
            "SELECT * FROM pending_actions WHERE id = $1 FOR UPDATE",
            action_id,
        )
        if row is None:
            return DeferTransition(None, False)
        action = PendingAction.from_row(row)
        if action.status != ActionStatus.PENDING:
            return DeferTransition(action, False)

        database_now = now or await connection.fetchval("SELECT clock_timestamp()")
        if not isinstance(database_now, datetime):
            database_now = datetime.now(UTC)
        if action.expires_at is not None and action.expires_at < database_now:
            expired = await transition_pending_action(
                connection,
                action_id=action_id,
                target_status=ActionStatus.EXPIRED,
                decided_by="system:expiry",
                event_actor="system:expiry",
                event_reason="approval window elapsed",
                event_metadata={"tool_name": action.tool_name},
                now=database_now,
                _already_in_transaction=True,
            )
            if expired.success_observation is not None:
                success_observations.append(expired.success_observation)
            return DeferTransition(
                expired.action,
                expired.changed,
                expired_instead=True,
                success_observation=expired.success_observation,
            )

        intent = await _lock_delivery_root(connection, action_id)
        if intent is None:
            return DeferTransition(action, False, delivery_missing=True)

        presentations = await connection.fetch(
            """
            SELECT p.id, p.intent_id, p.presentation_generation, p.state
              FROM approval_delivery_presentations AS p
             WHERE p.intent_id = $1
                OR p.cohort_id IN (
                    SELECT m.cohort_id FROM approval_delivery_cohort_members AS m
                     WHERE m.intent_id = $1
                )
             ORDER BY p.presentation_generation, p.id
             FOR UPDATE
            """,
            intent["id"],
        )
        highest_generation = max(
            (
                int(item["presentation_generation"])
                for item in presentations
                if item["intent_id"] == intent["id"]
            ),
            default=0,
        )
        successor_generation = highest_generation + 1
        if successor_generation > 1000:
            raise ValueError("approval delivery presentation generation limit reached")

        tag = await connection.execute(
            """
            UPDATE approval_delivery_presentations
               SET state = 'superseded', last_reason_code = 'defer_rescheduled',
                   next_attempt_at = NULL, claim_token = NULL,
                   claim_expires_at = NULL, updated_at = $2
             WHERE intent_id = $1 AND state = ANY($3::text[])
            """,
            intent["id"],
            database_now,
            list(_PRESTART_STATES),
        )
        superseded_count = _affected_rows(tag)

        cohort_id, membership_changed = await _mark_cohort_membership_ineligible(
            connection,
            intent_id=intent["id"],
        )
        if cohort_id is not None:
            await _cancel_empty_cohort_before_handoff(connection, cohort_id)

        ambiguous_count, delivered_count, attempt_count = await _delivery_evidence_counts(
            connection, intent["id"]
        )

        new_expires_at = database_now + timedelta(hours=hours)
        updated = await connection.fetchrow(
            "UPDATE pending_actions SET expires_at = $1 WHERE id = $2 AND status = 'pending' "
            "RETURNING *",
            new_expires_at,
            action_id,
        )
        if updated is None:
            latest = await connection.fetchrow(
                "SELECT * FROM pending_actions WHERE id = $1",
                action_id,
            )
            return DeferTransition(PendingAction.from_row(latest) if latest else None, False)

        await connection.execute(
            """
            INSERT INTO approval_delivery_presentations (
                intent_id, cohort_id, subject_key, subject_kind, presentation_mode,
                presentation_generation, presentation_key, state, last_reason_code,
                not_before, next_attempt_at
            ) VALUES ($1, NULL, $2::text, 'action', 'single', $3::integer,
                      $2::text || ':p:' || ($3::integer)::text,
                      'ready', 'defer_rescheduled', $4, $4)
            """,
            intent["id"],
            intent["action_key"],
            successor_generation,
            new_expires_at,
        )
        await _record_delivery_summary(
            connection,
            action_id=action_id,
            actor=actor,
            reason_code="defer_rescheduled",
            superseded_count=superseded_count,
            successor_generation=successor_generation,
            cohort_membership_changed=membership_changed,
            ambiguous_count=ambiguous_count,
            delivered_count=delivered_count,
            attempt_count=attempt_count,
            occurred_at=database_now,
        )
        observation = LifecycleSuccessObservation(
            operation="defer",
            reason="defer_rescheduled",
            successor_generation=successor_generation,
            superseded_count=superseded_count,
            cohort_membership_changed=membership_changed,
        )
        success_observations.append(observation)
        return DeferTransition(
            PendingAction.from_row(updated),
            True,
            success_observation=observation,
        )


__all__ = [
    "DeferTransition",
    "LifecycleSuccessObservation",
    "PendingTransition",
    "defer_pending_action",
    "emit_lifecycle_success",
    "transition_pending_action",
]
