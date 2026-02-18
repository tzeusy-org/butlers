"""Shared Pydantic response/request models for the Dashboard API.

Provides generic wrappers (ApiResponse, PaginatedResponse), error format,
pagination metadata, and common summary models used across all endpoints.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

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

    @property
    def has_more(self) -> bool:
        """True when more items exist beyond the current page."""
        return self.offset + self.limit < self.total

    model_config = {"json_schema_extra": {"properties": {"has_more": {"type": "boolean"}}}}


class PaginatedResponse[T](BaseModel):
    """API response wrapper for paginated list endpoints.

    ``{"data": [T, ...], "meta": PaginationMeta}``
    """

    data: list[T]
    meta: PaginationMeta


# ---------------------------------------------------------------------------
# Common domain summaries
# ---------------------------------------------------------------------------


class ScheduleEntry(BaseModel):
    """A single scheduled task from butler.toml."""

    name: str
    cron: str
    prompt: str


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
    db: str = ""
    description: str | None = None
    modules: list[str] = Field(default_factory=list)
    schedule_count: int = 0


class SkillInfo(BaseModel):
    """Skill name and SKILL.md content for a butler."""

    name: str
    content: str


class ButlerDetail(ButlerSummary):
    """Full butler detail with config, modules, skills, and schedule."""

    description: str | None = None
    db_name: str | None = None
    modules: list[ModuleInfo] = Field(default_factory=list)
    schedules: list[ScheduleEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


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


class Issue(BaseModel):
    """Active issue detected across butler infrastructure."""

    severity: str  # "critical" or "warning"
    type: str  # "unreachable", "module_error", "notification_failure", etc.
    butler: str
    description: str
    link: str | None = None


# ---------------------------------------------------------------------------
# Trigger models
# ---------------------------------------------------------------------------


class TriggerRequest(BaseModel):
    """Request body for triggering a runtime session on a butler."""

    prompt: str


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
# Cost models
# ---------------------------------------------------------------------------


class CostSummary(BaseModel):
    """Aggregate cost summary across all butlers."""

    period: str = "today"
    total_cost_usd: float
    total_sessions: int
    total_input_tokens: int
    total_output_tokens: int
    by_butler: dict[str, float] = Field(default_factory=dict)
    by_model: dict[str, float] = Field(default_factory=dict)


class DailyCost(BaseModel):
    """Cost data for a single day."""

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
    ExpireStaleActionsResponse,
    RuleConstraintSuggestion,
)
from butlers.api.models.audit import AuditEntry  # noqa: E402
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
from butlers.api.models.memory import (  # noqa: E402
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
from butlers.api.models.session import SessionDetail  # noqa: E402
from butlers.api.models.state import StateEntry, StateSetRequest  # noqa: E402
from butlers.api.models.timeline import TimelineEvent, TimelineResponse  # noqa: E402
from butlers.api.models.trace import SpanNode, TraceDetail, TraceSummary  # noqa: E402

__all__ = [
    "ApprovalAction",
    "ApprovalActionApproveRequest",
    "ApprovalActionRejectRequest",
    "ApprovalMetrics",
    "ApprovalRule",
    "ApprovalRuleCreateRequest",
    "ApprovalRuleFromActionRequest",
    "AuditEntry",
    "ApiMeta",
    "ApiResponse",
    "ButlerConfigResponse",
    "CostSummary",
    "ButlerDetail",
    "ButlerSummary",
    "ConnectorCheckpoint",
    "ConnectorCounters",
    "ConnectorDaySummary",
    "ConnectorDetail",
    "ConnectorFanoutEntry",
    "ConnectorStats",
    "ConnectorStatsBucket",
    "ConnectorStatsSummary",
    "ConnectorSummary",
    "DailyCost",
    "derive_liveness",
    "Episode",
    "Fact",
    "MemoryActivity",
    "MemoryStats",
    "MemoryRule",
    "ErrorDetail",
    "ErrorResponse",
    "ExpireStaleActionsResponse",
    "HealthResponse",
    "Issue",
    "ModuleInfo",
    "ModuleRuntimeStateResponse",
    "ModuleSetEnabledRequest",
    "ModuleStatus",
    "NotificationStats",
    "NotificationSummary",
    "PaginatedResponse",
    "PaginationMeta",
    "RuleConstraintSuggestion",
    "Schedule",
    "ScheduleCost",
    "SearchResponse",
    "SearchResult",
    "ScheduleCreate",
    "ScheduleEntry",
    "ScheduleUpdate",
    "SessionDetail",
    "SessionSummary",
    "SkillInfo",
    "SpanNode",
    "StateEntry",
    "StateSetRequest",
    "TraceDetail",
    "TraceSummary",
    "TickResponse",
    "TimelineEvent",
    "TimelineResponse",
    "TopSession",
    "TriggerRequest",
    "TriggerResponse",
]
