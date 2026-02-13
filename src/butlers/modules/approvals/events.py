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
    ACTION_EXECUTION_SUCCEEDED = "action_execution_succeeded"
    ACTION_EXECUTION_FAILED = "action_execution_failed"
    RULE_CREATED = "rule_created"
    RULE_REVOKED = "rule_revoked"


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
    if action_id is None and rule_id is None:
        raise ValueError("Approval event must include action_id and/or rule_id")

    event_name = event_type.value if isinstance(event_type, ApprovalEventType) else str(event_type)
    event_time = occurred_at if occurred_at is not None else datetime.now(UTC)
    event_metadata = metadata or {}

    await pool.execute(
        "INSERT INTO approval_events "
        "(event_type, action_id, rule_id, actor, reason, event_metadata, occurred_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7)",
        event_name,
        action_id,
        rule_id,
        actor,
        reason,
        json.dumps(event_metadata),
        event_time,
    )
