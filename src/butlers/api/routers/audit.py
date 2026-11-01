"""Audit log endpoints — paginated, filterable audit history.

Writer shim section
-------------------
``log_audit_entry`` is a backward-compatible shim: it accepts the legacy
``dashboard_audit_log`` field shape (butler / operation / request_summary /
user_context) and maps it onto the canonical :func:`append` primitive so callers
that still speak the old shape keep working.  It writes ONLY to
``public.audit_log``.

New primitive section (core_092)
---------------------------------
``append`` (defined in :mod:`butlers.core.audit` and re-exported here, where
the routers and tests reach it) inserts into the ``public.audit_log`` table, the
canonical audit primitive introduced in core_092.

``GET /api/audit-log`` and ``GET /api/audit-log/{id}`` read **solely** from
``public.audit_log`` and return ``PaginatedResponse[AuditLogEntry]`` /
``ApiResponse[AuditLogEntry]`` respectively.  The historical
``switchboard.dashboard_audit_log`` rows were backfilled into
``public.audit_log`` by migration core_124, so the legacy UNION read arm was
removed (bu-j26e8) — the canonical table is the single source of truth.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from asyncpg.exceptions import UndefinedTableError
from fastapi import APIRouter, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.deps import Db
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.models.audit import AuditEntry, AuditLogEntry
from butlers.api.owner_time_bounds import owner_zoneinfo, resolve_owner_time_bound
from butlers.core.audit import (
    AuditTableNotAvailableError,
    append,
    audit_log_appended_total,
    on_error_row,
)
from butlers.core.credential_keys import normalize_key_param

logger = logging.getLogger(__name__)


def _emit_issue_event(payload: dict[str, Any]) -> None:
    # An error-result audit row means the /api/issues feed may have changed.
    # Imported lazily: the events router imports this one.
    from butlers.api.routers.events import emit_event

    emit_event("issue", payload)


on_error_row(_emit_issue_event)

router = APIRouter(prefix="/api/audit-log", tags=["audit"])

_CREDENTIAL_LIFECYCLE_ACTIONS_SQL = """
    'attempted', 'connected', 'disconnected', 'failed', 'overrode',
    'revoked', 'rotated', 'set', 'verified', 'warned'
"""

_CREDENTIAL_SUCCESS_AUDIT_ACTIONS = frozenset(
    {
        "attempted",
        "connected",
        "disconnected",
        "overrode",
        "revoked",
        "rotated",
        "set",
        "verified",
        "warned",
    }
)


def credential_lifecycle_outcome(action: str, error: str | None) -> tuple[str | None, str | None]:
    """Return the explicit outcome for a known credential lifecycle action.

    ``failed`` is the audit action consumed by the Issues failure spine. Its
    error must be a caller-supplied safe diagnostic. Other known lifecycle
    actions are successful state transitions; unknown actions preserve the
    generic append helper's backwards-compatible ``None`` outcome.
    """
    if action == "failed":
        return "error", error
    if action in _CREDENTIAL_SUCCESS_AUDIT_ACTIONS:
        return "success", None
    return None, None


_PRIVILEGED_CONSEQUENCE_SQL = f"""
(
    action LIKE 'approval.%'
    OR action = 'approvals.policy'
    OR action LIKE 'model.%'
    OR action LIKE 'permission.%'
    OR action LIKE 'data.%'
    OR action LIKE 'webhook.%'
    OR action LIKE 'spend.%'
    OR action LIKE 'schedule.%'
    OR action = 'runtime_config_patch'
    OR action LIKE 'PUT /api/butlers/%/model-overrides'
    OR action IN ({_CREDENTIAL_LIFECYCLE_ACTIONS_SQL})
    OR result = 'error'
)
"""


def _empty_audit_page(*, offset: int, limit: int) -> PaginatedResponse[AuditLogEntry]:
    """Return an empty audit payload for degraded-read scenarios."""
    return PaginatedResponse[AuditLogEntry](
        data=[],
        meta=PaginationMeta(total=0, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# Writer shim: accept the legacy dashboard_audit_log field shape
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
    """Append an audit log entry to the canonical ``public.audit_log`` table.

    This is the API-layer compatibility shim that maps the legacy
    ``dashboard_audit_log`` field shape onto the canonical :func:`append`
    primitive (bu-h47nm):

    - ``butler``        → ``actor``
    - ``operation``     → ``action``
    - ``request_summary.path`` → ``target`` (when present)
    - ``request_summary``/``user_context`` → ``metadata`` JSONB
    - ``result`` / ``error`` → the ``result`` / ``error`` columns

    Writes ONLY to ``public.audit_log`` — the single canonical audit source
    (legacy ``dashboard_audit_log`` history was backfilled by core_124).
    Silently logs and swallows errors so audit logging never breaks the primary
    operation.
    """
    try:
        pool = db.pool("switchboard")
    except Exception:
        logger.warning(
            "Failed to acquire audit pool: butler=%s operation=%s",
            butler,
            operation,
            exc_info=True,
        )
        return

    # Pre-coerce non-JSON-safe values to plain JSON types before they land in
    # the ``metadata`` JSONB column (append() json.dumps()es metadata itself,
    # but doing the coercion here keeps the stored shape identical to legacy).
    safe_summary = json.loads(json.dumps(request_summary or {}, default=str))
    safe_context = json.loads(json.dumps(user_context or {}, default=str))

    target = safe_summary.get("path")
    target_str = str(target) if target else None

    metadata: dict[str, Any] = {"request_summary": safe_summary}
    if safe_context:
        metadata["user_context"] = safe_context

    try:
        await append(
            pool,
            butler,
            operation,
            target=target_str,
            metadata=metadata,
            result=result,
            error=error,
        )
    except AuditTableNotAvailableError:
        logger.warning(
            "Audit table unavailable, dropping entry: butler=%s operation=%s",
            butler,
            operation,
        )
    except Exception:
        logger.warning(
            "Failed to log audit entry: butler=%s operation=%s",
            butler,
            operation,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# GET /api/audit-log — list audit entries from public.audit_log
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[AuditLogEntry])
async def list_audit_log(
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(
        100, ge=1, le=1000, description="Max records to return (default 100, max 1000)"
    ),
    since: datetime | None = Query(None, description="ISO 8601 timestamp lower bound"),
    from_date: str | None = Query(
        None,
        description="Owner-timezone calendar-day or ISO 8601 lower bound on ts",
    ),
    to_date: str | None = Query(
        None,
        description="Owner-timezone calendar-day or ISO 8601 inclusive upper bound on ts",
    ),
    actor: str | None = Query(None, description="Filter by actor (exact match)"),
    action: str | None = Query(None, description="Filter by action (exact match)"),
    key: str | None = Query(
        None,
        description=(
            "Filter by credential key (exact match on normalised target). "
            "Accepts canonical short-prefix form (e.g. 'u:google') or "
            "long-scope form (e.g. 'user:google'). "
            "Uses ix_audit_log_target_ts index for efficient lookup."
        ),
    ),
    result: str | None = Query(
        None,
        description=(
            "Filter by outcome (exact match on the 'result' column persisted "
            "since core_122), e.g. 'success' or 'error'. Rows written before "
            "the audit-writer unification have a NULL result and are excluded "
            "by any non-empty filter value."
        ),
    ),
    kind: str | None = Query(
        None,
        description=(
            "Filter preset. 'privileged' selects consequence-bearing action families "
            "and rows with an explicit error outcome."
        ),
    ),
    *,
    db: Db,
) -> PaginatedResponse[AuditLogEntry]:
    """Return paginated audit log entries from ``public.audit_log``.

    Supports filtering by actor, action, ISO ``since``, owner-timezone
    ``from_date``/``to_date`` bounds, outcome (``?result=``), and credential
    key (``?key=``). Results are ordered by ``ts DESC`` (newest first).

    The ``?key=`` parameter filters rows whose ``target`` column equals the
    normalised credential key, using the ``ix_audit_log_target_ts`` index
    on ``(target, ts DESC)`` for efficient lookup.  Combinable with all
    other filter parameters.
    """
    pool = db.credential_shared_pool()

    # Treat empty / whitespace-only ?key= as "no filter" so that blank inputs
    # behave consistently with omitting the parameter entirely.  Then normalise
    # the non-empty value so that both short-prefix ('u:google') and long-scope
    # ('user:google') inputs produce the same canonical filter value.
    key = (key or "").strip() or None
    normalised_key: str | None = None
    if key is not None:
        try:
            normalised_key = normalize_key_param(key)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Validate ?kind= — only "privileged" is currently supported.
    kind = (kind or "").strip() or None
    if kind is not None and kind != "privileged":
        raise HTTPException(
            status_code=422, detail=f"Unsupported kind: {kind!r}. Use 'privileged'."
        )

    # Build dynamic WHERE clause
    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if since is not None:
        conditions.append(f"ts >= ${idx}")
        args.append(since)
        idx += 1

    if from_date or to_date:
        owner_tz = await owner_zoneinfo(pool)
        if from_date:
            conditions.append(f"ts >= ${idx}")
            args.append(resolve_owner_time_bound(from_date, owner_tz, upper=False))
            idx += 1

        if to_date:
            conditions.append(f"ts <= ${idx}")
            args.append(resolve_owner_time_bound(to_date, owner_tz, upper=True))
            idx += 1

    if actor is not None:
        conditions.append(f"actor = ${idx}")
        args.append(actor)
        idx += 1

    if action is not None:
        conditions.append(f"action = ${idx}")
        args.append(action)
        idx += 1

    if normalised_key is not None:
        conditions.append(f"target = ${idx}")
        args.append(normalised_key)
        idx += 1

    result = (result or "").strip() or None
    if result is not None:
        conditions.append(f"result = ${idx}")
        args.append(result)
        idx += 1

    # kind=privileged selects a fixed consequence allowlist rather than trying
    # to keep a denylist ahead of newly introduced machine-cadence events.
    # The existing legacy ``approvals.policy`` mutation is included alongside
    # the singular ``approval.*`` decision family.
    if kind == "privileged":
        conditions.append(_PRIVILEGED_CONSEQUENCE_SQL)

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    # Count query — canonical source is the single source of truth (bu-j26e8;
    # the historical dashboard_audit_log rows were backfilled by core_124).
    count_sql = f"SELECT count(*) FROM public.audit_log{where_clause}"
    try:
        total = await pool.fetchval(count_sql, *args) or 0
    except UndefinedTableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Audit log is not available — migration core_092 may not have run",
        ) from exc

    # Paged data query — order by ts DESC, slice with LIMIT/OFFSET directly
    # against the canonical table (no cross-source merge).
    data_sql = (
        f"SELECT id, ts, actor, action, target, note, ip, request_id, "
        f"metadata, result, error "
        f"FROM public.audit_log{where_clause} "
        f"ORDER BY ts DESC "
        f"LIMIT ${idx} OFFSET ${idx + 1}"
    )

    try:
        rows = await pool.fetch(data_sql, *args, limit, offset)
    except UndefinedTableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Audit log is not available — migration core_092 may not have run",
        ) from exc

    page = [AuditLogEntry.from_record(row) for row in rows]

    return PaginatedResponse[AuditLogEntry](
        data=page,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/audit-log/{id} — fetch single audit entry by id
# ---------------------------------------------------------------------------


@router.get("/{entry_id}", response_model=ApiResponse[AuditLogEntry])
async def get_audit_log_entry(
    entry_id: int,
    *,
    db: Db,
) -> ApiResponse[AuditLogEntry]:
    """Return a single audit log entry by its integer id.

    Raises HTTP 404 if no row with the given id exists.
    Raises HTTP 503 if the audit_log table has not yet been migrated.
    """
    pool = db.credential_shared_pool()

    try:
        row = await pool.fetchrow(
            "SELECT id, ts, actor, action, target, note, ip, request_id, "
            "metadata, result, error "
            "FROM public.audit_log "
            "WHERE id = $1",
            entry_id,
        )
    except UndefinedTableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Audit log is not available — migration core_092 may not have run",
        ) from exc

    if row is None:
        raise HTTPException(status_code=404, detail="Audit log entry not found")

    return ApiResponse[AuditLogEntry](data=AuditLogEntry.from_record(row))


# ---------------------------------------------------------------------------
# Keep AuditEntry exported for routers that still import it from here
# ---------------------------------------------------------------------------

__all__ = [
    "AuditEntry",
    "AuditLogEntry",
    "AuditTableNotAvailableError",
    "append",
    "audit_log_appended_total",
    "log_audit_entry",
    "router",
]
