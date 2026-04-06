"""Dashboard API routes for QA Staffer visibility.

Provides:

- ``router`` — QA routes at ``/api/qa``

Endpoints:
- GET  /api/qa/summary                              — staffer status, last/next patrol, stats
- GET  /api/qa/patrols                              — paginated patrol list
- GET  /api/qa/patrols/{patrolId}                   — full patrol with nested findings
- GET  /api/qa/patrols/{patrolId}/findings          — findings for a patrol
- GET  /api/qa/investigations                       — paginated QA-originated healing attempts
- GET  /api/qa/known-issues                         — known issue tracker (by fingerprint)
- POST /api/qa/known-issues/{fingerprint}/dismiss   — dismiss a known issue
- DELETE /api/qa/known-issues/{fingerprint}/dismiss — un-dismiss a known issue
- POST /api/qa/force-patrol                         — trigger immediate patrol
- GET  /api/qa/trends                               — daily aggregated stats
- GET  /api/qa/dismissals                           — list active dismissals
- DELETE /api/qa/dismissals/{fingerprint}           — remove a dismissal
- GET  /api/qa/settings/allowed-repos               — list allowed repositories for PR creation
- POST /api/qa/settings/allowed-repos               — add a repository to the whitelist
- PATCH /api/qa/settings/allowed-repos/{owner}/{repo} — toggle enabled flag
- DELETE /api/qa/settings/allowed-repos/{owner}/{repo} — remove a repository from the whitelist

All reads/writes query ``public.qa_patrols``, ``public.qa_findings``,
``public.qa_dismissals``, ``public.healing_attempts``, and
``public.qa_allowed_repositories`` via the shared credential pool.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiMeta, ApiResponse, PaginatedResponse, PaginationMeta
from butlers.core.qa.repo_whitelist import parse_repo_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/qa", tags=["qa"])


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


def _shared_pool(db: DatabaseManager):
    """Return the shared credential pool, raising 503 if unavailable."""
    try:
        return db.credential_shared_pool()
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Shared database pool is not available",
        )


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


class QaCircuitBreaker(BaseModel):
    """Circuit breaker state for QA investigations."""

    tripped: bool
    consecutive_failures: int


class QaSummary(BaseModel):
    """QA staffer status summary for the dashboard."""

    staffer_status: str = "unknown"
    last_patrol_at: datetime | None = None
    next_patrol_at: datetime | None = None
    last_patrol: QaPatrolSummary | None = None
    stats_24h: QaStats24h
    stats_all_time: QaAllTimeStats
    active_sources: list[str] = Field(default_factory=list)
    circuit_breaker: QaCircuitBreaker = Field(
        default_factory=lambda: QaCircuitBreaker(tripped=False, consecutive_failures=0)
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
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    error_detail: str | None = None


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
    """Response from a force-patrol request."""

    accepted: bool
    message: str


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
        dedup_reason=row["dedup_reason"],
        healing_attempt_id=healing_attempt_id,
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
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        closed_at=row.get("closed_at"),
        error_detail=row.get("error_detail"),
    )


# ---------------------------------------------------------------------------
# GET /api/qa/summary
# ---------------------------------------------------------------------------


@router.get("/summary", response_model=ApiResponse[QaSummary])
async def get_qa_summary(
    db: DatabaseManager = Depends(_get_db_manager),
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

    # Circuit breaker — count consecutive failures at tail of healing_attempts.
    # Uses the same status sets as CIRCUIT_BREAKER_FAILURE_STATUSES and
    # TERMINAL_STATUSES in butlers.core.healing.dispatch/tracking so dashboard
    # reporting matches actual dispatcher semantics:
    #   failure: 'failed', 'timeout', 'anonymization_failed'  (matches dispatch.py)
    #   success: 'pr_merged'
    #   excluded from circuit: 'unfixable' — indicates "no fix possible" by design
    cb_rows = await pool.fetch(
        """
        SELECT status
        FROM public.healing_attempts
        WHERE qa_patrol_id IS NOT NULL
          AND status IN ('pr_merged', 'failed', 'timeout', 'anonymization_failed')
        ORDER BY updated_at DESC
        LIMIT 20
        """
    )
    consecutive_failures = 0
    for cb_row in cb_rows:
        if cb_row["status"] == "pr_merged":
            break
        consecutive_failures += 1

    # Threshold of 5 consecutive failures triggers circuit breaker
    _CB_THRESHOLD = 5
    cb_tripped = consecutive_failures >= _CB_THRESHOLD
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

    summary = QaSummary(
        staffer_status=staffer_status,
        last_patrol_at=last_patrol_at,
        next_patrol_at=None,  # Requires scheduler integration; not available via DB
        last_patrol=last_patrol,
        stats_24h=stats_24h,
        stats_all_time=stats_all_time,
        active_sources=active_sources,
        circuit_breaker=circuit_breaker,
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
               first_seen, last_seen, dedup_reason, healing_attempt_id, created_at
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
        f" first_seen, last_seen, dedup_reason, healing_attempt_id, created_at"
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
# GET /api/qa/investigations — paginated QA-originated healing attempts
# ---------------------------------------------------------------------------


_VALID_INVESTIGATION_STATUSES = {
    "dispatch_pending",
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
        f" created_at, updated_at, closed_at, error_detail"
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
# POST /api/qa/force-patrol — request an immediate patrol cycle
# ---------------------------------------------------------------------------


@router.post("/force-patrol", response_model=ApiResponse[ForcePatrolResponse], status_code=202)
async def force_patrol(
    force_patrol_fn=Depends(_get_force_patrol_fn),
) -> ApiResponse[ForcePatrolResponse]:
    """Request an immediate patrol cycle.

    When the QA module is available in-process (daemon mode), the patrol runs
    synchronously and the result is returned.  In standalone API mode (no
    callable wired) the response is ``accepted=False`` with an informational
    message — no action is taken.

    Override ``_get_force_patrol_fn`` via ``app.dependency_overrides`` to wire
    the live QA module callable in daemon deployments.
    """
    if force_patrol_fn is not None:
        try:
            result = await force_patrol_fn()
            accepted = result.get("status") not in ("skipped",)
            message = (
                f"Patrol triggered: {result.get('status', 'unknown')} "
                f"({result.get('findings_count', 0)} findings)"
                if accepted
                else f"Patrol skipped: {result.get('reason', 'unknown')}"
            )
            return ApiResponse(data=ForcePatrolResponse(accepted=accepted, message=message))
        except Exception as exc:  # noqa: BLE001
            error_code = uuid.uuid4().hex
            logger.exception("force-patrol callable raised [error_code=%s]", error_code)
            raise HTTPException(
                status_code=503,
                detail=f"Force patrol failed [error_code={error_code}]",
            ) from exc

    # Standalone mode — no in-process callable wired; patrol cannot be triggered.
    # The dashboard affordance is still available but has no effect until the
    # QA module callable is injected via ``app.dependency_overrides``.
    return ApiResponse(
        data=ForcePatrolResponse(
            accepted=False,
            message="Force patrol is only supported when the QA daemon is running in-process.",
        )
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
