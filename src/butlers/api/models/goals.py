"""Pydantic models for the goal-ledger read API (RFC 0038 D19, bu-0rbswe).

The projection is content-free (D17): identities, states, labels, digests, counts and
receipts only. It carries no grant snapshot, tool arguments, results or prompt text, and it
creates no kernel fields. A field is nullable only where the authoritative row can truly be
absent (a goal with no outcome yet has ``outcome = null``); an unavailable source is named in
``sources_degraded``, never rendered as null.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class GoalSummary(BaseModel):
    """One goal-ledger row as listed."""

    id: UUID
    origin: str
    state: str
    exposed: bool
    admitted_exposed: bool
    chain_depth: int
    caused_by_goal: UUID | None = None
    caused_by_effect: UUID | None = None
    regions: list[str] = Field(default_factory=list)
    outcome: str | None = None
    created_at: datetime
    updated_at: datetime


class GoalInvocation(BaseModel):
    """One node of the delegation tree."""

    id: UUID
    parent_invocation_id: UUID | None = None
    role: str
    depth: int
    status: str
    exposed: bool
    exposure_cause: str | None = None
    used_turns: int
    max_turns: int
    used_tokens: int
    max_tokens: int
    end_reason: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class GoalEffect(BaseModel):
    """One effect with its receipts; the arguments are never carried (D17)."""

    id: UUID
    invocation_id: UUID
    capability: str
    region: str
    status: str
    approval_ref: str | None = None
    receipt_ref: str | None = None
    delivery_ref: str | None = None
    recipient_entity: UUID | None = None
    recipient_channel: str | None = None
    created_at: datetime
    updated_at: datetime


class GoalApprovalLineage(BaseModel):
    """An approval-parked effect and the goals its lineage connects it to."""

    effect_id: UUID
    approval_ref: str
    effect_status: str
    capability: str
    caused_by_goal: UUID | None = None
    caused_by_effect: UUID | None = None
    caused_goals: list[UUID] = Field(default_factory=list)


class GoalBudget(BaseModel):
    """Finite budget counters (reserved is held, used is spent)."""

    max_invocations: int
    reserved_invocations: int
    used_invocations: int
    max_children: int
    active_children: int
    max_calls: int
    reserved_calls: int
    used_calls: int
    max_tokens: int
    reserved_tokens: int
    used_tokens: int
    max_spend: Decimal
    reserved_spend: Decimal
    used_spend: Decimal
    deadline: datetime


class GoalOutcome(BaseModel):
    """The durable lead outcome and its once-written resolution."""

    outcome: str
    final_status: str | None = None
    delivery_failure: str | None = None
    relied_effects: list[UUID] = Field(default_factory=list)
    reply_effect_id: UUID | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class GoalDetail(GoalSummary):
    """One goal with its delegation tree, effects, approvals, budget and outcome."""

    invocations: list[GoalInvocation]
    effects: list[GoalEffect]
    approvals: list[GoalApprovalLineage]
    budget: GoalBudget | None = None
    goal_outcome: GoalOutcome | None = None
    exposure_causes: list[str] = Field(default_factory=list)


class RegionGoal(BaseModel):
    """A recent goal that used a region; one goal id appears under each region it used."""

    id: UUID
    state: str
    origin: str
    created_at: datetime


class RegionObligation(BaseModel):
    """A schedule coming due; content-blind (no prompt text, arguments or error text)."""

    name: str
    source: str
    cron: str | None = None
    next_run_at: datetime


class RegionSummary(BaseModel):
    region: str
    recent_goals: list[RegionGoal] = Field(default_factory=list)
    obligations_due: list[RegionObligation] = Field(default_factory=list)


class RegionSummaries(BaseModel):
    regions: list[RegionSummary]
    #: Sources that failed this request: ``goal_ledger`` or ``obligations:<butler>``.
    sources_degraded: list[str] = Field(default_factory=list)
