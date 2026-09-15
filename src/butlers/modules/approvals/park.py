"""Atomic admission for actions that require human approval.

Every ordinary ``status='pending'`` producer enters through
:func:`park_pending_action`. A schema-local server-held rollout row selects
exactly one path: the default-off path commits only the established pending
action, while the enabled path atomically commits the action, immutable
delivery-intent root, RFC 0021 burst admission, and initial presentation/cohort
records. Legacy emission rows remain read-only in both paths.

Prepared insight actions retain their explicitly non-notifying path. They are
surfaced by the insight digest and are not approval-delivery recovery subjects.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from butlers.core.approvals_policy import (
    approval_push_deliver_at,
    get_approvals_policy_quiet_hours,
)
from butlers.modules.approvals.notifications import ApprovalPushRuntime
from butlers.modules.approvals.rollout import read_approval_delivery_rollout

AdmissionMode = Literal["single", "cohort_anchor", "collapsed"]
_ORIGIN_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")


@dataclass(frozen=True, slots=True)
class ParkRequest:
    """Validated input to the sole ordinary pending-action admission path."""

    action_id: uuid.UUID
    tool_name: str
    tool_args: dict[str, Any]
    agent_summary: str | None
    requested_at: datetime
    expires_at: datetime | None
    session_id: uuid.UUID | None = None
    why: str | None = None
    evidence: tuple[dict[str, str], ...] = ()
    blast_radius: str | None = None
    reversibility: str | None = None
    origin_butler: str = ""
    deduplication_key: str | None = None


@dataclass(frozen=True, slots=True)
class ParkAdmission:
    """Durable identity and admission decision returned after commit."""

    action_id: uuid.UUID
    intent_id: uuid.UUID | None
    action_key: str | None
    admission_mode: AdmissionMode | None
    presentation_key: str | None
    cohort_key: str | None
    not_before: datetime | None
    duplicate: bool
    legacy_duplicate: bool = False


def _validate_request(request: ParkRequest) -> None:
    if not isinstance(request.action_id, uuid.UUID):
        raise ValueError("action_id must be a UUID")
    if not request.tool_name.strip():
        raise ValueError("tool_name must be non-empty")
    if not isinstance(request.tool_args, dict):
        raise ValueError("tool_args must be a JSON object")
    if not isinstance(request.origin_butler, str) or not _ORIGIN_RE.fullmatch(
        request.origin_butler
    ):
        raise ValueError("origin_butler must be a canonical butler name")
    if request.requested_at.tzinfo is None or request.requested_at.utcoffset() is None:
        raise ValueError("requested_at must be timezone-aware")
    if request.expires_at is not None and (
        request.expires_at.tzinfo is None or request.expires_at.utcoffset() is None
    ):
        raise ValueError("expires_at must be timezone-aware")
    if request.why is not None and len(request.why) > 4000:
        raise ValueError("why must be at most 4000 characters")
    for item in request.evidence:
        if not isinstance(item, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in item.items()
        ):
            raise ValueError("evidence must contain string-to-string objects")


@asynccontextmanager
async def _connection(source: Any) -> AsyncIterator[Any]:
    """Yield an existing asyncpg connection or one acquired from a pool."""
    if hasattr(source, "acquire"):
        async with source.acquire() as connection:
            yield connection
    else:
        yield source


async def _existing_admission(
    connection: Any,
    *,
    action_id: uuid.UUID,
    deduplication_key: str | None,
) -> ParkAdmission | None:
    row = await connection.fetchrow(
        """
        SELECT pa.id AS action_id, adi.id AS intent_id, adi.action_key,
               adi.admission_mode, presentation.presentation_key,
               cohort.cohort_key, presentation.not_before
          FROM pending_actions AS pa
          LEFT JOIN approval_delivery_intents AS adi ON adi.action_id = pa.id
          LEFT JOIN approval_delivery_cohort_members AS member ON member.intent_id = adi.id
          LEFT JOIN approval_delivery_cohorts AS cohort ON cohort.id = member.cohort_id
          LEFT JOIN LATERAL (
              SELECT presentation_key, not_before
                FROM approval_delivery_presentations
               WHERE (adi.admission_mode = 'cohort_anchor' AND cohort_id = cohort.id)
                  OR (adi.admission_mode <> 'cohort_anchor' AND intent_id = adi.id)
               ORDER BY presentation_generation DESC
               LIMIT 1
          ) AS presentation ON true
         WHERE pa.id = $1
            OR (
                $2::text IS NOT NULL
                AND pa.deduplication_key = $2
                AND pa.status IN ('pending', 'approved', 'rejected', 'abandoned')
            )
         ORDER BY (pa.id = $1) DESC, pa.requested_at DESC, pa.id
         LIMIT 1
        """,
        action_id,
        deduplication_key,
    )
    if row is None:
        return None
    return ParkAdmission(
        action_id=row["action_id"],
        intent_id=row["intent_id"],
        action_key=row["action_key"],
        admission_mode=row["admission_mode"],
        presentation_key=row["presentation_key"],
        cohort_key=row["cohort_key"],
        not_before=row["not_before"],
        duplicate=True,
        legacy_duplicate=row["intent_id"] is None,
    )


async def _existing_pending_action(
    connection: Any,
    *,
    action_id: uuid.UUID,
    deduplication_key: str | None,
) -> ParkAdmission | None:
    """Resolve a disabled-rollout duplicate without touching recovery tables."""
    row = await connection.fetchrow(
        """
        SELECT id
          FROM pending_actions
         WHERE id = $1
            OR (
                $2::text IS NOT NULL
                AND deduplication_key = $2
                AND status IN ('pending', 'approved', 'rejected', 'abandoned')
            )
         ORDER BY (id = $1) DESC, requested_at DESC, id
         LIMIT 1
        """,
        action_id,
        deduplication_key,
    )
    if row is None:
        return None
    return ParkAdmission(
        action_id=row["id"],
        intent_id=None,
        action_key=None,
        admission_mode=None,
        presentation_key=None,
        cohort_key=None,
        not_before=None,
        duplicate=True,
        legacy_duplicate=True,
    )


async def _park_without_delivery(connection: Any, request: ParkRequest) -> ParkAdmission:
    """Preserve pending-action behavior without any notification writer before cutover."""
    await connection.execute(
        "SELECT pg_advisory_xact_lock(hashtext('approval-delivery:' || current_schema()))"
    )
    existing = await _existing_pending_action(
        connection,
        action_id=request.action_id,
        deduplication_key=request.deduplication_key,
    )
    if existing is not None:
        return existing
    await connection.execute(
        """
        INSERT INTO pending_actions (
            id, tool_name, tool_args, agent_summary, session_id, status,
            requested_at, expires_at, why, evidence, blast_radius, reversibility,
            deduplication_key
        ) VALUES ($1, $2, $3, $4, $5, 'pending', $6, $7, $8, $9, $10, $11, $12)
        """,
        request.action_id,
        request.tool_name,
        request.tool_args,
        request.agent_summary,
        request.session_id,
        request.requested_at,
        request.expires_at,
        request.why,
        list(request.evidence),
        request.blast_radius,
        request.reversibility,
        request.deduplication_key,
    )
    return ParkAdmission(
        action_id=request.action_id,
        intent_id=None,
        action_key=None,
        admission_mode=None,
        presentation_key=None,
        cohort_key=None,
        not_before=None,
        duplicate=False,
    )


async def _insert_presentation(
    connection: Any,
    *,
    intent_id: uuid.UUID | None,
    cohort_id: uuid.UUID | None,
    subject_key: str,
    mode: Literal["single", "burst_digest", "collapsed"],
    generation: int,
    state: Literal["ready", "collapsed"],
    not_before: datetime,
    reason_code: str | None,
) -> str:
    presentation_key = f"{subject_key}:p:{generation}"
    await connection.execute(
        """
        INSERT INTO approval_delivery_presentations (
            intent_id, cohort_id, subject_key, subject_kind, presentation_mode,
            presentation_generation, presentation_key, state, last_reason_code,
            not_before, next_attempt_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
            CASE WHEN $8::text = 'ready' THEN $10::timestamptz ELSE NULL END
        )
        """,
        intent_id,
        cohort_id,
        subject_key,
        "action" if intent_id is not None else "cohort",
        mode,
        generation,
        presentation_key,
        state,
        reason_code,
        not_before,
    )
    return presentation_key


async def _ensure_cohort_presentation(
    connection: Any,
    *,
    cohort_id: uuid.UUID,
    cohort_key: str,
    not_before: datetime,
    reason_code: str | None,
) -> str | None:
    current = await connection.fetchrow(
        """
        SELECT presentation_key, state, presentation_generation
          FROM approval_delivery_presentations
         WHERE cohort_id = $1
         ORDER BY presentation_generation DESC
         LIMIT 1
        """,
        cohort_id,
    )
    if current is not None and current["state"] in {
        "ready",
        "claimed",
        "retry_wait",
        "handoff_started",
        "delivered",
        "ambiguous",
    }:
        return current["presentation_key"]
    generation = 1 if current is None else int(current["presentation_generation"]) + 1
    return await _insert_presentation(
        connection,
        intent_id=None,
        cohort_id=cohort_id,
        subject_key=cohort_key,
        mode="burst_digest",
        generation=generation,
        state="ready",
        not_before=not_before,
        reason_code=reason_code,
    )


async def _admit(connection: Any, request: ParkRequest) -> ParkAdmission:
    await connection.execute(
        "SELECT pg_advisory_xact_lock(hashtext('approval-delivery:' || current_schema()))"
    )
    existing = await _existing_admission(
        connection,
        action_id=request.action_id,
        deduplication_key=request.deduplication_key,
    )
    if existing is not None:
        return existing

    # transaction_timestamp() is fixed before a contended advisory-lock wait.
    # Admission policy must observe database time after it wins serialization.
    database_now = await connection.fetchval("SELECT clock_timestamp()")
    policy = await get_approvals_policy_quiet_hours(connection)
    quiet_release = approval_push_deliver_at(policy, now=database_now)
    not_before = quiet_release or database_now
    reason_code = "quiet_hours" if quiet_release is not None else None
    owning_schema = await connection.fetchval("SELECT current_schema()")

    prior_count = int(
        await connection.fetchval(
            """
            SELECT count(*) FROM approval_delivery_intents
             WHERE created_at >= $1::timestamptz - interval '10 minutes'
            """,
            database_now,
        )
        or 0
    )
    cohort = await connection.fetchrow(
        """
        SELECT id, cohort_key FROM approval_delivery_cohorts
         WHERE window_ends_at > $1
         ORDER BY window_started_at DESC
         LIMIT 1
         FOR UPDATE
        """,
        database_now,
    )
    if cohort is not None:
        mode: AdmissionMode = "collapsed"
    elif prior_count < 3:
        mode = "single"
    else:
        mode = "cohort_anchor"

    await connection.execute(
        """
        INSERT INTO pending_actions (
            id, tool_name, tool_args, agent_summary, session_id, status,
            requested_at, expires_at, why, evidence, blast_radius, reversibility,
            deduplication_key
        ) VALUES ($1, $2, $3, $4, $5, 'pending', $6, $7, $8, $9, $10, $11, $12)
        """,
        request.action_id,
        request.tool_name,
        request.tool_args,
        request.agent_summary,
        request.session_id,
        request.requested_at,
        request.expires_at,
        request.why,
        list(request.evidence),
        request.blast_radius,
        request.reversibility,
        request.deduplication_key,
    )
    intent_id = uuid.uuid4()
    action_key = f"approval:{owning_schema}:{request.action_id}"
    await connection.execute(
        """
        INSERT INTO approval_delivery_intents (
            id, action_id, action_key, owning_schema, origin_butler,
            admission_mode, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        intent_id,
        request.action_id,
        action_key,
        owning_schema,
        request.origin_butler,
        mode,
        database_now,
    )

    cohort_key: str | None = None
    if mode == "single":
        presentation_key = await _insert_presentation(
            connection,
            intent_id=intent_id,
            cohort_id=None,
            subject_key=action_key,
            mode="single",
            generation=1,
            state="ready",
            not_before=not_before,
            reason_code=reason_code,
        )
    else:
        if cohort is None:
            cohort_id = uuid.uuid4()
            cohort_key = f"approval-cohort:{owning_schema}:{cohort_id}"
            await connection.execute(
                """
                INSERT INTO approval_delivery_cohorts (
                    id, cohort_key, owning_schema, window_started_at,
                    window_ends_at, created_at
                ) VALUES (
                    $1, $2, $3, $4::timestamptz,
                    $4::timestamptz + interval '10 minutes', $5
                )
                """,
                cohort_id,
                cohort_key,
                owning_schema,
                database_now,
                database_now,
            )
        else:
            cohort_id = cohort["id"]
            cohort_key = cohort["cohort_key"]
        await connection.execute(
            """
            INSERT INTO approval_delivery_cohort_members (cohort_id, intent_id, eligible, joined_at)
            VALUES ($1, $2, true, $3)
            """,
            cohort_id,
            intent_id,
            database_now,
        )
        if mode == "cohort_anchor":
            presentation_key = await _ensure_cohort_presentation(
                connection,
                cohort_id=cohort_id,
                cohort_key=cohort_key,
                not_before=not_before,
                reason_code=reason_code,
            )
        else:
            presentation_key = await _insert_presentation(
                connection,
                intent_id=intent_id,
                cohort_id=None,
                subject_key=action_key,
                mode="collapsed",
                generation=1,
                state="collapsed",
                not_before=database_now,
                reason_code=None,
            )
            await _ensure_cohort_presentation(
                connection,
                cohort_id=cohort_id,
                cohort_key=cohort_key,
                not_before=not_before,
                reason_code=reason_code,
            )

    return ParkAdmission(
        action_id=request.action_id,
        intent_id=intent_id,
        action_key=action_key,
        admission_mode=mode,
        presentation_key=presentation_key,
        cohort_key=cohort_key,
        not_before=not_before,
        duplicate=False,
    )


async def park_pending_action(
    pool: Any,
    *,
    action_id: uuid.UUID,
    tool_name: str,
    tool_args: dict[str, Any],
    agent_summary: str | None,
    requested_at: datetime,
    expires_at: datetime | None,
    session_id: uuid.UUID | None = None,
    why: str | None = None,
    evidence: Sequence[dict[str, str]] | None = None,
    blast_radius: str | None = None,
    reversibility: str | None = None,
    origin_butler: str | None,
    approval_push_runtime: ApprovalPushRuntime | None = None,
    deduplication_key: str | None = None,
) -> ParkAdmission:
    """Park one action through the server-held additive rollout boundary.

    Disabled schemas commit only the established pending action. Enabled
    schemas atomically create durable recovery state. Neither path writes or
    dispatches through the read-only legacy emission path.
    """
    del approval_push_runtime
    request = ParkRequest(
        action_id=action_id,
        tool_name=tool_name,
        tool_args=tool_args,
        agent_summary=agent_summary,
        requested_at=requested_at,
        expires_at=expires_at,
        session_id=session_id,
        why=why,
        evidence=tuple(evidence or ()),
        blast_radius=blast_radius,
        reversibility=reversibility,
        origin_butler=origin_butler or "",
        deduplication_key=deduplication_key,
    )
    _validate_request(request)
    async with _connection(pool) as connection:
        async with connection.transaction():
            rollout = await read_approval_delivery_rollout(connection, lock=True)
            if rollout.admission_enabled:
                return await _admit(connection, request)
            return await _park_without_delivery(connection, request)


async def park_prepared_action(
    pool: Any,
    *,
    action_id: uuid.UUID,
    tool_name: str,
    tool_args: dict[str, Any],
    agent_summary: str | None,
    requested_at: datetime,
    expires_at: datetime,
    why: str | None = None,
    evidence: Sequence[dict[str, str]] | None = None,
    blast_radius: str | None = None,
    reversibility: str | None = None,
    deduplication_key: str,
) -> None:
    """Insert one explicitly digest-only prepared action without a push intent."""
    await pool.execute(
        "INSERT INTO pending_actions "
        "(id, tool_name, tool_args, agent_summary, session_id, status, origin, "
        "requested_at, expires_at, why, evidence, blast_radius, reversibility, "
        "deduplication_key) "
        "VALUES ($1, $2, $3, $4, NULL, 'pending', 'prepared', $5, $6, $7, $8, $9, $10, $11)",
        action_id,
        tool_name,
        tool_args,
        agent_summary,
        requested_at,
        expires_at,
        why,
        list(evidence) if evidence is not None else [],
        blast_radius,
        reversibility,
        deduplication_key,
    )


__all__ = [
    "AdmissionMode",
    "ParkAdmission",
    "ParkRequest",
    "park_pending_action",
    "park_prepared_action",
]
