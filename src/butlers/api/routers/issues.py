"""Issues aggregation endpoint.

Aggregates live reachability problems and grouped audit-log error history
into a single issues feed.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import anyio
from fastapi import APIRouter, Depends

from butlers.api.audit_grouping import build_audit_group_query, issue_from_audit_group_row
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


def _get_db_manager() -> DatabaseManager | None:
    """Stub dependency for DatabaseManager injection.

    Overridden by ``wire_db_dependencies()`` at app startup.  Returns ``None``
    when the DatabaseManager has not been initialized (e.g. partial startup or
    unit-test context without a live DB), allowing the issues endpoint to
    remain available with reduced functionality.
    """
    try:
        return get_db_manager()
    except RuntimeError:
        return None


async def _list_audit_error_issues(db: DatabaseManager | None) -> list[Issue]:
    """Return grouped error issues derived from the audit log.

    Grouping key is normalized first-line error message (with tmp-path
    normalization). Each group exposes first/last timestamps and occurrences.
    """
    if db is None:
        return []

    try:
        pool = db.pool("switchboard")
    except KeyError:
        return []

    try:
        rows = await pool.fetch(build_audit_group_query())
    except Exception:
        logger.warning("Failed to query audit-derived issues", exc_info=True)
        return []

    return [issue_from_audit_group_row(row) for row in rows]


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
    """Check if a butler is reachable. Returns an Issue if not.

    Retries once on stale-connection errors (evicts the cached client first).
    """
    for attempt in range(2):
        try:
            client = await asyncio.wait_for(
                mgr.get_client(info.name),
                timeout=_STATUS_TIMEOUT_S,
            )
            await asyncio.wait_for(client.ping(), timeout=_STATUS_TIMEOUT_S)
            return None
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            await mgr.invalidate_client(info.name)
            if attempt == 0:
                continue
        except (ButlerUnreachableError, TimeoutError):
            break
        except Exception:
            logger.warning("Unexpected error checking butler %s", info.name, exc_info=True)
            return Issue(
                severity="critical",
                type="unreachable",
                butler=info.name,
                description=f"Butler '{info.name}' check failed unexpectedly",
                link=f"/butlers/{info.name}",
            )
    return Issue(
        severity="critical",
        type="unreachable",
        butler=info.name,
        description=f"Butler '{info.name}' is not responding",
        link=f"/butlers/{info.name}",
    )


@router.get("", response_model=ApiResponse[list[Issue]])
async def list_issues(
    mgr: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    db: DatabaseManager | None = Depends(_get_db_manager),
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
