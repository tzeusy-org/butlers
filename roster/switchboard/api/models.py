"""Pydantic models for the switchboard API.

Provides models for routing log, registry entries, connector ingestion,
and unified ingestion rules (switchboard butler).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, field_validator, model_validator


class RoutingEntry(BaseModel):
    """A single entry in the switchboard routing log."""

    id: str
    source_butler: str
    target_butler: str
    tool_name: str
    success: bool
    duration_ms: int | None = None
    error: str | None = None
    created_at: str


class RegistryEntry(BaseModel):
    """A butler entry in the switchboard registry."""

    name: str
    endpoint_url: str
    description: str | None = None
    modules: list = []
    capabilities: list = []
    last_seen_at: str | None = None
    eligibility_state: str = "active"
    liveness_ttl_seconds: int = 300
    quarantined_at: str | None = None
    quarantine_reason: str | None = None
    route_contract_min: int = 1
    route_contract_max: int = 1
    eligibility_updated_at: str | None = None
    registered_at: str


class HeartbeatRequest(BaseModel):
    """Request body for the POST /api/heartbeat endpoint."""

    butler_name: str


class HeartbeatResponse(BaseModel):
    """Response body for the POST /api/heartbeat endpoint."""

    status: str
    eligibility_state: str


class SetEligibilityRequest(BaseModel):
    """Request body for POST /api/switchboard/registry/{name}/eligibility."""

    eligibility_state: str

    @field_validator("eligibility_state")
    @classmethod
    def state_valid(cls, v: str) -> str:
        allowed = {"active", "stale", "quarantined"}
        if v not in allowed:
            raise ValueError(f"eligibility_state must be one of {sorted(allowed)}")
        return v


class SetEligibilityResponse(BaseModel):
    """Response body for POST /api/switchboard/registry/{name}/eligibility."""

    name: str
    previous_state: str
    new_state: str


# ---------------------------------------------------------------------------
# Connector ingestion models
# ---------------------------------------------------------------------------


class ConnectorEntry(BaseModel):
    """A connector entry from the connector_registry table.

    Fields align with what is needed for Overview/Connectors tab cards and
    health badge rows in the ingestion dashboard.
    """

    connector_type: str
    endpoint_identity: str
    instance_id: str | None = None
    version: str | None = None
    state: str = "unknown"
    error_message: str | None = None
    uptime_s: int | None = None
    last_heartbeat_at: str | None = None
    first_seen_at: str
    registered_via: str = "self"
    # Cumulative counters
    counter_messages_ingested: int = 0
    counter_messages_failed: int = 0
    counter_source_api_calls: int = 0
    counter_checkpoint_saves: int = 0
    counter_dedupe_accepted: int = 0
    # Today's aggregated stats (from connector_heartbeat_log via Prometheus)
    today_messages_ingested: int = 0
    today_messages_failed: int = 0
    # Checkpoint info
    checkpoint_cursor: str | None = None
    checkpoint_updated_at: str | None = None
    # Runtime-configurable settings (e.g. discretion thresholds)
    settings: dict | None = None


class ConnectorSummary(BaseModel):
    """Aggregate summary across all connectors.

    Drives the summary row at the top of the Connectors tab.
    """

    total_connectors: int = 0
    online_count: int = 0
    stale_count: int = 0
    offline_count: int = 0
    unknown_count: int = 0
    total_messages_ingested: int = 0
    total_messages_failed: int = 0
    error_rate_pct: float = 0.0


class ConnectorStatsHourly(BaseModel):
    """Hourly rollup stats for a single connector.

    Drives volume trend charts with 24h/7d/30d period support.
    """

    connector_type: str
    endpoint_identity: str
    hour: str
    messages_ingested: int = 0
    messages_failed: int = 0
    source_api_calls: int = 0
    dedupe_accepted: int = 0
    heartbeat_count: int = 0
    healthy_count: int = 0
    degraded_count: int = 0
    error_count: int = 0


class ConnectorStatsDaily(BaseModel):
    """Daily rollup stats for a single connector.

    Drives volume trend charts for the 7d/30d period options.
    """

    connector_type: str
    endpoint_identity: str
    day: str
    messages_ingested: int = 0
    messages_failed: int = 0
    source_api_calls: int = 0
    dedupe_accepted: int = 0
    heartbeat_count: int = 0
    healthy_count: int = 0
    degraded_count: int = 0
    error_count: int = 0
    uptime_pct: float | None = None


class FanoutRow(BaseModel):
    """A single row in the connector × butler fanout matrix.

    One row per (connector_type, endpoint_identity, target_butler) tuple,
    aggregated over the requested period.
    """

    connector_type: str
    endpoint_identity: str
    target_butler: str
    message_count: int = 0


class IngestionOverviewStats(BaseModel):
    """Aggregate ingestion overview statistics for the Overview tab.

    Covers the stat-row cards and derived metrics needed by the Overview tab.
    All counts are for the requested period (default 24h).
    """

    period: str = "24h"
    total_ingested: int = 0
    total_skipped: int = 0
    total_metadata_only: int = 0
    llm_calls_saved: int = 0
    active_connectors: int = 0
    # Tier breakdown counts (for donut chart)
    tier1_full_count: int = 0
    tier2_metadata_count: int = 0
    tier3_skip_count: int = 0


# ---------------------------------------------------------------------------
# Ingestion rule condition schemas
# ---------------------------------------------------------------------------


class SenderDomainCondition(BaseModel):
    """Condition schema for rule_type='sender_domain'."""

    domain: str
    match: Literal["exact", "suffix"]

    @field_validator("domain")
    @classmethod
    def domain_lowercase_nonempty(cls, v: str) -> str:
        if not v or v != v.lower():
            raise ValueError("domain must be lowercase and non-empty")
        return v


class SenderAddressCondition(BaseModel):
    """Condition schema for rule_type='sender_address'."""

    address: str

    @field_validator("address")
    @classmethod
    def address_lowercase_nonempty(cls, v: str) -> str:
        if not v or v != v.lower():
            raise ValueError("address must be lowercase and non-empty")
        return v


class HeaderCondition(BaseModel):
    """Condition schema for rule_type='header_condition'."""

    header: str
    op: Literal["present", "equals", "contains"]
    value: str | None = None

    @model_validator(mode="after")
    def validate_op_value(self) -> HeaderCondition:
        if self.op in ("equals", "contains"):
            if not self.value:
                raise ValueError(f"value must be present and non-empty for op='{self.op}'")
        elif self.op == "present":
            if self.value is not None:
                raise ValueError("value must be null or omitted for op='present'")
        return self


class MimeTypeCondition(BaseModel):
    """Condition schema for rule_type='mime_type'."""

    type: str

    @field_validator("type")
    @classmethod
    def type_lowercase_nonempty(cls, v: str) -> str:
        if not v or v != v.lower():
            raise ValueError("type must be lowercase and non-empty")
        return v


# All 7 rule types supported by the unified ingestion_rules table (design.md D7)
INGESTION_RULE_TYPES = frozenset(
    {
        "sender_domain",
        "sender_address",
        "header_condition",
        "mime_type",
        "substring",
        "chat_id",
        "channel_id",
    }
)

# Rule types valid per connector type (design.md D8 scope-aware validation)
_CONNECTOR_RULE_TYPES: dict[str, frozenset[str]] = {
    "gmail": frozenset({"sender_domain", "sender_address", "substring"}),
    "telegram-bot": frozenset({"chat_id", "substring"}),
    "discord": frozenset({"channel_id", "substring"}),
}

# Supported simple actions (route_to:<butler> is validated separately)
SIMPLE_ACTIONS = frozenset({"skip", "metadata_only", "low_priority_queue", "pass_through"})

# Valid actions for global scope (all actions)
GLOBAL_ACTIONS = SIMPLE_ACTIONS  # route_to:<butler> validated separately

# Valid actions for connector scope (design.md D3)
CONNECTOR_ACTIONS = frozenset({"block"})


class SubstringCondition(BaseModel):
    """Condition schema for rule_type='substring'."""

    pattern: str

    @field_validator("pattern")
    @classmethod
    def pattern_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("pattern must be non-empty")
        return v


class ChatIdCondition(BaseModel):
    """Condition schema for rule_type='chat_id'."""

    chat_id: str

    @field_validator("chat_id")
    @classmethod
    def chat_id_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("chat_id must be non-empty")
        return v


class ChannelIdCondition(BaseModel):
    """Condition schema for rule_type='channel_id'."""

    channel_id: str

    @field_validator("channel_id")
    @classmethod
    def channel_id_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("channel_id must be non-empty")
        return v


def validate_condition(rule_type: str, condition: dict[str, Any]) -> dict[str, Any]:
    """Validate condition JSONB against the rule_type schema.

    Returns the validated condition dict.
    Raises ValueError on schema mismatch.
    """
    if rule_type == "sender_domain":
        return SenderDomainCondition(**condition).model_dump()
    elif rule_type == "sender_address":
        return SenderAddressCondition(**condition).model_dump()
    elif rule_type == "header_condition":
        cond = HeaderCondition(**condition)
        d = cond.model_dump()
        if cond.op == "present":
            d["value"] = None
        return d
    elif rule_type == "mime_type":
        return MimeTypeCondition(**condition).model_dump()
    elif rule_type == "substring":
        return SubstringCondition(**condition).model_dump()
    elif rule_type == "chat_id":
        return ChatIdCondition(**condition).model_dump()
    elif rule_type == "channel_id":
        return ChannelIdCondition(**condition).model_dump()
    else:
        raise ValueError(f"Unknown rule_type: {rule_type!r}")


def validate_ingestion_action(action: str, scope: str) -> str:
    """Validate action for the unified ingestion_rules table, scope-aware.

    - scope='global': action must be one of SIMPLE_ACTIONS or 'route_to:<butler>'
    - scope='connector:*': action must be 'block'
    Raises ValueError on invalid scope/action combination.
    """
    if scope == "global":
        if action in GLOBAL_ACTIONS:
            return action
        if action.startswith("route_to:") and len(action) > len("route_to:"):
            return action
        raise ValueError(
            f"Invalid action {action!r} for global scope. "
            f"Must be one of {sorted(GLOBAL_ACTIONS)} or 'route_to:<butler>'"
        )
    elif scope.startswith("connector:"):
        if action not in CONNECTOR_ACTIONS:
            raise ValueError(
                f"Invalid action {action!r} for connector scope. "
                f"Must be one of {sorted(CONNECTOR_ACTIONS)}"
            )
        return action
    else:
        raise ValueError(
            f"Invalid scope {scope!r}. Must be 'global' or 'connector:<type>:<identity>'"
        )


def validate_scope(scope: str) -> str:
    """Validate scope value per design.md D2.

    Returns the scope string unchanged if valid.
    Raises ValueError otherwise.
    """
    if scope == "global":
        return scope
    if scope.startswith("connector:"):
        parts = scope.split(":", 2)
        if len(parts) < 3 or not parts[1] or not parts[2]:
            raise ValueError(
                f"Invalid connector scope {scope!r}. "
                "Must be 'connector:<connector_type>:<endpoint_identity>'"
            )
        return scope
    raise ValueError(f"Invalid scope {scope!r}. Must be 'global' or 'connector:<type>:<identity>'")


def validate_rule_type_for_scope(rule_type: str, scope: str) -> str:
    """Validate rule_type is compatible with the scope's connector type.

    For global scope, all rule types are valid.
    For connector scope, rule_type must be compatible with the connector type
    extracted from the scope string.
    Raises ValueError on incompatible rule_type/scope combination.
    """
    if scope == "global":
        return rule_type

    if scope.startswith("connector:"):
        parts = scope.split(":", 2)
        if len(parts) < 3:
            return rule_type  # scope validation handles this
        connector_type = parts[1]
        allowed = _CONNECTOR_RULE_TYPES.get(connector_type)
        if allowed is not None and rule_type not in allowed:
            raise ValueError(
                f"rule_type {rule_type!r} is not compatible with connector type "
                f"{connector_type!r}. Must be one of {sorted(allowed)}"
            )
    return rule_type


# ---------------------------------------------------------------------------
# Ingestion rule API models (unified, design.md D8)
# ---------------------------------------------------------------------------


class IngestionRule(BaseModel):
    """A persisted ingestion rule returned from the API."""

    id: str
    scope: str
    rule_type: str
    condition: dict[str, Any]
    action: str
    priority: int
    enabled: bool
    name: str | None = None
    description: str | None = None
    created_by: str
    created_at: str
    updated_at: str
    deleted_at: str | None = None


class IngestionRuleCreate(BaseModel):
    """Request body for POST /api/switchboard/ingestion-rules."""

    scope: str
    rule_type: str
    condition: dict[str, Any]
    action: str
    priority: int
    enabled: bool = True
    name: str | None = None
    description: str | None = None

    @field_validator("scope")
    @classmethod
    def scope_valid(cls, v: str) -> str:
        return validate_scope(v)

    @field_validator("rule_type")
    @classmethod
    def rule_type_valid(cls, v: str) -> str:
        if v not in INGESTION_RULE_TYPES:
            raise ValueError(f"rule_type must be one of {sorted(INGESTION_RULE_TYPES)}")
        return v

    @field_validator("priority")
    @classmethod
    def priority_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("priority must be >= 0")
        return v

    @model_validator(mode="after")
    def cross_field_validation(self) -> IngestionRuleCreate:
        # Validate action for scope
        try:
            validate_ingestion_action(self.action, self.scope)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        # Validate condition schema
        try:
            validate_condition(self.rule_type, self.condition)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"condition invalid for rule_type={self.rule_type!r}: {exc}") from exc
        # Validate rule_type is compatible with scope
        try:
            validate_rule_type_for_scope(self.rule_type, self.scope)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self


class IngestionRuleUpdate(BaseModel):
    """Request body for PATCH /api/switchboard/ingestion-rules/:id.

    All fields are optional (partial update).
    """

    scope: str | None = None
    condition: dict[str, Any] | None = None
    action: str | None = None
    priority: int | None = None
    enabled: bool | None = None
    name: str | None = None
    description: str | None = None

    @field_validator("scope")
    @classmethod
    def scope_valid(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_scope(v)
        return v

    @field_validator("priority")
    @classmethod
    def priority_non_negative(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("priority must be >= 0")
        return v


class IngestionRuleTestEnvelope(BaseModel):
    """Sample envelope for dry-run ingestion rule testing."""

    sender_address: str = ""
    source_channel: str = ""
    headers: dict[str, str] = {}
    mime_parts: list[str] = []
    raw_key: str = ""


class IngestionRuleTestRequest(BaseModel):
    """Request body for POST /api/switchboard/ingestion-rules/test."""

    envelope: IngestionRuleTestEnvelope
    scope: str = "global"

    @field_validator("scope")
    @classmethod
    def scope_valid(cls, v: str) -> str:
        return validate_scope(v)


class IngestionRuleTestResult(BaseModel):
    """Result of a dry-run ingestion rule test."""

    matched: bool
    decision: str | None = None
    target_butler: str | None = None
    matched_rule_id: str | None = None
    matched_rule_type: str | None = None
    reason: str


class IngestionRuleTestResponse(BaseModel):
    """Response envelope for POST /api/switchboard/ingestion-rules/test."""

    data: IngestionRuleTestResult


# Backfill job models
# ---------------------------------------------------------------------------


class BackfillJobEntry(BaseModel):
    """A single backfill job row from switchboard.backfill_jobs.

    Exposes all fields required for the backfill job management dashboard.
    """

    id: str
    connector_type: str
    endpoint_identity: str
    target_categories: list[str] = []
    date_from: str
    date_to: str
    rate_limit_per_hour: int = 100
    daily_cost_cap_cents: int = 500
    status: str = "pending"
    cursor: dict | None = None
    rows_processed: int = 0
    rows_skipped: int = 0
    cost_spent_cents: int = 0
    error: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str


class BackfillJobSummary(BaseModel):
    """Summarised view of a backfill job for list endpoints.

    Omits cursor to keep list responses lightweight.
    """

    id: str
    connector_type: str
    endpoint_identity: str
    target_categories: list[str] = []
    date_from: str
    date_to: str
    rate_limit_per_hour: int = 100
    daily_cost_cap_cents: int = 500
    status: str = "pending"
    rows_processed: int = 0
    rows_skipped: int = 0
    cost_spent_cents: int = 0
    error: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str


class CreateBackfillJobRequest(BaseModel):
    """Request body for POST /api/switchboard/backfill."""

    connector_type: str
    endpoint_identity: str
    target_categories: list[str] = []
    date_from: str
    date_to: str
    rate_limit_per_hour: int = 100
    daily_cost_cap_cents: int = 500


class BackfillLifecycleResponse(BaseModel):
    """Response body for backfill lifecycle actions (pause/cancel/resume).

    Returns the job's updated status after the action.
    """

    job_id: str
    status: str


# ---------------------------------------------------------------------------
# Thread affinity settings and override models
# ---------------------------------------------------------------------------


class ThreadAffinitySettings(BaseModel):
    """Global thread-affinity settings row from thread_affinity_settings."""

    enabled: bool = True
    """Whether thread-affinity lookup is globally active."""

    ttl_days: int = 30
    """Max age in days for routing_log rows considered for affinity lookup."""

    thread_overrides: dict[str, str] = {}
    """Per-thread overrides: {thread_id: "disabled" | "force:<butler>"}."""

    updated_at: str | None = None


class ThreadAffinitySettingsUpdate(BaseModel):
    """Request body for PATCH /api/switchboard/thread-affinity/settings."""

    enabled: bool | None = None
    """Toggle global thread-affinity routing."""

    ttl_days: int | None = None
    """Max age window in days (must be positive)."""


class ThreadOverrideUpsert(BaseModel):
    """Request body for PUT /api/switchboard/thread-affinity/overrides/:thread_id."""

    mode: str
    """Override mode: 'disabled' or 'force:<butler>'.

    - 'disabled': suppress affinity for this thread.
    - 'force:<butler>': route this thread directly to the named butler.
    """

    @field_validator("mode")
    @classmethod
    def mode_valid(cls, v: str) -> str:
        if v == "disabled":
            return v
        if v.startswith("force:") and v[len("force:") :]:
            return v
        raise ValueError("mode must be 'disabled' or 'force:<butler>' with a non-empty butler name")


class ThreadOverrideEntry(BaseModel):
    """A single per-thread override entry."""

    thread_id: str
    mode: str


# ---------------------------------------------------------------------------
# Routing instruction models
# ---------------------------------------------------------------------------


class EligibilitySegment(BaseModel):
    """A single segment in the eligibility timeline."""

    state: str
    start_at: str
    end_at: str


class EligibilityHistoryResponse(BaseModel):
    """24h eligibility timeline for a butler."""

    butler_name: str
    segments: list[EligibilitySegment]
    window_start: str
    window_end: str


class RoutingInstruction(BaseModel):
    """A persisted routing instruction returned from the API."""

    id: str
    instruction: str
    priority: int
    enabled: bool
    created_by: str
    created_at: str
    updated_at: str


class RoutingInstructionCreate(BaseModel):
    """Request body for POST /api/switchboard/routing-instructions."""

    instruction: str
    priority: int = 100
    enabled: bool = True

    @field_validator("instruction")
    @classmethod
    def instruction_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("instruction must be non-empty")
        return v.strip()

    @field_validator("priority")
    @classmethod
    def priority_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("priority must be >= 0")
        return v


class RoutingInstructionUpdate(BaseModel):
    """Request body for PATCH /api/switchboard/routing-instructions/{id}.

    All fields are optional (partial update).
    """

    instruction: str | None = None
    priority: int | None = None
    enabled: bool | None = None

    @field_validator("instruction")
    @classmethod
    def instruction_nonempty(cls, v: str | None) -> str | None:
        if v is not None:
            if not v.strip():
                raise ValueError("instruction must be non-empty")
            return v.strip()
        return v

    @field_validator("priority")
    @classmethod
    def priority_non_negative(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("priority must be >= 0")
        return v


class CursorUpdateRequest(BaseModel):
    """Request body for PATCH /connectors/{type}/{identity}/cursor."""

    cursor: str

    @field_validator("cursor")
    @classmethod
    def cursor_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("cursor must be non-empty")
        return v


class ConnectorSettingsUpdateRequest(BaseModel):
    """Request body for PATCH /connectors/{type}/{identity}/settings.

    Accepts a partial JSON object that is merged into the existing settings.
    """

    settings: dict
