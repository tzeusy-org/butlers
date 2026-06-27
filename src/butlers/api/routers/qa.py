"""Dashboard API routes for QA Staffer visibility.

Provides:

- ``router`` — QA routes at ``/api/qa``

Endpoints:
- GET  /api/qa/summary                              — staffer status, last/next patrol, stats
- GET  /api/qa/patrols                              — paginated patrol list
- GET  /api/qa/patrols/{patrolId}                   — full patrol with nested findings
- GET  /api/qa/patrols/{patrolId}/findings          — findings for a patrol
- GET  /api/qa/findings/by-attempt/{attemptId}      — finding that dispatched an attempt
- GET  /api/qa/cases                                — paginated QA case summaries
- GET  /api/qa/investigations                       — paginated QA-originated healing attempts
                                                      (with current_phase and workflow_deadline_at)
- GET  /api/qa/meta-review                          — QA-self-recursive findings (operator lane)
- GET  /api/qa/known-issues                         — known issue tracker (by fingerprint)
- POST /api/qa/known-issues/{fingerprint}/dismiss   — dismiss a known issue
- DELETE /api/qa/known-issues/{fingerprint}/dismiss — un-dismiss a known issue
- POST /api/qa/force-patrol                         — trigger immediate patrol
- POST /api/qa/dev/synthetic-findings               — queue a synthetic finding for next patrol
- GET  /api/qa/trends                               — daily aggregated stats
- GET  /api/qa/dismissals                           — list active dismissals
- DELETE /api/qa/dismissals/{fingerprint}           — remove a dismissal
- GET  /api/qa/settings/allowed-repos               — list allowed repositories for PR creation
- POST /api/qa/settings/allowed-repos               — add a repository to the whitelist
- PATCH /api/qa/settings/allowed-repos/{owner}/{repo} — toggle enabled flag
- DELETE /api/qa/settings/allowed-repos/{owner}/{repo} — remove a repository from the whitelist
- GET  /api/qa/circuit-breaker                        — circuit breaker status
- POST /api/qa/circuit-breaker/reset                  — reset tripped circuit breaker
- GET  /api/qa/settings/repo                          — repository configuration
- PUT  /api/qa/settings/repo                          — update repository URL
- POST /api/qa/settings/repo/sync                     — trigger immediate repo sync

All reads/writes query ``public.qa_patrols``, ``public.qa_findings``,
``public.qa_dismissals``, ``public.healing_attempts``, and
``public.qa_allowed_repositories`` via the shared credential pool.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    MCPClientManager,
    get_mcp_manager,
)
from butlers.api.models import (
    ApiMeta,
    ApiResponse,
    ErrorDetail,
    ErrorResponse,
    PaginatedResponse,
    PaginationMeta,
)
from butlers.config import ConfigError, load_config
from butlers.core.healing.dispatch import CIRCUIT_BREAKER_FAILURE_STATUSES
from butlers.core.healing.fingerprint import compute_fingerprint_from_report
from butlers.core.qa.github_pr import GithubPrClient, get_pr_client, parse_pr_url
from butlers.core.qa.models import QaFinding
from butlers.core.qa.notes import InvestigationNotes
from butlers.core.qa.repo_whitelist import parse_repo_url
from butlers.core.qa.severity import (
    escalated_open_cases_sql,
    headline_for_case,
    map_severity,
    short_id_from_uuid,
    state_of_case,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/qa", tags=["qa"])

# Default roster location — mirrors butlers.api.routers.butlers._DEFAULT_ROSTER_DIR
_DEFAULT_ROSTER_DIR = Path(__file__).resolve().parents[4] / "roster"
# Name of the QA staffer butler in the roster
_QA_BUTLER_NAME = "qa"

_SYNTHETIC_FINDINGS_ENV = "QA_ALLOW_SYNTHETIC_FINDINGS"
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
_SEVERITY_INT_TO_HINT: dict[int, str] = {
    0: "critical",
    1: "high",
    2: "medium",
    3: "low",
    4: "info",
}
_QA_CASE_SINCE_DELTAS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
_QA_CASE_SEVERITY_SQL: dict[str, str] = {
    "high": "COALESCE(f.severity, a.severity) IN (0, 1)",
    "medium": "COALESCE(f.severity, a.severity) = 2",
    "low": "COALESCE(f.severity, a.severity) IN (3, 4)",
}
_QA_CASE_HUMAN_ACTION_SQL = (
    "a.error_detail ILIKE '%human action%' "
    "OR a.error_detail ILIKE '%operator%' "
    "OR a.error_detail ILIKE '%escalat%'"
)
_QA_CASE_STATE_SQL: dict[str, str] = {
    "detect": (
        "a.status NOT IN ('pr_merged', 'unfixable', 'pr_open', 'investigating') "
        f"AND NOT (a.status = 'failed' AND ({_QA_CASE_HUMAN_ACTION_SQL}))"
    ),
    "diagnose": "a.status = 'investigating'",
    "pr": "a.status = 'pr_open'",
    "landed": "a.status = 'pr_merged'",
    "escalated": (
        f"a.status = 'unfixable' OR (a.status = 'failed' AND ({_QA_CASE_HUMAN_ACTION_SQL}))"
    ),
}


# ---------------------------------------------------------------------------
# Dependency stubs
# ---------------------------------------------------------------------------


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _get_force_patrol_fn():
    """Dependency stub for the force-patrol callable.

    Override via ``app.dependency_overrides[_get_force_patrol_fn]`` to inject
    the QA module's ``_handle_force_patrol`` coroutine when the daemon is
    available in-process.  Returns None by default (standalone API mode).
    """
    return None


def _get_credentials_status_fn():
    """Dependency stub for the credentials-status callable.

    Override via ``app.dependency_overrides[_get_credentials_status_fn]`` to
    inject a callable that returns a ``dict`` describing the QA credential
    status (e.g., whether ``BUTLERS_QA_GH_TOKEN`` is set).

    The callable must be an async function with no required arguments and
    must return a dict with at least ``{"gh_token_present": bool}``.

    Returns ``None`` by default (standalone API mode — status is unknown).
    """
    return None


def _get_repo_sync_fn():
    """Dependency stub for the managed repo clone sync callable.

    Override via ``app.dependency_overrides[_get_repo_sync_fn]`` to inject
    a callable that triggers ``ManagedRepoClone.refresh()``.
    Returns None by default (standalone API mode).
    """
    return None


def _get_staffer_info_fn():
    """Dependency stub for the QA staffer static config info callable.

    Override via ``app.dependency_overrides[_get_staffer_info_fn]`` to inject
    a callable that returns a ``dict`` with keys:
    - ``port`` (int): the QA daemon's listen port
    - ``model`` (str | None): the effective model name in use
    - ``patrol_interval_minutes`` (int): patrol cadence from the QA module config

    The callable must be an async function with no required arguments, as it is
    awaited inside ``get_qa_summary``.

    Returns ``None`` by default (standalone API mode — values fall back to
    the roster config file and a DB catalog query).
    """
    return None


def _shared_pool(db: DatabaseManager):
    """Return the shared credential pool, raising 503 if unavailable."""
    try:
        return db.credential_shared_pool()
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Shared database pool is not available",
        )


async def _resolve_github_token(db: DatabaseManager) -> str | None:
    """Resolve a GitHub token for live PR metadata, or ``None``.

    Resolution order (first hit wins):

    1. CredentialStore ``BUTLERS_QA_GH_TOKEN`` (the QA subsystem's canonical
       token, used for PR creation).
    2. Conventional ``GITHUB_TOKEN`` / ``GH_TOKEN`` from the environment
       (ops provisions either via the daemon env).

    Returns ``None`` when no token can be found — the PR panel then degrades
    gracefully to the honest "unavailable" state.
    """
    try:
        from butlers.core.qa.dispatch import QA_GH_TOKEN_KEY
        from butlers.credential_store import CredentialStore

        pool = db.credential_shared_pool()
        token = await CredentialStore(pool).resolve(QA_GH_TOKEN_KEY)
        if token:
            return token
    except Exception:
        # CredentialStore unavailable / pool missing — fall through to env.
        logger.debug("CredentialStore GitHub token resolution failed; trying env", exc_info=True)

    for env_key in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(env_key, "").strip()
        if value:
            return value
    return None


async def _resolve_credentials_status(db: DatabaseManager) -> dict[str, bool]:
    """Resolve QA credential *presence* booleans from the CredentialStore.

    Reads the shared ``butler_secrets`` store for the canonical QA credential
    keys and reports **only whether each is present** — secret values are never
    loaded or returned.  This is the production implementation behind the
    ``_get_credentials_status_fn`` dependency, wired by the daemon/app at
    startup (see ``butlers.api.deps.wire_db_dependencies``).

    Raises if the shared pool is unavailable; the caller (``get_qa_summary``)
    swallows such failures and reports the status as "unknown" (all ``None``).
    """
    from butlers.core.qa.dispatch import (
        QA_GH_TOKEN_KEY,
        QA_GIT_AUTHOR_EMAIL_KEY,
        QA_GIT_AUTHOR_NAME_KEY,
    )
    from butlers.credential_store import CredentialStore

    store = CredentialStore(db.credential_shared_pool())
    return {
        "gh_token_present": await store.has(QA_GH_TOKEN_KEY),
        "git_author_name_present": await store.has(QA_GIT_AUTHOR_NAME_KEY),
        "git_author_email_present": await store.has(QA_GIT_AUTHOR_EMAIL_KEY),
    }


def make_credentials_status_fn(get_db: Callable[[], DatabaseManager]):
    """Build the production ``_get_credentials_status_fn`` dependency provider.

    The returned zero-arg provider is suitable for
    ``app.dependency_overrides[_get_credentials_status_fn]``.  It yields an
    async callable that resolves the real credential-presence booleans from the
    CredentialStore at request time (so it always reflects the live pool).
    """

    def _provider():
        async def _fn() -> dict[str, bool]:
            return await _resolve_credentials_status(get_db())

        return _fn

    return _provider


async def _row_to_pr_summary_live(
    row: Any,
    *,
    token: str | None,
    client: GithubPrClient,
) -> QaPrSummary | None:
    """Build the dossier PR summary, enriched with live GitHub metadata.

    Falls back to the base (honest "unavailable") summary when there is no PR,
    no token, or GitHub is unreachable — the dossier never breaks on a GitHub
    failure.
    """
    summary = _row_to_pr_summary(row)
    if summary is None:
        return None

    parsed = parse_pr_url(summary.url)
    if parsed is None:
        return summary

    owner, repo, number = parsed
    meta = await client.fetch(owner, repo, number, token=token)
    return summary.model_copy(
        update={
            "ci_status": meta.ci_status,
            "additions": meta.additions,
            "deletions": meta.deletions,
        }
    )


def _synthetic_findings_enabled() -> bool:
    """Return True when synthetic QA finding injection is explicitly enabled."""
    raw = os.environ.get(_SYNTHETIC_FINDINGS_ENV, "").strip().lower()
    return raw in _TRUTHY_ENV_VALUES


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class QaPatrolSummary(BaseModel):
    """Lightweight patrol record for list views."""

    id: uuid.UUID
    started_at: datetime
    completed_at: datetime | None = None
    status: str
    findings_count: int
    novel_count: int
    dispatched_count: int
    log_lookback_minutes: int
    sources_polled: list[str] = Field(default_factory=list)
    error_detail: str | None = None


class QaFindingRecord(BaseModel):
    """A single QA finding record from a patrol."""

    id: uuid.UUID
    patrol_id: uuid.UUID
    fingerprint: str
    source_type: str
    source_butler: str
    severity: int
    exception_type: str
    event_summary: str
    call_site: str
    occurrence_count: int
    first_seen: datetime
    last_seen: datetime
    dedup_reason: str | None = None
    healing_attempt_id: uuid.UUID | None = None
    source_session_trigger_source: str | None = None
    structured_evidence: dict | None = None
    created_at: datetime


class QaPatrolDetail(QaPatrolSummary):
    """Full patrol record with nested findings."""

    findings: list[QaFindingRecord] = Field(default_factory=list)


class QaDismissal(BaseModel):
    """A dismissal record for a known issue fingerprint."""

    fingerprint: str
    dismissed_until: datetime
    dismissed_by: str
    created_at: datetime


class QaActiveDismissal(BaseModel):
    """Active dismissal data embedded in a QA case dossier."""

    fingerprint: str
    expires_at: datetime
    reason: str | None = None


class KnownIssue(BaseModel):
    """A known issue grouped by fingerprint with aggregated stats."""

    fingerprint: str
    source_butler: str
    source_type: str
    severity: int
    exception_type: str
    event_summary: str
    call_site: str
    occurrence_count: int
    first_seen: datetime
    last_seen: datetime
    patrol_count: int
    healing_attempt_id: uuid.UUID | None = None
    dismissal: QaDismissal | None = None


class QaStats24h(BaseModel):
    """Aggregate stats over the last 24 hours."""

    patrols_completed: int
    total_findings: int
    novel_findings: int
    dispatched_investigations: int
    prs_opened: int = 0


class QaAllTimeStats(BaseModel):
    """All-time aggregate stats."""

    total_patrols: int
    total_findings: int
    novel_findings: int
    dispatched_investigations: int
    prs_merged: int = 0
    prs_failed: int = 0
    success_rate: float = 0.0


class QaKpiBlock(BaseModel):
    """KPI strip metrics for the QA dossier dashboard."""

    prs_landed_24h: int
    mttr_24h_seconds: float | None = None
    self_resolved_7d_pct: float
    active_cases_now: int
    # Prior-period comparison values for delta sub-labels.
    # prs_landed_prior_24h: count in the 24h-48h window (prior 24h period).
    # mttr_prior_24h_seconds: average MTTR in the 24h-48h window; null when sample is empty.
    # self_resolved_prior_7d_pct: self-resolve rate in the 7d-14d window; null when sample is empty.
    prs_landed_prior_24h: int = 0
    mttr_prior_24h_seconds: float | None = None
    self_resolved_prior_7d_pct: float | None = None


class QaActiveBreakdown(BaseModel):
    """Small active-case status breakdown for the QA dossier dashboard."""

    awaiting_ci: int
    escalated_open_cases: int


class QaCaseSummary(BaseModel):
    """Summary row for the QA Cases API."""

    id: uuid.UUID
    short_id: str
    sev: Literal["high", "medium", "low"]
    butler: str
    headline: str | None = None
    detected: datetime
    age_seconds: int
    state: Literal["detect", "diagnose", "pr", "landed", "escalated"]
    pr_state: Literal["drafted", "open", "merged", "closed"] | None = None
    pr_url: str | None = None


class QaPrSummary(BaseModel):
    """Pull request summary embedded in a QA case dossier."""

    number: int
    state: Literal["drafted", "open", "merged", "closed"]
    title: str
    branch: str
    # CI status and diff stats are not tracked locally (no GitHub fetch path
    # exists yet — see bu-cnvg7.3). ``None`` means "unavailable", which the UI
    # renders honestly instead of asserting a "unknown" / "+0/-0" placeholder.
    ci_status: Literal["passing", "failing", "pending", "unknown"] | None = None
    additions: int | None = None
    deletions: int | None = None
    opened_at: datetime
    merged_at: datetime | None = None
    url: str


class QaJournalEvent(BaseModel):
    """A single chronological event in the QA case journal."""

    id: uuid.UUID
    ts: datetime
    step: Literal[
        "flagged",
        "sampled",
        "cross-checked",
        "considered",
        "concluded",
        "drafted",
        "wait",
        "merged",
        "tick",
        "escalated",
    ]
    text: str
    detail: str | None = None
    data: dict[str, Any]


class QaCaseDossier(BaseModel):
    """Full case payload for the QA dossier renderer."""

    case: QaCaseSummary
    state_track_stage: Literal["detect", "diagnose", "pr", "landed", "escalated"]
    fingerprint: str | None = None
    dismissal: QaActiveDismissal | None = None
    investigation_notes: InvestigationNotes | None = None
    pr: QaPrSummary | None = None
    journal: list[QaJournalEvent] = Field(default_factory=list)


class QaCircuitBreaker(BaseModel):
    """Circuit breaker state for QA investigations."""

    tripped: bool
    consecutive_failures: int


class QaCredentialsStatus(BaseModel):
    """Credential availability status for QA investigations.

    Populated when the daemon wires a ``credentials_status_fn`` into the
    ``_get_credentials_status_fn`` dependency.  When the daemon is not
    available (standalone API mode) all fields carry their ``None`` default
    so the dashboard can distinguish "unknown" from "missing".
    """

    gh_token_present: bool | None = None
    """Whether ``BUTLERS_QA_GH_TOKEN`` is set in the credential store.

    ``None`` means the status could not be determined (e.g., no daemon
    wired in).  ``True`` means the token is present; ``False`` means it
    is missing and operator action is required.
    """

    git_author_name_present: bool | None = None
    """Whether ``BUTLERS_QA_GIT_AUTHOR_NAME`` is set in the credential store.

    ``None`` means the status could not be determined; ``True``/``False``
    mirror ``gh_token_present`` semantics for the git commit identity, which is
    validated and surfaced independently from the GitHub token.
    """

    git_author_email_present: bool | None = None
    """Whether ``BUTLERS_QA_GIT_AUTHOR_EMAIL`` is set in the credential store."""

    provisioning_hint: str | None = None
    """Actionable instructions for provisioning the missing credential.

    Only populated when ``gh_token_present`` is ``False``.
    """


class QaSummary(BaseModel):
    """QA staffer status summary for the dashboard."""

    staffer_status: str = "unknown"
    last_patrol_at: datetime | None = None
    next_patrol_at: datetime | None = None
    last_patrol: QaPatrolSummary | None = None
    stats_24h: QaStats24h
    stats_all_time: QaAllTimeStats
    kpis: QaKpiBlock
    active_breakdown: QaActiveBreakdown
    active_sources: list[str] = Field(default_factory=list)
    circuit_breaker: QaCircuitBreaker = Field(
        default_factory=lambda: QaCircuitBreaker(tripped=False, consecutive_failures=0)
    )
    credentials_status: QaCredentialsStatus = Field(
        default_factory=QaCredentialsStatus,
        description="GH token and credential availability for QA investigations.",
    )
    port: int | None = Field(
        default=None,
        description="Daemon listen port of the QA staffer butler.",
    )
    model: str | None = Field(
        default=None,
        description="Effective LLM model name in use by the QA staffer.",
    )
    patrol_interval_minutes: int | None = Field(
        default=None,
        description="Patrol cadence in minutes from [modules.qa].patrol_interval_minutes.",
    )


class DismissRequest(BaseModel):
    """Request body for dismissing a known issue."""

    dismissed_until: datetime | None = None
    dismissed_by: str = "dashboard_user"


class QaInvestigation(BaseModel):
    """A QA-originated healing attempt with PR info."""

    id: uuid.UUID
    fingerprint: str
    butler_name: str
    status: str
    severity: int
    exception_type: str
    call_site: str
    sanitized_msg: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    healing_session_id: uuid.UUID | None = None
    qa_patrol_id: uuid.UUID | None = None
    current_phase: str | None = None
    workflow_deadline_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    error_detail: str | None = None
    # PR review conversation tracking fields (added in core_057/core_058)
    review_state: str | None = None
    last_review_check_at: datetime | None = None
    review_feedback_summary: str | None = None
    follow_up_count: int = 0
    # Per-cycle follow-up budgeting and outcome fields (added in core_068)
    follow_up_cycle_patrol_id: uuid.UUID | None = None
    follow_up_cycle_count: int = 0
    last_follow_up_status: str | None = None
    last_follow_up_session_id: uuid.UUID | None = None
    last_follow_up_error: str | None = None
    last_follow_up_at: datetime | None = None


class QaTrendsDay(BaseModel):
    """A single day's patrol aggregate for trend charts."""

    date: str  # ISO date string yyyy-mm-dd
    patrols_completed: int
    total_findings: int
    novel_findings: int
    dispatched_count: int
    success_rate: float  # fraction of patrols that were clean, 0.0–1.0


class AllowedRepo(BaseModel):
    """A single entry in the QA repository whitelist."""

    id: uuid.UUID
    owner: str
    repo: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AllowedRepoCreate(BaseModel):
    """Request body for adding a repository to the whitelist.

    Accepts either ``owner/repo`` format or a full GitHub HTTPS/SSH URL.
    The server normalises to lowercase ``owner`` and ``repo``.
    """

    owner_repo: str = Field(
        ...,
        description=(
            "Repository in 'owner/repo' format, or a full GitHub URL "
            "(HTTPS: https://github.com/owner/repo or SSH: git@github.com:owner/repo)."
        ),
    )
    enabled: bool = True


class AllowedRepoPatch(BaseModel):
    """Request body for toggling the enabled flag on a whitelisted repository."""

    enabled: bool


class QaSourceBreakdown(BaseModel):
    """Per-source finding count for breakdown charts."""

    source_type: str
    count: int


class QaTrends(BaseModel):
    """7-day trend data for the QA overview charts."""

    days: list[QaTrendsDay]
    source_breakdown: list[QaSourceBreakdown]


class ForcePatrolResponse(BaseModel):
    """Response from a force-patrol request.

    ``triggered`` reports whether a patrol cycle was actually kicked off — either
    in-process (when ``_get_force_patrol_fn`` is wired, e.g. embedded/daemon use)
    or, in the typical standalone dashboard deployment, by crossing the process
    boundary into the QA daemon via its ``force_patrol`` MCP tool.  It is
    ``False`` only when no patrol could be started (no in-process callable AND no
    reachable daemon, or the patrol was skipped — e.g. one is already running).

    ``accepted`` mirrors ``triggered`` for backward compatibility with existing
    dashboard clients.  Callers MUST surface ``triggered`` honestly instead of
    claiming a patrol ran when it did not (the latent no-op fixed in bu-lcbzw).
    """

    accepted: bool
    triggered: bool = False
    message: str


class SyntheticFindingCreate(BaseModel):
    """Operator-injected synthetic QA finding for dev/staging validation."""

    source_butler: str = Field(
        default="general",
        min_length=1,
        description="Butler name to attribute the validation finding to.",
    )
    severity: int = Field(
        default=2,
        ge=0,
        le=4,
        description="Severity hint (0=critical, 4=info). Canonical scoring may adjust it.",
    )
    exception_type: str = Field(
        default="SyntheticQaValidationError",
        min_length=1,
        description="Synthetic exception class name used for fingerprinting.",
    )
    event_summary: str = Field(
        default=(
            "Synthetic QA validation canary injected by operator; this is not a real product "
            "bug and should follow the UNFIXABLE protocol."
        ),
        min_length=1,
        description="Sanitized summary shown to the QA investigation agent.",
    )
    call_site: str = Field(
        default="qa.validation.synthetic",
        min_length=1,
        description="Synthetic call-site identifier used for fingerprinting.",
    )
    occurrence_count: int = Field(
        default=1,
        ge=1,
        le=100,
        description="Synthetic occurrence count to persist with the finding.",
    )
    trigger_source: str | None = Field(
        default="dashboard",
        description="Optional trigger_source provenance to store on the finding.",
    )


class SyntheticFindingResponse(BaseModel):
    """Response returned after queueing a synthetic QA finding."""

    accepted: bool
    patrol_id: uuid.UUID
    finding_id: uuid.UUID
    fingerprint: str
    message: str


class CircuitBreakerAttempt(BaseModel):
    """A recent healing attempt relevant to circuit breaker state."""

    id: str
    status: str
    closed_at: str


class CircuitBreakerStatus(BaseModel):
    """Current state of the QA dispatch circuit breaker."""

    tripped: bool
    threshold: int
    recent_statuses: list[str]
    recent_attempts: list[CircuitBreakerAttempt]


class CircuitBreakerResetResponse(BaseModel):
    """Response from resetting the circuit breaker."""

    reset: bool
    message: str


class QaRepoConfig(BaseModel):
    """Current QA repository configuration."""

    repo_url: str
    clone_path: str | None = None
    last_synced_at: datetime | None = None
    last_sync_error: str | None = None
    created_at: datetime
    updated_at: datetime


class QaRepoConfigUpdate(BaseModel):
    """Request body for updating the QA repository URL."""

    repo_url: str = Field(..., description="Git repository URL (HTTPS recommended)")


class QaRepoSyncResponse(BaseModel):
    """Response from a repo sync request."""

    synced: bool
    clone_path: str | None = None
    error: str | None = None


class QaGitAuthorUpdate(BaseModel):
    """Request body for updating the QA git author identity.

    Both fields are stored in the shared credential store as
    ``BUTLERS_QA_GIT_AUTHOR_NAME`` / ``BUTLERS_QA_GIT_AUTHOR_EMAIL`` and consumed
    at investigation-dispatch time to author QA-generated commits.
    """

    name: str = Field(..., min_length=1, description="Git author name (e.g. 'QA Staffer')")
    email: str = Field(..., min_length=3, description="Git author email")


class QaGitAuthorStatus(BaseModel):
    """Presence status of the QA git author identity after a write.

    Mirrors the ``credentials_status`` fields surfaced by ``GET /api/qa/summary``
    so the dashboard can update its status badges without an extra round-trip.
    """

    git_author_name_present: bool
    git_author_email_present: bool


class QaMetaReviewFinding(BaseModel):
    """A QA-self-recursive finding routed to the operator meta-review lane.

    These are findings where ``source_butler == "qa"`` and the originating
    session's ``trigger_source`` identifies a QA-owned investigation.  They
    are surfaced here rather than auto-investigated to prevent self-recursion
    spirals.  Operators review and triage them manually.
    """

    id: uuid.UUID
    patrol_id: uuid.UUID
    fingerprint: str
    source_type: str
    source_butler: str
    severity: int
    exception_type: str
    event_summary: str
    call_site: str
    occurrence_count: int
    first_seen: datetime
    last_seen: datetime
    source_session_trigger_source: str | None = None
    structured_evidence: dict | None = None
    dedup_reason: str | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Helper — row conversion
# ---------------------------------------------------------------------------


def _row_to_patrol_summary(row: Any) -> QaPatrolSummary:
    """Convert a qa_patrols asyncpg record to QaPatrolSummary."""
    sources = row["sources_polled"] or []
    return QaPatrolSummary(
        id=row["id"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        status=row["status"],
        findings_count=row["findings_count"],
        novel_count=row["novel_count"],
        dispatched_count=row["dispatched_count"],
        log_lookback_minutes=row["log_lookback_minutes"],
        sources_polled=list(sources),
        error_detail=row["error_detail"],
    )


def _row_to_finding(row: Any) -> QaFindingRecord:
    """Convert a qa_findings asyncpg record to QaFindingRecord."""
    healing_attempt_id: uuid.UUID | None = None
    raw_haid = row["healing_attempt_id"]
    if raw_haid is not None:
        try:
            healing_attempt_id = uuid.UUID(str(raw_haid))
        except (ValueError, AttributeError):
            pass

    # structured_evidence is a JSONB column.  asyncpg returns JSONB as a
    # Python string when no custom codec is registered; parse it if needed.
    raw_evidence = row.get("structured_evidence")
    structured_evidence: dict | None = None
    if isinstance(raw_evidence, dict):
        structured_evidence = raw_evidence
    elif isinstance(raw_evidence, str):
        try:
            parsed = json.loads(raw_evidence)
            if isinstance(parsed, dict):
                structured_evidence = parsed
        except (ValueError, TypeError):
            pass

    return QaFindingRecord(
        id=row["id"],
        patrol_id=row["patrol_id"],
        fingerprint=row["fingerprint"],
        source_type=row["source_type"],
        source_butler=row["source_butler"],
        severity=row["severity"],
        exception_type=row["exception_type"],
        event_summary=row["event_summary"],
        call_site=row["call_site"],
        occurrence_count=row["occurrence_count"],
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        dedup_reason=row.get("dedup_reason"),
        healing_attempt_id=healing_attempt_id,
        source_session_trigger_source=row.get("source_session_trigger_source"),
        structured_evidence=structured_evidence,
        created_at=row["created_at"],
    )


def _row_to_dismissal(row: Any) -> QaDismissal:
    """Convert a qa_dismissals asyncpg record to QaDismissal."""
    return QaDismissal(
        fingerprint=row["fingerprint"],
        dismissed_until=row["dismissed_until"],
        dismissed_by=row["dismissed_by"],
        created_at=row["created_at"],
    )


def _row_to_active_dismissal(row: Any) -> QaActiveDismissal:
    """Convert an active qa_dismissals lookup row to QaActiveDismissal."""
    return QaActiveDismissal(
        fingerprint=row["fingerprint"],
        expires_at=row["expires_at"],
        reason=row.get("reason"),
    )


def _row_to_investigation(row: Any) -> QaInvestigation:
    """Convert a healing_attempts asyncpg record to QaInvestigation."""
    healing_session_id: uuid.UUID | None = None
    raw_hid = row["healing_session_id"]
    if raw_hid is not None:
        try:
            healing_session_id = uuid.UUID(str(raw_hid))
        except (ValueError, AttributeError):
            pass

    qa_patrol_id: uuid.UUID | None = None
    raw_pid = row["qa_patrol_id"]
    if raw_pid is not None:
        try:
            qa_patrol_id = uuid.UUID(str(raw_pid))
        except (ValueError, AttributeError):
            pass

    return QaInvestigation(
        id=row["id"],
        fingerprint=row["fingerprint"],
        butler_name=row["butler_name"],
        status=row["status"],
        severity=row["severity"],
        exception_type=row["exception_type"],
        call_site=row["call_site"],
        sanitized_msg=row.get("sanitized_msg"),
        pr_url=row.get("pr_url"),
        pr_number=row.get("pr_number"),
        healing_session_id=healing_session_id,
        qa_patrol_id=qa_patrol_id,
        current_phase=row.get("current_phase"),
        workflow_deadline_at=row.get("workflow_deadline_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        closed_at=row.get("closed_at"),
        error_detail=row.get("error_detail"),
        review_state=row.get("review_state"),
        last_review_check_at=row.get("last_review_check_at"),
        review_feedback_summary=row.get("review_feedback_summary"),
        follow_up_count=row.get("follow_up_count") or 0,
        follow_up_cycle_patrol_id=row.get("follow_up_cycle_patrol_id"),
        follow_up_cycle_count=row.get("follow_up_cycle_count") or 0,
        last_follow_up_status=row.get("last_follow_up_status"),
        last_follow_up_session_id=row.get("last_follow_up_session_id"),
        last_follow_up_error=row.get("last_follow_up_error"),
        last_follow_up_at=row.get("last_follow_up_at"),
    )


def _jsonb_dict(value: Any) -> dict[str, Any] | None:
    """Normalize an asyncpg JSONB value into a dict when possible."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _finding_from_case_row(row: Any) -> QaFinding | None:
    """Build the linked finding helper model from a QA case query row."""
    if row.get("finding_id") is None:
        return None
    return QaFinding(
        fingerprint=row["finding_fingerprint"],
        source_type=row["finding_source_type"],
        source_butler=row["finding_source_butler"],
        severity=row["finding_severity"],
        exception_type=row["finding_exception_type"],
        event_summary=row["finding_event_summary"],
        call_site=row["finding_call_site"],
        occurrence_count=row["finding_occurrence_count"],
        first_seen=row["finding_first_seen"],
        last_seen=row["finding_last_seen"],
        timestamp=row["finding_last_seen"],
        source_session_trigger_source=row.get("finding_source_session_trigger_source"),
        structured_evidence=_jsonb_dict(row.get("finding_structured_evidence")),
    )


def _pr_state_for_case(row: Any) -> Literal["drafted", "open", "merged", "closed"] | None:
    """Map healing status into the compact case-list PR state."""
    status = row["status"]
    if status == "pr_merged":
        return "merged"
    if status == "pr_open":
        return "open"
    if status == "drafted":
        return "drafted"
    if row.get("pr_url") and status in {"failed", "timeout", "unfixable", "anonymization_failed"}:
        return "closed"
    return None


def _row_to_case_summary(row: Any) -> QaCaseSummary:
    """Convert a healing attempt plus latest linked finding into a case summary."""
    finding = _finding_from_case_row(row)
    detected = row["detected"]
    severity = row["case_severity"]
    return QaCaseSummary(
        id=row["id"],
        short_id=short_id_from_uuid(row["id"]),
        sev=map_severity(severity),
        butler=row["butler_name"],
        headline=headline_for_case(row, finding),
        detected=detected,
        age_seconds=max(0, int(row["age_seconds"] or 0)),
        state=state_of_case(row),
        pr_state=_pr_state_for_case(row),
        pr_url=row.get("pr_url"),
    )


def _case_not_found_response(case_id: uuid.UUID) -> JSONResponse:
    """Return the RFC 0007 error envelope for missing QA cases."""
    body = ErrorResponse(
        error=ErrorDetail(
            code="QA_CASE_NOT_FOUND",
            message=f"QA case not found: {case_id}",
        )
    )
    return JSONResponse(status_code=404, content=body.model_dump())


def _investigation_notes_from_case_row(row: Any) -> InvestigationNotes | None:
    """Extract the latest linked finding's investigation notes, when present."""
    structured_evidence = _jsonb_dict(row.get("finding_structured_evidence"))
    if not structured_evidence:
        return None

    notes = structured_evidence.get("investigation_notes")
    if isinstance(notes, dict):
        try:
            return InvestigationNotes.model_validate(notes)
        except ValidationError:
            return None
    return None


def _pr_number_from_url(url: str | None) -> int | None:
    if not url:
        return None
    try:
        return int(url.rstrip("/").rsplit("/", 1)[-1])
    except (TypeError, ValueError):
        return None


def _row_to_pr_summary(row: Any) -> QaPrSummary | None:
    """Build the dossier PR summary from persisted healing attempt fields."""
    pr_url = row.get("pr_url")
    pr_number = row.get("pr_number") or _pr_number_from_url(pr_url)
    branch = row.get("branch_name")
    pr_state = _pr_state_for_case(row)

    if not pr_url or pr_number is None or not branch or pr_state is None:
        return None

    return QaPrSummary(
        number=pr_number,
        state=pr_state,
        title=f"PR #{pr_number}",
        branch=branch,
        # Base summary carries no CI status / diff stat: the healing_attempts
        # row does not persist them. The dossier endpoint enriches these via a
        # live GitHub fetch (``_row_to_pr_summary_live``) when a token is
        # available; without one, None stays None — honest "unavailable".
        ci_status=None,
        additions=None,
        deletions=None,
        opened_at=row["created_at"],
        merged_at=row.get("closed_at") if pr_state == "merged" else None,
        url=pr_url,
    )


def _row_to_journal_event(row: Any) -> QaJournalEvent:
    return QaJournalEvent(
        id=row["id"],
        ts=row["ts"],
        step=row["step"],
        text=row["text"],
        detail=row.get("detail"),
        data=_jsonb_dict(row.get("data")) or {},
    )


# ---------------------------------------------------------------------------
# Circuit breaker constants — shared by /summary and /circuit-breaker endpoints.
# CIRCUIT_BREAKER_FAILURE_STATUSES is imported from butlers.core.healing.dispatch
# (the canonical definition). Threshold matches QaDispatchConfig default.
# ---------------------------------------------------------------------------

_CIRCUIT_BREAKER_THRESHOLD = 5
_QA_CIRCUIT_BREAKER_WHERE_CLAUSE = """
FROM public.healing_attempts
WHERE qa_patrol_id IS NOT NULL
  AND closed_at IS NOT NULL
  AND (
        healing_session_id IS NOT NULL
        OR status = 'manual_reset'
      )
"""


async def _fetch_recent_circuit_breaker_rows(
    pool: asyncpg.Pool,
    *,
    limit: int,
    include_ids: bool = False,
) -> list[asyncpg.Record]:
    """Fetch recent QA breaker rows using the same filter as dispatch admission control."""

    select_cols = "id, status, closed_at" if include_ids else "status"
    return await pool.fetch(
        f"""
        SELECT {select_cols}
        {_QA_CIRCUIT_BREAKER_WHERE_CLAUSE}
        ORDER BY closed_at DESC
        LIMIT $1
        """,
        limit,
    )


def _compute_circuit_breaker_state(
    rows: list[asyncpg.Record],
    *,
    threshold: int,
) -> tuple[list[str], int, bool]:
    """Return statuses, consecutive failure count, and tripped state."""

    statuses = [row["status"] for row in rows]
    consecutive_failures = 0
    for status in statuses:
        if status in CIRCUIT_BREAKER_FAILURE_STATUSES:
            consecutive_failures += 1
            continue
        break
    tripped = len(statuses) >= threshold and all(
        status in CIRCUIT_BREAKER_FAILURE_STATUSES for status in statuses
    )
    return statuses, consecutive_failures, tripped


# ---------------------------------------------------------------------------
# Staffer static info helpers — port, model, patrol_interval_minutes
# ---------------------------------------------------------------------------


def _read_staffer_info_from_toml(
    roster_dir: Path | None = None,
) -> tuple[int | None, int | None]:
    """Read port and patrol_interval_minutes from the QA butler's toml.

    Returns ``(port, patrol_interval_minutes)``.  Returns ``(None, None)`` on
    any config error so that summary construction is never blocked by a missing
    or malformed config file.
    """
    try:
        target = (roster_dir or _DEFAULT_ROSTER_DIR) / _QA_BUTLER_NAME
        config = load_config(target)
        patrol_interval = config.modules.get("qa", {}).get("patrol_interval_minutes")
        if isinstance(patrol_interval, int):
            return config.port, patrol_interval
        return config.port, None
    except (ConfigError, OSError, ValueError):
        logger.debug("_read_staffer_info_from_toml: could not read QA butler config", exc_info=True)
        return None, None


async def _fetch_model_from_catalog(pool: asyncpg.Pool) -> str | None:
    """Return the effective model alias QA would spawn for workhorse-complexity work.

    Mirrors the spawn-time resolution performed by
    ``butlers.core.model_routing._RESOLVE_SQL`` (read-only — no round-robin
    counter mutation), restricted to ``complexity_tier = 'workhorse'`` because
    that is the canonical tier QA uses for its investigations (formerly 'medium').

    Resolution rules:
    - A row in ``public.butler_model_overrides`` for ``butler_name='qa'`` may
      override any of ``enabled``, ``complexity_tier``, ``priority``; missing
      override columns fall back to the catalog row (``COALESCE``).
    - Only candidates with effective ``enabled = TRUE`` and effective
      ``complexity_tier = 'workhorse'`` are considered.
    - The candidate with the highest effective priority wins; ties broken
      deterministically by ``mc.created_at ASC, mc.id ASC`` (matching the
      spawn-time row ordering).
    - Returns ``None`` if no workhorse-tier candidate is enabled, or on query
      failure (debug-logged, non-fatal).
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT mc.alias
            FROM public.model_catalog mc
            LEFT JOIN public.butler_model_overrides bmo
                ON bmo.catalog_entry_id = mc.id
                AND bmo.butler_name = $1
            WHERE
                COALESCE(bmo.enabled, mc.enabled) = TRUE
                AND COALESCE(bmo.complexity_tier, mc.complexity_tier) = 'workhorse'
            ORDER BY COALESCE(bmo.priority, mc.priority) DESC,
                     mc.created_at ASC,
                     mc.id ASC
            LIMIT 1
            """,
            _QA_BUTLER_NAME,
        )
        return str(row["alias"]) if row is not None else None
    except Exception:
        logger.debug("_fetch_model_from_catalog: query failed (non-fatal)", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# GET /api/qa/summary
# ---------------------------------------------------------------------------


@router.get("/summary", response_model=ApiResponse[QaSummary])
async def get_qa_summary(
    db: DatabaseManager = Depends(_get_db_manager),
    credentials_status_fn=Depends(_get_credentials_status_fn),
    staffer_info_fn=Depends(_get_staffer_info_fn),
) -> ApiResponse[QaSummary]:
    """Return QA staffer summary: last patrol, 24h stats, all-time stats, active sources."""
    pool = _shared_pool(db)

    # Last completed patrol
    last_patrol_row = await pool.fetchrow(
        """
        SELECT id, started_at, completed_at, status, findings_count, novel_count,
               dispatched_count, log_lookback_minutes, sources_polled, error_detail
        FROM public.qa_patrols
        WHERE status != 'running'
        ORDER BY started_at DESC
        LIMIT 1
        """
    )
    last_patrol: QaPatrolSummary | None = None
    last_patrol_at: datetime | None = None
    if last_patrol_row is not None:
        last_patrol = _row_to_patrol_summary(last_patrol_row)
        last_patrol_at = last_patrol_row["started_at"]

    # 24h stats
    cutoff_24h = datetime.now(tz=UTC) - timedelta(hours=24)
    stats_24h_row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE status NOT IN ('running', 'error')) AS patrols_completed,
            COALESCE(SUM(findings_count), 0) AS total_findings,
            COALESCE(SUM(novel_count), 0) AS novel_findings,
            COALESCE(SUM(dispatched_count), 0) AS dispatched_investigations
        FROM public.qa_patrols
        WHERE started_at >= $1
        """,
        cutoff_24h,
    )

    # PRs opened in last 24h (QA-originated healing attempts that got a PR)
    prs_opened_24h = int(
        await pool.fetchval(
            """
            SELECT COUNT(*)
            FROM public.healing_attempts
            WHERE qa_patrol_id IS NOT NULL
              AND pr_url IS NOT NULL
              AND created_at >= $1
            """,
            cutoff_24h,
        )
        or 0
    )

    stats_24h = QaStats24h(
        patrols_completed=int(stats_24h_row["patrols_completed"] or 0),
        total_findings=int(stats_24h_row["total_findings"] or 0),
        novel_findings=int(stats_24h_row["novel_findings"] or 0),
        dispatched_investigations=int(stats_24h_row["dispatched_investigations"] or 0),
        prs_opened=prs_opened_24h,
    )

    cutoff_7d = datetime.now(tz=UTC) - timedelta(days=7)
    cutoff_48h = datetime.now(tz=UTC) - timedelta(hours=48)
    cutoff_14d = datetime.now(tz=UTC) - timedelta(days=14)
    kpis_row = await pool.fetchrow(
        """
        SELECT
            -- Current-period KPIs
            COUNT(*) FILTER (
                WHERE status = 'pr_merged'
                  AND closed_at >= $1
            ) AS prs_landed_24h,
            AVG(EXTRACT(EPOCH FROM (closed_at - created_at))) FILTER (
                WHERE closed_at >= $1
                  AND status IN ('pr_merged', 'failed', 'timeout', 'unfixable')
            ) AS mttr_24h_seconds,
            (
                100.0 * COUNT(*) FILTER (
                    WHERE status = 'pr_merged'
                      AND closed_at >= $2
                ) / NULLIF(
                    COUNT(*) FILTER (
                        WHERE status IN ('pr_merged', 'unfixable', 'failed')
                          AND closed_at >= $2
                    ),
                    0
                )
            ) AS self_resolved_7d_pct,
            COUNT(*) FILTER (
                WHERE status IN ('dispatch_pending', 'investigating', 'pr_open')
            ) AS active_cases_now,
            -- Prior-period KPIs (for delta sub-labels)
            COUNT(*) FILTER (
                WHERE status = 'pr_merged'
                  AND closed_at >= $3
                  AND closed_at < $1
            ) AS prs_landed_prior_24h,
            AVG(EXTRACT(EPOCH FROM (closed_at - created_at))) FILTER (
                WHERE closed_at >= $3
                  AND closed_at < $1
                  AND status IN ('pr_merged', 'failed', 'timeout', 'unfixable')
            ) AS mttr_prior_24h_seconds,
            (
                100.0 * COUNT(*) FILTER (
                    WHERE status = 'pr_merged'
                      AND closed_at >= $4
                      AND closed_at < $2
                ) / NULLIF(
                    COUNT(*) FILTER (
                        WHERE status IN ('pr_merged', 'unfixable', 'failed')
                          AND closed_at >= $4
                          AND closed_at < $2
                    ),
                    0
                )
            ) AS self_resolved_prior_7d_pct
        FROM public.healing_attempts
        WHERE qa_patrol_id IS NOT NULL
          AND (
              status IN ('dispatch_pending', 'investigating', 'pr_open')
              OR closed_at >= $4
          )
        """,
        cutoff_24h,
        cutoff_7d,
        cutoff_48h,
        cutoff_14d,
    )
    kpis = QaKpiBlock(
        prs_landed_24h=int(kpis_row["prs_landed_24h"] or 0) if kpis_row else 0,
        mttr_24h_seconds=(
            float(kpis_row["mttr_24h_seconds"])
            if kpis_row and kpis_row["mttr_24h_seconds"] is not None
            else None
        ),
        self_resolved_7d_pct=(
            round(float(kpis_row["self_resolved_7d_pct"]), 2)
            if kpis_row and kpis_row["self_resolved_7d_pct"] is not None
            else 0.0
        ),
        active_cases_now=int(kpis_row["active_cases_now"] or 0) if kpis_row else 0,
        prs_landed_prior_24h=int(kpis_row["prs_landed_prior_24h"] or 0) if kpis_row else 0,
        mttr_prior_24h_seconds=(
            float(kpis_row["mttr_prior_24h_seconds"])
            if kpis_row and kpis_row["mttr_prior_24h_seconds"] is not None
            else None
        ),
        self_resolved_prior_7d_pct=(
            round(float(kpis_row["self_resolved_prior_7d_pct"]), 2)
            if kpis_row and kpis_row["self_resolved_prior_7d_pct"] is not None
            else None
        ),
    )

    active_breakdown_row = await pool.fetchrow(
        f"""
        SELECT
            (
                SELECT COUNT(*)
                FROM public.healing_attempts
                WHERE qa_patrol_id IS NOT NULL
                  AND status = 'pr_open'
            ) AS awaiting_ci,
            ({escalated_open_cases_sql(qa_only=True)}) AS escalated_open_cases
        """
    )
    active_breakdown = QaActiveBreakdown(
        awaiting_ci=(int(active_breakdown_row["awaiting_ci"] or 0) if active_breakdown_row else 0),
        escalated_open_cases=(
            int(active_breakdown_row["escalated_open_cases"] or 0) if active_breakdown_row else 0
        ),
    )

    # All-time stats
    all_time_row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE status != 'running') AS total_patrols,
            COALESCE(SUM(findings_count), 0) AS total_findings,
            COALESCE(SUM(novel_count), 0) AS novel_findings,
            COALESCE(SUM(dispatched_count), 0) AS dispatched_investigations
        FROM public.qa_patrols
        """
    )

    # All-time PR stats for QA-originated attempts.
    # Uses the same failure statuses as CIRCUIT_BREAKER_FAILURE_STATUSES in
    # butlers.core.healing.dispatch: 'failed', 'timeout', 'anonymization_failed'.
    # 'unfixable' is intentionally excluded — it indicates "no fix is possible"
    # (a design decision), not a dispatch failure.
    pr_stats_row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE status = 'pr_merged') AS prs_merged,
            COUNT(*) FILTER (
                WHERE status IN ('failed', 'timeout', 'anonymization_failed')
            ) AS prs_failed,
            COUNT(*) FILTER (WHERE status != 'dispatch_pending') AS total_dispatched
        FROM public.healing_attempts
        WHERE qa_patrol_id IS NOT NULL
        """
    )
    prs_merged = int(pr_stats_row["prs_merged"] or 0) if pr_stats_row else 0
    prs_failed = int(pr_stats_row["prs_failed"] or 0) if pr_stats_row else 0
    total_dispatched = int(pr_stats_row["total_dispatched"] or 0) if pr_stats_row else 0
    success_rate = (prs_merged / total_dispatched) if total_dispatched > 0 else 0.0

    stats_all_time = QaAllTimeStats(
        total_patrols=int(all_time_row["total_patrols"] or 0),
        total_findings=int(all_time_row["total_findings"] or 0),
        novel_findings=int(all_time_row["novel_findings"] or 0),
        dispatched_investigations=int(all_time_row["dispatched_investigations"] or 0),
        prs_merged=prs_merged,
        prs_failed=prs_failed,
        success_rate=round(success_rate, 4),
    )

    # Circuit breaker — mirror the exact launched-attempt + manual_reset
    # sentinel filter used by the QA dispatch gate so dashboard reporting
    # matches actual dispatcher semantics.
    cb_rows = await _fetch_recent_circuit_breaker_rows(
        pool,
        limit=_CIRCUIT_BREAKER_THRESHOLD,
    )
    cb_statuses, consecutive_failures, cb_tripped = _compute_circuit_breaker_state(
        cb_rows,
        threshold=_CIRCUIT_BREAKER_THRESHOLD,
    )
    circuit_breaker = QaCircuitBreaker(
        tripped=cb_tripped,
        consecutive_failures=consecutive_failures,
    )

    # Active sources — derive from the most recent patrols (last 10)
    active_sources: list[str] = []
    sources_rows = await pool.fetch(
        """
        SELECT sources_polled
        FROM public.qa_patrols
        ORDER BY started_at DESC
        LIMIT 10
        """
    )
    seen: set[str] = set()
    for row in sources_rows:
        for src in row["sources_polled"] or []:
            if src not in seen:
                seen.add(src)
                active_sources.append(src)

    # Derive staffer_status — circuit breaker takes priority over unknown/error
    if cb_tripped:
        staffer_status = "circuit_breaker_tripped"
    elif last_patrol is None:
        staffer_status = "unknown"
    elif last_patrol.status == "error":
        staffer_status = "error"
    else:
        staffer_status = "healthy"

    # Credentials status — populated when daemon wires credentials_status_fn
    credentials_status = QaCredentialsStatus()
    if credentials_status_fn is not None:
        try:
            raw_creds = await credentials_status_fn()
            if isinstance(raw_creds, QaCredentialsStatus):
                parsed_creds = raw_creds
            elif isinstance(raw_creds, dict):
                parsed_creds = QaCredentialsStatus.model_validate(raw_creds)
            else:
                logger.warning(
                    "get_qa_summary: credentials_status_fn returned unsupported type %s; "
                    "treating credentials status as unknown",
                    type(raw_creds).__name__,
                )
                parsed_creds = None

            if parsed_creds is not None:
                provisioning_hint: str | None = None
                if parsed_creds.gh_token_present is False:
                    provisioning_hint = (
                        "BUTLERS_QA_GH_TOKEN is missing. "
                        "Provision via: butler secrets set BUTLERS_QA_GH_TOKEN <token> "
                        "(requires 'repo' scope)"
                    )
                credentials_status = QaCredentialsStatus(
                    gh_token_present=parsed_creds.gh_token_present,
                    git_author_name_present=parsed_creds.git_author_name_present,
                    git_author_email_present=parsed_creds.git_author_email_present,
                    provisioning_hint=provisioning_hint,
                )
        except Exception:
            logger.warning(
                "get_qa_summary: credentials_status_fn failed (non-fatal)", exc_info=True
            )

    # Staffer static config: port, model, patrol_interval_minutes
    staffer_port: int | None = None
    staffer_model: str | None = None
    staffer_patrol_interval: int | None = None

    if staffer_info_fn is not None:
        # Daemon-wired path: get all three fields from the injected callable.
        try:
            raw_info = await staffer_info_fn()
            if isinstance(raw_info, dict):
                staffer_port = raw_info.get("port")
                staffer_model = raw_info.get("model")
                staffer_patrol_interval = raw_info.get("patrol_interval_minutes")
        except Exception:
            logger.warning("get_qa_summary: staffer_info_fn failed (non-fatal)", exc_info=True)
    else:
        # Standalone API mode: read from butler.toml + model catalog DB query.
        staffer_port, staffer_patrol_interval = _read_staffer_info_from_toml()
        staffer_model = await _fetch_model_from_catalog(pool)

    summary = QaSummary(
        staffer_status=staffer_status,
        last_patrol_at=last_patrol_at,
        next_patrol_at=None,  # Requires scheduler integration; not available via DB
        last_patrol=last_patrol,
        stats_24h=stats_24h,
        stats_all_time=stats_all_time,
        kpis=kpis,
        active_breakdown=active_breakdown,
        active_sources=active_sources,
        circuit_breaker=circuit_breaker,
        credentials_status=credentials_status,
        port=staffer_port,
        model=staffer_model,
        patrol_interval_minutes=staffer_patrol_interval,
    )
    return ApiResponse(data=summary)


# ---------------------------------------------------------------------------
# GET /api/qa/patrols — paginated patrol list
# ---------------------------------------------------------------------------


@router.get("/patrols", response_model=PaginatedResponse[QaPatrolSummary])
async def list_patrols(
    status: str | None = Query(None, description="Filter by status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[QaPatrolSummary]:
    """List patrol cycles with optional status filter."""
    pool = _shared_pool(db)

    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    _VALID_PATROL_STATUSES = {
        "running",
        "clean",
        "findings_dispatched",
        "error",
        "skipped_overlap",
        "suppressed",
    }

    if status is not None:
        if status not in _VALID_PATROL_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Valid values: {sorted(_VALID_PATROL_STATUSES)}",
            )
        conditions.append(f"status = ${idx}")
        args.append(status)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = int(await pool.fetchval(f"SELECT COUNT(*) FROM public.qa_patrols{where}", *args) or 0)

    rows = await pool.fetch(
        f"SELECT id, started_at, completed_at, status, findings_count, novel_count,"
        f" dispatched_count, log_lookback_minutes, sources_polled, error_detail"
        f" FROM public.qa_patrols{where}"
        f" ORDER BY started_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [_row_to_patrol_summary(r) for r in rows]
    return PaginatedResponse(
        data=data, meta=PaginationMeta(total=total, offset=offset, limit=limit)
    )


# ---------------------------------------------------------------------------
# GET /api/qa/patrols/{patrolId} — full patrol with nested findings
# ---------------------------------------------------------------------------


@router.get("/patrols/{patrol_id}", response_model=ApiResponse[QaPatrolDetail])
async def get_patrol(
    patrol_id: uuid.UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[QaPatrolDetail]:
    """Return a single patrol with all nested findings."""
    pool = _shared_pool(db)

    patrol_row = await pool.fetchrow(
        """
        SELECT id, started_at, completed_at, status, findings_count, novel_count,
               dispatched_count, log_lookback_minutes, sources_polled, error_detail
        FROM public.qa_patrols
        WHERE id = $1
        """,
        patrol_id,
    )
    if patrol_row is None:
        raise HTTPException(status_code=404, detail=f"Patrol {patrol_id} not found")

    finding_rows = await pool.fetch(
        """
        SELECT id, patrol_id, fingerprint, source_type, source_butler, severity,
               exception_type, event_summary, call_site, occurrence_count,
               first_seen, last_seen, dedup_reason, healing_attempt_id,
               source_session_trigger_source, structured_evidence, created_at
        FROM public.qa_findings
        WHERE patrol_id = $1
        ORDER BY severity ASC, last_seen DESC
        """,
        patrol_id,
    )

    summary = _row_to_patrol_summary(patrol_row)
    findings = [_row_to_finding(r) for r in finding_rows]

    detail = QaPatrolDetail(
        id=summary.id,
        started_at=summary.started_at,
        completed_at=summary.completed_at,
        status=summary.status,
        findings_count=summary.findings_count,
        novel_count=summary.novel_count,
        dispatched_count=summary.dispatched_count,
        log_lookback_minutes=summary.log_lookback_minutes,
        sources_polled=summary.sources_polled,
        error_detail=summary.error_detail,
        findings=findings,
    )
    return ApiResponse(data=detail)


# ---------------------------------------------------------------------------
# GET /api/qa/patrols/{patrolId}/findings — findings for a patrol
# ---------------------------------------------------------------------------


@router.get("/patrols/{patrol_id}/findings", response_model=PaginatedResponse[QaFindingRecord])
async def list_patrol_findings(
    patrol_id: uuid.UUID,
    source_type: str | None = Query(None, description="Filter by source type"),
    dedup_reason: str | None = Query(None, description="Filter by dedup reason (null = novel)"),
    novel_only: bool = Query(False, description="Only return novel (non-deduplicated) findings"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[QaFindingRecord]:
    """List findings for a specific patrol with optional filters."""
    pool = _shared_pool(db)

    # Verify patrol exists
    exists = await pool.fetchval("SELECT 1 FROM public.qa_patrols WHERE id = $1", patrol_id)
    if not exists:
        raise HTTPException(status_code=404, detail=f"Patrol {patrol_id} not found")

    conditions: list[str] = ["patrol_id = $1"]
    args: list[Any] = [patrol_id]
    idx = 2

    if source_type is not None:
        conditions.append(f"source_type = ${idx}")
        args.append(source_type)
        idx += 1

    if novel_only:
        conditions.append("dedup_reason IS NULL")
    elif dedup_reason is not None:
        conditions.append(f"dedup_reason = ${idx}")
        args.append(dedup_reason)
        idx += 1

    where = " WHERE " + " AND ".join(conditions)

    total = int(await pool.fetchval(f"SELECT COUNT(*) FROM public.qa_findings{where}", *args) or 0)

    rows = await pool.fetch(
        f"SELECT id, patrol_id, fingerprint, source_type, source_butler, severity,"
        f" exception_type, event_summary, call_site, occurrence_count,"
        f" first_seen, last_seen, dedup_reason, healing_attempt_id,"
        f" source_session_trigger_source, structured_evidence, created_at"
        f" FROM public.qa_findings{where}"
        f" ORDER BY severity ASC, last_seen DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [_row_to_finding(r) for r in rows]
    return PaginatedResponse(
        data=data, meta=PaginationMeta(total=total, offset=offset, limit=limit)
    )


# ---------------------------------------------------------------------------
# GET /api/qa/findings/by-attempt/{attempt_id} — finding that dispatched an attempt
# ---------------------------------------------------------------------------


@router.get(
    "/findings/by-attempt/{attempt_id}",
    response_model=ApiResponse[QaFindingRecord],
)
async def get_finding_by_attempt(
    attempt_id: uuid.UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[QaFindingRecord]:
    """Return the QA finding that dispatched a given healing attempt.

    Resolves the dispatch reason for an investigation: which patrol observed it,
    how the finding was classified, any dedup history, and the structured
    evidence (session/request/trace identifiers) captured at discovery time.

    When multiple findings reference the same attempt (rejoin on rerun of a
    deduped fingerprint), the most recently created row is returned.

    Returns 404 when no finding links to ``attempt_id``; healing attempts
    created outside QA (retries, synthetic paths) won't have a linked finding.
    """
    pool = _shared_pool(db)

    row = await pool.fetchrow(
        """
        SELECT id, patrol_id, fingerprint, source_type, source_butler, severity,
               exception_type, event_summary, call_site, occurrence_count,
               first_seen, last_seen, dedup_reason, healing_attempt_id,
               source_session_trigger_source, structured_evidence, created_at
        FROM public.qa_findings
        WHERE healing_attempt_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        attempt_id,
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No QA finding is linked to healing attempt {attempt_id}",
        )

    return ApiResponse(data=_row_to_finding(row))


# ---------------------------------------------------------------------------
# GET /api/qa/cases — paginated QA case summaries
# ---------------------------------------------------------------------------


@router.get("/cases", response_model=PaginatedResponse[QaCaseSummary])
async def list_cases(
    sev: Literal["high", "medium", "low", "all"] = Query(
        "all",
        description="Filter by mapped case severity",
    ),
    state: Literal["detect", "diagnose", "pr", "landed", "escalated", "all"] = Query(
        "all",
        description="Filter by mapped QA case state",
    ),
    since: Literal["24h", "7d", "30d", "all"] = Query(
        "7d",
        description="Only include attempts created within this window",
    ),
    butler: list[str] | None = Query(
        None,
        description="Filter by one or more butler names",
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[QaCaseSummary]:
    """List QA case summaries from QA-originated healing attempts."""
    pool = _shared_pool(db)

    conditions: list[str] = ["a.qa_patrol_id IS NOT NULL"]
    args: list[Any] = []
    idx = 1

    if since != "all":
        conditions.append(f"a.created_at >= ${idx}")
        args.append(datetime.now(UTC) - _QA_CASE_SINCE_DELTAS[since])
        idx += 1

    if sev != "all":
        conditions.append(_QA_CASE_SEVERITY_SQL[sev])

    if state != "all":
        conditions.append(f"({_QA_CASE_STATE_SQL[state]})")

    butler_filters = [name.strip() for name in butler or [] if name.strip()]
    if butler_filters:
        conditions.append(f"a.butler_name = ANY(${idx}::text[])")
        args.append(butler_filters)
        idx += 1

    where = " WHERE " + " AND ".join(conditions)
    latest_finding_join = """
        LEFT JOIN LATERAL (
            SELECT id, fingerprint, source_type, source_butler, severity,
                   exception_type, event_summary, call_site, occurrence_count,
                   first_seen, last_seen, source_session_trigger_source,
                   structured_evidence, created_at,
                   MIN(first_seen) OVER () AS detected_at
            FROM public.qa_findings
            WHERE healing_attempt_id = a.id
            ORDER BY created_at DESC
            LIMIT 1
        ) f ON TRUE
    """
    count_join = latest_finding_join if sev != "all" else ""

    total = int(
        await pool.fetchval(
            f"""
            SELECT COUNT(*)
            FROM public.healing_attempts a
            {count_join}
            {where}
            """,
            *args,
        )
        or 0
    )

    rows = await pool.fetch(
        f"""
        SELECT
            a.id,
            a.butler_name,
            a.status,
            a.severity,
            a.exception_type,
            a.call_site,
            a.sanitized_msg,
            a.pr_url,
            a.created_at,
            a.error_detail,
            COALESCE(f.severity, a.severity) AS case_severity,
            COALESCE(f.detected_at, a.created_at) AS detected,
            EXTRACT(EPOCH FROM (now() - COALESCE(f.detected_at, a.created_at)))::int
                AS age_seconds,
            f.id AS finding_id,
            f.fingerprint AS finding_fingerprint,
            f.source_type AS finding_source_type,
            f.source_butler AS finding_source_butler,
            f.severity AS finding_severity,
            f.exception_type AS finding_exception_type,
            f.event_summary AS finding_event_summary,
            f.call_site AS finding_call_site,
            f.occurrence_count AS finding_occurrence_count,
            f.first_seen AS finding_first_seen,
            f.last_seen AS finding_last_seen,
            f.source_session_trigger_source AS finding_source_session_trigger_source,
            f.structured_evidence AS finding_structured_evidence
        FROM public.healing_attempts a
        {latest_finding_join}
        {where}
        ORDER BY a.created_at DESC
        OFFSET ${idx} LIMIT ${idx + 1}
        """,
        *args,
        offset,
        limit,
    )

    data = [_row_to_case_summary(r) for r in rows]
    return PaginatedResponse(
        data=data, meta=PaginationMeta(total=total, offset=offset, limit=limit)
    )


@router.get("/cases/{case_id}", response_model=ApiResponse[QaCaseDossier])
async def get_case(
    case_id: uuid.UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[QaCaseDossier] | JSONResponse:
    """Return the full dossier payload for one QA case."""
    pool = _shared_pool(db)

    row = await pool.fetchrow(
        """
        SELECT
            a.id,
            a.butler_name,
            a.status,
            a.severity,
            a.exception_type,
            a.call_site,
            a.sanitized_msg,
            a.branch_name,
            a.pr_url,
            a.pr_number,
            a.created_at,
            a.closed_at,
            a.error_detail,
            COALESCE(f.severity, a.severity) AS case_severity,
            COALESCE(f.detected_at, a.created_at) AS detected,
            EXTRACT(EPOCH FROM (now() - COALESCE(f.detected_at, a.created_at)))::int
                AS age_seconds,
            f.id AS finding_id,
            f.fingerprint AS finding_fingerprint,
            f.source_type AS finding_source_type,
            f.source_butler AS finding_source_butler,
            f.severity AS finding_severity,
            f.exception_type AS finding_exception_type,
            f.event_summary AS finding_event_summary,
            f.call_site AS finding_call_site,
            f.occurrence_count AS finding_occurrence_count,
            f.first_seen AS finding_first_seen,
            f.last_seen AS finding_last_seen,
            f.source_session_trigger_source AS finding_source_session_trigger_source,
            f.structured_evidence AS finding_structured_evidence
        FROM public.healing_attempts a
        LEFT JOIN LATERAL (
            SELECT id, fingerprint, source_type, source_butler, severity,
                   exception_type, event_summary, call_site, occurrence_count,
                   first_seen, last_seen, source_session_trigger_source,
                   structured_evidence, created_at,
                   MIN(first_seen) OVER () AS detected_at
            FROM public.qa_findings
            WHERE healing_attempt_id = a.id
            ORDER BY created_at DESC
            LIMIT 1
        ) f ON TRUE
        WHERE a.id = $1
        """,
        case_id,
    )
    if row is None:
        return _case_not_found_response(case_id)

    dismissal: QaActiveDismissal | None = None
    finding_fingerprint = row.get("finding_fingerprint")
    if finding_fingerprint:
        dismissal_row = await pool.fetchrow(
            """
            SELECT fingerprint, dismissed_until AS expires_at, NULL::text AS reason
            FROM public.qa_dismissals
            WHERE fingerprint = $1
              AND dismissed_until > now()
            ORDER BY dismissed_until DESC
            LIMIT 1
            """,
            finding_fingerprint,
        )
        if dismissal_row is not None:
            dismissal = _row_to_active_dismissal(dismissal_row)

    journal_rows = await pool.fetch(
        """
        SELECT id, ts, step, text, detail, data
        FROM (
            SELECT id, ts, step, text, detail, data
            FROM public.qa_investigation_events
            WHERE attempt_id = $1
            ORDER BY ts DESC
            LIMIT 50
        ) recent
        ORDER BY ts ASC
        """,
        case_id,
    )
    case = _row_to_case_summary(row)
    gh_token = await _resolve_github_token(db)
    pr_summary = await _row_to_pr_summary_live(row, token=gh_token, client=get_pr_client())
    dossier = QaCaseDossier(
        case=case,
        state_track_stage=case.state,
        fingerprint=row.get("finding_fingerprint"),
        dismissal=dismissal,
        investigation_notes=_investigation_notes_from_case_row(row),
        pr=pr_summary,
        journal=[_row_to_journal_event(journal_row) for journal_row in journal_rows],
    )
    return ApiResponse(data=dossier)


@router.get("/cases/{case_id}/journal", response_model=PaginatedResponse[QaJournalEvent])
async def get_case_journal(
    case_id: uuid.UUID,
    cursor: datetime | None = Query(
        None,
        description="ISO timestamp of the last event already seen",
    ),
    limit: int = Query(50, ge=1, le=500),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[QaJournalEvent] | JSONResponse:
    """Return a chronological page of journal events for one QA case."""
    pool = _shared_pool(db)

    exists = await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM public.healing_attempts WHERE id = $1)",
        case_id,
    )
    if not exists:
        return _case_not_found_response(case_id)

    conditions = ["attempt_id = $1"]
    args: list[Any] = [case_id]
    if cursor is not None:
        conditions.append("ts > $2")
        args.append(cursor)

    total = int(
        await pool.fetchval(
            f"""
            SELECT COUNT(*)
            FROM public.qa_investigation_events
            WHERE {" AND ".join(conditions)}
            """,
            *args,
        )
        or 0
    )
    rows = await pool.fetch(
        f"""
        SELECT id, ts, step, text, detail, data
        FROM public.qa_investigation_events
        WHERE {" AND ".join(conditions)}
        ORDER BY ts ASC
        LIMIT ${len(args) + 1}
        """,
        *args,
        limit,
    )
    return PaginatedResponse(
        data=[_row_to_journal_event(row) for row in rows],
        meta=PaginationMeta(total=total, offset=0, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/qa/investigations — paginated QA-originated healing attempts
# ---------------------------------------------------------------------------


_VALID_INVESTIGATION_STATUSES = {
    "investigating",
    "pr_open",
    "pr_merged",
    "failed",
    "timeout",
    "unfixable",
    "anonymization_failed",
}


@router.get("/investigations", response_model=PaginatedResponse[QaInvestigation])
async def list_investigations(
    status: str | None = Query(None, description="Filter by status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[QaInvestigation]:
    """List QA-originated healing attempts (those with non-null qa_patrol_id).

    Each record includes pr_url, pr_number, and current status.
    """
    pool = _shared_pool(db)

    conditions: list[str] = ["qa_patrol_id IS NOT NULL"]
    args: list[Any] = []
    idx = 1

    if status is not None:
        if status not in _VALID_INVESTIGATION_STATUSES:
            valid = sorted(_VALID_INVESTIGATION_STATUSES)
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Valid values: {valid}",
            )
        conditions.append(f"status = ${idx}")
        args.append(status)
        idx += 1

    where = " WHERE " + " AND ".join(conditions)

    total = int(
        await pool.fetchval(f"SELECT COUNT(*) FROM public.healing_attempts{where}", *args) or 0
    )

    rows = await pool.fetch(
        f"SELECT id, fingerprint, butler_name, status, severity, exception_type, call_site,"
        f" sanitized_msg, pr_url, pr_number, healing_session_id, qa_patrol_id,"
        f" current_phase, workflow_deadline_at,"
        f" created_at, updated_at, closed_at, error_detail,"
        f" review_state, last_review_check_at, review_feedback_summary, follow_up_count,"
        f" follow_up_cycle_patrol_id, follow_up_cycle_count,"
        f" last_follow_up_status, last_follow_up_session_id,"
        f" last_follow_up_error, last_follow_up_at"
        f" FROM public.healing_attempts{where}"
        f" ORDER BY created_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [_row_to_investigation(r) for r in rows]
    return PaginatedResponse(
        data=data, meta=PaginationMeta(total=total, offset=offset, limit=limit)
    )


# ---------------------------------------------------------------------------
# GET /api/qa/known-issues — known issue tracker grouped by fingerprint
# ---------------------------------------------------------------------------


@router.get("/known-issues", response_model=PaginatedResponse[KnownIssue])
async def list_known_issues(
    source_butler: str | None = Query(None, description="Filter by source butler"),
    severity: int | None = Query(None, ge=0, le=4, description="Filter by severity"),
    dismissed: bool | None = Query(
        None, description="Filter: True=dismissed only, False=active only"
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[KnownIssue]:
    """List known issues grouped by fingerprint with aggregated stats.

    Returns one row per unique fingerprint, showing the most recent occurrence
    details, total occurrence count across patrols, and any active dismissal.
    """
    pool = _shared_pool(db)

    now = datetime.now(tz=UTC)

    # Build WHERE conditions shared by both count and aggregation queries.
    # source_butler and severity are per-row column values (not aggregated),
    # so WHERE filters are correct and allow the same clause to be reused for
    # the count query.
    where_clauses: list[str] = []
    filter_args: list[Any] = []
    idx = 1

    if source_butler is not None:
        where_clauses.append(f"f.source_butler = ${idx}")
        filter_args.append(source_butler)
        idx += 1

    if severity is not None:
        where_clauses.append(f"f.severity = ${idx}")
        filter_args.append(severity)
        idx += 1

    # Dismissal filter is expressed as a condition fragment using a $N placeholder
    # for `now`.  Its placeholder index starts at `idx` (after filter_args).
    dismissed_condition = _build_dismissed_condition(dismissed, idx)
    dismissed_extra: list[Any] = [now] if dismissed is not None else []

    # Combine all WHERE conditions into a single clause used by both queries.
    all_where_clauses = where_clauses[:]
    if dismissed_condition:
        all_where_clauses.append(dismissed_condition)
    where_sql = ("WHERE " + " AND ".join(all_where_clauses)) if all_where_clauses else ""

    # All args: filter_args then optional now, then offset/limit for data query.
    base_args: list[Any] = filter_args + dismissed_extra
    pagination_idx = idx + len(dismissed_extra)

    # Count total distinct fingerprints (respecting all filters)
    count_sql = f"""
        SELECT COUNT(DISTINCT f.fingerprint)
        FROM public.qa_findings f
        LEFT JOIN public.qa_dismissals d ON d.fingerprint = f.fingerprint
        {where_sql}
    """
    total = int(await pool.fetchval(count_sql, *base_args) or 0)

    # Aggregate query: one row per fingerprint
    agg_sql = f"""
        SELECT
            f.fingerprint,
            MAX(f.source_butler) AS source_butler,
            MAX(f.source_type) AS source_type,
            MAX(f.severity) AS severity,
            MAX(f.exception_type) AS exception_type,
            MAX(f.event_summary) AS event_summary,
            MAX(f.call_site) AS call_site,
            SUM(f.occurrence_count) AS occurrence_count,
            MIN(f.first_seen) AS first_seen,
            MAX(f.last_seen) AS last_seen,
            COUNT(DISTINCT f.patrol_id) AS patrol_count,
            MAX(f.healing_attempt_id::text) AS healing_attempt_id
        FROM public.qa_findings f
        LEFT JOIN public.qa_dismissals d ON d.fingerprint = f.fingerprint
        {where_sql}
        GROUP BY f.fingerprint
        ORDER BY MAX(f.last_seen) DESC
        OFFSET ${pagination_idx} LIMIT ${pagination_idx + 1}
    """
    agg_args = base_args + [offset, limit]
    rows = await pool.fetch(agg_sql, *agg_args)

    if not rows:
        return PaginatedResponse(
            data=[], meta=PaginationMeta(total=total, offset=offset, limit=limit)
        )

    # Batch-fetch dismissals for returned fingerprints
    fingerprints = [r["fingerprint"] for r in rows]
    dismissal_rows = await pool.fetch(
        """
        SELECT fingerprint, dismissed_until, dismissed_by, created_at
        FROM public.qa_dismissals
        WHERE fingerprint = ANY($1::text[])
        """,
        fingerprints,
    )
    dismissal_map: dict[str, QaDismissal] = {
        r["fingerprint"]: _row_to_dismissal(r) for r in dismissal_rows
    }

    data: list[KnownIssue] = []
    for r in rows:
        fp = r["fingerprint"]
        healing_attempt_id: uuid.UUID | None = None
        raw_haid = r["healing_attempt_id"]
        if raw_haid:
            try:
                healing_attempt_id = uuid.UUID(str(raw_haid))
            except (ValueError, AttributeError):
                pass

        data.append(
            KnownIssue(
                fingerprint=fp,
                source_butler=r["source_butler"],
                source_type=r["source_type"],
                severity=int(r["severity"]),
                exception_type=r["exception_type"],
                event_summary=r["event_summary"],
                call_site=r["call_site"],
                occurrence_count=int(r["occurrence_count"]),
                first_seen=r["first_seen"],
                last_seen=r["last_seen"],
                patrol_count=int(r["patrol_count"]),
                healing_attempt_id=healing_attempt_id,
                dismissal=dismissal_map.get(fp),
            )
        )

    return PaginatedResponse(
        data=data, meta=PaginationMeta(total=total, offset=offset, limit=limit)
    )


def _build_dismissed_condition(dismissed: bool | None, next_idx: int) -> str:
    """Return a bare SQL condition fragment (no WHERE keyword) for dismissal filtering.

    Returns an empty string when no dismissal filter is requested.
    The caller is responsible for incorporating this into a WHERE clause.
    """
    if dismissed is True:
        return f"d.fingerprint IS NOT NULL AND d.dismissed_until > ${next_idx}"
    elif dismissed is False:
        return f"(d.fingerprint IS NULL OR d.dismissed_until <= ${next_idx})"
    return ""


# ---------------------------------------------------------------------------
# POST /api/qa/known-issues/{fingerprint}/dismiss
# ---------------------------------------------------------------------------


@router.post(
    "/known-issues/{fingerprint}/dismiss",
    response_model=ApiResponse[QaDismissal],
    status_code=200,
)
async def dismiss_known_issue(
    fingerprint: str,
    body: DismissRequest = Body(default_factory=DismissRequest),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[QaDismissal]:
    """Dismiss a known issue fingerprint to suppress future investigation dispatch.

    Creates or replaces the dismissal record for the given fingerprint.
    If ``dismissed_until`` is not specified, the dismissal never expires
    (set to a far-future timestamp: year 9999).
    """
    pool = _shared_pool(db)

    dismissed_until = body.dismissed_until
    if dismissed_until is None:
        # Indefinite dismissal: far future
        dismissed_until = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)

    dismissed_by = body.dismissed_by if body.dismissed_by not in (None, "") else "dashboard_user"

    row = await pool.fetchrow(
        """
        INSERT INTO public.qa_dismissals (fingerprint, dismissed_until, dismissed_by, created_at)
        VALUES ($1, $2, $3, now())
        ON CONFLICT (fingerprint) DO UPDATE
            SET dismissed_until = EXCLUDED.dismissed_until,
                dismissed_by    = EXCLUDED.dismissed_by
        RETURNING fingerprint, dismissed_until, dismissed_by, created_at
        """,
        fingerprint,
        dismissed_until,
        dismissed_by,
    )

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to create dismissal")

    return ApiResponse(data=_row_to_dismissal(row))


# ---------------------------------------------------------------------------
# DELETE /api/qa/known-issues/{fingerprint}/dismiss
# ---------------------------------------------------------------------------


@router.delete(
    "/known-issues/{fingerprint}/dismiss",
    response_model=ApiResponse[dict],
    status_code=200,
)
async def undismiss_known_issue(
    fingerprint: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Remove a dismissal for a known issue fingerprint.

    After removal, the fingerprint becomes eligible for investigation dispatch
    again on the next patrol cycle.
    """
    pool = _shared_pool(db)

    result = await pool.execute(
        "DELETE FROM public.qa_dismissals WHERE fingerprint = $1",
        fingerprint,
    )

    # asyncpg returns "DELETE N" as a string
    deleted_count = 0
    if isinstance(result, str) and result.startswith("DELETE "):
        try:
            deleted_count = int(result.split(" ", 1)[1])
        except (ValueError, IndexError):
            pass

    if deleted_count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No active dismissal found for fingerprint '{fingerprint}'",
        )

    return ApiResponse(
        data={"fingerprint": fingerprint, "deleted": True},
        meta=ApiMeta(),
    )


# ---------------------------------------------------------------------------
# GET /api/qa/trends — 7-day daily patrol stats + source breakdown
# ---------------------------------------------------------------------------


@router.get("/trends", response_model=ApiResponse[QaTrends])
async def get_qa_trends(
    days: int = Query(7, ge=1, le=30, description="Number of days to include"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[QaTrends]:
    """Return daily patrol aggregates and per-source finding counts for trend charts.

    ``days`` — entries for calendar days that had at least one patrol record
    within the window (no zero-filled days), ordered ascending by date.
    ``source_breakdown`` — total findings per source_type over the window.
    """
    pool = _shared_pool(db)

    # Daily patrol aggregates — use an explicit UTC timestamptz boundary so the
    # window calculation is not affected by the DB server's session TimeZone.
    daily_rows = await pool.fetch(
        """
        SELECT
            (started_at AT TIME ZONE 'UTC')::date::text AS date,
            COUNT(*) FILTER (WHERE status NOT IN ('running', 'error')) AS patrols_completed,
            COALESCE(SUM(findings_count), 0) AS total_findings,
            COALESCE(SUM(novel_count), 0) AS novel_findings,
            COALESCE(SUM(dispatched_count), 0) AS dispatched_count,
            COUNT(*) FILTER (WHERE status = 'clean') AS clean_count
        FROM public.qa_patrols
        WHERE started_at >= (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
              - ($1 - 1) * INTERVAL '1 day'
        GROUP BY (started_at AT TIME ZONE 'UTC')::date
        ORDER BY date ASC
        """,
        days,
    )

    trend_days: list[QaTrendsDay] = []
    for row in daily_rows:
        completed = int(row["patrols_completed"] or 0)
        clean = int(row["clean_count"] or 0)
        success_rate = (clean / completed) if completed > 0 else 0.0
        trend_days.append(
            QaTrendsDay(
                date=row["date"],
                patrols_completed=completed,
                total_findings=int(row["total_findings"] or 0),
                novel_findings=int(row["novel_findings"] or 0),
                dispatched_count=int(row["dispatched_count"] or 0),
                success_rate=round(success_rate, 4),
            )
        )

    # Source breakdown — aggregate findings over the same UTC-anchored window.
    source_rows = await pool.fetch(
        """
        SELECT source_type, SUM(occurrence_count) AS count
        FROM public.qa_findings f
        JOIN public.qa_patrols p ON p.id = f.patrol_id
        WHERE p.started_at >= (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
              - ($1 - 1) * INTERVAL '1 day'
        GROUP BY source_type
        ORDER BY count DESC
        """,
        days,
    )

    source_breakdown = [
        QaSourceBreakdown(source_type=row["source_type"], count=int(row["count"] or 0))
        for row in source_rows
    ]

    return ApiResponse(data=QaTrends(days=trend_days, source_breakdown=source_breakdown))


# ---------------------------------------------------------------------------
# Cross-process force-patrol via the QA daemon's force_patrol MCP tool
# ---------------------------------------------------------------------------

#: Name of the daemon-side QA MCP tool that triggers an immediate patrol cycle.
_FORCE_PATROL_TOOL = "force_patrol"

#: Timeout for the cross-process MCP call to the QA daemon.  A patrol can run a
#: full scan synchronously, so allow generous headroom.
_MCP_FORCE_PATROL_TIMEOUT_S = 120.0


async def _force_patrol_via_daemon(
    mcp_mgr: MCPClientManager,
) -> dict | None:
    """Invoke the QA daemon's ``force_patrol`` MCP tool and return its result dict.

    The dashboard API runs in a separate process from the butler daemon and has
    no QA module, so it cannot run a patrol directly.  This calls the
    daemon-side ``force_patrol`` tool (registered by the QA module) so the patrol
    actually runs in the daemon process where the patrol machinery lives.

    The tool is hosted by the QA staffer butler.  If that butler is unreachable
    (e.g. not running), we fall back to any other registered butler that exposes
    the tool — but in practice only the QA butler wires the QA module, so the
    fallback simply confirms no daemon can run the patrol.

    Returns
    -------
    dict | None
        The parsed patrol-result payload (``{"status": ..., ...}``) when a
        daemon ran the patrol; ``None`` when no daemon could be reached or the
        tool call failed on every candidate.
    """
    # Try the QA butler first, then any other registered butler.
    candidates = [_QA_BUTLER_NAME] + [n for n in mcp_mgr.butler_names if n != _QA_BUTLER_NAME]

    for candidate in candidates:
        try:
            client = await asyncio.wait_for(
                mcp_mgr.get_client(candidate),
                timeout=_MCP_FORCE_PATROL_TIMEOUT_S,
            )
        except Exception:
            # ButlerUnreachableError, TimeoutError, or any connection failure.
            logger.debug(
                "force-patrol: butler %s unreachable; trying next",
                candidate,
                exc_info=True,
            )
            continue

        try:
            mcp_result = await asyncio.wait_for(
                client.call_tool(_FORCE_PATROL_TOOL, {}),
                timeout=_MCP_FORCE_PATROL_TIMEOUT_S,
            )
        except Exception:
            # Tool not registered on this butler, or the call failed — try next.
            logger.debug(
                "force-patrol: force_patrol call failed on butler %s; trying next",
                candidate,
                exc_info=True,
            )
            await mcp_mgr.invalidate_client(candidate)
            continue

        # Parse the tool result payload (a JSON dict from _handle_force_patrol).
        if not mcp_result.is_error and mcp_result.content:
            for block in mcp_result.content:
                text = getattr(block, "text", None)
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(payload, dict):
                    logger.info(
                        "force-patrol: ran via butler %s force_patrol tool (status=%s)",
                        candidate,
                        payload.get("status"),
                    )
                    return payload
                break

        logger.warning(
            "force-patrol: butler %s force_patrol returned no usable result",
            candidate,
        )

    return None


def _force_patrol_message(result: dict) -> tuple[bool, str]:
    """Map a patrol-result dict into ``(triggered, message)``.

    A ``status`` of ``"skipped"`` means the patrol did not run (e.g. one is
    already in progress, or the module is disabled).
    """
    triggered = result.get("status") not in ("skipped",)
    if triggered:
        message = (
            f"Patrol triggered: {result.get('status', 'unknown')} "
            f"({result.get('findings_count', 0)} findings)"
        )
    else:
        message = f"Patrol skipped: {result.get('reason', 'unknown')}"
    return triggered, message


# ---------------------------------------------------------------------------
# POST /api/qa/force-patrol — request an immediate patrol cycle
# ---------------------------------------------------------------------------


@router.post("/force-patrol", response_model=ApiResponse[ForcePatrolResponse], status_code=202)
async def force_patrol(
    force_patrol_fn=Depends(_get_force_patrol_fn),
    mcp_mgr: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[ForcePatrolResponse]:
    """Request an immediate patrol cycle.

    When the QA module is available in-process (embedded/daemon mode, via a
    ``_get_force_patrol_fn`` override) the patrol runs synchronously in-process
    and the result is returned.

    In the typical standalone dashboard deployment the API runs in a separate
    process from the QA daemon and has no QA module, so it crosses the process
    boundary by invoking the daemon's ``force_patrol`` MCP tool via the
    ``MCPClientManager``.  That tool runs the patrol in the daemon process where
    the patrol machinery lives.  When a daemon runs the patrol the response
    reports ``triggered=True``; when no daemon can be reached (e.g. the QA butler
    is not running) it reports ``triggered=False`` with a truthful message.

    Override ``_get_force_patrol_fn`` via ``app.dependency_overrides`` to wire
    the live QA module callable in embedded deployments.
    """
    if force_patrol_fn is not None:
        try:
            result = await force_patrol_fn()
            triggered, message = _force_patrol_message(result)
            return ApiResponse(
                data=ForcePatrolResponse(accepted=triggered, triggered=triggered, message=message)
            )
        except Exception as exc:  # noqa: BLE001
            error_code = uuid.uuid4().hex
            logger.exception("force-patrol callable raised [error_code=%s]", error_code)
            raise HTTPException(
                status_code=503,
                detail=f"Force patrol failed [error_code={error_code}]",
            ) from exc

    # Standalone mode — no in-process callable wired.  Cross the process boundary
    # by invoking the QA daemon's ``force_patrol`` MCP tool, which runs the patrol
    # in the daemon process where the patrol machinery lives.
    result = await _force_patrol_via_daemon(mcp_mgr)
    if result is not None:
        triggered, message = _force_patrol_message(result)
        return ApiResponse(
            data=ForcePatrolResponse(accepted=triggered, triggered=triggered, message=message)
        )

    # The QA daemon could not be reached (e.g. butler not running) or rejected
    # the call.  Report triggered=False so the UI does not falsely claim a patrol
    # ran.
    logger.warning(
        "force-patrol: QA daemon unreachable via MCP; no patrol was triggered. "
        "Ensure the QA staffer butler daemon is running with the qa module enabled."
    )
    return ApiResponse(
        data=ForcePatrolResponse(
            accepted=False,
            triggered=False,
            message=("Force patrol unavailable — QA daemon unreachable, no patrol triggered."),
        )
    )


# ---------------------------------------------------------------------------
# POST /api/qa/dev/synthetic-findings — queue a synthetic finding for the next patrol
# ---------------------------------------------------------------------------


@router.post(
    "/dev/synthetic-findings",
    response_model=ApiResponse[SyntheticFindingResponse],
    status_code=202,
)
async def create_synthetic_finding(
    body: SyntheticFindingCreate = Body(default_factory=SyntheticFindingCreate),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[SyntheticFindingResponse]:
    """Queue a synthetic QA finding for the next scheduled patrol.

    This validation hook is intended for dev/staging. It persists a queued
    ``qa_findings`` row so the next patrol rehydrates it even when the dashboard
    API is running out-of-process from the QA daemon.
    """
    if not _synthetic_findings_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                "Synthetic QA finding injection is disabled. Set "
                f"{_SYNTHETIC_FINDINGS_ENV}=true to enable this operator-only hook."
            ),
        )

    pool = _shared_pool(db)
    now = datetime.now(tz=UTC)
    patrol_id = uuid.uuid4()
    patrol_error_detail = "Synthetic validation placeholder patrol created by dashboard API"

    fp_result = compute_fingerprint_from_report(
        error_type=body.exception_type,
        error_message=body.event_summary,
        call_site=body.call_site,
        traceback_str=None,
        severity_hint=_SEVERITY_INT_TO_HINT.get(body.severity),
    )
    structured_evidence = {
        "synthetic_validation": True,
        "injected_via": "dashboard_api",
        "expected_outcome": "Treat as validation canary and follow the UNFIXABLE protocol.",
    }

    await pool.execute(
        """
        INSERT INTO public.qa_patrols (
            id, completed_at, status, findings_count, novel_count, dispatched_count,
            log_lookback_minutes, sources_polled, error_detail
        )
        VALUES ($1, now(), 'suppressed', 0, 0, 0, 0, '{}', $2)
        """,
        patrol_id,
        patrol_error_detail,
    )

    row = await pool.fetchrow(
        """
        INSERT INTO public.qa_findings (
            patrol_id, fingerprint, source_type, source_butler,
            severity, exception_type, event_summary, call_site,
            occurrence_count, first_seen, last_seen, dedup_reason,
            source_session_trigger_source, structured_evidence, dispatch_queued
        )
        VALUES (
            $1, $2, 'butler_reports', $3,
            $4, $5, $6, $7,
            $8, $9, $10, NULL,
            $11, $12, TRUE
        )
        RETURNING id, fingerprint, patrol_id
        """,
        patrol_id,
        fp_result.fingerprint,
        body.source_butler.strip(),
        fp_result.severity,
        body.exception_type,
        body.event_summary,
        body.call_site,
        body.occurrence_count,
        now,
        now,
        body.trigger_source,
        structured_evidence,
    )
    if row is None:
        raise HTTPException(status_code=500, detail="Synthetic finding insert returned no row")

    return ApiResponse(
        data=SyntheticFindingResponse(
            accepted=True,
            patrol_id=row["patrol_id"],
            finding_id=row["id"],
            fingerprint=row["fingerprint"],
            message=(
                "Synthetic QA finding queued. The next scheduled patrol will rehydrate it "
                "and attempt dispatch."
            ),
        ),
        meta=ApiMeta(),
    )


# ---------------------------------------------------------------------------
# GET /api/qa/dismissals — list active dismissals
# ---------------------------------------------------------------------------


@router.get("/dismissals", response_model=PaginatedResponse[QaDismissal])
async def list_dismissals(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[QaDismissal]:
    """List all active dismissals (dismissed_until > now()).

    Active dismissals suppress QA investigation dispatch for matching
    fingerprints. Operators can use this endpoint to review and remove
    dismissals that are no longer needed.
    """
    pool = _shared_pool(db)

    now = datetime.now(tz=UTC)

    total = int(
        await pool.fetchval(
            "SELECT COUNT(*) FROM public.qa_dismissals WHERE dismissed_until > $1",
            now,
        )
        or 0
    )

    rows = await pool.fetch(
        """
        SELECT fingerprint, dismissed_until, dismissed_by, created_at
        FROM public.qa_dismissals
        WHERE dismissed_until > $1
        ORDER BY created_at DESC
        OFFSET $2 LIMIT $3
        """,
        now,
        offset,
        limit,
    )

    data = [_row_to_dismissal(r) for r in rows]
    return PaginatedResponse(
        data=data, meta=PaginationMeta(total=total, offset=offset, limit=limit)
    )


# ---------------------------------------------------------------------------
# DELETE /api/qa/dismissals/{fingerprint} — remove a dismissal
# ---------------------------------------------------------------------------


@router.delete("/dismissals/{fingerprint}", response_model=ApiResponse[dict], status_code=200)
async def delete_dismissal(
    fingerprint: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Remove a dismissal by fingerprint.

    After removal, the fingerprint becomes eligible for investigation dispatch
    again on the next patrol cycle.
    """
    pool = _shared_pool(db)

    result = await pool.execute(
        "DELETE FROM public.qa_dismissals WHERE fingerprint = $1",
        fingerprint,
    )

    deleted_count = 0
    if isinstance(result, str) and result.startswith("DELETE "):
        try:
            deleted_count = int(result.split(" ", 1)[1])
        except (ValueError, IndexError):
            pass

    if deleted_count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No dismissal found for fingerprint '{fingerprint}'",
        )

    return ApiResponse(
        data={"fingerprint": fingerprint, "deleted": True},
        meta=ApiMeta(),
    )


# ---------------------------------------------------------------------------
# Allowed repositories CRUD
# /api/qa/settings/allowed-repos
# ---------------------------------------------------------------------------


def _row_to_allowed_repo(row: Any) -> AllowedRepo:
    """Convert an asyncpg Record to an AllowedRepo model."""
    return AllowedRepo(
        id=row["id"],
        owner=row["owner"],
        repo=row["repo"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# GET /api/qa/settings/allowed-repos — list all whitelisted repos


@router.get(
    "/settings/allowed-repos",
    response_model=ApiResponse[list[AllowedRepo]],
)
async def list_allowed_repos(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[AllowedRepo]]:
    """Return all repositories in the QA PR creation whitelist, ordered by owner/repo.

    When the list is empty, ALL QA PR creation is blocked (fail-closed).
    """
    pool = _shared_pool(db)
    rows = await pool.fetch(
        """
        SELECT id, owner, repo, enabled, created_at, updated_at
        FROM public.qa_allowed_repositories
        ORDER BY owner ASC, repo ASC
        """
    )
    return ApiResponse[list[AllowedRepo]](data=[_row_to_allowed_repo(r) for r in rows])


# POST /api/qa/settings/allowed-repos — add a repo to the whitelist


@router.post(
    "/settings/allowed-repos",
    response_model=ApiResponse[AllowedRepo],
    status_code=201,
)
async def create_allowed_repo(
    body: AllowedRepoCreate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[AllowedRepo]:
    """Add a repository to the QA PR creation whitelist.

    The ``owner_repo`` field accepts either ``owner/repo`` format or a full
    GitHub HTTPS / SSH URL.  Values are stored in lowercase.

    Returns 409 if the repository is already present (regardless of
    ``enabled`` state).

    Returns 422 if ``owner_repo`` cannot be parsed into ``owner/repo``.
    """
    pool = _shared_pool(db)

    parsed = parse_repo_url(body.owner_repo)
    if parsed is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot parse '{body.owner_repo}' as a repository. "
                "Expected 'owner/repo', 'https://github.com/owner/repo', "
                "or 'git@github.com:owner/repo'."
            ),
        )
    owner, repo = parsed

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO public.qa_allowed_repositories (owner, repo, enabled)
            VALUES ($1, $2, $3)
            RETURNING id, owner, repo, enabled, created_at, updated_at
            """,
            owner,
            repo,
            body.enabled,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail=f"Repository '{owner}/{repo}' is already in the whitelist",
        )
    except Exception:
        logger.error("Failed to add allowed repo '%s/%s'", owner, repo, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to add repository to whitelist")

    if row is None:
        raise HTTPException(status_code=500, detail="Insert returned no row")

    return ApiResponse[AllowedRepo](data=_row_to_allowed_repo(row), meta=ApiMeta())


# PATCH /api/qa/settings/allowed-repos/{owner}/{repo} — toggle enabled flag


@router.patch(
    "/settings/allowed-repos/{owner}/{repo}",
    response_model=ApiResponse[AllowedRepo],
)
async def patch_allowed_repo(
    owner: str,
    repo: str,
    body: AllowedRepoPatch,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[AllowedRepo]:
    """Toggle the ``enabled`` flag for a whitelisted repository.

    Returns 404 if the ``owner/repo`` combination is not found.
    """
    pool = _shared_pool(db)

    row = await pool.fetchrow(
        """
        UPDATE public.qa_allowed_repositories
        SET enabled = $1, updated_at = now()
        WHERE owner = $2 AND repo = $3
        RETURNING id, owner, repo, enabled, created_at, updated_at
        """,
        body.enabled,
        owner.lower(),
        repo.lower(),
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Repository '{owner}/{repo}' not found in whitelist",
        )

    return ApiResponse[AllowedRepo](data=_row_to_allowed_repo(row), meta=ApiMeta())


# DELETE /api/qa/settings/allowed-repos/{owner}/{repo} — remove from whitelist


@router.delete(
    "/settings/allowed-repos/{owner}/{repo}",
    response_model=ApiResponse[dict],
    status_code=200,
)
async def delete_allowed_repo(
    owner: str,
    repo: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Remove a repository from the QA PR creation whitelist.

    Returns 404 if the ``owner/repo`` combination is not found.
    """
    pool = _shared_pool(db)

    result = await pool.execute(
        "DELETE FROM public.qa_allowed_repositories WHERE owner = $1 AND repo = $2",
        owner.lower(),
        repo.lower(),
    )

    deleted_count = 0
    if isinstance(result, str) and result.startswith("DELETE "):
        try:
            deleted_count = int(result.split(" ", 1)[1])
        except (ValueError, IndexError):
            pass

    if deleted_count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"Repository '{owner}/{repo}' not found in whitelist",
        )

    return ApiResponse(
        data={"owner": owner.lower(), "repo": repo.lower(), "deleted": True},
        meta=ApiMeta(),
    )


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


@router.get("/circuit-breaker", response_model=ApiResponse[CircuitBreakerStatus])
async def get_circuit_breaker_status(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CircuitBreakerStatus]:
    """Return the current QA dispatch circuit breaker state."""
    pool = _shared_pool(db)

    rows = await _fetch_recent_circuit_breaker_rows(
        pool,
        limit=_CIRCUIT_BREAKER_THRESHOLD,
        include_ids=True,
    )
    statuses, _, tripped = _compute_circuit_breaker_state(
        rows,
        threshold=_CIRCUIT_BREAKER_THRESHOLD,
    )

    return ApiResponse(
        data=CircuitBreakerStatus(
            tripped=tripped,
            threshold=_CIRCUIT_BREAKER_THRESHOLD,
            recent_statuses=statuses,
            recent_attempts=[
                CircuitBreakerAttempt(
                    id=str(row["id"]),
                    status=row["status"],
                    closed_at=row["closed_at"].isoformat(),
                )
                for row in rows
            ],
        ),
        meta=ApiMeta(),
    )


@router.post("/circuit-breaker/reset", response_model=ApiResponse[CircuitBreakerResetResponse])
async def reset_circuit_breaker(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CircuitBreakerResetResponse]:
    """Reset the QA circuit breaker by inserting a synthetic success record.

    This breaks the consecutive-failure chain so future dispatches can proceed.
    """
    pool = _shared_pool(db)

    # Verify it's actually tripped before resetting
    rows = await _fetch_recent_circuit_breaker_rows(
        pool,
        limit=_CIRCUIT_BREAKER_THRESHOLD,
    )
    _, _, tripped = _compute_circuit_breaker_state(
        rows,
        threshold=_CIRCUIT_BREAKER_THRESHOLD,
    )

    if not tripped:
        return ApiResponse(
            data=CircuitBreakerResetResponse(
                reset=False,
                message="Circuit breaker is not tripped — no reset needed.",
            ),
            meta=ApiMeta(),
        )

    # Insert a synthetic patrol + manual_reset healing attempt so that:
    # 1. The healing_attempts row has qa_patrol_id set (visible to CB queries)
    # 2. The status breaks the all-failures chain (manual_reset ∉ failure statuses)
    # 3. The FK constraint on qa_patrol_id → qa_patrols(id) is satisfied
    # Note: manual_reset is not in VALID_STATUSES so it bypasses the state machine
    # (direct INSERT, not via update_attempt_status). This is intentional — it's a
    # synthetic record that only exists to break the failure chain.
    synthetic_patrol_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.qa_patrols (id, status, completed_at)
        VALUES ($1, 'clean', now())
        """,
        synthetic_patrol_id,
    )
    await pool.execute(
        """
        INSERT INTO public.healing_attempts (
            fingerprint, butler_name, status, severity,
            exception_type, call_site, created_at, updated_at, closed_at,
            error_detail, qa_patrol_id
        ) VALUES (
            'circuit-breaker-reset-' || gen_random_uuid()::text,
            'dashboard',
            'manual_reset',
            4,
            'CircuitBreakerReset',
            'dashboard.circuit_breaker.reset',
            now(), now(), now(),
            'Manual reset via QA dashboard',
            $1
        )
        """,
        synthetic_patrol_id,
    )

    logger.info("QA circuit breaker reset via dashboard")

    return ApiResponse(
        data=CircuitBreakerResetResponse(
            reset=True,
            message="Circuit breaker reset. Future dispatches will proceed normally.",
        ),
        meta=ApiMeta(),
    )


# ---------------------------------------------------------------------------
# Repository configuration
# ---------------------------------------------------------------------------


@router.get("/settings/repo", response_model=ApiResponse[QaRepoConfig])
async def get_repo_config(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[QaRepoConfig]:
    """Return the current QA repository configuration."""
    pool = _shared_pool(db)
    row = await pool.fetchrow(
        "SELECT repo_url, clone_path, last_synced_at, last_sync_error, "
        "created_at, updated_at FROM public.qa_repo_config LIMIT 1"
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Repo config not initialized")

    return ApiResponse(
        data=QaRepoConfig(
            repo_url=row["repo_url"],
            clone_path=row["clone_path"],
            last_synced_at=row["last_synced_at"],
            last_sync_error=row["last_sync_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        ),
        meta=ApiMeta(),
    )


@router.put("/settings/repo", response_model=ApiResponse[QaRepoConfig])
async def update_repo_config(
    body: QaRepoConfigUpdate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[QaRepoConfig]:
    """Update the QA repository URL.

    The actual re-clone happens lazily on the next patrol cycle or manual sync.
    """
    pool = _shared_pool(db)
    row = await pool.fetchrow(
        """
        UPDATE public.qa_repo_config
        SET repo_url = $1, updated_at = now()
        RETURNING repo_url, clone_path, last_synced_at, last_sync_error,
                  created_at, updated_at
        """,
        body.repo_url.strip(),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Repo config not initialized")

    return ApiResponse(
        data=QaRepoConfig(
            repo_url=row["repo_url"],
            clone_path=row["clone_path"],
            last_synced_at=row["last_synced_at"],
            last_sync_error=row["last_sync_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        ),
        meta=ApiMeta(),
    )


@router.put("/settings/git-author", response_model=ApiResponse[QaGitAuthorStatus])
async def update_git_author(
    body: QaGitAuthorUpdate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[QaGitAuthorStatus]:
    """Store the QA git author identity in the shared credential store.

    Persists ``BUTLERS_QA_GIT_AUTHOR_NAME`` and ``BUTLERS_QA_GIT_AUTHOR_EMAIL``
    via the same ``CredentialStore`` / ``butler_secrets`` backend the rest of the
    QA subsystem reads from at dispatch time (see
    ``QaModule._resolve_git_identity`` and ``core.qa.dispatch``).  The values are
    commit metadata, not secrets, so they are stored with ``is_sensitive=False``.
    """
    from butlers.core.qa.dispatch import QA_GIT_AUTHOR_EMAIL_KEY, QA_GIT_AUTHOR_NAME_KEY
    from butlers.credential_store import CredentialStore

    name = body.name.strip()
    email = body.email.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Git author name must not be empty")
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(
            status_code=422, detail="Git author email must be a valid email address"
        )

    store = CredentialStore(_shared_pool(db))
    await store.store(
        QA_GIT_AUTHOR_NAME_KEY,
        name,
        category="qa",
        description="QA staffer git author name for investigation commits",
        is_sensitive=False,
    )
    await store.store(
        QA_GIT_AUTHOR_EMAIL_KEY,
        email,
        category="qa",
        description="QA staffer git author email for investigation commits",
        is_sensitive=False,
    )

    logger.info("QA git author identity updated via dashboard")

    return ApiResponse(
        data=QaGitAuthorStatus(git_author_name_present=True, git_author_email_present=True),
        meta=ApiMeta(),
    )


@router.post("/settings/repo/sync", response_model=ApiResponse[QaRepoSyncResponse])
async def sync_repo(
    repo_sync_fn=Depends(_get_repo_sync_fn),
) -> ApiResponse[QaRepoSyncResponse]:
    """Trigger an immediate repo sync (git fetch + reset to origin/main).

    Requires the QA daemon to be running in-process to wire the sync callable.
    """
    if repo_sync_fn is None:
        raise HTTPException(
            status_code=503,
            detail="Repo sync is only available when the QA daemon is running in-process.",
        )
    try:
        result_path = await repo_sync_fn()
        return ApiResponse(
            data=QaRepoSyncResponse(
                synced=True,
                clone_path=str(result_path) if result_path else None,
            ),
            meta=ApiMeta(),
        )
    except Exception as exc:
        error_code = uuid.uuid4().hex
        logger.exception("Repo sync failed [error_code=%s]", error_code)
        return ApiResponse(
            data=QaRepoSyncResponse(
                synced=False,
                error=f"Sync failed [error_code={error_code}]: {exc}",
            ),
            meta=ApiMeta(),
        )


# ---------------------------------------------------------------------------
# GET /api/qa/meta-review — QA-self-recursive findings operator lane
# ---------------------------------------------------------------------------

#: QA self-recursion trigger sources that gate normal investigation.
#: Findings from QA sessions with these trigger sources are routed here
#: instead of being auto-investigated to prevent self-recursion spirals.
#: Must match the dispatch barrier in butlers.core.qa.dispatch
#: (``trigger_src in {"healing", "qa"}``).
_QA_SELF_RECURSION_TRIGGER_SOURCES = frozenset({"healing", "qa"})


@router.get("/meta-review", response_model=PaginatedResponse[QaMetaReviewFinding])
async def list_meta_review_findings(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[QaMetaReviewFinding]:
    """Return QA-self-recursive findings routed to the operator meta-review lane.

    These are findings where ``source_butler == 'qa'`` and the originating
    session's ``trigger_source`` identifies a QA-owned investigation (i.e.
    ``source_session_trigger_source`` is in ``{"healing", "qa_investigation"}``
    or is NULL/unrecognized from a QA butler session).

    Operators review and triage these manually.  They are never auto-investigated
    to prevent self-recursion spirals.  The dispatch self-recursion barrier
    (Gate 0) routes these findings here at dispatch time rather than suppressing
    them silently, so operators have full visibility into QA failures that the
    QA staffer chose not to investigate itself.

    Results are ordered by ``last_seen DESC`` (most recent first).
    """
    pool = _shared_pool(db)

    # Meta-review findings are those from source_butler == 'qa' where the
    # trigger source identifies a QA investigation context.  Findings with
    # NULL source_session_trigger_source from a QA butler are also included as
    # a precaution (per spec: "treated as potentially recursive").
    meta_review_condition = (
        "f.source_butler = 'qa' "
        "AND ("
        "  f.source_session_trigger_source IS NULL "
        "  OR f.source_session_trigger_source = ANY($1::text[])"
        ")"
    )
    trigger_sources = list(_QA_SELF_RECURSION_TRIGGER_SOURCES)

    total = int(
        await pool.fetchval(
            f"SELECT COUNT(*) FROM public.qa_findings f WHERE {meta_review_condition}",
            trigger_sources,
        )
        or 0
    )

    rows = await pool.fetch(
        f"""
        SELECT f.id, f.patrol_id, f.fingerprint, f.source_type, f.source_butler,
               f.severity, f.exception_type, f.event_summary, f.call_site,
               f.occurrence_count, f.first_seen, f.last_seen,
               f.source_session_trigger_source, f.structured_evidence,
               f.dedup_reason, f.created_at
        FROM public.qa_findings f
        WHERE {meta_review_condition}
        ORDER BY f.last_seen DESC
        OFFSET $2 LIMIT $3
        """,
        trigger_sources,
        offset,
        limit,
    )

    data: list[QaMetaReviewFinding] = []
    for r in rows:
        raw_evidence = r.get("structured_evidence")
        structured_evidence: dict | None = None
        if isinstance(raw_evidence, dict):
            structured_evidence = raw_evidence
        elif isinstance(raw_evidence, str):
            try:
                parsed = json.loads(raw_evidence)
                if isinstance(parsed, dict):
                    structured_evidence = parsed
            except (ValueError, TypeError):
                pass

        data.append(
            QaMetaReviewFinding(
                id=r["id"],
                patrol_id=r["patrol_id"],
                fingerprint=r["fingerprint"],
                source_type=r["source_type"],
                source_butler=r["source_butler"],
                severity=r["severity"],
                exception_type=r["exception_type"],
                event_summary=r["event_summary"],
                call_site=r["call_site"],
                occurrence_count=r["occurrence_count"],
                first_seen=r["first_seen"],
                last_seen=r["last_seen"],
                source_session_trigger_source=r.get("source_session_trigger_source"),
                structured_evidence=structured_evidence,
                dedup_reason=r.get("dedup_reason"),
                created_at=r["created_at"],
            )
        )

    return PaginatedResponse(
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )
