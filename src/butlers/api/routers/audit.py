"""Audit log endpoints — paginated, filterable audit history.

Legacy section
--------------
``log_audit_entry`` queries the Switchboard butler's ``dashboard_audit_log``
table and is called by other routers to record audit entries for legacy write
operations.

New primitive section (core_092)
---------------------------------
``append`` inserts into the ``public.audit_log`` table (the canonical audit
primitive introduced in core_092).  It returns the new row id and increments
the ``audit_log_appended_total{action}`` Prometheus counter.

``GET /api/audit-log`` and ``GET /api/audit-log/{id}`` read from
``public.audit_log`` and return ``PaginatedResponse[AuditLogEntry]`` /
``ApiResponse[AuditLogEntry]`` respectively.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

from asyncpg.exceptions import UndefinedTableError
from fastapi import APIRouter, Depends, HTTPException, Query
from prometheus_client import Counter

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.models.audit import AuditEntry, AuditLogEntry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit-log", tags=["audit"])

# ---------------------------------------------------------------------------
# Prometheus counter — incremented per successful append to public.audit_log
# ---------------------------------------------------------------------------

audit_log_appended_total = Counter(
    "audit_log_appended_total",
    "Number of rows appended to public.audit_log, partitioned by action.",
    labelnames=["action"],
)


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _empty_audit_page(*, limit: int) -> PaginatedResponse[AuditLogEntry]:
    """Return an empty audit payload for degraded-read scenarios."""
    return PaginatedResponse[AuditLogEntry](
        data=[],
        meta=PaginationMeta(total=0, offset=0, limit=limit),
    )


# ---------------------------------------------------------------------------
# Legacy helper: log an audit entry into dashboard_audit_log
# ---------------------------------------------------------------------------


async def log_audit_entry(
    db: DatabaseManager,
    butler: str,
    operation: str,
    request_summary: dict,
    result: str = "success",
    error: str | None = None,
    user_context: dict | None = None,
) -> None:
    """Insert an audit log entry into the switchboard database.

    Silently logs and swallows errors so audit logging never breaks the
    primary operation.
    """
    # Pre-coerce non-JSON-safe values to strings, then hand the codec dicts —
    # wrapping with json.dumps() here would double-encode and store JSONB
    # string scalars in JSONB columns instead of objects.
    safe_summary = json.loads(json.dumps(request_summary, default=str))
    safe_context = json.loads(json.dumps(user_context or {}, default=str))

    try:
        pool = db.pool("switchboard")
        await pool.execute(
            "INSERT INTO dashboard_audit_log "
            "(butler, operation, request_summary, result, error, user_context) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            butler,
            operation,
            safe_summary,
            result,
            error,
            safe_context,
        )
    except Exception:
        logger.warning(
            "Failed to log audit entry: butler=%s operation=%s",
            butler,
            operation,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# New primitive helper: append to public.audit_log
# ---------------------------------------------------------------------------


async def append(
    pool,
    actor: str,
    action: str,
    *,
    target: str | None = None,
    note: str | None = None,
    ip: str | None = None,
    request_id: uuid.UUID | None = None,
) -> int:
    """Append one row to ``public.audit_log`` and return the new row id.

    Increments ``audit_log_appended_total{action}`` on success.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the database that holds
        ``public.audit_log``.
    actor:
        Identity of the actor that triggered the change (e.g. ``"owner"``
        or a butler name).
    action:
        Short, machine-readable verb describing the change
        (e.g. ``"model_priority_change"``).
    target:
        Optional fully-qualified name of the affected resource
        (e.g. ``"butler:qa"`` or ``"rule:42"``).
    note:
        Optional human-readable free-text description of the change.
    ip:
        Optional source IP address as a string (e.g. ``"1.2.3.4"``).
    request_id:
        Optional UUID correlating the audit entry to an HTTP request.

    Returns
    -------
    int
        The ``id`` of the newly-inserted row.
    """
    row_id: int = await pool.fetchval(
        "INSERT INTO public.audit_log "
        "(actor, action, target, note, ip, request_id) "
        "VALUES ($1, $2, $3, $4, $5::inet, $6) "
        "RETURNING id",
        actor,
        action,
        target,
        note,
        ip,
        request_id,
    )
    audit_log_appended_total.labels(action=action).inc()
    return row_id


# ---------------------------------------------------------------------------
# GET /api/audit-log — list audit entries from public.audit_log
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[AuditLogEntry])
async def list_audit_log(
    since: str | None = Query(None, description="ISO 8601 timestamp lower bound"),
    actor: str | None = Query(None, description="Filter by actor (exact match)"),
    action: str | None = Query(None, description="Filter by action (exact match)"),
    limit: int = Query(
        100, ge=1, le=1000, description="Max records to return (default 100, max 1000)"
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[AuditLogEntry]:
    """Return paginated audit log entries from ``public.audit_log``.

    Supports filtering by actor, action, and a lower-bound timestamp.
    Results are ordered by ``ts DESC`` (newest first).
    """
    pool = db.credential_shared_pool()

    # Build dynamic WHERE clause
    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if since is not None:
        parsed_since = datetime.fromisoformat(since)
        conditions.append(f"ts >= ${idx}")
        args.append(parsed_since)
        idx += 1

    if actor is not None:
        conditions.append(f"actor = ${idx}")
        args.append(actor)
        idx += 1

    if action is not None:
        conditions.append(f"action = ${idx}")
        args.append(action)
        idx += 1

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    # Count query
    count_sql = f"SELECT count(*) FROM public.audit_log{where_clause}"
    try:
        total = await pool.fetchval(count_sql, *args) or 0
    except UndefinedTableError:
        logger.info("public.audit_log missing; returning empty audit log payload")
        return _empty_audit_page(limit=limit)

    # Data query — server-side LIMIT only (no offset for this endpoint)
    data_sql = (
        f"SELECT id, ts, actor, action, target, note, ip, request_id "
        f"FROM public.audit_log{where_clause} "
        f"ORDER BY ts DESC "
        f"LIMIT ${idx}"
    )
    args.append(limit)

    try:
        rows = await pool.fetch(data_sql, *args)
    except UndefinedTableError:
        logger.info("public.audit_log missing during fetch; returning empty audit log payload")
        return _empty_audit_page(limit=limit)

    entries = [AuditLogEntry.from_record(row) for row in rows]

    return PaginatedResponse[AuditLogEntry](
        data=entries,
        meta=PaginationMeta(total=total, offset=0, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/audit-log/{id} — fetch single audit entry by id
# ---------------------------------------------------------------------------


@router.get("/{entry_id}", response_model=ApiResponse[AuditLogEntry])
async def get_audit_log_entry(
    entry_id: int,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[AuditLogEntry]:
    """Return a single audit log entry by its integer id.

    Raises HTTP 404 if no row with the given id exists.
    """
    pool = db.credential_shared_pool()

    try:
        row = await pool.fetchrow(
            "SELECT id, ts, actor, action, target, note, ip, request_id "
            "FROM public.audit_log "
            "WHERE id = $1",
            entry_id,
        )
    except UndefinedTableError:
        raise HTTPException(status_code=404, detail="Audit log entry not found")

    if row is None:
        raise HTTPException(status_code=404, detail="Audit log entry not found")

    return ApiResponse[AuditLogEntry](data=AuditLogEntry.from_record(row))


# ---------------------------------------------------------------------------
# Keep AuditEntry exported for routers that still import it from here
# ---------------------------------------------------------------------------

__all__ = [
    "AuditEntry",
    "AuditLogEntry",
    "append",
    "audit_log_appended_total",
    "log_audit_entry",
    "router",
]
