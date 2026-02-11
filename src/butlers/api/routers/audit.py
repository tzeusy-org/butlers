"""Audit log endpoints — paginated, filterable audit history.

Queries the Switchboard butler's ``dashboard_audit_log`` table and returns
results in the standard ``PaginatedResponse`` envelope.

Also provides a ``log_audit_entry`` helper that other routers call to record
audit entries after write operations.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import PaginatedResponse, PaginationMeta
from butlers.api.models.audit import AuditEntry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit-log", tags=["audit"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Helper: log an audit entry
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
    try:
        pool = db.pool("switchboard")
        await pool.execute(
            "INSERT INTO dashboard_audit_log "
            "(butler, operation, request_summary, result, error, user_context) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            butler,
            operation,
            json.dumps(request_summary),
            result,
            error,
            json.dumps(user_context or {}),
        )
    except Exception:
        logger.warning(
            "Failed to log audit entry: butler=%s operation=%s",
            butler,
            operation,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# GET /api/audit-log — list audit entries
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[AuditEntry])
async def list_audit_log(
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    butler: str | None = Query(None, description="Filter by butler name"),
    operation: str | None = Query(None, description="Filter by operation type"),
    since: str | None = Query(None, description="ISO 8601 timestamp lower bound"),
    until: str | None = Query(None, description="ISO 8601 timestamp upper bound"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[AuditEntry]:
    """Return paginated audit log entries from the Switchboard database.

    Supports filtering by butler, operation, and date range.
    Results are ordered by ``created_at DESC`` (newest first).
    """
    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Switchboard database is not available",
        )

    # Build dynamic WHERE clause
    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if butler is not None:
        conditions.append(f"butler = ${idx}")
        args.append(butler)
        idx += 1

    if operation is not None:
        conditions.append(f"operation = ${idx}")
        args.append(operation)
        idx += 1

    if since is not None:
        parsed_since = datetime.fromisoformat(since)
        conditions.append(f"created_at >= ${idx}")
        args.append(parsed_since)
        idx += 1

    if until is not None:
        parsed_until = datetime.fromisoformat(until)
        conditions.append(f"created_at <= ${idx}")
        args.append(parsed_until)
        idx += 1

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    # Count query
    count_sql = f"SELECT count(*) FROM dashboard_audit_log{where_clause}"
    total = await pool.fetchval(count_sql, *args) or 0

    # Data query
    data_sql = (
        f"SELECT id, butler, operation, request_summary, result, error, "
        f"user_context, created_at "
        f"FROM dashboard_audit_log{where_clause} "
        f"ORDER BY created_at DESC "
        f"OFFSET ${idx} LIMIT ${idx + 1}"
    )
    args.extend([offset, limit])

    rows = await pool.fetch(data_sql, *args)

    entries = [
        AuditEntry(
            id=row["id"],
            butler=row["butler"],
            operation=row["operation"],
            request_summary=(
                row["request_summary"]
                if isinstance(row["request_summary"], dict)
                else json.loads(row["request_summary"])
            ),
            result=row["result"],
            error=row["error"],
            user_context=(
                row["user_context"]
                if isinstance(row["user_context"], dict)
                else json.loads(row["user_context"])
            ),
            created_at=row["created_at"],
        )
        for row in rows
    ]

    return PaginatedResponse[AuditEntry](
        data=entries,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )
