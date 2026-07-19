"""Pydantic models for the Dashboard Decisions lane API (bu-ckkpz.2).

Maps ``butlers.jobs.decision_review``'s ``DecisionBead``/``EscalationHit``
dataclasses (bu-ckkpz.4) onto a dashboard-consumable wire shape. See that
module's docstring for label-only decision classification and the beads-export
read path this endpoint reuses -- both are intentionally NOT reimplemented
here.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DecisionBeadSummary(BaseModel):
    """One open, decision-marked bead, oldest-first.

    ``escalated_*`` fields are populated from the single longest-blocked
    escalation hit against this decision (``compute_decision_digest``'s
    ``escalations`` are pre-sorted by ``block_age`` descending), or all
    ``None`` when this decision has not escalated.
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
