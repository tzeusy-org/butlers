"""Pydantic models for the approvals API domain.

Provides response/request models for the approvals dashboard API endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TargetContact(BaseModel):
    """Compact contact object resolved from a contact_id in action constraints.

    Included in ApprovalAction when ``tool_args`` contains a ``contact_id`` that
    resolves to a known contact in public.contacts.
    """

    id: str
    name: str
    roles: list[str] = Field(default_factory=list)


class ApprovalAction(BaseModel):
    """Approval action representation for dashboard API.

    Maps to PendingAction from the approvals module with frontend-friendly
    field names and types.
    """

    id: str
    tool_name: str
    tool_args: dict[str, Any]
    status: str
    requested_at: datetime
    agent_summary: str | None = None
    session_id: str | None = None
    expires_at: datetime | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    execution_result: dict[str, Any] | None = None
    approval_rule_id: str | None = None
    target_contact: TargetContact | None = None


class ApprovalRule(BaseModel):
    """Approval rule representation for dashboard API.

    Maps to ApprovalRule from the approvals module with frontend-friendly
    field names and types.
    """

    id: str
    tool_name: str
    arg_constraints: dict[str, Any]
    description: str
    created_from: str | None = None
    created_at: datetime
    expires_at: datetime | None = None
    max_uses: int | None = None
    use_count: int = 0
    active: bool = True


class RuleConstraintSuggestion(BaseModel):
    """Suggested constraints for creating a rule from an action."""

    action_id: str
    tool_name: str
    tool_args: dict[str, Any]
    suggested_constraints: dict[str, Any]


class ApprovalMetrics(BaseModel):
    """Aggregate metrics for the approvals dashboard."""

    total_pending: int = 0
    total_approved_today: int = 0
    total_rejected_today: int = 0
    total_auto_approved_today: int = 0
    total_expired_today: int = 0
    avg_decision_latency_seconds: float | None = None
    auto_approval_rate: float = 0.0
    rejection_rate: float = 0.0
    failure_count_today: int = 0
    active_rules_count: int = 0


class ApprovalActionApproveRequest(BaseModel):
    """Request body for approving an action."""

    create_rule: bool = Field(default=False, description="Create a standing rule from this action")


class ApprovalActionRejectRequest(BaseModel):
    """Request body for rejecting an action."""

    reason: str | None = Field(default=None, description="Reason for rejection")


class ApprovalRuleCreateRequest(BaseModel):
    """Request body for creating a new approval rule."""

    tool_name: str
    arg_constraints: dict[str, Any]
    description: str
    expires_at: str | None = None
    max_uses: int | None = None


class ApprovalRuleFromActionRequest(BaseModel):
    """Request body for creating a rule from an action."""

    action_id: str
    constraint_overrides: dict[str, Any] | None = None


class ExpireStaleActionsResponse(BaseModel):
    """Response from expiring stale actions."""

    expired_count: int
    expired_ids: list[str]


class AutonomySuggestionVelocity(BaseModel):
    """Approval velocity data for an autonomy suggestion."""

    avg_seconds: float | None = None
    sample_count: int = 0
    fast_approval: bool = False
    updated_at: datetime | None = None


class AutonomySuggestion(BaseModel):
    """Autonomy promotion/demotion suggestion for dashboard API.

    Represents a suggestion that a frequently-approved tool pattern should
    be promoted to a standing rule (promotion), or a failing auto-approved
    action pattern should be demoted (demotion).
    """

    id: str
    suggestion_type: str  # "promotion" or "demotion"
    pattern_fingerprint: str
    tool_name: str
    representative_args: dict[str, Any]
    status: str  # "pending", "confirmed", "dismissed", "superseded"
    approval_count_at_creation: int = 0
    scope_description: str
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    resulting_rule_id: str | None = None
    cooldown_until: datetime | None = None
    dismissal_reason: str | None = None
    velocity: AutonomySuggestionVelocity | None = None


class AutonomySuggestionDismissRequest(BaseModel):
    """Request body for dismissing an autonomy suggestion."""

    reason: str | None = Field(default=None, description="Optional reason for dismissal")
    cooldown_days: int = Field(default=30, ge=0, description="Days before suggestion can reappear")
