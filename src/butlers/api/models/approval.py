"""Pydantic models for the approvals API domain.

Provides response/request models for the approvals dashboard API endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator


class TargetContact(BaseModel):
    """Compact target object resolved from an entity_id in action constraints.

    Included in ApprovalAction when ``tool_args`` contains an ``entity_id`` that
    resolves to a known entity in public.entities. ``id`` carries the entity_id.
    """

    id: str
    name: str
    roles: list[str] = Field(default_factory=list)


class EntityRef(BaseModel):
    """Human-readable resolution of an entity UUID referenced by a pending action.

    Pending actions such as ``relationship_assert_fact`` carry raw
    ``public.entities`` UUIDs in their ``tool_args`` (e.g. ``subject``,
    ``object``). Resolving those to ``canonical_name`` lets the Dispatch
    dossier explain *who/what* a fact references instead of showing bare UUIDs.
    """

    id: str
    name: str
    entity_type: str | None = None
    roles: list[str] = Field(default_factory=list)


class ApprovalEvidence(BaseModel):
    """A typed reference supporting a proposed approval action."""

    type: Literal["fact", "entity", "url", "text"]
    ref: str
    note: str


class ApprovalAction(BaseModel):
    """Approval action representation for dashboard API.

    Maps to PendingAction from the approvals module with frontend-friendly
    field names and types.
    """

    id: str
    butler: str
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
    why: str | None = None
    evidence: list[ApprovalEvidence] = Field(default_factory=list)
    blast_radius: Literal["none", "self", "contact", "external"] | None = None
    reversibility: Literal["reversible", "compensable", "irreversible"] | None = None
    dispatched: bool = Field(
        default=False,
        description=(
            "True when the approved action was actually dispatched and executed "
            "(status 'executed'). False when it was approved but not yet run "
            "(e.g. no reachable butler daemon); such actions remain in 'approved' "
            "state and can be retried."
        ),
    )
    push_outcome: Literal["delivered", "deferred", "collapsed", "duplicate", "failed"] | None = (
        Field(
            default=None,
            description=(
                "Terminal outcome of the owner-facing approval push for this action, "
                "or null if no push was ever attempted."
            ),
        )
    )
    push_failed: bool = Field(
        default=False,
        description=(
            "True when this action is still pending AND the owner was never actually "
            "notified (push_outcome is null or 'failed'). Never fabricate calm: a "
            "true value here means this row must not render as an ordinary pending "
            "action (bu-mda0r)."
        ),
    )


class ApprovalDetail(BaseModel):
    """Full dossier for a single approval — returned by GET /api/approvals/{id}.

    Includes all ApprovalAction fields plus the dossier-specific fields:
    ``title`` (human-readable headline), ``proposed_action`` (summary of the
    tool call being approved), and the Dispatch-language ``why`` / ``evidence``
    rationale block.
    """

    id: str
    title: str
    butler: str
    created_at: datetime
    expires_at: datetime | None = None
    why: str | None = None
    evidence: list[ApprovalEvidence] = Field(default_factory=list)
    blast_radius: Literal["none", "self", "contact", "external"] | None = None
    reversibility: Literal["reversible", "compensable", "irreversible"] | None = None
    proposed_action: dict[str, Any]
    status: str
    decided_by: str | None = None
    decided_at: datetime | None = None
    denial_reason: str | None = None
    execution_result: dict[str, Any] | None = None
    target_contact: TargetContact | None = None
    session_id: str | None = Field(
        default=None,
        description=(
            "Originating session UUID that produced this action, when known. "
            "Lets the dossier link back to the session/trace that proposed the "
            "action so the owner can scrutinize the evidence before deciding."
        ),
    )
    referenced_entities: list[EntityRef] = Field(
        default_factory=list,
        description=(
            "Entity UUIDs found in the proposed action's tool_args, resolved to "
            "public.entities canonical names. Lets the dossier name who/what a "
            "fact references (e.g. subject/object of relationship_assert_fact)."
        ),
    )


class ApprovalAbandonRequest(BaseModel):
    """Explicit accountable reason for dashboard-only stalled-action abandonment."""

    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def require_non_blank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value.strip()

    push_outcome: Literal["delivered", "deferred", "collapsed", "duplicate", "failed"] | None = (
        Field(
            default=None,
            description=(
                "Terminal outcome of the owner-facing approval push for this action, "
                "or null if no push was ever attempted."
            ),
        )
    )
    push_failed: bool = Field(
        default=False,
        description=(
            "True when this action is still pending AND the owner was never actually "
            "notified. Never fabricate calm (bu-mda0r)."
        ),
    )


class ApprovalSummary(BaseModel):
    """Compact approval item for the flat-list GET /api/approvals endpoint."""

    id: str
    butler: str
    tool_name: str
    status: str
    created_at: datetime
    expires_at: datetime | None = None
    why: str | None = None
    blast_radius: Literal["none", "self", "contact", "external"] | None = None
    reversibility: Literal["reversible", "compensable", "irreversible"] | None = None
    push_outcome: Literal["delivered", "deferred", "collapsed", "duplicate", "failed"] | None = (
        Field(
            default=None,
            description=(
                "Terminal outcome of the owner-facing approval push for this action, "
                "or null if no push was ever attempted."
            ),
        )
    )
    push_failed: bool = Field(
        default=False,
        description=(
            "True when this action is still pending AND the owner was never actually "
            "notified. Never fabricate calm (bu-mda0r)."
        ),
    )


class ApprovalsPolicy(BaseModel):
    """Owner Attention Policy singleton — GET/PUT /api/approvals/policy."""

    quiet_start_hour: int | None = Field(default=None, ge=0, le=23)
    quiet_end_hour: int | None = Field(default=None, ge=0, le=23)
    timezone: str = "UTC"


class ApprovalsPolicyUpdate(ApprovalsPolicy):
    """Write-boundary validation for the stable Owner Attention Policy payload."""

    @model_validator(mode="after")
    def validate_owner_attention_policy(self) -> ApprovalsPolicyUpdate:
        """Reject partial windows and timezone values that runtime cannot honor."""
        if (self.quiet_start_hour is None) != (self.quiet_end_hour is None):
            raise ValueError("quiet_start_hour and quiet_end_hour must be supplied together")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a recognized IANA timezone") from exc
        return self


class ApprovalApproveRequest(BaseModel):
    """Request body for POST /api/approvals/{id}/approve."""

    edits: dict[str, Any] | None = Field(default=None, description="Optional edits to tool args")


class ApprovalDenyRequest(BaseModel):
    """Request body for POST /api/approvals/{id}/deny."""

    reason: str | None = Field(default=None, description="Reason for denial")


class ApprovalDeferRequest(BaseModel):
    """Request body for POST /api/approvals/{id}/defer."""

    hours: int = Field(..., ge=1, le=168, description="Hours to defer (1–168 inclusive)")


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


class ApprovalGatedTool(BaseModel):
    """Configured approval gate and the active rules that narrow it.

    A configured gate remains present even when ``active_rules`` is empty.
    That empty state is the dashboard's truthful "always ask" baseline,
    rather than an absence that can be mistaken for an ungated tool.
    """

    butler: str
    tool_name: str
    risk_tier: Literal["low", "medium", "high", "critical"]
    expiry_hours: int
    active_rules: list[ApprovalRule] = Field(default_factory=list)


class RuleConstraintSuggestion(BaseModel):
    """Suggested constraints for creating a rule from an action."""

    action_id: str
    tool_name: str
    tool_args: dict[str, Any]
    suggested_constraints: dict[str, Any]


class ApprovalMetrics(BaseModel):
    """Aggregate metrics for the approvals dashboard.

    ``ApiResponse.meta`` carries availability per independently aggregated
    family: ``pending_actions_sources_degraded`` and
    ``approval_rules_sources_degraded``. A zero in either metric is truthful
    only when its matching metadata key is absent.
    """

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
    callback_secret_configured: bool | None = Field(
        default=None,
        description=(
            "Whether APPROVAL_CALLBACK_SECRET resolves via the shared credential "
            "store. False means every approval push is structurally disabled "
            "(each attempt will resolve 'failed') until it is provisioned. Null "
            "when this could not be determined (e.g. no approvals pool available)."
        ),
    )


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
    action_id: str | None = None
    suggestion_type: str  # "promotion" or "demotion"
    pattern_fingerprint: str
    fingerprint_version: int = 1
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
