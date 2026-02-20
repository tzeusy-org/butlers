"""Issues aggregation endpoint.

Aggregates live reachability problems and grouped audit-log error history
into a single issues feed.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_db_manager,
    get_mcp_manager,
)
from butlers.api.models import ApiResponse, Issue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/issues", tags=["issues"])

_STATUS_TIMEOUT_S = 5.0
_ISSUE_TYPE_MAX_LEN = 80


def _get_db_manager_optional() -> DatabaseManager | None:
    """Return DatabaseManager when initialized; otherwise ``None``.

    The issues endpoint should remain available even when DB pools are not yet
    initialized (for example in unit tests or partial startup states).
    """
    try:
        return get_db_manager()
    except RuntimeError:
        return None


def _slug(value: str) -> str:
    """Build a short, deterministic slug suitable for issue type keys."""
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not normalized:
        return "unknown"
    return normalized[:_ISSUE_TYPE_MAX_LEN]


def _summarize_error(error: str | None) -> str | None:
    """Return a concise one-line error summary."""
    if not error:
        return None
    line = error.splitlines()[0].strip()
    if not line:
        return None
    if len(line) > 140:
        return f"{line[:137]}..."
    return line


def _issue_from_audit_group_row(row) -> Issue:
    """Map one grouped audit row into an issue entry."""
    error_message = str(row["error_summary"])
    butlers = [str(b) for b in (row["butlers"] or [])]
    if not butlers:
        butlers = ["unknown"]

    schedule_names = [str(name) for name in (row["schedule_names"] or [])]
    has_schedule = bool(row["has_schedule"])

    if has_schedule:
        severity = "critical"
        issue_type = (
            f"scheduled_task_failure:{_slug(schedule_names[0])}"
            if len(schedule_names) == 1
            else "scheduled_task_failure:multiple"
        )
        if len(schedule_names) == 1 and len(butlers) == 1:
            description = (
                f"Scheduled task '{schedule_names[0]}' failure on '{butlers[0]}': {error_message}"
            )
        elif len(schedule_names) == 1:
            description = (
                f"Scheduled task '{schedule_names[0]}' failures across "
                f"{len(butlers)} butlers: {error_message}"
            )
        elif len(butlers) == 1:
            description = f"Scheduled task failures on '{butlers[0]}': {error_message}"
        else:
            description = f"Scheduled task failures across {len(butlers)} butlers: {error_message}"
    else:
        severity = "warning"
        issue_type = f"audit_error_group:{_slug(error_message)}"
        if len(butlers) == 1:
            description = f"{error_message} ({butlers[0]})"
        else:
            description = f"{error_message} ({len(butlers)} butlers)"

    butler = butlers[0] if len(butlers) == 1 else "multiple"
    return Issue(
        severity=severity,
        type=issue_type,
        butler=butler,
        description=description,
        link="/audit-log",
        error_message=error_message,
        occurrences=int(row["occurrences"] or 1),
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        butlers=butlers,
    )


async def _list_audit_error_issues(db: DatabaseManager | None) -> list[Issue]:
    """Return grouped error issues derived from the audit log.

    Grouping key is normalized first-line error message. Each group exposes
    first/last timestamps and total occurrences.
    """
    if db is None:
        return []

    try:
        pool = db.pool("switchboard")
    except KeyError:
        return []

    try:
        rows = await pool.fetch(
            """
            WITH normalized_errors AS (
                SELECT
                    butler,
                    created_at,
                    COALESCE(
                        NULLIF(BTRIM(SPLIT_PART(error, E'\n', 1)), ''),
                        'Unknown error'
                    ) AS error_summary,
                    (
                        operation = 'session'
                        AND COALESCE(request_summary->>'trigger_source', '') LIKE 'schedule:%'
                    ) AS is_schedule,
                    NULLIF(
                        SPLIT_PART(COALESCE(request_summary->>'trigger_source', ''), ':', 2),
                        ''
                    ) AS schedule_name
                FROM dashboard_audit_log
                WHERE result = 'error'
            )
            SELECT
                error_summary,
                MIN(created_at) AS first_seen_at,
                MAX(created_at) AS last_seen_at,
                COUNT(*)::int AS occurrences,
                ARRAY_AGG(DISTINCT butler ORDER BY butler) AS butlers,
                BOOL_OR(is_schedule) AS has_schedule,
                ARRAY_REMOVE(
                    ARRAY_AGG(DISTINCT schedule_name ORDER BY schedule_name),
                    NULL
                ) AS schedule_names
            FROM normalized_errors
            GROUP BY error_summary
            ORDER BY last_seen_at DESC
            """
        )
    except Exception:
        logger.warning("Failed to query audit-derived issues", exc_info=True)
        return []

    return [_issue_from_audit_group_row(row) for row in rows]


def _last_seen_epoch(ts: datetime | None) -> float:
    """Return a sortable epoch value for optional timestamps."""
    if ts is None:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.timestamp()


async def _check_butler_reachability(
    mgr: MCPClientManager,
    info: ButlerConnectionInfo,
) -> Issue | None:
    """Check if a butler is reachable. Returns an Issue if not."""
    try:
        client = await asyncio.wait_for(
            mgr.get_client(info.name),
            timeout=_STATUS_TIMEOUT_S,
        )
        await asyncio.wait_for(client.ping(), timeout=_STATUS_TIMEOUT_S)
        return None
    except (ButlerUnreachableError, TimeoutError):
        return Issue(
            severity="critical",
            type="unreachable",
            butler=info.name,
            description=f"Butler '{info.name}' is not responding",
            link=f"/butlers/{info.name}",
        )
    except Exception:
        logger.warning("Unexpected error checking butler %s", info.name, exc_info=True)
        return Issue(
            severity="critical",
            type="unreachable",
            butler=info.name,
            description=f"Butler '{info.name}' check failed unexpectedly",
            link=f"/butlers/{info.name}",
        )


@router.get("", response_model=ApiResponse[list[Issue]])
async def list_issues(
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    db: DatabaseManager | None = Depends(_get_db_manager_optional),
) -> ApiResponse[list[Issue]]:
    """Return grouped issues across butler infrastructure.

    Checks all butlers in parallel for:
    - Unreachable services (critical, live)
    - Grouped audit failures (warning/critical with first/last seen + count)

    Results are sorted by recency (most recent ``last_seen_at`` first).
    """
    tasks = [_check_butler_reachability(mgr, info) for info in configs]
    reachability_results, audit_issues = await asyncio.gather(
        asyncio.gather(*tasks),
        _list_audit_error_issues(db),
    )

    now = datetime.now(UTC)
    issues: list[Issue] = []
    for issue in reachability_results:
        if issue is None:
            continue
        issue.error_message = issue.description
        issue.occurrences = 1
        issue.first_seen_at = now
        issue.last_seen_at = now
        issue.butlers = [issue.butler]
        issues.append(issue)

    issues.extend(audit_issues)

    severity_order = {"critical": 0, "warning": 1}
    issues.sort(
        key=lambda i: (
            -_last_seen_epoch(i.last_seen_at),
            severity_order.get(i.severity, 2),
            i.butler,
            i.type,
        )
    )

    return ApiResponse[list[Issue]](data=issues)
