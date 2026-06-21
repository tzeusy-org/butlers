"""Shared Pydantic response/request models for the Dashboard API.

Provides generic wrappers (ApiResponse, PaginatedResponse), error format,
pagination metadata, and common summary models used across all endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

# ---------------------------------------------------------------------------
# Base response wrappers
# ---------------------------------------------------------------------------


class ApiMeta(BaseModel):
    """Extensible metadata bag attached to every API response."""

    model_config = {"extra": "allow"}


class ApiResponse[T](BaseModel):
    """Generic API response wrapper.

    All successful responses follow ``{"data": T, "meta": {...}}``.
    """

    data: T
    meta: ApiMeta = Field(default_factory=ApiMeta)


class ErrorDetail(BaseModel):
    """Structured error payload."""

    code: str
    message: str
    butler: str | None = None
    details: dict | None = None


class ErrorResponse(BaseModel):
    """Standard error response envelope."""

    error: ErrorDetail


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class PaginationMeta(BaseModel):
    """Pagination metadata for list endpoints."""

    total: int
    offset: int
    limit: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_more(self) -> bool:
        """True when more items exist beyond the current page."""
        return self.offset + self.limit < self.total


class PaginatedResponse[T](BaseModel):
    """API response wrapper for paginated list endpoints.

    ``{"data": [T, ...], "meta": PaginationMeta}``
    """

    data: list[T]
    meta: PaginationMeta


class CursorPaginationMeta(BaseModel):
    """Pagination metadata for cursor-based (keyset) list endpoints.

    Replaces offset+total with an opaque cursor token.
    """

    next_cursor: str | None = None
    has_more: bool


class CursorPaginatedResponse[T](BaseModel):
    """API response wrapper for cursor-paginated list endpoints.

    ``{"data": [T, ...], "meta": CursorPaginationMeta}``
    """

    data: list[T]
    meta: CursorPaginationMeta


class KeysetMeta(BaseModel):
    """Keyset (cursor) pagination metadata that also echoes the page ``limit``.

    ``{"limit": 50, "next_cursor": "<opaque>|null", "has_more": true}``

    Distinct from :class:`CursorPaginationMeta` (which omits ``limit``) so the
    sessions list can surface the effective page size without disturbing other
    cursor-paginated endpoints.
    """

    limit: int
    next_cursor: str | None = None
    has_more: bool


class KeysetResponse[T](BaseModel):
    """API response wrapper for keyset-paginated list endpoints.

    ``{"data": [T, ...], "meta": KeysetMeta}``
    """

    data: list[T]
    meta: KeysetMeta


# ---------------------------------------------------------------------------
# Common domain summaries
# ---------------------------------------------------------------------------


class ScheduleEntry(BaseModel):
    """A single scheduled task from butler.toml."""

    name: str
    cron: str
    prompt: str | None = None


class ModuleInfo(BaseModel):
    """Module status information."""

    name: str
    enabled: bool = True
    config: dict | None = None


class ButlerSummary(BaseModel):
    """Lightweight butler representation for list views.

    Combines static config data (name, port, description, modules) with
    live status obtained by probing the butler's MCP server.  When a butler
    is unreachable, ``status`` is set to ``"down"``.
    """

    name: str
    status: str
    port: int
    type: str = "butler"  # "butler" or "staffer"
    db: str = ""
    description: str | None = None
    modules: list[str] = Field(default_factory=list)
    schedule_count: int = 0
    sessions_24h: int = 0
    last_session_started_at: datetime | None = None


class SkillInfo(BaseModel):
    """Skill name and SKILL.md content for a butler."""

    name: str
    content: str


class ProcessFacts(BaseModel):
    """Container-boundary-safe process facts for the butler Overview tab.

    Derived from stable topology sources rather than per-process OS state.
    ``pid`` is intentionally absent; it is not safe across container boundaries.
    """

    container_name: str | None = None
    port: int
    registered_duration_seconds: float | None = None
    config_path: str


class ButlerDetail(ButlerSummary):
    """Full butler detail with config, modules, skills, and schedule."""

    description: str | None = None
    db_name: str | None = None
    db_schema: str | None = None
    modules: list[ModuleInfo] = Field(default_factory=list)
    schedules: list[ScheduleEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    process_facts: ProcessFacts | None = None


class SessionSummary(BaseModel):
    """Lightweight session representation for list views."""

    id: UUID
    butler: str | None = None
    prompt: str
    trigger_source: str
    request_id: str | None = None
    success: bool | None = None
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    model: str | None = None
    complexity: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class ButlerConfigResponse(BaseModel):
    """Full butler configuration returned by the config endpoint.

    Contains the parsed butler.toml as a dict plus the raw text content
    of the markdown config files (CLAUDE.md, AGENTS.md, MANIFESTO.md).
    Missing markdown files are represented as ``None``.
    """

    butler_toml: dict
    claude_md: str | None = None
    agents_md: str | None = None
    manifesto_md: str | None = None


class HealthResponse(BaseModel):
    """Health-check response."""

    status: str


# ---------------------------------------------------------------------------
# Issue models
# ---------------------------------------------------------------------------


def compute_issue_key(issue_type: str, butler: str) -> str:
    """Return a deterministic, persistence-safe key identifying an issue group.

    The key is stable across requests so a server-side dismissal (ack) can be
    keyed against it:

    - Audit-derived issues already carry a deterministic ``type`` slug that
      encodes the normalized error message (see ``audit_grouping``), so the
      ``type`` component alone identifies the group.
    - Reachability issues all share ``type == "unreachable"``; the ``butler``
      component disambiguates one unreachable butler from another.

    Multi-butler audit groups use ``butler == "multiple"``, which is itself
    stable for a given error type, so the composite key stays consistent.
    """
    return f"{issue_type}::{butler}"


class Issue(BaseModel):
    """Active issue detected across butler infrastructure."""

    severity: str  # "critical" or "warning"
    type: str  # "unreachable", "module_error", "notification_failure", etc.
    butler: str
    description: str
    link: str | None = None
    error_message: str | None = None
    occurrences: int = 1
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    butlers: list[str] = Field(default_factory=list)
    dismissed: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def issue_key(self) -> str:
        """Stable identifier used by the dismissal (ack) store and the UI."""
        return compute_issue_key(self.type, self.butler)


class DismissIssueRequest(BaseModel):
    """Request body for dismissing (acking) an issue group."""

    issue_key: str
    dismissed_by: str | None = None


# ---------------------------------------------------------------------------
# Trigger models
# ---------------------------------------------------------------------------


class TriggerRequest(BaseModel):
    """Request body for triggering a runtime session on a butler."""

    prompt: str
    complexity: str = "workhorse"


class TriggerResponse(BaseModel):
    """Response from triggering a runtime session."""

    session_id: str | None = None
    success: bool
    output: str | None = None


class TickResponse(BaseModel):
    """Response from a forced scheduler tick."""

    success: bool
    message: str | None = None


# ---------------------------------------------------------------------------
# MCP debugging models
# ---------------------------------------------------------------------------


class MCPToolInfo(BaseModel):
    """A tool exposed by a butler's MCP server."""

    name: str
    description: str | None = None
    input_schema: dict[str, Any] | None = None


class MCPToolCallRequest(BaseModel):
    """Request body for invoking an MCP tool on a butler."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class MCPToolCallResponse(BaseModel):
    """Response envelope for a proxied MCP tool invocation."""

    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | list[Any] | str | int | float | bool | None = None
    raw_text: str | None = None
    is_error: bool = False


# ---------------------------------------------------------------------------
# Cost models
# ---------------------------------------------------------------------------


class SpendSummary(BaseModel):
    """Aggregate spend summary across all butlers."""

    period: str = "today"
    total_cost_usd: float
    total_sessions: int
    total_input_tokens: int
    total_output_tokens: int
    by_butler: dict[str, float] = Field(default_factory=dict)
    by_model: dict[str, float] = Field(default_factory=dict)


class DailySpend(BaseModel):
    """Spend data for a single day."""

    date: str
    cost_usd: float
    sessions: int
    input_tokens: int
    output_tokens: int


class TopSession(BaseModel):
    """A session ranked by cost."""

    session_id: str
    butler: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    model: str
    started_at: str


class ScheduleCost(BaseModel):
    """Cost analysis for a single scheduled task."""

    schedule_name: str
    butler: str
    cron: str
    total_runs: int
    total_cost_usd: float
    avg_cost_per_run: float
    runs_per_day: float
    projected_monthly_usd: float


# ---------------------------------------------------------------------------
# Notification models (re-exported from sub-module)
# ---------------------------------------------------------------------------

from butlers.api.models.approval import (  # noqa: E402
    ApprovalAction,
    ApprovalActionApproveRequest,
    ApprovalActionRejectRequest,
    ApprovalMetrics,
    ApprovalRule,
    ApprovalRuleCreateRequest,
    ApprovalRuleFromActionRequest,
    AutonomySuggestion,
    AutonomySuggestionDismissRequest,
    AutonomySuggestionVelocity,
    ExpireStaleActionsResponse,
    RuleConstraintSuggestion,
)
from butlers.api.models.audit import AuditEntry, AuditLogEntry  # noqa: E402
from butlers.api.models.butler import ModuleStatus  # noqa: E402
from butlers.api.models.connector import (  # noqa: E402
    ConnectorCheckpoint,
    ConnectorCounters,
    ConnectorDaySummary,
    ConnectorDetail,
    ConnectorFanoutEntry,
    ConnectorStats,
    ConnectorStatsBucket,
    ConnectorStatsSummary,
    ConnectorSummary,
    derive_liveness,
)
from butlers.api.models.conversation import (  # noqa: E402
    ConversationCreateRequest,
    ConversationMessage,
    ConversationSearchResult,
    ConversationStats,
    ConversationSummary,
    ConversationUpdateRequest,
    MessageCreateRequest,
)
from butlers.api.models.memory import (  # noqa: E402
    ButlerMemoryStats,
    Episode,
    Fact,
    MemoryActivity,
    MemoryStats,
)
from butlers.api.models.memory import (  # noqa: E402
    Rule as MemoryRule,
)
from butlers.api.models.modules import (  # noqa: E402
    ModuleRuntimeStateResponse,
    ModuleSetEnabledRequest,
)
from butlers.api.models.notification import NotificationStats, NotificationSummary  # noqa: E402
from butlers.api.models.search import SearchResponse, SearchResult  # noqa: E402
from butlers.api.models.secrets import SecretEntry, SecretUpsertRequest  # noqa: E402
from butlers.api.models.session import (  # noqa: E402
    DailyActivity,
    DailyActivityBucket,
    HourlyActivity,
    HourlyActivityBucket,
    LatencyStats,
    SessionAggregate,
    SessionAggregateButler,
    SessionDetail,
    SessionKindBreakdown,
    SessionKindItem,
)
from butlers.api.models.state import StateEntry, StateSetRequest  # noqa: E402
from butlers.api.models.timeline import TimelineEvent, TimelineResponse  # noqa: E402

__all__ = [
    "ApprovalAction",
    "ApprovalActionApproveRequest",
    "ApprovalActionRejectRequest",
    "ApprovalMetrics",
    "ApprovalRule",
    "ApprovalRuleCreateRequest",
    "ApprovalRuleFromActionRequest",
    "AuditEntry",
    "AuditLogEntry",
    "AutonomySuggestion",
    "AutonomySuggestionDismissRequest",
    "AutonomySuggestionVelocity",
    "ApiMeta",
    "ApiResponse",
    "ButlerMemoryStats",
    "ButlerConfigResponse",
    "SpendSummary",
    "ButlerDetail",
    "ButlerSummary",
    "ConversationCreateRequest",
    "ConversationMessage",
    "ConversationSearchResult",
    "ConversationStats",
    "ConversationSummary",
    "ConversationUpdateRequest",
    "ConnectorCheckpoint",
    "ConnectorCounters",
    "ConnectorDaySummary",
    "ConnectorDetail",
    "ConnectorFanoutEntry",
    "ConnectorStats",
    "ConnectorStatsBucket",
    "ConnectorStatsSummary",
    "ConnectorSummary",
    "CursorPaginatedResponse",
    "CursorPaginationMeta",
    "DailyActivity",
    "DailyActivityBucket",
    "DailySpend",
    "derive_liveness",
    "Episode",
    "Fact",
    "MemoryActivity",
    "MemoryStats",
    "MemoryRule",
    "MessageCreateRequest",
    "ErrorDetail",
    "ErrorResponse",
    "ExpireStaleActionsResponse",
    "HealthResponse",
    "HourlyActivity",
    "HourlyActivityBucket",
    "Issue",
    "KeysetMeta",
    "KeysetResponse",
    "LatencyStats",
    "ModuleInfo",
    "ModuleRuntimeStateResponse",
    "ModuleSetEnabledRequest",
    "ModuleStatus",
    "NotificationStats",
    "NotificationSummary",
    "PaginatedResponse",
    "PaginationMeta",
    "ProcessFacts",
    "RuleConstraintSuggestion",
    "Schedule",
    "ScheduleCost",
    "SearchResponse",
    "SearchResult",
    "ScheduleCreate",
    "ScheduleEntry",
    "ScheduleUpdate",
    "SecretEntry",
    "SecretUpsertRequest",
    "SessionAggregate",
    "SessionAggregateButler",
    "SessionDetail",
    "SessionKindBreakdown",
    "SessionKindItem",
    "SessionSummary",
    "SkillInfo",
    "StateEntry",
    "StateSetRequest",
    "TickResponse",
    "TimelineEvent",
    "TimelineResponse",
    "TopSession",
    "TriggerRequest",
    "TriggerResponse",
]
