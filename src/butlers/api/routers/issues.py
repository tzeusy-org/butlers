"""Issues aggregation endpoint.

Aggregates live reachability problems and grouped audit-log error history
into a single issues feed.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import anyio
from fastapi import APIRouter, Body, Depends, HTTPException, Query

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
from butlers.api.models import ApiMeta, ApiResponse, DismissIssueRequest, Issue

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


async def _list_dismissed_keys(db: DatabaseManager | None) -> set[str]:
    """Return the set of issue keys that have been dismissed (acked) server-side."""
    if db is None:
        return set()
    try:
        pool = db.pool("switchboard")
    except KeyError:
        return set()
    try:
        rows = await pool.fetch("SELECT issue_key FROM public.dismissed_issues")
    except Exception:
        logger.warning("Failed to query dismissed issues", exc_info=True)
        return set()
    return {str(row["issue_key"]) for row in rows}


def _require_pool(db: DatabaseManager | None):
    """Return the switchboard pool or raise 503 when the DB is unavailable."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        return db.pool("switchboard")
    except KeyError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


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
    include_dismissed: bool = Query(
        False,
        description=(
            "When true, return only the issues that have been dismissed (acked) "
            "server-side instead of the active feed. Each returned issue carries "
            "``dismissed=True`` so the UI can offer a restore affordance."
        ),
    ),
) -> ApiResponse[list[Issue]]:
    """Return grouped issues across butler infrastructure.

    Checks all butlers in parallel for:
    - Unreachable services (critical, live)
    - Grouped audit failures (warning/critical with first/last seen + count)

    By default, issues the user has dismissed (acked) server-side are filtered
    out. Pass ``include_dismissed=true`` to instead return *only* the dismissed
    issues (each flagged ``dismissed=True``) so a mistakenly-dismissed issue can
    be restored from the UI.

    Results are sorted by recency (most recent ``last_seen_at`` first).
    """
    tasks = [_check_butler_reachability(mgr, info) for info in configs]
    reachability_results, audit_issues, dismissed_keys = await asyncio.gather(
        asyncio.gather(*tasks),
        _list_audit_error_issues(db),
        _list_dismissed_keys(db),
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

    # Partition by dismissal state. The ack is keyed by the issue's stable
    # ``issue_key`` so the dismissal persists across browsers and sessions.
    if include_dismissed:
        # Restore view: surface only the dismissed issues, flagged so the UI can
        # render a "Restore" affordance for each.
        issues = [issue for issue in issues if issue.issue_key in dismissed_keys]
        for issue in issues:
            issue.dismissed = True
    else:
        issues = [issue for issue in issues if issue.issue_key not in dismissed_keys]

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


@router.post("/dismiss", response_model=ApiResponse[dict], status_code=200)
async def dismiss_issue(
    body: DismissIssueRequest = Body(...),
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Dismiss (ack) an issue group so it no longer appears in the issues feed.

    The dismissal is persisted in ``public.dismissed_issues`` keyed by the
    issue's stable ``issue_key``, so it holds across browsers and sessions
    (unlike the old per-browser ``localStorage`` behaviour). Idempotent: a
    repeat dismissal of the same key updates the existing row.
    """
    key = (body.issue_key or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail="issue_key is required")

    pool = _require_pool(db)
    dismissed_by = body.dismissed_by if body.dismissed_by not in (None, "") else "dashboard_user"

    await pool.execute(
        """
        INSERT INTO public.dismissed_issues (issue_key, dismissed_by, created_at)
        VALUES ($1, $2, now())
        ON CONFLICT (issue_key) DO UPDATE
            SET dismissed_by = EXCLUDED.dismissed_by
        """,
        key,
        dismissed_by,
    )

    return ApiResponse(data={"issue_key": key, "dismissed": True}, meta=ApiMeta())


@router.delete("/dismiss/{issue_key:path}", response_model=ApiResponse[dict], status_code=200)
async def undismiss_issue(
    issue_key: str,
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Remove a dismissal so the issue group can reappear in the feed.

    Returns 404 when no dismissal exists for the given ``issue_key``.
    """
    key = (issue_key or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail="issue_key is required")

    pool = _require_pool(db)
    result = await pool.execute(
        "DELETE FROM public.dismissed_issues WHERE issue_key = $1",
        key,
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
            detail=f"No active dismissal found for issue_key '{key}'",
        )

    return ApiResponse(data={"issue_key": key, "deleted": True}, meta=ApiMeta())
