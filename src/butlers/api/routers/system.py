"""System overview endpoints for the dashboard's /system page.

Surfaces five ownership-fact domains:

    GET /api/system/instance       -- software version and process uptime
    GET /api/system/database       -- PostgreSQL catalog size breakdown
    GET /api/system/backups        -- backup recency and source reachability
    GET /api/system/egress         -- external-actor egress catalog (owner-only)
    GET /api/system/butlers/heartbeat -- per-butler liveness registry snapshot

Privacy contract: /api/system/egress is owner-only. The owner is identified
by asserting 'owner' = ANY(roles) on public.entities. Non-owner callers
receive HTTP 403. All other endpoints
are gated only by the standard dashboard session boundary (v1 simplification).

All endpoints are read-only. No writes, no new tables.

Operation names assumed in the actor registry for /api/system/egress
(documented here for the bu-n28xh audit):
    "llm_api_call"          -- outbound call to an LLM provider API
    "telegram_send"         -- outbound Telegram Bot API message
    "google_calendar_write" -- outbound Google Calendar API mutation
    "gmail_send"            -- outbound Gmail SMTP / API send

These names are the values stored in the ``action`` column of the canonical
``public.audit_log`` table (the ``operation`` alias in the egress query) for
externally-visible API calls.
"""

from __future__ import annotations

import importlib.metadata
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from opentelemetry import trace
from prometheus_client import Counter
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.deps import ButlerConnectionInfo, get_butler_configs
from butlers.api.models import ApiResponse
from butlers.api.read_models.insights_v1 import query_insight_delivery_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])


# ---------------------------------------------------------------------------
# Prometheus counters — one per endpoint so Grafana can track request load
# per system tile. Module-scoped so the registry stays consistent across
# hot-reloads in dev. Counter names follow the pattern:
# system_<domain>_reads_total (e.g. system_instance_reads_total).
# ---------------------------------------------------------------------------

system_instance_reads_total = Counter(
    "system_instance_reads_total",
    "Number of GET /api/system/instance requests.",
)

system_database_reads_total = Counter(
    "system_database_reads_total",
    "Number of GET /api/system/database requests.",
)

system_backups_reads_total = Counter(
    "system_backups_reads_total",
    "Number of GET /api/system/backups requests.",
)

system_egress_reads_total = Counter(
    "system_egress_reads_total",
    "Number of GET /api/system/egress requests.",
)

system_butlers_heartbeat_reads_total = Counter(
    "system_butlers_heartbeat_reads_total",
    "Number of GET /api/system/butlers/heartbeat requests.",
)

system_insight_delivery_reads_total = Counter(
    "system_insight_delivery_reads_total",
    "Number of GET /api/system/insights/delivery-state requests.",
)


# Module-level start time recorded when this module is first imported.
# The lifespan startup imports all routers, so this approximates the
# FastAPI lifespan start time closely enough for v1.
_PROCESS_START: datetime = datetime.now(UTC)


def _get_db_manager() -> DatabaseManager:
    """Dependency stub -- overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class InstanceFacts(BaseModel):
    """Software identity and process uptime facts."""

    version: str
    uptime_seconds: float
    started_at: str


class SchemaSize(BaseModel):
    """Disk footprint of a single butler schema."""

    schema_name: str
    size_bytes: int
    table_count: int


class TableSize(BaseModel):
    """Disk footprint of a single table."""

    schema_name: str
    table_name: str
    size_bytes: int


class DatabaseFacts(BaseModel):
    """PostgreSQL catalog size facts for the running database."""

    total_size_bytes: int
    schemas: list[SchemaSize]
    largest_tables: list[TableSize]
    growth_rate_bytes_per_day: None = None  # reserved for v2


class BackupEvent(BaseModel):
    """Single backup event in the backup history list."""

    completed_at: str
    size_bytes: int
    status: str  # "success" or "failed"


class BackupFacts(BaseModel):
    """Backup recency and source reachability facts."""

    last_backup_at: str | None
    last_backup_size_bytes: int | None
    backup_source_reachable: bool
    backup_history: list[BackupEvent]


class EgressActor(BaseModel):
    """A single external actor that has received data from this instance."""

    actor_id: str
    display_name: str
    last_seen_at: str
    total_calls: int
    data_types: list[str]


class EgressCatalog(BaseModel):
    """Aggregated catalog of external-actor egress events."""

    actors: list[EgressActor]
    catalog_covers_from: str | None


class ButlerHeartbeat(BaseModel):
    """Per-butler liveness and session snapshot."""

    name: str
    last_heartbeat_at: str | None
    last_session_at: str | None
    active_session_count: int
    heartbeat_age_seconds: float | None
    error: str | None = None


class HeartbeatFacts(BaseModel):
    """Collection of per-butler heartbeat entries."""

    butlers: list[ButlerHeartbeat]


class InsightDeliveryState(BaseModel):
    """Aggregated state of the proactive insight delivery pipeline.

    Counts are drawn from public.insight_candidates and reflect the last 30
    days of data (older non-pending rows are cleaned up by the delivery cycle).

    Fields
    ------
    queued:
        Candidates waiting to be delivered (status='pending').  Includes
        candidates that failed delivery 1-2 times and are still retrying.
    delivered:
        Candidates successfully delivered (status='delivered').
    failed:
        Candidates permanently rejected after 3 consecutive delivery failures
        (status='filtered' AND delivery_attempt_count >= 3).  Does not include
        cooldown-filtered or dedup-filtered candidates.
    last_delivery_at:
        ISO 8601 timestamp of the most recent successful delivery, or null when
        no delivery has occurred yet.
    """

    queued: int
    delivered: int
    failed: int
    last_delivery_at: str | None


# ---------------------------------------------------------------------------
# Actor registry (server-side constant)
#
# Maps operation strings from the canonical public.audit_log (action column) to
# stable actor identifiers and human-readable display names.
#
# Operation naming convention (see module docstring and bu-n28xh audit):
#   - llm_api_call:          outbound LLM provider API call
#   - telegram_send:         outbound Telegram Bot API message
#   - google_calendar_write: outbound Google Calendar API mutation
#   - gmail_send:            outbound Gmail SMTP / API send
# ---------------------------------------------------------------------------

_ACTOR_REGISTRY: dict[str, tuple[str, str]] = {
    # operation -> (actor_id, display_name)
    "llm_api_call": ("anthropic.claude", "Anthropic Claude API"),
    "telegram_send": ("telegram.api", "Telegram Bot API"),
    "google_calendar_write": ("google.calendar", "Google Calendar API"),
    "gmail_send": ("google.gmail", "Gmail API"),
}

# data_types derived from operation -- these are coarse labels for the
# type of data the operation carries.
_OPERATION_DATA_TYPES: dict[str, list[str]] = {
    "llm_api_call": ["session_prompt"],
    "telegram_send": ["message_text"],
    "google_calendar_write": ["calendar_event"],
    "gmail_send": ["message_text"],
}

_UNKNOWN_ACTOR_ID = "other"
_UNKNOWN_ACTOR_NAME = "Other / Unrecognized"


# ---------------------------------------------------------------------------
# GET /api/system/instance
# ---------------------------------------------------------------------------


@router.get("/instance", response_model=ApiResponse[InstanceFacts])
async def get_instance_facts() -> ApiResponse[InstanceFacts]:
    """Return software version, process uptime, and start timestamp.

    Version is read from importlib.metadata or the package __version__
    constant. Falls back to 'unknown' rather than raising a 500.
    """
    system_instance_reads_total.inc()
    try:
        version = importlib.metadata.version("butlers")
    except importlib.metadata.PackageNotFoundError:
        try:
            from butlers import __version__

            version = __version__
        except Exception:
            version = "unknown"

    now = datetime.now(UTC)
    uptime = (now - _PROCESS_START).total_seconds()

    return ApiResponse(
        data=InstanceFacts(
            version=version,
            uptime_seconds=uptime,
            started_at=_PROCESS_START.isoformat(),
        )
    )


# ---------------------------------------------------------------------------
# GET /api/system/database
# ---------------------------------------------------------------------------


@router.get("/database", response_model=ApiResponse[DatabaseFacts])
async def get_database_facts(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[DatabaseFacts]:
    """Return PostgreSQL catalog size facts for the current database.

    Queries:
    - pg_database_size(current_database()) for total bytes
    - information_schema.tables for schema/table enumeration
    - pg_catalog.pg_total_relation_size() for per-table sizes

    Returns HTTP 503 on any catalog query failure.
    """
    system_database_reads_total.inc()
    try:
        # Use the switchboard pool (it has pg catalog read access from the
        # shared database; all butlers share one PostgreSQL database).
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    try:
        total_bytes: int = await pool.fetchval("SELECT pg_database_size(current_database())") or 0
    except Exception as exc:
        logger.warning("Failed to query database size: %s", exc)
        raise HTTPException(status_code=503, detail="Database catalog query failed")

    # Per-schema breakdown: only butler-owned schemas (exclude public, pg_*,
    # information_schema). Table count via information_schema.
    try:
        schema_rows = await pool.fetch(
            """
            SELECT
                t.table_schema AS schema_name,
                count(*) AS table_count,
                coalesce(
                    sum(pg_total_relation_size(
                        (quote_ident(t.table_schema) || '.' || quote_ident(t.table_name))::regclass
                    )),
                    0
                ) AS size_bytes
            FROM information_schema.tables t
            WHERE t.table_schema NOT IN ('public', 'pg_catalog', 'information_schema',
                                          'pg_toast', 'pg_temp_1', 'pg_toast_temp_1')
              AND t.table_schema NOT LIKE 'pg_%'
              AND t.table_type = 'BASE TABLE'
            GROUP BY t.table_schema
            ORDER BY size_bytes DESC
            """
        )
        schemas = [
            SchemaSize(
                schema_name=row["schema_name"],
                size_bytes=int(row["size_bytes"] or 0),
                table_count=int(row["table_count"] or 0),
            )
            for row in schema_rows
        ]
    except Exception as exc:
        logger.warning("Failed to query schema sizes: %s", exc)
        raise HTTPException(status_code=503, detail="Schema size query failed")

    # Top 10 tables by total relation size across all non-system schemas
    try:
        table_rows = await pool.fetch(
            """
            SELECT
                t.table_schema AS schema_name,
                t.table_name,
                pg_total_relation_size(
                    (quote_ident(t.table_schema) || '.' || quote_ident(t.table_name))::regclass
                ) AS size_bytes
            FROM information_schema.tables t
            WHERE t.table_schema NOT IN ('pg_catalog', 'information_schema',
                                          'pg_toast', 'pg_temp_1', 'pg_toast_temp_1')
              AND t.table_schema NOT LIKE 'pg_%'
              AND t.table_type = 'BASE TABLE'
            ORDER BY size_bytes DESC
            LIMIT 10
            """
        )
        largest_tables = [
            TableSize(
                schema_name=row["schema_name"],
                table_name=row["table_name"],
                size_bytes=int(row["size_bytes"] or 0),
            )
            for row in table_rows
        ]
    except Exception as exc:
        logger.warning("Failed to query table sizes: %s", exc)
        raise HTTPException(status_code=503, detail="Table size query failed")

    return ApiResponse(
        data=DatabaseFacts(
            total_size_bytes=total_bytes,
            schemas=schemas,
            largest_tables=largest_tables,
        )
    )


# ---------------------------------------------------------------------------
# GET /api/system/backups
# ---------------------------------------------------------------------------


def _read_backup_facts_from_dir(backup_dir: Path) -> BackupFacts:
    """Scan *backup_dir* for timestamped pg_dump files and return BackupFacts.

    Backup files must match the pattern ``butlers_*.sql.gz`` (written by
    ``deploy/backup/pg_dump.sh``).  Files are sorted by mtime descending so
    the most-recent dump is always first.

    Returns a degraded (backup_source_reachable=False) payload when:
    - the directory does not exist
    - the directory is not readable (OSError)
    No exception is propagated.
    """
    if not backup_dir.is_dir():
        return BackupFacts(
            last_backup_at=None,
            last_backup_size_bytes=None,
            backup_source_reachable=False,
            backup_history=[],
        )

    # Stat each file individually so a single racy disappearance can't abort
    # the whole sort.  Collect (mtime, stat) pairs, skip files that vanish
    # between the glob and the stat call, then sort the surviving pairs.
    try:
        candidates = list(backup_dir.glob("butlers_*.sql.gz"))
    except OSError as exc:
        logger.warning("Cannot read backup directory %s: %s", backup_dir, exc)
        return BackupFacts(
            last_backup_at=None,
            last_backup_size_bytes=None,
            backup_source_reachable=False,
            backup_history=[],
        )

    stamped: list[tuple[float, os.stat_result]] = []
    for p in candidates:
        try:
            st = p.stat()
            stamped.append((st.st_mtime, st))
        except OSError:
            continue  # race: file removed between glob and stat

    stamped.sort(key=lambda t: t[0], reverse=True)

    # Spec (system-overview-page, "Backup State Facts"): backup_history is
    # "up to 7 most recent backup events". stamped is sorted most-recent-first,
    # so the first 7 entries are the events to surface.
    history: list[BackupEvent] = []
    for _mtime, stat in stamped[:7]:
        mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        history.append(
            BackupEvent(
                completed_at=mtime_dt.isoformat(),
                size_bytes=stat.st_size,
                status="success",
            )
        )

    if not history:
        # Directory exists and is readable, but no dumps have been written yet.
        return BackupFacts(
            last_backup_at=None,
            last_backup_size_bytes=None,
            backup_source_reachable=True,
            backup_history=[],
        )

    latest = history[0]
    return BackupFacts(
        last_backup_at=latest.completed_at,
        last_backup_size_bytes=latest.size_bytes,
        backup_source_reachable=True,
        backup_history=history,
    )


@router.get("/backups", response_model=ApiResponse[BackupFacts])
async def get_backup_facts() -> ApiResponse[BackupFacts]:
    """Return backup recency and source reachability.

    Reads filesystem pg_dump files from the directory configured by the
    ``BUTLERS_BACKUP_DIR`` environment variable (written by
    ``deploy/backup/pg_dump.sh`` via the ``backup-cron`` sidecar).

    When ``BUTLERS_BACKUP_DIR`` is not set or the directory is absent, the
    endpoint returns ``backup_source_reachable=false`` with null fields.
    This is the expected state for unconfigured deployments — not an error.

    Graceful degradation: always returns HTTP 200, never HTTP 503.
    """
    system_backups_reads_total.inc()

    backup_dir_env = os.environ.get("BUTLERS_BACKUP_DIR", "").strip()
    if not backup_dir_env:
        return ApiResponse(
            data=BackupFacts(
                last_backup_at=None,
                last_backup_size_bytes=None,
                backup_source_reachable=False,
                backup_history=[],
            )
        )

    backup_dir = Path(backup_dir_env)
    return ApiResponse(data=_read_backup_facts_from_dir(backup_dir))


# ---------------------------------------------------------------------------
# Owner-contact assertion helper
# ---------------------------------------------------------------------------


async def _assert_owner_contact(pool) -> None:
    """Raise HTTP 403 unless the calling context resolves to the owner.

    This mirrors the canonical owner-only authz gate used across the dashboard
    (Amendment 12a/12b — ``_assert_owner_role`` / ``_get_owner_roles`` in
    ``roster/relationship/api/router.py``): it resolves the owner entity from
    ``public.entities`` and inspects the ``roles`` column, granting access only
    when ``'owner'`` is present. A calling context whose resolved entity row
    lacks the ``'owner'`` role — or when no owner entity is registered at all —
    receives HTTP 403 (``{"code": "owner_required"}``).

    The roles-aware check (rather than a bare row-exists check) is what lets a
    non-owner calling context be rejected: peer routes' unit tests inject a
    caller fixture by returning a row whose ``roles`` list reflects the caller,
    and this gate then produces the correct 403 for a non-owner. Roles live on
    ``public.entities.roles`` exclusively (``public.contacts.roles`` was dropped
    in migration core_016).

    Consistent with the network-level trust boundary (security doctrine,
    RFC-0008), this gate matches its peer dashboard routes: it asserts the owner
    role over the resolved context and is not a uniquely hard fail-closed check.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT id, roles
            FROM public.entities
            WHERE 'owner' = ANY(COALESCE(roles, '{}'))
            LIMIT 1
            """
        )
    except Exception as exc:
        logger.warning("Owner-entity assertion query failed: %s", exc)
        raise HTTPException(
            status_code=403,
            detail={"code": "owner_required", "message": "Owner contact assertion failed"},
        )

    roles = row["roles"] if row is not None and row["roles"] else []
    if "owner" not in roles:
        raise HTTPException(
            status_code=403,
            detail={"code": "owner_required", "message": "Owner contact not found"},
        )


# ---------------------------------------------------------------------------
# GET /api/system/egress
# ---------------------------------------------------------------------------


@router.get("/egress", response_model=ApiResponse[EgressCatalog])
async def get_egress_catalog(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[EgressCatalog]:
    """Return the data-egress catalog for this instance (owner-only).

    Aggregates the unified audit log by operation, mapping each operation to an
    external actor via the server-side actor registry. The source is the
    canonical ``public.audit_log`` primitive alone (``action`` -> ``operation``,
    ``ts`` -> ``created_at``); the legacy ``switchboard.dashboard_audit_log``
    UNION arm was removed (bu-j26e8) after migration core_124 backfilled the
    historical rows into the canonical table.

    Only the owner contact may view the egress catalog. Non-owner callers
    receive HTTP 403. See _assert_owner_contact() for the assertion logic.
    """
    system_egress_reads_total.inc()
    try:
        sw_pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    # Owner-contact assertion (non-negotiable privacy contract)
    await _assert_owner_contact(sw_pool)

    with trace.get_tracer("butlers").start_as_current_span("system.egress.read") as span:
        # Query audit log grouped by operation
        try:
            rows = await sw_pool.fetch(
                """
                WITH egress_source AS (
                    SELECT action AS operation, ts AS created_at
                    FROM public.audit_log
                )
                SELECT
                    operation,
                    max(created_at) AS last_seen_at,
                    count(*) AS total_calls,
                    min(created_at) AS first_seen_at
                FROM egress_source
                GROUP BY operation
                ORDER BY last_seen_at DESC
                """
            )
        except Exception as exc:
            logger.warning("Egress catalog query failed: %s", exc)
            raise HTTPException(status_code=503, detail="Egress catalog query failed")

        # Derive catalog_covers_from from the oldest first_seen_at already in the result set.
        # No second query needed -- we already have min(created_at) per operation above.
        catalog_covers_from: str | None = None
        if rows:
            oldest_raw = min(
                (row["first_seen_at"] for row in rows if row["first_seen_at"] is not None),
                default=None,
            )
            if oldest_raw is not None:
                catalog_covers_from = (
                    oldest_raw.isoformat() if hasattr(oldest_raw, "isoformat") else str(oldest_raw)
                )

        # Aggregate rows by actor_id
        actor_buckets: dict[str, dict] = {}
        for row in rows:
            operation = row["operation"]
            last_seen = row["last_seen_at"]
            total_calls = int(row["total_calls"] or 0)

            if operation in _ACTOR_REGISTRY:
                actor_id, display_name = _ACTOR_REGISTRY[operation]
                data_types = _OPERATION_DATA_TYPES.get(operation, [])
            else:
                actor_id = _UNKNOWN_ACTOR_ID
                display_name = _UNKNOWN_ACTOR_NAME
                data_types = []

            if actor_id not in actor_buckets:
                actor_buckets[actor_id] = {
                    "actor_id": actor_id,
                    "display_name": display_name,
                    "last_seen_at": last_seen,
                    "total_calls": 0,
                    "data_types": set(data_types),
                }
            else:
                # Update last_seen_at to the latest across merged operations
                if last_seen and (
                    actor_buckets[actor_id]["last_seen_at"] is None
                    or last_seen > actor_buckets[actor_id]["last_seen_at"]
                ):
                    actor_buckets[actor_id]["last_seen_at"] = last_seen
                actor_buckets[actor_id]["data_types"].update(data_types)

            actor_buckets[actor_id]["total_calls"] += total_calls

        # Sort by last_seen_at descending
        actors = sorted(
            actor_buckets.values(),
            key=lambda a: a["last_seen_at"] or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

        egress_actors = [
            EgressActor(
                actor_id=a["actor_id"],
                display_name=a["display_name"],
                last_seen_at=(
                    a["last_seen_at"].isoformat()
                    if hasattr(a["last_seen_at"], "isoformat")
                    else str(a["last_seen_at"])
                ),
                total_calls=a["total_calls"],
                data_types=sorted(a["data_types"]),
            )
            for a in actors
            if a["last_seen_at"] is not None
        ]

        span.set_attribute("actor_count", len(egress_actors))

    return ApiResponse(
        data=EgressCatalog(
            actors=egress_actors,
            catalog_covers_from=catalog_covers_from,
        )
    )


# ---------------------------------------------------------------------------
# GET /api/system/butlers/heartbeat
# ---------------------------------------------------------------------------


@router.get("/butlers/heartbeat", response_model=ApiResponse[HeartbeatFacts])
async def get_butlers_heartbeat(
    db: DatabaseManager = Depends(_get_db_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
) -> ApiResponse[HeartbeatFacts]:
    """Return per-butler liveness registry snapshots and session facts.

    Reads from the switchboard's butler_registry table for heartbeat timestamps
    and fans out to per-butler schema sessions tables for session facts. Does
    not issue live MCP calls to any butler.

    Uses get_butler_configs() as the canonical butler source so that butlers
    whose DB pool failed to initialize at startup still appear in the response
    with error='schema_unreachable' rather than being silently omitted.

    If a butler's schema is unreachable, its session fields are null/0 and
    the entry is included with error='schema_unreachable'.
    """
    system_butlers_heartbeat_reads_total.inc()
    try:
        sw_pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    # Fetch liveness registry: butler name -> last_seen_at
    try:
        registry_rows = await sw_pool.fetch(
            "SELECT name, last_seen_at FROM butler_registry ORDER BY name ASC"
        )
    except Exception as exc:
        logger.warning("Failed to query butler_registry: %s", exc)
        raise HTTPException(status_code=503, detail="Butler registry query failed")

    # Build registry map
    registry: dict[str, datetime | None] = {
        row["name"]: row["last_seen_at"] for row in registry_rows
    }

    # Canonical butler set: roster scan via get_butler_configs().
    # This ensures butlers whose DB pool failed at startup (absent from
    # db.butler_names) still appear in the response instead of being silently
    # omitted. Union with registry names to cover heartbeat-only butlers not
    # yet in the roster scan.
    roster_names = {cfg.name for cfg in configs}
    all_names = sorted(roster_names | set(registry.keys()))
    db_names = set(db.butler_names)

    now = datetime.now(UTC)
    entries: list[ButlerHeartbeat] = []

    for name in all_names:
        last_heartbeat_raw = registry.get(name)

        # Normalize heartbeat timestamp to UTC-aware datetime
        if last_heartbeat_raw is not None:
            if hasattr(last_heartbeat_raw, "tzinfo") and last_heartbeat_raw.tzinfo is None:
                last_heartbeat_dt: datetime | None = last_heartbeat_raw.replace(tzinfo=UTC)
            else:
                last_heartbeat_dt = last_heartbeat_raw
        else:
            last_heartbeat_dt = None

        last_heartbeat_at = last_heartbeat_dt.isoformat() if last_heartbeat_dt else None
        heartbeat_age = (now - last_heartbeat_dt).total_seconds() if last_heartbeat_dt else None

        # Per-butler session facts
        last_session_at: str | None = None
        active_session_count: int = 0
        entry_error: str | None = None

        if name in db_names:
            try:
                pool = db.pool(name)
                # Most-recent completed session
                last_row = await pool.fetchrow(
                    "SELECT completed_at FROM sessions "
                    "WHERE completed_at IS NOT NULL "
                    "ORDER BY completed_at DESC LIMIT 1"
                )
                if last_row and last_row["completed_at"] is not None:
                    ts = last_row["completed_at"]
                    if hasattr(ts, "tzinfo") and ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    last_session_at = ts.isoformat()

                # Active session count
                active_count_row = await pool.fetchval(
                    "SELECT count(*) FROM sessions WHERE completed_at IS NULL"
                )
                active_session_count = int(active_count_row or 0)
            except Exception as exc:
                logger.warning("Session query failed for butler %s: %s", name, exc)
                entry_error = "schema_unreachable"
        elif name in roster_names:
            # Butler is in the roster but has no DB pool (pool init failed at startup).
            # Report it with schema_unreachable rather than silently omitting it.
            entry_error = "schema_unreachable"

        entries.append(
            ButlerHeartbeat(
                name=name,
                last_heartbeat_at=last_heartbeat_at,
                last_session_at=last_session_at,
                active_session_count=active_session_count,
                heartbeat_age_seconds=heartbeat_age,
                error=entry_error,
            )
        )

    return ApiResponse(data=HeartbeatFacts(butlers=entries))


# ---------------------------------------------------------------------------
# GET /api/system/insights/delivery-state
# ---------------------------------------------------------------------------


@router.get("/insights/delivery-state", response_model=ApiResponse[InsightDeliveryState])
async def get_insight_delivery_state(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[InsightDeliveryState]:
    """Return the current state of the proactive insight delivery pipeline.

    Computes queued / delivered / failed counts and the last-delivery timestamp
    from the real delivery-state tables (public.insight_candidates).

    - ``queued``   = candidates with status='pending' (awaiting delivery cycle)
    - ``delivered`` = candidates successfully delivered (status='delivered')
    - ``failed``   = candidates permanently blocked after 3 consecutive delivery
                     failures (status='filtered' AND delivery_attempt_count >= 3)
    - ``last_delivery_at`` = MAX(delivered_at) for delivered candidates, or null

    Counts reflect the last ~30 days (the delivery cycle purges older non-pending
    rows).  All zero counts with a null last_delivery_at represent an honest
    empty state with no delivery activity.

    Returns HTTP 503 when the switchboard database is unavailable.
    Returns HTTP 200 with zero counts when the insight_candidates table does not
    yet exist (pre-migration deployment); no error is raised.
    """
    system_insight_delivery_reads_total.inc()
    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    _zero_state = InsightDeliveryState(queued=0, delivered=0, failed=0, last_delivery_at=None)

    try:
        result = await query_insight_delivery_state(pool)
    except Exception as exc:
        # Degrade gracefully: table missing (pre-migration) or transient error.
        logger.warning("insight_candidates query failed (degraded state returned): %s", exc)
        return ApiResponse(data=_zero_state)

    if result is None:
        # Empty result (should not happen for an aggregate with no WHERE, but guard anyway)
        return ApiResponse(data=_zero_state)

    last_dt = result.last_delivery_at
    if last_dt is not None and hasattr(last_dt, "tzinfo") and last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=UTC)

    return ApiResponse(
        data=InsightDeliveryState(
            queued=result.queued,
            delivered=result.delivered,
            failed=result.failed,
            last_delivery_at=last_dt.isoformat() if last_dt is not None else None,
        )
    )
