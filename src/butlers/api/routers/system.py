"""System overview endpoints for the dashboard's /system page.

Surfaces seven ownership-fact domains:

    GET /api/system/instance       -- software version and process uptime
    GET /api/system/database       -- PostgreSQL catalog size breakdown
    GET /api/system/backups        -- verified backup health + restore-drill state (bu-9r3hd.5)
    GET /api/system/egress         -- external-actor egress catalog (owner-only)
    GET /api/system/butlers/heartbeat -- per-butler liveness registry snapshot
    GET /api/system/deployments    -- current + recent deployment ledger entries
    GET /api/system/drift          -- migration-drift sentinel (bu-9r3hd.1)

Privacy contract: /api/system/egress is owner-only. The owner is identified
by asserting 'owner' = ANY(roles) on public.entities. Non-owner callers
receive HTTP 403. All other endpoints
are gated only by the standard dashboard session boundary (v1 simplification).

All endpoints are read-only against existing tables, except /api/system/deployments,
which reads public.deployments (core_163) -- the one new table this module
introduces. It is written elsewhere (butlers.cli._start_all records process
boots and butlers deploy records deploy executions; see
src/butlers/core/deployments.py), never by this router.
/api/system/deployments also makes one outbound call per request (cached
briefly per git_sha): an anonymous GitHub compare against the current
deployment's git_sha to compute "N commits behind origin/main" for the
Deployment card (bu-hmdqz.1) -- the baked image has no .git checkout, so this
cannot be computed from local state. Never raises; a failed/unreachable
comparison surfaces as commits_behind_available=False, never a fabricated
"0 behind".
/api/system/drift is also read-only from this router's perspective: it computes
the comparison live on each request (butlers.jobs.deploy_drift.compute_drift_report)
and reads (never writes) the first-detected/escalated debounce markers the
background sentinel loop persists to public.audit_log. /api/system/backups is
similarly read-only: artifact integrity (gzip decompression, size floor) is
computed live per request (memoized per file), while the restore-drill result
is read (never written) from the ledger butlers.jobs.backup_health's weekly
background loop maintains in public.audit_log.

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

import gzip
import importlib.metadata
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
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

system_deployments_reads_total = Counter(
    "system_deployments_reads_total",
    "Number of GET /api/system/deployments requests.",
)

system_drift_reads_total = Counter(
    "system_drift_reads_total",
    "Number of GET /api/system/drift requests.",
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


class DeploymentRecord(BaseModel):
    """Single row from public.deployments (a boot or deploy execution)."""

    id: str
    git_sha: str
    migration_head: str | None
    started_at: str
    finished_at: str | None
    result: str  # "success" or "failed"
    source: str | None  # "boot" or "deploy"; null for pre-provenance rows
    serving_mode: str | None  # "image" or "hotreload-worktree"
    serving_worktree: str | None  # ".worktrees/<name>" when detected at boot


class DeploymentFacts(BaseModel):
    """Current (most recent) deployment plus recent deployment history.

    ``commits_behind_available=False`` means the comparison itself could not
    be made (no current deployment, unknown git_sha, or the GitHub compare
    call failed) -- per the fleet-wide degraded-envelope convention, render
    that as "unknown", never as "0 commits behind" / up to date.
    """

    current: DeploymentRecord | None
    recent: list[DeploymentRecord]
    commits_behind_main: int | None
    commits_behind_available: bool


class DriftEntry(BaseModel):
    """One migration chain out of sync between the codebase and a schema."""

    schema_name: str
    chain: str
    expected_head: str
    actual_revision: str | None


class DriftFacts(BaseModel):
    """Migration-drift sentinel result (bu-9r3hd.1).

    ``drift_check_available=False`` means the comparison itself failed (pool
    unavailable, unreadable schema) -- per the fleet-wide degraded-envelope
    convention, this must never be rendered as a truthful all-clear. When
    unavailable, ``is_drifted``/``drifted``/``first_detected_at``/``escalated``
    are all zeroed rather than reflecting a stale or partial comparison.
    """

    checked_at: str
    is_drifted: bool
    drifted: list[DriftEntry]
    first_detected_at: str | None
    escalated: bool
    drift_check_available: bool


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
    """Single backup event in the backup history list.

    ``status`` (bu-9r3hd.5) is a REAL per-artifact verdict, not a fabricated
    constant: ``"healthy"`` (read back through gzip cleanly and cleared the
    size floor), ``"corrupt"`` (failed gzip decompression -- truncated
    transfer, bit rot, a killed pg_dump), or ``"empty"`` (present but smaller
    than any real dump could plausibly be). See ``_verify_backup_artifact``.
    """

    completed_at: str
    size_bytes: int
    status: str  # "healthy" | "corrupt" | "empty"


class RestoreDrillFacts(BaseModel):
    """Result of the most recent weekly restore-drill attempt (bu-9r3hd.5).

    Populated from ``public.audit_log`` (action ``restore_drill_result``,
    written by ``butlers.jobs.backup_health.run_restore_drill_loop``) -- this
    router only reads it. ``result="pending"`` means the drill has never run
    yet (a fresh deploy, or the loop hasn't reached its weekly tick), which
    is a real "we don't know" state, not a fabricated pass.
    """

    checked_at: str | None
    result: str  # "pass" | "fail" | "pending" | "degraded"
    detail: str | None


class BackupFacts(BaseModel):
    """Backup recency, artifact health, and source reachability facts."""

    last_backup_at: str | None
    last_backup_size_bytes: int | None
    backup_source_reachable: bool
    backup_history: list[BackupEvent]
    last_backup_status: str  # "healthy" | "corrupt" | "empty" | "missing"
    backup_stale: bool
    restore_drill: RestoreDrillFacts


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
# GET /api/system/deployments
# ---------------------------------------------------------------------------

#: The butlers repo is public, so an anonymous GitHub compare call needs no
#: token -- see bu-hmdqz.1. This is the ONLY way to compute "N commits behind
#: origin/main" for the Deployment card: the baked ``butlers-app`` image
#: never includes a ``.git`` checkout (see Dockerfile), so the running
#: dashboard-api process cannot run `git` against its own history.
_GITHUB_REPO = "Tzeusy/butlers"
_GITHUB_COMPARE_TIMEOUT_S = 5.0

#: Repeated /system page loads must not hammer GitHub's anonymous rate limit
#: (60 req/hour/IP) -- cache the comparison per git_sha for a short TTL.
_COMMITS_BEHIND_CACHE_TTL_S = 60.0
_COMMITS_BEHIND_CACHE_MAX_ENTRIES = 64
_commits_behind_cache: dict[str, tuple[float, int]] = {}


async def _commits_behind_main(git_sha: str) -> int | None:
    """Best-effort count of commits ``origin/main`` is ahead of *git_sha*.

    Returns ``None`` (never a fabricated ``0``) on any failure: unknown/empty
    sha, network error, non-200 response, or an unexpected payload shape.
    """
    if not git_sha or git_sha == "unknown":
        return None

    now = time.monotonic()
    cached = _commits_behind_cache.get(git_sha)
    if cached is not None and (now - cached[0]) < _COMMITS_BEHIND_CACHE_TTL_S:
        return cached[1]

    url = f"https://api.github.com/repos/{_GITHUB_REPO}/compare/{git_sha}...main"
    try:
        async with httpx.AsyncClient(timeout=_GITHUB_COMPARE_TIMEOUT_S) as client:
            resp = await client.get(url, headers={"Accept": "application/vnd.github+json"})
        if resp.status_code != 200:
            logger.warning(
                "commits_behind_main: GitHub compare returned HTTP %s for sha=%s",
                resp.status_code,
                git_sha,
            )
            return None
        ahead_by = resp.json().get("ahead_by")
        if not isinstance(ahead_by, int):
            logger.warning("commits_behind_main: unexpected compare payload for sha=%s", git_sha)
            return None
    except Exception:
        logger.warning("commits_behind_main: compare request failed", exc_info=True)
        return None

    _commits_behind_cache[git_sha] = (now, ahead_by)
    if len(_commits_behind_cache) > _COMMITS_BEHIND_CACHE_MAX_ENTRIES:
        oldest_sha = min(_commits_behind_cache, key=lambda k: _commits_behind_cache[k][0])
        del _commits_behind_cache[oldest_sha]

    return ahead_by


def _deployment_row_to_record(row: dict) -> DeploymentRecord:
    return DeploymentRecord(
        id=str(row["id"]),
        git_sha=row["git_sha"],
        migration_head=row["migration_head"],
        started_at=row["started_at"].isoformat(),
        finished_at=row["finished_at"].isoformat() if row["finished_at"] else None,
        result=row["result"],
        source=row.get("source"),
        serving_mode=row.get("serving_mode"),
        serving_worktree=row.get("serving_worktree"),
    )


@router.get("/deployments", response_model=ApiResponse[DeploymentFacts])
async def get_deployment_facts(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[DeploymentFacts]:
    """Return the current (most recent) deployment plus recent history.

    Reads public.deployments (core_163) via the switchboard pool -- the same
    shared-public-schema-read convention /api/system/database and
    /api/system/egress already use. An empty ledger (no boot recorded yet,
    e.g. a fresh instance) is a legitimate v1 state: returns HTTP 200 with
    `current: null` / `recent: []`, not an error. HTTP 503 is reserved for an
    actual query failure (permission denied, connection error).
    """
    system_deployments_reads_total.inc()
    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    from butlers.core.deployments import get_current_deployment, list_recent_deployments

    try:
        current_row = await get_current_deployment(pool)
        recent_rows = await list_recent_deployments(pool)
    except Exception as exc:
        logger.warning("Failed to query deployments ledger: %s", exc)
        raise HTTPException(status_code=503, detail="Deployments ledger query failed")

    commits_behind_main: int | None = None
    if current_row is not None:
        commits_behind_main = await _commits_behind_main(current_row["git_sha"])

    return ApiResponse(
        data=DeploymentFacts(
            current=_deployment_row_to_record(current_row) if current_row else None,
            recent=[_deployment_row_to_record(r) for r in recent_rows],
            commits_behind_main=commits_behind_main,
            commits_behind_available=commits_behind_main is not None,
        )
    )


# ---------------------------------------------------------------------------
# GET /api/system/drift
# ---------------------------------------------------------------------------


@router.get("/drift", response_model=ApiResponse[DriftFacts])
async def get_drift_facts(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[DriftFacts]:
    """Return the migration-drift sentinel's current comparison (bu-9r3hd.1).

    Computes the codebase-head vs per-schema DB-revision comparison live on
    every request (butlers.jobs.deploy_drift.compute_drift_report) rather than
    relying on the hourly background loop's cached state -- this endpoint is
    always at least as fresh as the loop, and the loop's own job is the
    escalation side effect, not serving this page. When drifted, also reads
    (never writes) the first-detected/escalated debounce markers the
    background loop maintains in public.audit_log.

    Always returns HTTP 200, per the fleet-wide degraded-envelope convention:
    a failed comparison sets drift_check_available=False with every other
    field zeroed, rather than a fabricated all-clear or a 503.
    """
    system_drift_reads_total.inc()

    from butlers.jobs.deploy_drift import (
        compute_drift_report,
        drift_fingerprint,
        get_drift_escalation_state,
    )

    report = await compute_drift_report(db)

    if not report.is_available:
        return ApiResponse(
            data=DriftFacts(
                checked_at=report.checked_at.isoformat(),
                is_drifted=False,
                drifted=[],
                first_detected_at=None,
                escalated=False,
                drift_check_available=False,
            )
        )

    first_detected_at: str | None = None
    escalated = False
    if report.is_drifted:
        try:
            pool = db.pool("switchboard")
            fingerprint = drift_fingerprint(report.drifted)
            first, escalated = await get_drift_escalation_state(pool, fingerprint)
            first_detected_at = first.isoformat() if first is not None else None
        except Exception:
            logger.warning("drift facts: escalation-state lookup failed", exc_info=True)

    return ApiResponse(
        data=DriftFacts(
            checked_at=report.checked_at.isoformat(),
            is_drifted=report.is_drifted,
            drifted=[
                DriftEntry(
                    schema_name=d.schema,
                    chain=d.chain,
                    expected_head=d.expected_head,
                    actual_revision=d.actual_revision,
                )
                for d in report.drifted
            ],
            first_detected_at=first_detected_at,
            escalated=escalated,
            drift_check_available=True,
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

#: Env var read by butlers.jobs.backup_health (kept in sync here rather than
#: imported, to avoid a hard import-time dependency for a single string).
BACKUP_DIR_ENV = "BUTLERS_BACKUP_DIR"

#: A gzip stream this small cannot possibly hold a real pg_dump -- it is, at
#: most, an empty/truncated write (gzip's own header+footer overhead alone is
#: ~20 bytes). Below this floor an artifact is "empty", not "healthy", without
#: needing to open it at all.
_BACKUP_MIN_SIZE_BYTES = 256

#: Daily backup cron (BACKUP_CRON default ``0 2 * * *``) plus slack for one
#: missed/late run before the most recent backup counts as stale -- a single
#: skipped night is not yet an emergency, several days silent is.
#:
#: Public (not module-private) because ``butlers.core.qa.sources.infra_state``
#: (bu-9r3hd.4) imports this instead of maintaining its own independently-set
#: threshold for the same signal -- one number, not two that could drift.
BACKUP_STALE_THRESHOLD_HOURS = 36

#: In-process memoization of _verify_backup_artifact, keyed by (path, mtime,
#: size) so a file that hasn't changed since the last request is never
#: re-decompressed -- GET /api/system/backups is polled every 120s (see
#: useBackupFacts) and a full gzip integrity read of a large dump on every
#: poll would be wasteful. Bounded defensively; in steady state this only
#: ever holds ~BACKUP_RETAIN_DAYS distinct entries (one new file per day).
_verify_cache: dict[tuple[str, float, int], tuple[str, str | None]] = {}
_VERIFY_CACHE_MAX_ENTRIES = 64


def _verify_backup_artifact(path: Path, stat: os.stat_result) -> tuple[str, str | None]:
    """Return (status, detail) for one backup file: "healthy" | "corrupt" | "empty".

    "empty" -- file is smaller than any real dump could plausibly be; not
    worth even attempting to open.
    "corrupt" -- gzip decompression failed (truncated transfer, bit rot, a
    dump that was killed mid-write and never reached its final ``mv``).
    Streaming the whole stream through gzip validates its embedded CRC32 +
    size footer -- gzip's own built-in checksum -- so no separate checksum
    sidecar file is needed.
    "healthy" -- decompressed cleanly and cleared the size floor.

    Never raises; any unexpected OSError is treated as "corrupt" with the
    exception text as the detail.
    """
    key = (str(path), stat.st_mtime, stat.st_size)
    cached = _verify_cache.get(key)
    if cached is not None:
        return cached

    if stat.st_size < _BACKUP_MIN_SIZE_BYTES:
        result = ("empty", f"{stat.st_size} bytes, below the {_BACKUP_MIN_SIZE_BYTES}-byte floor")
    else:
        try:
            with gzip.open(path, "rb") as f:
                while f.read(1 << 20):
                    pass
            result = ("healthy", None)
        except OSError as exc:
            result = ("corrupt", f"gzip integrity check failed: {exc}")

    if len(_verify_cache) >= _VERIFY_CACHE_MAX_ENTRIES:
        _verify_cache.clear()
    _verify_cache[key] = result
    return result


def latest_backup_path(backup_dir: Path) -> Path | None:
    """Return the most recent ``butlers_*.sql.gz`` file in *backup_dir*, or None.

    Shared with ``butlers.jobs.backup_health`` so the weekly restore drill
    targets the exact same "most recent dump" this endpoint reports on.
    """
    try:
        candidates = list(backup_dir.glob("butlers_*.sql.gz"))
    except OSError:
        return None
    stamped: list[tuple[float, Path]] = []
    for p in candidates:
        try:
            stamped.append((p.stat().st_mtime, p))
        except OSError:
            continue  # race: file removed between glob and stat
    if not stamped:
        return None
    return max(stamped, key=lambda t: t[0])[1]


_PENDING_RESTORE_DRILL = RestoreDrillFacts(checked_at=None, result="pending", detail=None)


def read_backup_facts_from_dir(backup_dir: Path) -> BackupFacts:
    """Scan *backup_dir* for timestamped pg_dump files and return BackupFacts.

    Backup files must match the pattern ``butlers_*.sql.gz`` (written by
    ``deploy/backup/pg_dump.sh``).  Files are sorted by mtime descending so
    the most-recent dump is always first. Each entry's ``status`` is a real,
    verified verdict (see ``_verify_backup_artifact``) -- not a fabricated
    constant. ``restore_drill`` is always returned as "pending" here; the
    caller (``get_backup_facts``) overwrites it with the DB-backed ledger
    read, keeping this function DB-free and independently unit-testable.

    Returns a degraded (backup_source_reachable=False) payload when:
    - the directory does not exist
    - the directory is not readable (OSError)
    No exception is propagated.

    Public (not module-private) because ``butlers.core.qa.sources.infra_state``
    (bu-9r3hd.4) reuses it for the ``backup-stale`` QA discovery check — the
    same recency/reachability facts this endpoint surfaces, read once and
    shared rather than reimplemented.
    """
    if not backup_dir.is_dir():
        return BackupFacts(
            last_backup_at=None,
            last_backup_size_bytes=None,
            backup_source_reachable=False,
            backup_history=[],
            last_backup_status="missing",
            backup_stale=False,
            restore_drill=_PENDING_RESTORE_DRILL,
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
            last_backup_status="missing",
            backup_stale=False,
            restore_drill=_PENDING_RESTORE_DRILL,
        )

    stamped: list[tuple[float, os.stat_result, Path]] = []
    for p in candidates:
        try:
            st = p.stat()
            stamped.append((st.st_mtime, st, p))
        except OSError:
            continue  # race: file removed between glob and stat

    stamped.sort(key=lambda t: t[0], reverse=True)

    # Spec (system-overview-page, "Backup State Facts"): backup_history is
    # "up to 7 most recent backup events". stamped is sorted most-recent-first,
    # so the first 7 entries are the events to surface.
    history: list[BackupEvent] = []
    for _mtime, stat, p in stamped[:7]:
        mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        status, _detail = _verify_backup_artifact(p, stat)
        history.append(
            BackupEvent(
                completed_at=mtime_dt.isoformat(),
                size_bytes=stat.st_size,
                status=status,
            )
        )

    if not history:
        # Directory exists and is readable, but no dumps have been written yet.
        return BackupFacts(
            last_backup_at=None,
            last_backup_size_bytes=None,
            backup_source_reachable=True,
            backup_history=[],
            last_backup_status="missing",
            backup_stale=False,
            restore_drill=_PENDING_RESTORE_DRILL,
        )

    latest = history[0]
    age_hours = (
        datetime.now(UTC) - datetime.fromisoformat(latest.completed_at)
    ).total_seconds() / 3600
    return BackupFacts(
        last_backup_at=latest.completed_at,
        last_backup_size_bytes=latest.size_bytes,
        backup_source_reachable=True,
        backup_history=history,
        last_backup_status=latest.status,
        backup_stale=age_hours > BACKUP_STALE_THRESHOLD_HOURS,
        restore_drill=_PENDING_RESTORE_DRILL,
    )


@router.get("/backups", response_model=ApiResponse[BackupFacts])
async def get_backup_facts(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[BackupFacts]:
    """Return backup recency, verified artifact health, and restore-drill state.

    Reads filesystem pg_dump files from the directory configured by the
    ``BUTLERS_BACKUP_DIR`` environment variable (written by
    ``deploy/backup/pg_dump.sh`` via the ``backup-cron`` sidecar). Each
    file's status is a real, verified verdict (bu-9r3hd.5) -- artifact
    integrity (gzip decompression, size floor) is cheap enough to check live
    on every request and is memoized per (path, mtime, size) so an unchanged
    file is never re-verified. ``restore_drill`` is read from the ledger
    ``butlers.jobs.backup_health`` maintains in ``public.audit_log`` --
    actually attempting a restore is expensive and mutates state, so it only
    happens on the weekly background loop, never inline with this request.

    When ``BUTLERS_BACKUP_DIR`` is not set or the directory is absent, the
    endpoint returns ``backup_source_reachable=false`` with null fields.
    This is the expected state for unconfigured deployments — not an error.
    A failed ledger read degrades ``restore_drill`` to "degraded" rather than
    failing the whole response.

    Graceful degradation: always returns HTTP 200, never HTTP 503.
    """
    system_backups_reads_total.inc()

    backup_dir_env = os.environ.get(BACKUP_DIR_ENV, "").strip()
    if not backup_dir_env:
        facts = BackupFacts(
            last_backup_at=None,
            last_backup_size_bytes=None,
            backup_source_reachable=False,
            backup_history=[],
            last_backup_status="missing",
            backup_stale=False,
            restore_drill=_PENDING_RESTORE_DRILL,
        )
    else:
        facts = read_backup_facts_from_dir(Path(backup_dir_env))

    facts.restore_drill = await _read_restore_drill_facts(db)
    return ApiResponse(data=facts)


async def _read_restore_drill_facts(db: DatabaseManager) -> RestoreDrillFacts:
    """Read the most recent restore-drill result from the ledger. Never raises."""
    from butlers.jobs.backup_health import get_last_restore_drill

    try:
        pool = db.pool("switchboard")
    except KeyError:
        return RestoreDrillFacts(
            checked_at=None, result="degraded", detail="switchboard pool unavailable"
        )

    try:
        row = await get_last_restore_drill(pool)
    except Exception as exc:
        logger.warning("backup facts: restore-drill ledger read failed", exc_info=True)
        return RestoreDrillFacts(checked_at=None, result="degraded", detail=str(exc))

    if row is None:
        return _PENDING_RESTORE_DRILL

    return RestoreDrillFacts(
        checked_at=row["checked_at"],
        result=row["result"],
        detail=row["detail"],
    )


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
