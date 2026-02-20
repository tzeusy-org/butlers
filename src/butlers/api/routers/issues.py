"""Active issues aggregation endpoint.

Scans all butlers for problems: unreachable services, module failures,
and other anomalies. Returns a sorted list of active issues.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta

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
_AUDIT_LOOKBACK_HOURS = 24
_AUDIT_MAX_ACTIVE_ISSUES = 200
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


def _safe_request_summary(value: object) -> dict:
    """Normalize ``request_summary`` payload to a dict."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


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


def _issue_from_audit_row(row) -> Issue | None:
    """Map one failed audit stream row into an active issue."""
    butler = str(row["butler"])
    operation = str(row["operation"])
    request_summary = _safe_request_summary(row["request_summary"])
    trigger_source = str(request_summary.get("trigger_source") or "")
    error_summary = _summarize_error(row["error"])

    if operation == "session" and trigger_source.startswith("schedule:"):
        schedule_name = trigger_source.split(":", 1)[1] or "unknown"
        description = f"Scheduled task '{schedule_name}' failed on '{butler}'"
        if error_summary:
            description += f": {error_summary}"
        return Issue(
            severity="critical",
            type=f"scheduled_task_failure:{_slug(schedule_name)}",
            butler=butler,
            description=description,
            link="/audit-log",
        )

    stream = operation
    if trigger_source:
        stream = f"{operation} ({trigger_source})"
    description = f"Recent '{stream}' error on '{butler}'"
    if error_summary:
        description += f": {error_summary}"

    trigger_slug = _slug(trigger_source) if trigger_source else "default"
    return Issue(
        severity="warning",
        type=f"audit_error:{_slug(operation)}:{trigger_slug}",
        butler=butler,
        description=description,
        link="/audit-log",
    )


async def _list_audit_error_issues(db: DatabaseManager | None) -> list[Issue]:
    """Return active issues derived from failed audit-log streams.

    Active = latest row per ``(butler, operation, trigger_source)`` stream is
    ``result='error'`` within the lookback window.
    """
    if db is None:
        return []

    try:
        pool = db.pool("switchboard")
    except KeyError:
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=_AUDIT_LOOKBACK_HOURS)
    try:
        rows = await pool.fetch(
            """
            WITH latest_stream_events AS (
                SELECT DISTINCT ON (
                    butler,
                    operation,
                    COALESCE(request_summary->>'trigger_source', '')
                )
                    butler,
                    operation,
                    request_summary,
                    result,
                    error,
                    created_at
                FROM dashboard_audit_log
                WHERE created_at >= $1
                ORDER BY
                    butler,
                    operation,
                    COALESCE(request_summary->>'trigger_source', ''),
                    created_at DESC
            )
            SELECT butler, operation, request_summary, result, error, created_at
            FROM latest_stream_events
            WHERE result = 'error'
            ORDER BY created_at DESC
            LIMIT $2
            """,
            cutoff,
            _AUDIT_MAX_ACTIVE_ISSUES,
        )
    except Exception:
        logger.warning("Failed to query audit-derived issues", exc_info=True)
        return []

    issues: list[Issue] = []
    for row in rows:
        issue = _issue_from_audit_row(row)
        if issue is not None:
            issues.append(issue)
    return issues


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
    """Return all active issues across butler infrastructure.

    Checks all butlers in parallel for:
    - Unreachable services (critical)
    - Module failures (warning) — stub for now
    - Notification failures (warning) — stub for now

    Results sorted by severity (critical first), then butler name.
    """
    tasks = [_check_butler_reachability(mgr, info) for info in configs]
    reachability_results, audit_issues = await asyncio.gather(
        asyncio.gather(*tasks),
        _list_audit_error_issues(db),
    )

    issues: list[Issue] = [r for r in reachability_results if r is not None]
    issues.extend(audit_issues)

    # Sort: critical first, then by butler name
    severity_order = {"critical": 0, "warning": 1}
    issues.sort(key=lambda i: (severity_order.get(i.severity, 2), i.butler))

    return ApiResponse[list[Issue]](data=issues)
