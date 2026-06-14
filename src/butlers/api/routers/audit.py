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
from typing import TYPE_CHECKING, Any

import asyncpg
from asyncpg.exceptions import UndefinedTableError
from fastapi import APIRouter, Depends, HTTPException, Query
from prometheus_client import Counter

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.models.audit import AuditEntry, AuditLogEntry
from butlers.core.credential_keys import normalize_key_param

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

audit_log_appended_total = Counter(
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
# Legacy dashboard_audit_log → AuditLogEntry normalisation (bu-isi4i)
# ---------------------------------------------------------------------------
#
# Several mutation sites (runtime_config, schedules, state, calendar workspace,
# the dashboard audit middleware) write ONLY to the Switchboard butler's
# ``dashboard_audit_log`` table via ``log_audit_entry`` / ``emit_dashboard_audit``.
# The /audit-log read endpoint reads ``public.audit_log`` and so those mutations
# were invisible.  Rather than migrate every writer (broad, high-regression),
# the read endpoint UNIONs both sources: it normalises ``dashboard_audit_log``
# rows into the ``AuditLogEntry`` shape so they appear alongside the canonical
# ``public.audit_log`` rows.  Writers are untouched (lowest regression).


def _coerce_uuid(value: object) -> uuid.UUID | None:
    """Best-effort coerce a value to a UUID, returning None on failure."""
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


def _legacy_synthetic_id(row_id: object) -> int:
    """Derive a stable, collision-free synthetic int id for a legacy row.

    ``public.audit_log`` ids are positive ``BIGSERIAL`` values.  Legacy
    ``dashboard_audit_log`` rows are keyed by UUID, which has no int form, so we
    map each UUID to a *negative* int (deterministic per UUID).  Negative ids
    never collide with the positive ids of the canonical table and remain stable
    across requests, so the frontend (which uses ``id`` only as a React key /
    expand toggle) renders both sources without conflict.  The per-id endpoint
    ``GET /api/audit-log/{id}`` still resolves only positive ids against
    ``public.audit_log``; legacy rows are list-only by design.
    """
    parsed = _coerce_uuid(row_id)
    if parsed is None:
        # Fall back to a hash of the stringified id; still negative + stable.
        seed = abs(hash(str(row_id)))
    else:
        seed = parsed.int
    # Keep within signed 63-bit range, then negate (offset by 1 so 0 -> -1).
    return -((seed % ((1 << 62) - 1)) + 1)


def _normalize_dashboard_row(row: Any) -> AuditLogEntry:
    """Map a ``dashboard_audit_log`` record into an ``AuditLogEntry``.

    Field mapping
    -------------
    - ``id``     → deterministic negative synthetic int (see ``_legacy_synthetic_id``)
    - ``ts``     ← ``created_at``
    - ``actor``  ← ``user_context.principal`` (falls back to ``butler``)
    - ``action`` ← ``operation``
    - ``target`` ← ``request_summary.path`` when present
    - ``note``   ← compact ``result``/``error`` summary
    - ``ip``     ← ``user_context.client_ip`` / ``forwarded_for`` when present
    - ``request_id`` ← ``request_summary.trace_id`` when it parses as a UUID
    """
    user_context = row["user_context"] or {}
    request_summary = row["request_summary"] or {}
    if not isinstance(user_context, dict):
        user_context = {}
    if not isinstance(request_summary, dict):
        request_summary = {}

    butler = row["butler"]
    actor = user_context.get("principal") or butler

    ip_value = user_context.get("client_ip") or user_context.get("forwarded_for")
    ip_str = str(ip_value) if ip_value else None

    target = request_summary.get("path")
    target_str = str(target) if target else None

    result = row["result"]
    error = row["error"]
    if error:
        note = f"{result}: {error}"
    else:
        note = result or None

    request_id = _coerce_uuid(request_summary.get("trace_id"))

    return AuditLogEntry(
        id=_legacy_synthetic_id(row["id"]),
        ts=row["created_at"],
        actor=actor,
        action=row["operation"],
        target=target_str,
        note=note,
        ip=ip_str,
        request_id=request_id,
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

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    # Count query (canonical source)
    count_sql = f"SELECT count(*) FROM public.audit_log{where_clause}"
    try:
        canonical_total = await pool.fetchval(count_sql, *args) or 0
    except UndefinedTableError:
        raise HTTPException(
            status_code=503,
            detail="Audit log is not available — migration core_092 may not have run",
        )

    # Canonical data query.  We over-fetch (OFFSET 0, LIMIT offset+limit) so that
    # after merging with the legacy source below we have enough candidates to
    # slice the correct page.  Per-source ordering is ts DESC.
    head = offset + limit
    canonical_sql = (
        f"SELECT id, ts, actor, action, target, note, ip, request_id "
        f"FROM public.audit_log{where_clause} "
        f"ORDER BY ts DESC "
        f"LIMIT ${idx}"
    )

    try:
        canonical_rows = await pool.fetch(canonical_sql, *args, head)
    except UndefinedTableError:
        raise HTTPException(
            status_code=503,
            detail="Audit log is not available — migration core_092 may not have run",
        )

    entries = [AuditLogEntry.from_record(row) for row in canonical_rows]

    # ------------------------------------------------------------------
    # Legacy source: switchboard.dashboard_audit_log (bu-isi4i).
    #
    # Mutations from runtime_config / schedules / state / calendar workspace /
    # the dashboard audit middleware write only here.  We fold them into the
    # same result set so they become visible on /audit-log.  The legacy table
    # has no ``target`` column, so the credential-key filter (``?key=``) can
    # never match a legacy row — when ?key= is set we skip this source entirely
    # to preserve the credential-key filter's exact semantics.
    # ------------------------------------------------------------------
    legacy_total = 0
    if normalised_key is None:
        legacy_total, legacy_entries = await _fetch_legacy_entries(
            db,
            since=since,
            actor=actor,
            action=action,
            limit=head,
        )
        entries.extend(legacy_entries)

    # Merge: order the combined set by ts DESC, then slice the requested page.
    entries.sort(key=lambda e: e.ts, reverse=True)
    page = entries[offset : offset + limit]

    return PaginatedResponse[AuditLogEntry](
        data=page,
        meta=PaginationMeta(total=canonical_total + legacy_total, offset=offset, limit=limit),
    )


async def _fetch_legacy_entries(
    db: DatabaseManager,
    *,
    since: datetime | None,
    actor: str | None,
    action: str | None,
    limit: int,
) -> tuple[int, list[AuditLogEntry]]:
    """Fetch + normalise rows from ``switchboard.dashboard_audit_log``.

    Returns ``(total, entries)``.  Degrades gracefully to ``(0, [])`` when the
    switchboard pool or table is unavailable so the canonical source still
    renders.  Filters are mapped onto the legacy column shape:

    - ``since``  → ``created_at >= $``
    - ``action`` → ``operation = $``   (operation is the legacy action label)
    - ``actor``  → ``butler = $`` OR ``user_context->>'principal' = $``
    """
    try:
        sw_pool = db.pool("switchboard")
    except KeyError:
        return 0, []

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if since is not None:
        conditions.append(f"created_at >= ${idx}")
        args.append(since)
        idx += 1

    if action is not None:
        conditions.append(f"operation = ${idx}")
        args.append(action)
        idx += 1

    if actor is not None:
        conditions.append(f"(butler = ${idx} OR user_context->>'principal' = ${idx})")
        args.append(actor)
        idx += 1

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    count_sql = f"SELECT count(*) FROM dashboard_audit_log{where_clause}"
    data_sql = (
        f"SELECT id, butler, operation, request_summary, result, error, "
        f"user_context, created_at "
        f"FROM dashboard_audit_log{where_clause} "
        f"ORDER BY created_at DESC "
        f"LIMIT ${idx}"
    )

    try:
        total = await sw_pool.fetchval(count_sql, *args) or 0
        rows = await sw_pool.fetch(data_sql, *args, limit)
    except UndefinedTableError:
        # Legacy table not migrated (e.g. switchboard not provisioned) — the
        # canonical source still renders.
        return 0, []

    return total, [_normalize_dashboard_row(row) for row in rows]


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
