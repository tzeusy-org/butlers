"""Pydantic models for the Dashboard Decisions lane API (bu-ckkpz.2).

Maps ``butlers.jobs.decision_review``'s ``DecisionBead``/``EscalationHit``
dataclasses (bu-ckkpz.4) onto a dashboard-consumable wire shape. See that
module's docstring for label-only decision classification and the beads-export
read path this endpoint reuses -- both are intentionally NOT reimplemented
here.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DecisionBeadSummary(BaseModel):
    """One open, decision-marked bead, oldest-first.

    ``escalated_*`` fields are populated from the single longest-blocked
    escalation hit against this decision (``compute_decision_digest``'s
    ``escalations`` are pre-sorted by ``block_age`` descending), or all
    ``None`` when this decision has not escalated. Structured context fields
    retain only validated source values; their availability fields distinguish
    a per-record metadata problem from the digest-wide degraded envelope.
    """

    id: str
    title: str
    priority: int | None = None
    created_at: datetime
    age_hours: float
    escalated: bool = False
    escalated_blocked_id: str | None = None
    escalated_blocked_title: str | None = None
    escalated_blocked_kind: str | None = None  # "p1_bug" | "deploy"
    escalated_block_hours: float | None = None
    description: str | None = None
    options: list[str] | None = None
    default: str | None = None
    due_at: datetime | None = None
    structured_details_available: bool = False
    structured_details_unavailable_reason: str | None = None
    intent: DecisionIntentSummary | None = None


class DecisionIntentSummary(BaseModel):
    """A recorded owner choice and its honest apply state (bu-ckkpz.3).

    ``status`` is ``pending``/``applying`` (recorded, awaiting the tracker
    bridge), ``applied`` or ``failed``. ``failure_reason`` and ``last_error``
    are categorical codes, never raw ``bd`` output.
    """

    id: UUID
    bead_id: str
    option: str
    status: str
    source: str
    created_at: datetime
    failure_reason: str | None = None
    last_error: str | None = None


class DecisionPromptDetail(BaseModel):
    """One Telegram decision prompt, as the connector needs it to verify a tap."""

    id: UUID
    bead_id: str
    options: list[str]
    created_at: datetime
    delivery_outcome: str | None = None
    intent: DecisionIntentSummary | None = None


class RecordDecisionIntentRequest(BaseModel):
    option: str = Field(min_length=1, max_length=512)


class ChooseDecisionPromptRequest(BaseModel):
    option_index: int = Field(ge=0, le=15)


DecisionBeadSummary.model_rebuild()
