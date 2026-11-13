"""Dashboard models of the owner-facing decision set view (bu-jv1moa.27)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from butlers.api.models.approval import ApprovalGoalLineage


class DecisionSetFact(BaseModel):
    label: str
    value: str


class DecisionSetItemView(BaseModel):
    position: int
    effect_id: str
    capability: str
    #: A rule or schedule change: decided only when ticked on its own, never inside "all".
    explicit_only: bool
    #: Submitted back with a decision, with ``effect_id``, to bind it to what was shown.
    item_digest: str
    recipient_channel: str | None = None
    #: False: the card shows no facts and offers no decision; review the approval itself.
    exact: bool
    reason: Literal["missing", "unavailable", "not_pending", "changed", "no_preview"] | None = None
    title: str | None = None
    facts: list[DecisionSetFact] = Field(default_factory=list)
    lineage: ApprovalGoalLineage | None = None


class DecisionSetView(BaseModel):
    set_id: str
    version: int
    goal_id: str
    state: str
    expires_at: datetime
    #: Submitted back with a decision to bind it to this exact version.
    digest: str
    items: list[DecisionSetItemView]
    decidable: bool
    reason: Literal["state", "expired", "items"] | None = None
    #: What an unticked item comes to (the producer's ``UNTICKED_OUTCOME``): ``left`` stays
    #: parked for its own decision, ``rejected`` is rejected. The UI words it from this value.
    unticked_outcome: str
