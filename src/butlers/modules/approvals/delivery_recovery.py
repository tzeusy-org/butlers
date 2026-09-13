"""Schema-local repository and rendering for approval delivery recovery."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from butlers.core.approval_delivery_worker import DeliveryClaim, HandoffResult
from butlers.modules.approvals.notifications import (
    build_approval_digest_envelope,
    build_approval_request_envelope,
)

_LEASE_SECONDS = 30
_BACKOFF_BASE_SECONDS = 15
_BACKOFF_CAP_SECONDS = 15 * 60
_STUCK_SLO_SECONDS = 15 * 60
_SAFE_REASONS = frozenset(
    {
        "quiet_hours",
        "owner_recipient_unavailable",
        "callback_secret_unavailable",
        "transport_unavailable",
        "provider_preflight_failed",
        "provider_outcome_unknown",
        "action_approved",
        "action_rejected",
        "action_expired",
        "action_abandoned",
        "defer_rescheduled",
        "cohort_empty",
    }
)


@dataclass(frozen=True, slots=True)
class DirectRenderSubject:
    """Current non-secret action dossier read only while a claim is fenced."""

    action: Mapping[str, Any]
    origin_butler: str


@dataclass(frozen=True, slots=True)
class DigestRenderSubject:
    """Current eligible cohort count; membership identities are not rendered."""

    pending_count: int
    origin_butler: str


@dataclass(frozen=True, slots=True)
class DeliveryBacklogSnapshot:
    """Content-blind derived worker health."""

    due_count: int
    expired_lease_count: int
    ambiguous_count: int
    stuck_count: int
    oldest_due_age_seconds: float | None


def _backoff_seconds(presentation_key: str, attempt_number: int) -> float:
    """Return deterministic capped exponential delay with 0-20% jitter."""
    exponent = min(max(attempt_number - 1, 0), 6)
    base = min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2**exponent))
    digest = hashlib.sha256(f"{presentation_key}:{attempt_number}".encode()).digest()
    jitter = int.from_bytes(digest[:2], "big") / 65535 * 0.20
    return min(float(_BACKOFF_CAP_SECONDS), base * (1 + jitter))


def _updated_rows(command_tag: str) -> int:
    try:
        return int(command_tag.rsplit(" ", 1)[-1])
    except (TypeError, ValueError):
        return 0


class ApprovalDeliveryRepository:
    """Fenced writes restricted to approval delivery tables in the local schema."""

    def __init__(self, pool: Any, *, lease_seconds: int = _LEASE_SECONDS) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self._pool = pool
        self._lease_seconds = lease_seconds

    async def cancel_ineligible_presentations(self) -> int:
        """Cancel only presentation recovery; never transition a domain action."""
        direct = await self._pool.execute(
            """
            UPDATE approval_delivery_presentations AS p
               SET state = 'cancelled',
                   last_reason_code = CASE
                       WHEN pa.expires_at IS NOT NULL
                            AND pa.expires_at <= clock_timestamp() THEN 'action_expired'
                       WHEN pa.status IN ('approved', 'executed') THEN 'action_approved'
                       WHEN pa.status = 'rejected' THEN 'action_rejected'
                       WHEN pa.status = 'expired' THEN 'action_expired'
                       ELSE 'action_abandoned'
                   END,
                   next_attempt_at = NULL,
                   claim_token = NULL,
                   claim_expires_at = NULL,
                   updated_at = clock_timestamp()
              FROM approval_delivery_intents AS i
              JOIN pending_actions AS pa ON pa.id = i.action_id
             WHERE p.intent_id = i.id
               AND p.state IN ('ready', 'claimed', 'handoff_started', 'retry_wait')
               AND (
                   pa.status <> 'pending'
                   OR (pa.expires_at IS NOT NULL AND pa.expires_at <= clock_timestamp())
               )
            """
        )
        cohort = await self._pool.execute(
            """
            UPDATE approval_delivery_presentations AS p
               SET state = 'cancelled',
                   last_reason_code = 'cohort_empty',
                   next_attempt_at = NULL,
                   claim_token = NULL,
                   claim_expires_at = NULL,
                   updated_at = clock_timestamp()
             WHERE p.cohort_id IS NOT NULL
               AND p.state IN ('ready', 'claimed', 'handoff_started', 'retry_wait')
               AND NOT EXISTS (
                   SELECT 1
                     FROM approval_delivery_cohort_members AS m
                     JOIN approval_delivery_intents AS i ON i.id = m.intent_id
                     JOIN pending_actions AS pa ON pa.id = i.action_id
                    WHERE m.cohort_id = p.cohort_id
                      AND m.eligible
                      AND pa.status = 'pending'
                      AND (pa.expires_at IS NULL OR pa.expires_at > clock_timestamp())
               )
            """
        )
        return _updated_rows(direct) + _updated_rows(cohort)

    async def claim_next(self) -> DeliveryClaim | None:
        """Atomically claim one due/recoverable generation with SKIP LOCKED."""
        token = uuid.uuid4()
        row = await self._pool.fetchrow(
            """
            WITH candidate AS (
                SELECT p.id, p.state AS previous_state
                  FROM approval_delivery_presentations AS p
                 WHERE p.presentation_mode <> 'collapsed'
                   AND (
                       (p.state IN ('ready', 'retry_wait')
                           AND p.next_attempt_at <= clock_timestamp())
                       OR
                       (p.state IN ('claimed', 'handoff_started')
                           AND p.claim_expires_at <= clock_timestamp())
                   )
                   AND (
                       (p.intent_id IS NOT NULL AND EXISTS (
                           SELECT 1
                             FROM approval_delivery_intents AS i
                             JOIN pending_actions AS pa ON pa.id = i.action_id
                            WHERE i.id = p.intent_id
                              AND pa.status = 'pending'
                              AND (pa.expires_at IS NULL OR pa.expires_at > clock_timestamp())
                       ))
                       OR
                       (p.cohort_id IS NOT NULL AND EXISTS (
                           SELECT 1
                             FROM approval_delivery_cohort_members AS m
                             JOIN approval_delivery_intents AS i ON i.id = m.intent_id
                             JOIN pending_actions AS pa ON pa.id = i.action_id
                            WHERE m.cohort_id = p.cohort_id
                              AND m.eligible
                              AND pa.status = 'pending'
                              AND (pa.expires_at IS NULL OR pa.expires_at > clock_timestamp())
                       ))
                   )
                 ORDER BY
                    CASE WHEN p.state = 'handoff_started' THEN 0 ELSE 1 END,
                    COALESCE(p.next_attempt_at, p.claim_expires_at), p.id
                 FOR UPDATE OF p SKIP LOCKED
                 LIMIT 1
            )
            UPDATE approval_delivery_presentations AS p
               SET state = CASE
                       WHEN candidate.previous_state = 'handoff_started'
                       THEN 'handoff_started'
                       ELSE 'claimed'
                   END,
                   claim_fence = p.claim_fence + 1,
                   claim_token = $1,
                   claim_expires_at = clock_timestamp() + make_interval(secs => $2),
                   next_attempt_at = NULL,
                   updated_at = clock_timestamp()
              FROM candidate
             WHERE p.id = candidate.id
            RETURNING p.id, p.presentation_generation, p.presentation_key,
                      p.presentation_mode, p.claim_token, p.claim_fence,
                      candidate.previous_state = 'handoff_started' AS reconcile_only
            """,
            token,
            float(self._lease_seconds),
        )
        if row is None:
            return None
        return DeliveryClaim(
            presentation_id=row["id"],
            presentation_generation=row["presentation_generation"],
            presentation_key=row["presentation_key"],
            presentation_mode=row["presentation_mode"],
            claim_token=row["claim_token"],
            claim_fence=row["claim_fence"],
            reconcile_only=row["reconcile_only"],
        )

    async def load_render_subject(
        self, claim: DeliveryClaim
    ) -> DirectRenderSubject | DigestRenderSubject | None:
        """Read the current eligible subject without persisting rendered material."""
        row = await self._pool.fetchrow(
            """
            SELECT p.presentation_mode, i.origin_butler,
                   pa.id, pa.tool_name, pa.requested_at, pa.expires_at,
                   pa.why, pa.blast_radius, pa.reversibility
              FROM approval_delivery_presentations AS p
              JOIN approval_delivery_intents AS i ON i.id = p.intent_id
              JOIN pending_actions AS pa ON pa.id = i.action_id
             WHERE p.id = $1 AND p.presentation_generation = $2
               AND p.claim_token = $3 AND p.claim_fence = $4
               AND p.state = 'claimed'
               AND pa.status = 'pending'
               AND (pa.expires_at IS NULL OR pa.expires_at > clock_timestamp())
            """,
            claim.presentation_id,
            claim.presentation_generation,
            claim.claim_token,
            claim.claim_fence,
        )
        if row is not None:
            return DirectRenderSubject(
                action={
                    "id": row["id"],
                    "tool_name": row["tool_name"],
                    "requested_at": row["requested_at"],
                    "expires_at": row["expires_at"],
                    "why": row["why"],
                    "blast_radius": row["blast_radius"],
                    "reversibility": row["reversibility"],
                },
                origin_butler=row["origin_butler"],
            )
        row = await self._pool.fetchrow(
            """
            SELECT c.owning_schema,
                   count(*) FILTER (
                       WHERE m.eligible AND pa.status = 'pending'
                         AND (pa.expires_at IS NULL OR pa.expires_at > clock_timestamp())
                   )::integer AS pending_count
              FROM approval_delivery_presentations AS p
              JOIN approval_delivery_cohorts AS c ON c.id = p.cohort_id
              JOIN approval_delivery_cohort_members AS m ON m.cohort_id = c.id
              JOIN approval_delivery_intents AS i ON i.id = m.intent_id
              JOIN pending_actions AS pa ON pa.id = i.action_id
             WHERE p.id = $1 AND p.presentation_generation = $2
               AND p.claim_token = $3 AND p.claim_fence = $4
               AND p.state = 'claimed'
             GROUP BY c.owning_schema
            """,
            claim.presentation_id,
            claim.presentation_generation,
            claim.claim_token,
            claim.claim_fence,
        )
        if row is None or row["pending_count"] < 1:
            return None
        return DigestRenderSubject(
            pending_count=row["pending_count"],
            origin_butler=row["owning_schema"],
        )

    async def record_prestart_retry(self, claim: DeliveryClaim, reason_code: str) -> bool:
        """Record a proven pre-provider failure and schedule from database time."""
        self._validate_reason(reason_code)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                current = await connection.fetchrow(
                    """
                    SELECT attempt_count
                      FROM approval_delivery_presentations
                     WHERE id = $1 AND presentation_generation = $2
                       AND claim_token = $3 AND claim_fence = $4 AND state = 'claimed'
                     FOR UPDATE
                    """,
                    claim.presentation_id,
                    claim.presentation_generation,
                    claim.claim_token,
                    claim.claim_fence,
                )
                if current is None:
                    return False
                attempt_number = int(current["attempt_count"]) + 1
                delay = _backoff_seconds(claim.presentation_key, attempt_number)
                now = await connection.fetchval("SELECT clock_timestamp()")
                await connection.execute(
                    """
                    UPDATE approval_delivery_presentations
                       SET state = 'retry_wait', last_reason_code = $5,
                           next_attempt_at = $6::timestamptz + make_interval(secs => $7),
                           claim_token = NULL, claim_expires_at = NULL,
                           attempt_count = $8, updated_at = $6
                     WHERE id = $1 AND presentation_generation = $2
                       AND claim_token = $3 AND claim_fence = $4 AND state = 'claimed'
                    """,
                    claim.presentation_id,
                    claim.presentation_generation,
                    claim.claim_token,
                    claim.claim_fence,
                    reason_code,
                    now,
                    delay,
                    attempt_number,
                )
                await self._insert_attempt_result(
                    connection,
                    claim,
                    attempt_number=attempt_number,
                    outcome="safe_retry",
                    reason_code=reason_code,
                    provider_reference=None,
                    completed_at=now,
                )
                return True

    async def mark_handoff_started(self, claim: DeliveryClaim) -> bool:
        """Commit the final cancellation-safe marker under action-first locks."""
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                eligible = await self._lock_and_check_subject(connection, claim)
                if not eligible:
                    return False
                row = await connection.fetchrow(
                    """
                    UPDATE approval_delivery_presentations
                       SET state = 'handoff_started', attempt_count = attempt_count + 1,
                           claim_expires_at = clock_timestamp() + make_interval(secs => $5),
                           updated_at = clock_timestamp()
                     WHERE id = $1 AND presentation_generation = $2
                       AND claim_token = $3 AND claim_fence = $4 AND state = 'claimed'
                    RETURNING attempt_count, updated_at
                    """,
                    claim.presentation_id,
                    claim.presentation_generation,
                    claim.claim_token,
                    claim.claim_fence,
                    float(self._lease_seconds),
                )
                if row is None:
                    return False
                await connection.execute(
                    """
                    INSERT INTO approval_delivery_attempts (
                        presentation_id, presentation_generation, attempt_number,
                        claim_fence, started_at, outcome
                    ) VALUES ($1, $2, $3, $4, $5, 'started')
                    """,
                    claim.presentation_id,
                    claim.presentation_generation,
                    row["attempt_count"],
                    claim.claim_fence,
                    row["updated_at"],
                )
                return True

    async def heartbeat(self, claim: DeliveryClaim) -> bool:
        """Renew only the exact generation/token/fence owner."""
        result = await self._pool.execute(
            """
            UPDATE approval_delivery_presentations
               SET claim_expires_at = clock_timestamp() + make_interval(secs => $5),
                   updated_at = clock_timestamp()
             WHERE id = $1 AND presentation_generation = $2
               AND claim_token = $3 AND claim_fence = $4
               AND state IN ('claimed', 'handoff_started')
            """,
            claim.presentation_id,
            claim.presentation_generation,
            claim.claim_token,
            claim.claim_fence,
            float(self._lease_seconds),
        )
        return _updated_rows(result) == 1

    async def complete_handoff(self, claim: DeliveryClaim, result: HandoffResult) -> bool:
        """Persist one normalized result; stale claims cannot append evidence."""
        self._validate_result(result)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                current = await connection.fetchrow(
                    """
                    SELECT attempt_count
                      FROM approval_delivery_presentations
                     WHERE id = $1 AND presentation_generation = $2
                       AND claim_token = $3 AND claim_fence = $4
                       AND state = 'handoff_started'
                     FOR UPDATE
                    """,
                    claim.presentation_id,
                    claim.presentation_generation,
                    claim.claim_token,
                    claim.claim_fence,
                )
                if current is None:
                    late_attempt = await connection.fetchrow(
                        """
                        SELECT a.attempt_number
                          FROM approval_delivery_presentations AS p
                          JOIN approval_delivery_attempts AS a
                            ON a.presentation_id = p.id
                           AND a.presentation_generation = p.presentation_generation
                           AND a.outcome = 'started'
                           AND a.claim_fence = $3
                         WHERE p.id = $1 AND p.presentation_generation = $2
                           AND p.claim_fence = $3
                           AND p.state IN ('cancelled', 'superseded')
                           AND NOT EXISTS (
                               SELECT 1 FROM approval_delivery_attempts AS result
                                WHERE result.presentation_id = p.id
                                  AND result.attempt_number = a.attempt_number
                                  AND result.outcome <> 'started'
                           )
                         FOR UPDATE OF p
                        """,
                        claim.presentation_id,
                        claim.presentation_generation,
                        claim.claim_fence,
                    )
                    if late_attempt is None:
                        return False
                    now = await connection.fetchval("SELECT clock_timestamp()")
                    await self._insert_attempt_result(
                        connection,
                        claim,
                        attempt_number=late_attempt["attempt_number"],
                        outcome=result.classification,
                        reason_code=result.reason_code,
                        provider_reference=result.provider_reference,
                        completed_at=now,
                    )
                    return True
                attempt_number = int(current["attempt_count"])
                now = await connection.fetchval("SELECT clock_timestamp()")
                if result.classification == "confirmed":
                    state = "delivered"
                    next_attempt_at = None
                elif result.classification == "safe_retry":
                    state = "retry_wait"
                    next_attempt_at = now + timedelta(
                        seconds=_backoff_seconds(claim.presentation_key, attempt_number)
                    )
                else:
                    state = "ambiguous"
                    next_attempt_at = None
                updated = await connection.fetchrow(
                    """
                    UPDATE approval_delivery_presentations
                       SET state = $5, last_reason_code = $6, next_attempt_at = $7,
                           claim_token = NULL, claim_expires_at = NULL, updated_at = $8
                     WHERE id = $1 AND presentation_generation = $2
                       AND claim_token = $3 AND claim_fence = $4
                       AND state = 'handoff_started'
                    RETURNING id
                    """,
                    claim.presentation_id,
                    claim.presentation_generation,
                    claim.claim_token,
                    claim.claim_fence,
                    state,
                    result.reason_code,
                    next_attempt_at,
                    now,
                )
                if updated is None:
                    return False
                await self._insert_attempt_result(
                    connection,
                    claim,
                    attempt_number=attempt_number,
                    outcome=result.classification,
                    reason_code=result.reason_code,
                    provider_reference=result.provider_reference,
                    completed_at=now,
                )
                return True

    async def backlog_snapshot(self) -> DeliveryBacklogSnapshot:
        """Return safe derived due, lease, ambiguity, and stuck truth."""
        row = await self._pool.fetchrow(
            """
            SELECT
                count(*) FILTER (
                    WHERE state IN ('ready', 'retry_wait')
                      AND next_attempt_at <= clock_timestamp()
                )::integer AS due_count,
                count(*) FILTER (
                    WHERE state IN ('claimed', 'handoff_started')
                      AND claim_expires_at <= clock_timestamp()
                )::integer AS expired_lease_count,
                count(*) FILTER (WHERE state = 'ambiguous')::integer AS ambiguous_count,
                count(*) FILTER (
                    WHERE state = 'ambiguous'
                       OR (state IN ('claimed', 'handoff_started')
                           AND claim_expires_at <= clock_timestamp())
                       OR (state IN ('ready', 'retry_wait')
                           AND next_attempt_at <= clock_timestamp()
                               - make_interval(secs => $1))
                )::integer AS stuck_count,
                EXTRACT(EPOCH FROM (
                    clock_timestamp() - min(next_attempt_at) FILTER (
                        WHERE state IN ('ready', 'retry_wait')
                          AND next_attempt_at <= clock_timestamp()
                    )
                ))::double precision AS oldest_due_age_seconds
              FROM approval_delivery_presentations
            """,
            float(_STUCK_SLO_SECONDS),
        )
        return DeliveryBacklogSnapshot(
            due_count=row["due_count"],
            expired_lease_count=row["expired_lease_count"],
            ambiguous_count=row["ambiguous_count"],
            stuck_count=row["stuck_count"],
            oldest_due_age_seconds=row["oldest_due_age_seconds"],
        )

    async def _lock_and_check_subject(self, connection: Any, claim: DeliveryClaim) -> bool:
        direct = await connection.fetchrow(
            """
            SELECT pa.status, pa.expires_at
              FROM approval_delivery_presentations AS p
              JOIN approval_delivery_intents AS i ON i.id = p.intent_id
              JOIN pending_actions AS pa ON pa.id = i.action_id
             WHERE p.id = $1 AND p.presentation_generation = $2
             FOR UPDATE OF pa
            """,
            claim.presentation_id,
            claim.presentation_generation,
        )
        if direct is not None:
            await connection.fetchval(
                """
                SELECT i.id FROM approval_delivery_intents AS i
                JOIN approval_delivery_presentations AS p ON p.intent_id = i.id
                WHERE p.id = $1 FOR UPDATE OF i
                """,
                claim.presentation_id,
            )
            return direct["status"] == "pending" and (
                direct["expires_at"] is None
                or bool(
                    await connection.fetchval(
                        "SELECT $1::timestamptz > clock_timestamp()",
                        direct["expires_at"],
                    )
                )
            )

        rows = await connection.fetch(
            """
            SELECT pa.id
              FROM approval_delivery_presentations AS p
              JOIN approval_delivery_cohort_members AS m ON m.cohort_id = p.cohort_id
              JOIN approval_delivery_intents AS i ON i.id = m.intent_id
              JOIN pending_actions AS pa ON pa.id = i.action_id
             WHERE p.id = $1 AND m.eligible AND pa.status = 'pending'
               AND (pa.expires_at IS NULL OR pa.expires_at > clock_timestamp())
             ORDER BY pa.id
             FOR UPDATE OF pa
            """,
            claim.presentation_id,
        )
        if not rows:
            await connection.execute(
                """
                UPDATE approval_delivery_presentations
                   SET state = 'cancelled', last_reason_code = 'cohort_empty',
                       next_attempt_at = NULL, claim_token = NULL,
                       claim_expires_at = NULL, updated_at = clock_timestamp()
                 WHERE id = $1 AND presentation_generation = $2
                   AND claim_token = $3 AND claim_fence = $4 AND state = 'claimed'
                """,
                claim.presentation_id,
                claim.presentation_generation,
                claim.claim_token,
                claim.claim_fence,
            )
            return False
        await connection.fetch(
            """
            SELECT i.id
              FROM approval_delivery_presentations AS p
              JOIN approval_delivery_cohort_members AS m ON m.cohort_id = p.cohort_id
              JOIN approval_delivery_intents AS i ON i.id = m.intent_id
             WHERE p.id = $1
             ORDER BY i.id
             FOR UPDATE OF i
            """,
            claim.presentation_id,
        )
        await connection.fetch(
            """
            SELECT m.intent_id
              FROM approval_delivery_presentations AS p
              JOIN approval_delivery_cohort_members AS m ON m.cohort_id = p.cohort_id
             WHERE p.id = $1
             ORDER BY m.intent_id
             FOR UPDATE OF m
            """,
            claim.presentation_id,
        )
        return True

    @staticmethod
    async def _insert_attempt_result(
        connection: Any,
        claim: DeliveryClaim,
        *,
        attempt_number: int,
        outcome: Literal["confirmed", "safe_retry", "ambiguous"],
        reason_code: str | None,
        provider_reference: str | None,
        completed_at: Any,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO approval_delivery_attempts (
                presentation_id, presentation_generation, attempt_number,
                claim_fence, started_at, completed_at, outcome,
                reason_code, provider_reference
            ) VALUES ($1, $2, $3, $4, $5, $5, $6, $7, $8)
            """,
            claim.presentation_id,
            claim.presentation_generation,
            attempt_number,
            claim.claim_fence,
            completed_at,
            outcome,
            reason_code,
            provider_reference,
        )

    @staticmethod
    def _validate_reason(reason_code: str | None) -> None:
        if reason_code is not None and reason_code not in _SAFE_REASONS:
            raise ValueError("unknown approval delivery reason code")

    @classmethod
    def _validate_result(cls, result: HandoffResult) -> None:
        if result.classification not in {"confirmed", "safe_retry", "ambiguous"}:
            raise ValueError("unknown approval delivery handoff classification")
        cls._validate_reason(result.reason_code)
        if result.classification == "safe_retry" and result.reason_code is None:
            raise ValueError("safe_retry handoff requires a reason code")
        if (
            result.classification == "ambiguous"
            and result.reason_code != "provider_outcome_unknown"
        ):
            raise ValueError("ambiguous handoff requires provider_outcome_unknown")
        if result.provider_reference is not None and not (
            1 <= len(result.provider_reference) <= 256
        ):
            raise ValueError("provider_reference must contain 1 to 256 characters")


class ApprovalDeliveryRenderer:
    """Render current owner control-plane material only into transient memory."""

    def __init__(self, *, dashboard_base_url: str | None = None) -> None:
        self._dashboard_base_url = dashboard_base_url

    def render_single(
        self,
        subject: DirectRenderSubject,
        *,
        owner_recipient: str,
        callback_secret: str,
    ) -> dict[str, Any]:
        return build_approval_request_envelope(
            action=subject.action,
            origin_butler=subject.origin_butler,
            owner_recipient=owner_recipient,
            callback_secret=callback_secret,
            dashboard_base_url=self._dashboard_base_url,
        )

    def render_digest(
        self,
        subject: DigestRenderSubject,
        *,
        owner_recipient: str,
    ) -> dict[str, Any]:
        return build_approval_digest_envelope(
            pending_count=subject.pending_count,
            origin_butler=subject.origin_butler,
            owner_recipient=owner_recipient,
            dashboard_base_url=self._dashboard_base_url,
        )


__all__ = [
    "ApprovalDeliveryRenderer",
    "ApprovalDeliveryRepository",
    "DeliveryBacklogSnapshot",
    "DigestRenderSubject",
    "DirectRenderSubject",
]
