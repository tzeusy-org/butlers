"""Immutable approval audit event helpers."""

from __future__ import annotations

import enum
import json
import uuid
from datetime import UTC, datetime
from typing import Any


class ApprovalEventType(enum.StrEnum):
    """Canonical event names for approval audit records."""

    ACTION_QUEUED = "action_queued"
    ACTION_AUTO_APPROVED = "action_auto_approved"
    ACTION_APPROVED = "action_approved"
    ACTION_REJECTED = "action_rejected"
    ACTION_EXPIRED = "action_expired"
    ACTION_ABANDONED = "action_abandoned"
    ACTION_EXECUTION_SUCCEEDED = "action_execution_succeeded"
    ACTION_EXECUTION_FAILED = "action_execution_failed"
    RULE_CREATED = "rule_created"
    RULE_REVOKED = "rule_revoked"
    # Progressive autonomy ladder — promotion lifecycle
    PROMOTION_SUGGESTED = "promotion_suggested"
    PROMOTION_CONFIRMED = "promotion_confirmed"
    PROMOTION_DISMISSED = "promotion_dismissed"
    PROMOTION_SUPERSEDED = "promotion_superseded"
    # Progressive autonomy ladder — demotion lifecycle
    DEMOTION_SUGGESTED = "demotion_suggested"
    DEMOTION_CONFIRMED = "demotion_confirmed"
    DEMOTION_DISMISSED = "demotion_dismissed"


# Event types that represent suggestion lifecycle transitions rather than
# action/rule operations; these may legitimately lack both action_id and rule_id.
_SUGGESTION_EVENT_TYPES: frozenset[ApprovalEventType] = frozenset(
    {
        ApprovalEventType.PROMOTION_SUGGESTED,
        ApprovalEventType.PROMOTION_CONFIRMED,
        ApprovalEventType.PROMOTION_DISMISSED,
        ApprovalEventType.PROMOTION_SUPERSEDED,
        ApprovalEventType.DEMOTION_SUGGESTED,
        ApprovalEventType.DEMOTION_CONFIRMED,
        ApprovalEventType.DEMOTION_DISMISSED,
    }
)


async def record_approval_event(
    pool: Any,
    event_type: ApprovalEventType | str,
    *,
    actor: str,
    action_id: uuid.UUID | None = None,
    rule_id: uuid.UUID | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> None:
    """Persist an immutable approval event row."""
    canonical_type: ApprovalEventType | None = None
    if isinstance(event_type, ApprovalEventType):
        canonical_type = event_type
    else:
        try:
            canonical_type = ApprovalEventType(str(event_type))
        except ValueError:
            canonical_type = None

    is_suggestion_event = canonical_type in _SUGGESTION_EVENT_TYPES
    if action_id is None and rule_id is None and not is_suggestion_event:
        raise ValueError("Approval event must include action_id and/or rule_id")

    event_name = event_type.value if isinstance(event_type, ApprovalEventType) else str(event_type)
    event_time = occurred_at if occurred_at is not None else datetime.now(UTC)
    event_metadata = metadata or {}

    # Sanitize into a fully JSON-safe dict (UUID/datetime -> str) via a
    # json.dumps/loads round-trip, then bind the resulting DICT directly (no
    # second json.dumps): every asyncpg pool in this codebase registers
    # register_jsonb_codec() (src/butlers/db.py), whose encoder expects a
    # Python object and calls json.dumps() on it itself. Passing an
    # ALREADY-serialized JSON string (the previous approach here) makes that
    # encoder fire a SECOND time, double-encoding event_metadata into a
    # jsonb-typed STRING instead of an OBJECT (bu-cymc4; see
    # tests/relationship/test_jsonb_codec.py).
    safe_event_metadata = json.loads(json.dumps(event_metadata, default=str))

    await pool.execute(
        "INSERT INTO approval_events "
        "(event_type, action_id, rule_id, actor, reason, event_metadata, occurred_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7)",
        event_name,
        action_id,
        rule_id,
        actor,
        reason,
        safe_event_metadata,
        event_time,
    )
