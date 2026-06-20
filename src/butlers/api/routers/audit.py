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
``append`` inserts into the ``public.audit_log`` table (the canonical audit
primitive introduced in core_092).  It returns the new row id and increments
the ``audit_log_appended_total{action}`` Prometheus counter.

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
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

import asyncpg
from asyncpg.exceptions import UndefinedTableError
from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.models.audit import AuditEntry, AuditLogEntry
from butlers.core.credential_keys import normalize_key_param
from butlers.metrics_registry import get_or_create_counter

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit-log", tags=["audit"])

# ---------------------------------------------------------------------------
# Custom exception — raised by append() when the table is not yet migrated
# ---------------------------------------------------------------------------


class AuditTableNotAvailableError(Exception):
    """Raised by ``append()`` when ``public.audit_log`` does not exist.

    Callers (HTTP endpoints) should propagate this as HTTP 503 so that
    missing-table conditions surface explicitly rather than silently failing.
    """


# ---------------------------------------------------------------------------
# Prometheus counter — incremented per successful append to public.audit_log
# ---------------------------------------------------------------------------

audit_log_appended_total = get_or_create_counter(
    "audit_log_appended_total",
    "Number of rows appended to public.audit_log, partitioned by action.",
    labelnames=["action"],
)


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


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
# New primitive helper: append to public.audit_log
# ---------------------------------------------------------------------------


async def append(
    pool: asyncpg.Pool | asyncpg.Connection,
    actor: str,
    action: str,
    *,
    target: str | None = None,
    note: str | None = None,
    ip: str | None = None,
    request_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
    result: str | None = None,
    error: str | None = None,
) -> int:
    """Append one row to ``public.audit_log`` and return the new row id.

    Increments ``audit_log_appended_total{action}`` on success.

    Accepts either an asyncpg pool or an already-acquired connection so that
    callers can run the audit insert inside the same SQL transaction as the
    state change being audited (§D17 atomicity requirement).

    Parameters
    ----------
    pool:
        asyncpg connection pool **or** an existing asyncpg connection.  Pass a
        connection when the caller needs the audit insert to participate in the
        same open transaction (atomicity).
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
    metadata:
        Optional structured context dict persisted to the ``metadata`` JSONB
        column (core_122).  Non-JSON-safe values (UUID, datetime, …) are
        coerced to strings before storage.  ``None`` stores SQL ``NULL``.
    result:
        Optional outcome label persisted to the ``result`` column (core_122),
        e.g. ``"success"`` or ``"error"``.
    error:
        Optional error message persisted to the ``error`` column (core_122);
        only meaningful when *result* denotes a failure.

    The three core_122 parameters are keyword-only and default to ``None`` so
    every existing caller is unaffected.

    Returns
    -------
    int
        The ``id`` of the newly-inserted row.

    Raises
    ------
    AuditTableNotAvailableError
        When ``public.audit_log`` does not exist (migration not yet applied).
        Callers should propagate this as HTTP 503.
    """
    # Serialise metadata to a JSON string and cast with ``$N::jsonb`` so the
    # insert does not depend on a JSONB codec being registered on the pool /
    # connection the caller hands us.  ``None`` stays ``None`` → SQL NULL.
    metadata_json = json.dumps(metadata, default=str) if metadata is not None else None

    try:
        row_id: int = await pool.fetchval(
            "INSERT INTO public.audit_log "
            "(actor, action, target, note, ip, request_id, metadata, result, error) "
            "VALUES ($1, $2, $3, $4, $5::inet, $6, $7::jsonb, $8, $9) "
            "RETURNING id",
            actor,
            action,
            target,
            note,
            ip,
            request_id,
            metadata_json,
            result,
            error,
        )
    except UndefinedTableError as exc:
        raise AuditTableNotAvailableError(
            "public.audit_log is not available — migration core_092 may not have run"
        ) from exc
    audit_log_appended_total.labels(action=action).inc()
    return row_id


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
    kind: str | None = Query(
        None,
        description=(
            "Filter preset. 'privileged' excludes high-frequency operational noise "
            "(*_heartbeat actions and routine GET-path traffic), surfacing only "
            "mutation/security rows (permission.set, data.*, webhook.*, etc.)."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[AuditLogEntry]:
    """Return paginated audit log entries from ``public.audit_log``.

    Supports filtering by actor, action, a lower-bound timestamp, and
    credential key (``?key=``).  Results are ordered by ``ts DESC`` (newest
    first).

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

    # kind=privileged: exclude high-frequency operational noise.
    # Filters out actions ending in _heartbeat (butler/switchboard heartbeats)
    # and actions starting with "GET /" (routine HTTP-GET audit entries).
    if kind == "privileged":
        conditions.append("action NOT LIKE '%_heartbeat'")
        conditions.append("action NOT LIKE 'GET /%'")

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    # Count query — canonical source is the single source of truth (bu-j26e8;
    # the historical dashboard_audit_log rows were backfilled by core_124).
    count_sql = f"SELECT count(*) FROM public.audit_log{where_clause}"
    try:
        total = await pool.fetchval(count_sql, *args) or 0
    except UndefinedTableError:
        raise HTTPException(
            status_code=503,
            detail="Audit log is not available — migration core_092 may not have run",
        )

    # Paged data query — order by ts DESC, slice with LIMIT/OFFSET directly
    # against the canonical table (no cross-source merge).
    data_sql = (
        f"SELECT id, ts, actor, action, target, note, ip, request_id "
        f"FROM public.audit_log{where_clause} "
        f"ORDER BY ts DESC "
        f"LIMIT ${idx} OFFSET ${idx + 1}"
    )

    try:
        rows = await pool.fetch(data_sql, *args, limit, offset)
    except UndefinedTableError:
        raise HTTPException(
            status_code=503,
            detail="Audit log is not available — migration core_092 may not have run",
        )

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
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[AuditLogEntry]:
    """Return a single audit log entry by its integer id.

    Raises HTTP 404 if no row with the given id exists.
    Raises HTTP 503 if the audit_log table has not yet been migrated.
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
        raise HTTPException(
            status_code=503,
            detail="Audit log is not available — migration core_092 may not have run",
        )

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
