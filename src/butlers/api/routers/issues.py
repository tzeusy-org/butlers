"""Issues aggregation endpoint.

Aggregates live reachability problems and grouped audit-log error history
into a single issues feed.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta

import anyio
from asyncpg.exceptions import UndefinedTableError
from fastapi import APIRouter, Body, Depends, HTTPException, Query

from butlers.api.audit_grouping import (
    build_audit_group_occurrences_query,
    build_audit_group_query,
    issue_from_audit_group_row,
)
from butlers.api.db import DatabaseManager
from butlers.api.degraded import DegradedSources
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_db_manager,
    get_mcp_manager,
)
from butlers.api.models import (
    ApiMeta,
    ApiResponse,
    DismissIssueRequest,
    Issue,
    PaginatedResponse,
    PaginationMeta,
)
from butlers.api.models.audit import AuditLogEntry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/issues", tags=["issues"])

_STATUS_TIMEOUT_S = 5.0

# JARVIS pursuit move 13 (bu-qvnce.13): the audit-derived issues query was an
# unbounded all-time CTE with no LIMIT -- a fleet with years of audit history
# would scan and group every error row ever logged on every page load. Both a
# default time window AND a hard row cap now bound it; "all" opts out of the
# window (e.g. a deliberate full-history query) but the cap still applies so
# that path can never regress to a truly unbounded scan.
_DEFAULT_ISSUES_WINDOW = "7d"
_MAX_AUDIT_GROUP_ROWS = 500
_WINDOW_RE = re.compile(r"^(\d+)(h|d)$")

# Degraded-source names for the ``meta.sources_degraded`` envelope (bu-tpudw.3).
# This surface's whole product IS failure, so a query error on either source
# must never silently zero-fill into an all-clear empty feed (CLAUDE.md
# Degraded-Mode Response Envelope). Both sources read from the switchboard pool
# (``public.audit_log`` grouped errors + ``public.dismissed_issues`` acks).
_SOURCE_AUDIT_GROUPS = "audit-groups"
_SOURCE_ACKS = "acks"


def _is_missing_relation_error(exc: Exception) -> bool:
    """Return whether *exc* means the backing table simply does not exist yet.

    A pre-migration ``public.audit_log`` / ``public.dismissed_issues`` is a
    legitimately-absent source, not a degraded one (classify-before-flagging;
    mirrors :func:`butlers.api.routers.memory._is_missing_memory_schema_error`).
    Any other failure -- a dropped connection, a timeout, a permission error --
    is genuine and MUST be flagged so an incomplete feed never reads as a
    truthful all-clear.
    """
    if isinstance(exc, UndefinedTableError):
        return True
    msg = str(exc).lower()
    return "does not exist" in msg and ("relation" in msg or "table" in msg)


def _parse_issues_window(window: str) -> timedelta | None:
    """Parse the ``window`` query value into a lookback timedelta.

    Returns ``None`` for the literal ``"all"`` (no time-based filtering --
    the row cap alone bounds the query). Raises 422 for anything else that
    doesn't match ``<int>h`` / ``<int>d`` (e.g. ``"24h"``, ``"7d"``, ``"30d"``).
    """
    if window == "all":
        return None
    match = _WINDOW_RE.match(window)
    if not match:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid window '{window}'; expected '<N>h', '<N>d', or 'all'",
        )
    amount, unit = match.groups()
    return timedelta(hours=int(amount)) if unit == "h" else timedelta(days=int(amount))


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


async def _list_audit_error_issues(
    db: DatabaseManager | None,
    since: datetime | None,
    tracker: DegradedSources | None = None,
) -> list[Issue]:
    """Return grouped error issues derived from the audit log.

    Grouping key is normalized first-line error message (with tmp-path
    normalization). Each group exposes first/last timestamps and occurrences.

    ``since`` (bu-qvnce.13) bounds the underlying CTE to rows at or after that
    timestamp -- ``None`` (the ``window=all`` case) skips the time bound but
    the query is still capped at ``_MAX_AUDIT_GROUP_ROWS`` groups.

    ``tracker`` (bu-tpudw.3): when the fetch fails for a *genuine* reason (a
    dropped connection, a timeout, a permission error), the ``audit-groups``
    source is flagged so :func:`list_issues` can surface it via
    ``meta.sources_degraded`` instead of returning an all-clear empty feed. A
    legitimately-absent table (pre-migration ``public.audit_log``) is NOT a
    degraded source and is not flagged (see :func:`_is_missing_relation_error`).
    """
    if db is None:
        return []

    try:
        pool = db.pool("switchboard")
    except KeyError:
        return []

    try:
        if since is not None:
            query = build_audit_group_query(
                where_extra="\n                  AND created_at >= $1",
                limit=_MAX_AUDIT_GROUP_ROWS,
            )
            rows = await pool.fetch(query, since)
        else:
            query = build_audit_group_query(limit=_MAX_AUDIT_GROUP_ROWS)
            rows = await pool.fetch(query)
    except Exception as exc:
        if tracker is not None and not _is_missing_relation_error(exc):
            tracker.mark(_SOURCE_AUDIT_GROUPS, msg="Failed to query audit-derived issues")
        else:
            logger.warning("Failed to query audit-derived issues", exc_info=True)
        return []

    return [issue_from_audit_group_row(row) for row in rows]


async def _list_dismissed_acks(
    db: DatabaseManager | None,
    tracker: DegradedSources | None = None,
) -> dict[str, datetime | None]:
    """Return every acked issue_key mapped to its ack-time ``last_seen_at``.

    The mapped value is the recurrence watermark (JARVIS audit move 6,
    bu-86c4c.15): an ack is only honored while the issue's current
    ``last_seen_at`` has not advanced past this value. A ``None`` value means
    no watermark was recorded (a legacy ack, or an issue type that never
    carries a timestamp) and falls back to dismiss-forever for that row.

    ``tracker`` (bu-tpudw.3): a genuine query failure flags the ``acks`` source
    so the feed is not silently under-filtered (a dropped ack lets an
    already-acknowledged group reappear as if active). A legitimately-absent
    ``public.dismissed_issues`` table is not flagged.
    """
    if db is None:
        return {}
    try:
        pool = db.pool("switchboard")
    except KeyError:
        return {}
    try:
        rows = await pool.fetch("SELECT issue_key, last_seen_at FROM public.dismissed_issues")
    except Exception as exc:
        if tracker is not None and not _is_missing_relation_error(exc):
            tracker.mark(_SOURCE_ACKS, msg="Failed to query dismissed issues")
        else:
            logger.warning("Failed to query dismissed issues", exc_info=True)
        return {}
    return {str(row["issue_key"]): row["last_seen_at"] for row in rows}


def _still_acked(issue: Issue, acked_last_seen_at: datetime | None) -> bool:
    """Return True when an acked issue has NOT recurred since it was acked.

    Acknowledge-until-recurrence (JARVIS audit move 6, bu-86c4c.15): a
    dismissal only holds while the issue's ``last_seen_at`` is no newer than
    the value recorded at ack time. If the issue recurs (its ``last_seen_at``
    advances), the ack is considered stale and the issue reappears in the
    active feed automatically — the owner never has to remember to restore a
    mistakenly-dismissed-forever group.

    Falls back to the old dismiss-forever behavior (always still-acked) when
    either side lacks a timestamp to compare, since there is no recurrence
    signal to act on.
    """
    if acked_last_seen_at is None or issue.last_seen_at is None:
        return True
    return _last_seen_epoch(issue.last_seen_at) <= _last_seen_epoch(acked_last_seen_at)


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
            "When true, return only the issues that have been acknowledged "
            "server-side instead of the active feed. Each returned issue carries "
            "``dismissed=True`` so the UI can offer a restore affordance."
        ),
    ),
    window: str = Query(
        _DEFAULT_ISSUES_WINDOW,
        description=(
            "Time window bounding audit-derived (grouped) issues, e.g. '24h', "
            "'7d' (default), '30d', or 'all' to disable the time bound (a hard "
            f"cap of {_MAX_AUDIT_GROUP_ROWS} groups still applies regardless). "
            "Live reachability issues are unaffected -- they always reflect the "
            "current check."
        ),
    ),
) -> ApiResponse[list[Issue]]:
    """Return grouped issues across butler infrastructure.

    Checks all butlers in parallel for:
    - Unreachable services (critical, live)
    - Grouped audit failures (warning/critical with first/last seen + count,
      bounded to ``window`` -- bu-qvnce.13)

    By default, issues the user has acknowledged server-side are filtered out
    of the active feed — acknowledge-until-recurrence (JARVIS audit move 6,
    bu-86c4c.15): the ack holds only while the group has not recurred since it
    was acked (see :func:`_still_acked`); a fresh occurrence automatically
    un-acks the group and it reappears here with no owner action required.
    Pass ``include_dismissed=true`` to instead return *only* the
    still-acknowledged issues (each flagged ``dismissed=True``) so an ack can
    also be undone manually from the UI before it would have lapsed on its
    own.

    Results are sorted by recency (most recent ``last_seen_at`` first).

    Degraded sources (bu-tpudw.3): when a DB-backed source fails for a genuine
    reason, the response still returns HTTP 200 but carries
    ``meta.sources_degraded: list[str]`` naming the dropped source(s)
    (``audit-groups`` and/or ``acks``) per the fleet-wide degraded-envelope
    convention. The field is absent when every source answered. The frontend
    MUST NOT render its all-clear empty state while this list is non-empty --
    an incomplete feed is not an honest "no issues".
    """
    window_delta = _parse_issues_window(window)
    since = datetime.now(UTC) - window_delta if window_delta is not None else None
    tasks = [_check_butler_reachability(mgr, info) for info in configs]
    # bu-tpudw.3: this feed's product IS failure, so a query error on either
    # DB-backed source must be surfaced (meta.sources_degraded), never
    # zero-filled into an all-clear empty feed. The tracker accumulates any
    # genuinely-failed source across both queries below.
    tracker = DegradedSources(logger)
    reachability_results, audit_issues, acked_by_key = await asyncio.gather(
        asyncio.gather(*tasks),
        _list_audit_error_issues(db, since, tracker),
        _list_dismissed_acks(db, tracker),
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

    # Partition by ack state. The ack is keyed by the issue's stable
    # ``issue_key`` so it persists across browsers and sessions, but only
    # holds while the group hasn't recurred since it was acked.
    if include_dismissed:
        # Restore view: surface only the still-acknowledged issues, flagged so
        # the UI can render a "Restore" affordance for each. A recurred (no
        # longer acked) issue belongs in the active feed instead, not here.
        issues = [
            issue
            for issue in issues
            if issue.issue_key in acked_by_key
            and _still_acked(issue, acked_by_key[issue.issue_key])
        ]
        for issue in issues:
            issue.dismissed = True
    else:
        issues = [
            issue
            for issue in issues
            if issue.issue_key not in acked_by_key
            or not _still_acked(issue, acked_by_key[issue.issue_key])
        ]

    severity_order = {"critical": 0, "warning": 1}
    issues.sort(
        key=lambda i: (
            -_last_seen_epoch(i.last_seen_at),
            severity_order.get(i.severity, 2),
            i.butler,
            i.type,
        )
    )

    meta = ApiMeta(sources_degraded=tracker.names) if tracker.failed else ApiMeta()
    return ApiResponse[list[Issue]](data=issues, meta=meta)


@router.post("/dismiss", response_model=ApiResponse[dict], status_code=200)
async def dismiss_issue(
    body: DismissIssueRequest = Body(...),
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Acknowledge an issue group so it no longer appears in the active feed.

    The ack is persisted in ``public.dismissed_issues`` keyed by the issue's
    stable ``issue_key``, so it holds across browsers and sessions (unlike the
    old per-browser ``localStorage`` behaviour). Idempotent: a repeat
    acknowledgement of the same key updates the existing row.

    Acknowledge-until-recurrence (JARVIS audit move 6, bu-86c4c.15): the
    caller should pass the issue's current ``last_seen_at`` in
    ``body.last_seen_at`` so the ack records a recurrence watermark. If the
    group's ``last_seen_at`` later advances past this value (a genuine new
    occurrence), :func:`list_issues` automatically un-acks it — this is not
    dismiss-forever. Omitting ``last_seen_at`` falls back to dismiss-forever
    for that row, since there is no watermark to compare against.
    """
    key = (body.issue_key or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail="issue_key is required")

    pool = _require_pool(db)
    dismissed_by = body.dismissed_by if body.dismissed_by not in (None, "") else "dashboard_user"

    await pool.execute(
        """
        INSERT INTO public.dismissed_issues (issue_key, dismissed_by, created_at, last_seen_at)
        VALUES ($1, $2, now(), $3)
        ON CONFLICT (issue_key) DO UPDATE
            SET dismissed_by = EXCLUDED.dismissed_by,
                last_seen_at = EXCLUDED.last_seen_at
        """,
        key,
        dismissed_by,
        body.last_seen_at,
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


# ---------------------------------------------------------------------------
# GET /api/issues/{issue_key}/occurrences — drill-down for audit-derived groups
# ---------------------------------------------------------------------------


@router.get(
    "/{issue_key:path}/occurrences",
    response_model=PaginatedResponse[AuditLogEntry],
)
async def list_issue_occurrences(
    issue_key: str,
    window: str = Query(
        _DEFAULT_ISSUES_WINDOW,
        description=(
            "Time window used to re-derive the group, e.g. '24h', '7d' "
            "(default), '30d', or 'all'. MUST match the window the feed was "
            f"viewed under (bu-hmdqz.4): the same row cap ({_MAX_AUDIT_GROUP_ROWS}) "
            "and time bound as GET /api/issues apply here too, so a group's "
            "occurrence total never disagrees with what the feed just showed."
        ),
    ),
    offset: int = Query(0, ge=0, description="Number of occurrences to skip"),
    limit: int = Query(
        50, ge=1, le=500, description="Max occurrences to return (default 50, max 500)"
    ),
    db: DatabaseManager | None = Depends(_get_db_manager),
) -> PaginatedResponse[AuditLogEntry]:
    """Return the raw ``public.audit_log`` rows behind one audit-derived issue group.

    JARVIS audit move 6: a "Seen 47x" issue group previously offered no path to
    its 47 occurrences. This re-derives the group's grouping parameters
    (normalized error message, contributing butlers) from a fresh grouped
    query — rather than trying to reverse the lossy slug embedded in
    ``issue_key`` — then reuses the shared
    :func:`~butlers.api.audit_grouping.build_audit_group_occurrences_query`
    CTE to fetch the individual rows, so a group's occurrences can never
    disagree with the group definition itself. Note the occurrences query
    filters on ``error_summary`` alone (the actual ``GROUP BY`` key) — NOT on
    the group's aggregated ``has_schedule`` flag, which a group can straddle
    (see that function's docstring).

    ``window`` (bu-hmdqz.4): re-derivation now applies the exact same time
    bound and row cap as ``GET /api/issues``. Previously this endpoint always
    re-derived groups over *all* audit history with no cap, which (a) could
    silently change a group's reported ``occurrences`` total relative to what
    the feed showed under its own (possibly narrower) window, and (b) — before
    the group's identity key stopped depending on the aggregated butler set —
    could disagree on that butler set entirely and 404 a group the feed had
    just displayed.

    Only audit-derived groups (``audit_error_group:*`` /
    ``scheduled_task_failure:*``) have occurrences to drill into — live
    reachability issues (``unreachable``) always report exactly one
    synthetic occurrence and are not stored rows, so their key 404s here.

    Raises HTTP 404 when no active group matches ``issue_key`` within
    ``window`` (it may have been resolved/expired since the feed was last
    fetched, or may only exist outside the requested window).
    """
    key = (issue_key or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail="issue_key is required")

    window_delta = _parse_issues_window(window)
    since = datetime.now(UTC) - window_delta if window_delta is not None else None

    pool = _require_pool(db)

    try:
        if since is not None:
            group_rows = await pool.fetch(
                build_audit_group_query(
                    where_extra="\n                  AND created_at >= $1",
                    limit=_MAX_AUDIT_GROUP_ROWS,
                ),
                since,
            )
        else:
            group_rows = await pool.fetch(build_audit_group_query(limit=_MAX_AUDIT_GROUP_ROWS))
    except Exception as exc:
        logger.warning("Failed to query audit groups for occurrences", exc_info=True)
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    match = None
    for row in group_rows:
        issue = issue_from_audit_group_row(row)
        if issue.issue_key == key:
            match = row
            break

    if match is None:
        raise HTTPException(
            status_code=404,
            detail=f"No active issue group found for issue_key '{key}'",
        )

    error_summary = str(match["error_summary"])
    butlers = [str(b) for b in (match["butlers"] or [])] or ["unknown"]
    total = int(match["occurrences"] or 0)

    occurrences_sql = build_audit_group_occurrences_query()
    rows = await pool.fetch(occurrences_sql, error_summary, butlers, limit, offset)

    page = [AuditLogEntry.from_record(row) for row in rows]

    return PaginatedResponse[AuditLogEntry](
        data=page,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )
