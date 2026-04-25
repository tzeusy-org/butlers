"""Chronicler butler endpoints.

Read and correction API at ``/api/chronicler/*`` — distinct from the
operational ``/api/timeline`` cross-butler event stream. All responses
carry provenance fields (source_name, source_ref, precision, privacy)
per RFC 0014 §D7.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
import zoneinfo
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from butlers.api.db import DatabaseManager
from butlers.api.models import (
    ApiResponse,
    ErrorDetail,
    ErrorResponse,
    PaginatedResponse,
    PaginationMeta,
)
from butlers.chronicler.aggregations import category_for
from butlers.chronicler.day_close_writer import DAY_CLOSE_TASK_NAME, write_day_close_cache

_models_path = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("chronicler_api_models", _models_path)
if _spec is not None and _spec.loader is not None:
    _models = importlib.util.module_from_spec(_spec)
    sys.modules["chronicler_api_models"] = _models
    _spec.loader.exec_module(_models)

    ChroniclerPointEvent = _models.ChroniclerPointEvent
    ChroniclerEpisode = _models.ChroniclerEpisode
    ChroniclerOverride = _models.ChroniclerOverride
    SourceStateRow = _models.SourceStateRow
    SubsourceCheckpoint = _models.SubsourceCheckpoint
    SubmitCorrectionRequest = _models.SubmitCorrectionRequest
    AggregateByDayRow = _models.AggregateByDayRow
    SourceBreakdownEntry = _models.SourceBreakdownEntry
    CategoryBucket = _models.CategoryBucket
    CategoryBuckets = _models.CategoryBuckets
    DayCloseFreshResponse = _models.DayCloseFreshResponse
    DayCloseStaleResponse = _models.DayCloseStaleResponse
    DayCloseRefreshRequest = _models.DayCloseRefreshRequest
    DayCloseRefreshResponse = _models.DayCloseRefreshResponse
else:  # pragma: no cover — defensive
    raise RuntimeError("Failed to load chronicler API models module")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chronicler", tags=["chronicler"])

BUTLER_DB = "chronicler"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


class DayCloseDispatchCallable(Protocol):
    """Protocol for the day-close dispatch callable injected via _get_day_close_dispatch_fn.

    Accepts a prompt string and returns a SpawnerResult-compatible object.
    The handler calls write_day_close_cache() with the result.
    """

    async def __call__(self, *, prompt: str, trigger_source: str) -> Any: ...


def _get_day_close_dispatch_fn() -> DayCloseDispatchCallable | None:
    """Dependency stub — returns None by default.

    Returns None when running without an in-process spawner (e.g. standalone
    API mode, tests).  Override via ``app.dependency_overrides`` to inject the
    real spawner dispatch when the butler daemon is available.

    When None, the refresh endpoint returns 503.
    """
    return None


def _pool(db: DatabaseManager):
    try:
        return db.pool(BUTLER_DB)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Chronicler butler database is not available",
        )


def _coerce_payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
        if isinstance(loaded, dict):
            return loaded
        return {"raw": loaded}
    return {"raw": value}


def _row_to_episode(row: Any) -> ChroniclerEpisode:
    return ChroniclerEpisode(
        id=str(row["id"]),
        source_name=row["source_name"],
        source_ref=row["source_ref"],
        episode_type=row["episode_type"],
        start_at=row["start_at"],
        end_at=row["end_at"],
        precision=row["precision"],
        title=row["title"],
        payload=_coerce_payload(row["payload"]),
        privacy=row["privacy"],
        retention_days=row["retention_days"],
        tombstone_at=row["tombstone_at"],
        canonical_start_at=row["canonical_start_at"],
        canonical_end_at=row["canonical_end_at"],
        canonical_title=row["canonical_title"],
        canonical_privacy=row["canonical_privacy"],
        corrected_at=row["corrected_at"],
        correction_note=row["correction_note"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_point_event(row: Any) -> ChroniclerPointEvent:
    return ChroniclerPointEvent(
        id=str(row["id"]),
        source_name=row["source_name"],
        source_ref=row["source_ref"],
        event_type=row["event_type"],
        occurred_at=row["occurred_at"],
        precision=row["precision"],
        title=row["title"],
        payload=_coerce_payload(row["payload"]),
        privacy=row["privacy"],
        retention_days=row["retention_days"],
        tombstone_at=row["tombstone_at"],
        canonical_occurred_at=row["canonical_occurred_at"],
        canonical_title=row["canonical_title"],
        canonical_privacy=row["canonical_privacy"],
        corrected_at=row["corrected_at"],
        correction_note=row["correction_note"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_override(row: Any) -> ChroniclerOverride:
    return ChroniclerOverride(
        id=str(row["id"]),
        target_kind=row["target_kind"],
        target_id=str(row["target_id"]),
        corrected_start_at=row["corrected_start_at"],
        corrected_end_at=row["corrected_end_at"],
        corrected_title=row["corrected_title"],
        corrected_privacy=row["corrected_privacy"],
        corrected_tombstone_at=row["corrected_tombstone_at"],
        note=row["note"],
        submitted_by=row["submitted_by"],
        created_at=row["created_at"],
    )


# ── GET /api/chronicler/events ────────────────────────────────────────────


@router.get("/events", response_model=PaginatedResponse[ChroniclerPointEvent])
async def list_events(
    source_name: str | None = Query(None, description="Filter by source adapter"),
    event_type: str | None = Query(None, description="Filter by event type"),
    since: datetime | None = Query(None, description="occurred_at >= since"),
    until: datetime | None = Query(None, description="occurred_at < until"),
    include_tombstoned: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[ChroniclerPointEvent]:
    pool = _pool(db)

    clauses: list[str] = []
    args: list[Any] = []

    if not include_tombstoned:
        clauses.append("tombstone_at IS NULL")
    if source_name is not None:
        args.append(source_name)
        clauses.append(f"source_name = ${len(args)}")
    if event_type is not None:
        args.append(event_type)
        clauses.append(f"event_type = ${len(args)}")
    if since is not None:
        args.append(since)
        clauses.append(f"occurred_at >= ${len(args)}")
    if until is not None:
        args.append(until)
        clauses.append(f"occurred_at < ${len(args)}")

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    total = await pool.fetchval(f"SELECT count(*) FROM v_point_events_corrected{where}", *args) or 0

    args.append(limit)
    args.append(offset)
    rows = await pool.fetch(
        f"""
        SELECT * FROM v_point_events_corrected{where}
        ORDER BY occurred_at DESC
        LIMIT ${len(args) - 1} OFFSET ${len(args)}
        """,
        *args,
    )

    data = [_row_to_point_event(r) for r in rows]
    return PaginatedResponse[ChroniclerPointEvent](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ── GET /api/chronicler/episodes ──────────────────────────────────────────


@router.get("/episodes", response_model=PaginatedResponse[ChroniclerEpisode])
async def list_episodes(
    source_name: str | None = Query(None),
    episode_type: str | None = Query(None),
    start_from: datetime | None = Query(None, description="start_at >= start_from"),
    start_to: datetime | None = Query(None, description="start_at < start_to"),
    overlaps_start: datetime | None = Query(
        None,
        description="Overlap window start (both overlaps_start and overlaps_end required)",
    ),
    overlaps_end: datetime | None = Query(
        None,
        description="Overlap window end (both overlaps_start and overlaps_end required)",
    ),
    include_tombstoned: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[ChroniclerEpisode]:
    if (overlaps_start is None) != (overlaps_end is None):
        raise HTTPException(
            status_code=400,
            detail="overlaps_start and overlaps_end must be provided together",
        )

    pool = _pool(db)

    clauses: list[str] = []
    args: list[Any] = []

    if not include_tombstoned:
        clauses.append("tombstone_at IS NULL")
    if source_name is not None:
        args.append(source_name)
        clauses.append(f"source_name = ${len(args)}")
    if episode_type is not None:
        args.append(episode_type)
        clauses.append(f"episode_type = ${len(args)}")
    if start_from is not None:
        args.append(start_from)
        clauses.append(f"start_at >= ${len(args)}")
    if start_to is not None:
        args.append(start_to)
        clauses.append(f"start_at < ${len(args)}")
    if overlaps_start is not None and overlaps_end is not None:
        args.append(overlaps_end)
        clauses.append(f"start_at < ${len(args)}")
        args.append(overlaps_start)
        clauses.append(f"(end_at IS NULL OR end_at > ${len(args)})")

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    total = await pool.fetchval(f"SELECT count(*) FROM v_episodes_corrected{where}", *args) or 0

    args.append(limit)
    args.append(offset)
    rows = await pool.fetch(
        f"""
        SELECT * FROM v_episodes_corrected{where}
        ORDER BY start_at DESC
        LIMIT ${len(args) - 1} OFFSET ${len(args)}
        """,
        *args,
    )

    data = [_row_to_episode(r) for r in rows]
    return PaginatedResponse[ChroniclerEpisode](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ── GET /api/chronicler/episodes/{id} ─────────────────────────────────────


@router.get("/episodes/{episode_id}", response_model=ChroniclerEpisode)
async def get_episode(
    episode_id: UUID,
    include_tombstoned: bool = Query(False),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ChroniclerEpisode:
    pool = _pool(db)
    clause = "" if include_tombstoned else "AND tombstone_at IS NULL"
    row = await pool.fetchrow(
        f"SELECT * FROM v_episodes_corrected WHERE id = $1 {clause}",
        episode_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return _row_to_episode(row)


# ── GET /api/chronicler/episodes/{id}/events ──────────────────────────────


@router.get(
    "/episodes/{episode_id}/events",
    response_model=list[ChroniclerPointEvent],
)
async def list_episode_events(
    episode_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[ChroniclerPointEvent]:
    pool = _pool(db)
    # 404 if episode doesn't exist.
    exists = await pool.fetchval(
        "SELECT 1 FROM episodes WHERE id = $1",
        episode_id,
    )
    if not exists:
        raise HTTPException(status_code=404, detail="Episode not found")

    rows = await pool.fetch(
        """
        SELECT v.*
        FROM episode_event_links l
        JOIN v_point_events_corrected v ON v.id = l.event_id
        WHERE l.episode_id = $1
        ORDER BY v.occurred_at ASC
        """,
        episode_id,
    )
    return [_row_to_point_event(r) for r in rows]


# ── GET /api/chronicler/episodes/{id}/corrections ─────────────────────────


@router.get(
    "/episodes/{episode_id}/corrections",
    response_model=list[ChroniclerOverride],
)
async def list_episode_corrections(
    episode_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[ChroniclerOverride]:
    pool = _pool(db)
    exists = await pool.fetchval(
        "SELECT 1 FROM episodes WHERE id = $1",
        episode_id,
    )
    if not exists:
        raise HTTPException(status_code=404, detail="Episode not found")

    rows = await pool.fetch(
        """
        SELECT * FROM overrides
        WHERE target_kind = 'episode' AND target_id = $1
        ORDER BY created_at DESC
        """,
        episode_id,
    )
    return [_row_to_override(r) for r in rows]


# ── POST /api/chronicler/episodes/{id}/corrections ────────────────────────


@router.post(
    "/episodes/{episode_id}/corrections",
    response_model=ChroniclerOverride,
    status_code=201,
)
async def submit_episode_correction(
    episode_id: UUID,
    body: SubmitCorrectionRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ChroniclerOverride:
    pool = _pool(db)

    exists = await pool.fetchval(
        "SELECT 1 FROM episodes WHERE id = $1",
        episode_id,
    )
    if not exists:
        raise HTTPException(status_code=404, detail="Episode not found")

    if not any(
        (
            body.corrected_start_at is not None,
            body.corrected_end_at is not None,
            body.corrected_title is not None,
            body.corrected_privacy is not None,
            body.corrected_tombstone_at is not None,
            body.note is not None,
        )
    ):
        raise HTTPException(
            status_code=400,
            detail="At least one correction field or a note is required",
        )

    if body.corrected_privacy is not None and body.corrected_privacy not in (
        "normal",
        "sensitive",
        "restricted",
    ):
        raise HTTPException(
            status_code=400,
            detail="corrected_privacy must be one of 'normal', 'sensitive', 'restricted'",
        )

    row = await pool.fetchrow(
        """
        INSERT INTO overrides (
            target_kind, target_id, corrected_start_at, corrected_end_at,
            corrected_title, corrected_privacy, corrected_tombstone_at,
            note, submitted_by
        )
        VALUES ('episode', $1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING *
        """,
        episode_id,
        body.corrected_start_at,
        body.corrected_end_at,
        body.corrected_title,
        body.corrected_privacy,
        body.corrected_tombstone_at,
        body.note,
        body.submitted_by,
    )
    return _row_to_override(row)


# ── GET /api/chronicler/source-state ─────────────────────────────────────


def _rows_to_source_state(
    adapter_rows: list[Any],
    checkpoint_rows: list[Any],
) -> list[SourceStateRow]:
    """Build SourceStateRow list from adapter state and checkpoint rows.

    Joins projection_checkpoints rows to their parent source_adapter_state row.
    ``last_run_at`` and ``last_error`` are the latest values across all subsources.
    ``subsource_checkpoints`` contains the per-subsource detail array.
    """
    # Group checkpoint rows by source_name.
    checkpoints_by_source: dict[str, list[Any]] = {}
    for cp in checkpoint_rows:
        checkpoints_by_source.setdefault(cp["source_name"], []).append(cp)

    results: list[SourceStateRow] = []
    for row in adapter_rows:
        sn = row["source_name"]
        cps = checkpoints_by_source.get(sn, [])

        # Aggregate: latest last_run_at and latest last_error across subsources.
        last_run_at: datetime | None = None
        last_error: str | None = None
        for cp in cps:
            cp_run = cp["last_run_at"]
            if cp_run is not None and (last_run_at is None or cp_run > last_run_at):
                last_run_at = cp_run
                last_error = cp["last_error"]

        subsource_checkpoints = [
            SubsourceCheckpoint(
                subsource=cp["subsource"],
                last_run_at=cp["last_run_at"],
                last_error=cp["last_error"],
            )
            for cp in cps
        ] or None

        results.append(
            SourceStateRow(
                source_name=sn,
                chronicler_compatibility=row["chronicler_compatibility"],
                read_surface=row["read_surface"],
                boundary_semantics=row["boundary_semantics"],
                optional_schema=row["optional_schema"],
                active=row["active"],
                inactive_reason=row["inactive_reason"],
                last_run_at=last_run_at,
                last_error=last_error,
                subsource_checkpoints=subsource_checkpoints,
            )
        )

    return results


@router.get("/source-state", response_model=ApiResponse[list[SourceStateRow]])
async def list_source_state(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[SourceStateRow]]:
    """Return one record per registered source adapter joined with its projection checkpoints.

    Records are returned sorted by ``source_name ASC``.
    An empty ``source_adapter_state`` table returns ``{"data": [], "meta": {}}``.
    """
    pool = _pool(db)

    adapter_rows = await pool.fetch(
        "SELECT source_name, chronicler_compatibility, read_surface, boundary_semantics,"
        " optional_schema, active, inactive_reason"
        " FROM source_adapter_state"
        " ORDER BY source_name ASC"
    )

    checkpoint_rows = await pool.fetch(
        "SELECT source_name, subsource, last_run_at, last_error"
        " FROM projection_checkpoints"
        " ORDER BY source_name ASC, subsource ASC"
    )

    data = _rows_to_source_state(adapter_rows, checkpoint_rows)
    return ApiResponse[list[SourceStateRow]](data=data)


# ── Precision ordering ─────────────────────────────────────────────────────

# Ordered least-precise → most-precise.  Lower index = less precise.
_PRECISION_ORDER = ["unknown", "day", "hour", "minute", "exact"]


def _least_precise(values: list[str]) -> str:
    """Return the least-precise precision value from a list.

    If a value is not in the known ordering it is treated as less precise
    than ``unknown`` so that unrecognized tokens never silently inflate
    confidence.
    """
    if not values:
        return "unknown"
    return min(values, key=lambda p: _PRECISION_ORDER.index(p) if p in _PRECISION_ORDER else -1)


# ── GET /api/chronicler/aggregate/by-category ────────────────────────────


@router.get("/aggregate/by-category", response_model=ApiResponse[CategoryBuckets])
async def aggregate_by_category(
    start_at: datetime | None = Query(None, description="Inclusive window start (UTC or tz-aware)"),
    end_at: datetime | None = Query(None, description="Exclusive window end (UTC or tz-aware)"),
    tz: str = Query("UTC", description="IANA timezone for display purposes"),
    privacy_tier: str | None = Query(
        None,
        description=(
            "Comma-separated privacy values to include (e.g. 'normal,sensitive'). "
            "Default: exclude restricted (include normal and sensitive)."
        ),
    ),
    include_tombstoned: bool = Query(False),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CategoryBuckets]:
    """Return total episode duration bucketed by category across the requested window.

    Buckets are sorted by ``total_seconds DESC``, then ``category ASC`` for
    deterministic ordering.  Restricted episodes are excluded by default.
    Tombstoned episodes are excluded unless ``include_tombstoned=true``.
    Open episodes (``end_at IS NULL``) are clipped to ``query_end`` so that
    in-progress activities are counted up to the window boundary.
    """
    # ── Parameter validation ───────────────────────────────────────────
    if start_at is None or end_at is None:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="missing_parameter",
                    message="start_at and end_at are required",
                    butler="chronicler",
                )
            ).model_dump(exclude_none=True),
        )

    if end_at <= start_at:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="invalid_time_range",
                    message="end_at must be strictly after start_at",
                    butler="chronicler",
                )
            ).model_dump(exclude_none=True),
        )

    try:
        zoneinfo.ZoneInfo(tz)
    except (zoneinfo.ZoneInfoNotFoundError, KeyError):
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="invalid_timezone",
                    message=f"Unrecognized IANA timezone: {tz!r}",
                    butler="chronicler",
                )
            ).model_dump(exclude_none=True),
        )

    # ── Resolve privacy filter ─────────────────────────────────────────
    # Default: include 'normal' and 'sensitive'; exclude 'restricted'.
    if privacy_tier is not None:
        allowed_tiers = {t.strip() for t in privacy_tier.split(",") if t.strip()}
    else:
        allowed_tiers = {"normal", "sensitive"}

    pool = _pool(db)

    # ── Fetch raw episode rows from the corrected view ─────────────────
    # Read from v_episodes_corrected using start_at/end_at/privacy so that
    # user-submitted overrides are honoured.
    clauses: list[str] = [
        "start_at < $2",
        "(end_at IS NULL OR end_at > $1)",
    ]
    args: list[Any] = [start_at, end_at]

    if not include_tombstoned:
        clauses.append("tombstone_at IS NULL")

    # Build privacy IN clause from allowed_tiers.
    tier_placeholders = ", ".join(f"${len(args) + i + 1}" for i in range(len(allowed_tiers)))
    for tier in sorted(allowed_tiers):
        args.append(tier)
    clauses.append(f"privacy IN ({tier_placeholders})")

    where = "WHERE " + " AND ".join(clauses)

    rows = await pool.fetch(
        f"""
        SELECT
            source_name,
            episode_type,
            start_at,
            end_at,
            precision,
            privacy,
            retention_days,
            tombstone_at
        FROM v_episodes_corrected
        {where}
        """,
        *args,
    )

    # ── Aggregate in Python ────────────────────────────────────────────
    # group by (category, source_name) to build per-source breakdowns,
    # then roll up to per-category buckets.
    cat_src: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "total_seconds": 0.0,
            "episode_count": 0,
            "tombstoned": False,
            "precision_values": [],
            "retention_days_values": [],
        }
    )

    for row in rows:
        ep_start: datetime = row["start_at"]
        ep_end: datetime | None = row["end_at"]
        source_name: str = row["source_name"]
        episode_type: str = row["episode_type"]
        precision: str = row["precision"]
        retention_days: int | None = row["retention_days"]
        is_tombstoned: bool = row["tombstone_at"] is not None

        ep_category = category_for(source_name, episode_type)

        if ep_category == "other":
            logger.warning(
                "chronicler.aggregate.unmapped_source=%s episode_type=%s",
                source_name,
                episode_type,
            )

        # Clip open episodes to query_end.
        ep_end_resolved = ep_end if ep_end is not None else end_at

        # Duration = LEAST(end_at, query_end) - GREATEST(start_at, query_start), clamped at 0.
        overlap_start = max(ep_start, start_at)
        overlap_end = min(ep_end_resolved, end_at)
        if overlap_end <= overlap_start:
            continue
        overlap_seconds = (overlap_end - overlap_start).total_seconds()

        bucket_key = (ep_category, source_name)
        bucket = cat_src[bucket_key]
        bucket["total_seconds"] += overlap_seconds
        bucket["episode_count"] += 1
        bucket["tombstoned"] = bucket["tombstoned"] or is_tombstoned
        bucket["precision_values"].append(precision)
        bucket["retention_days_values"].append(retention_days)

    # Roll up per-(category, source) to per-category buckets.
    cat_buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total_seconds": 0.0,
            "episode_count": 0,
            "source_breakdown": [],
            "precision_values": [],
            "retention_days_values": [],
        }
    )

    for (ep_category, source_name), src_data in cat_src.items():
        bucket = cat_buckets[ep_category]
        bucket["total_seconds"] += src_data["total_seconds"]
        bucket["episode_count"] += src_data["episode_count"]
        bucket["precision_values"].extend(src_data["precision_values"])
        bucket["retention_days_values"].extend(src_data["retention_days_values"])
        bucket["source_breakdown"].append(
            SourceBreakdownEntry(
                source_name=source_name,
                total_seconds=src_data["total_seconds"],
                episode_count=src_data["episode_count"],
                tombstoned=src_data["tombstoned"],
            )
        )

    # Build CategoryBucket list sorted by total_seconds DESC, category ASC.
    result_buckets: list[CategoryBucket] = []
    for ep_category, data in cat_buckets.items():
        non_null_retentions = [r for r in data["retention_days_values"] if r is not None]
        result_buckets.append(
            CategoryBucket(
                category=ep_category,
                total_seconds=data["total_seconds"],
                episode_count=data["episode_count"],
                source_breakdown=data["source_breakdown"],
                precision=_least_precise(data["precision_values"]),
                retention_floor_days=min(non_null_retentions) if non_null_retentions else None,
            )
        )

    result_buckets.sort(key=lambda b: (-b.total_seconds, b.category))

    return ApiResponse[CategoryBuckets](
        data=CategoryBuckets(
            start_at=start_at,
            end_at=end_at,
            tz=tz,
            buckets=result_buckets,
        )
    )


# ── GET /api/chronicler/aggregate/by-day ─────────────────────────────────


@router.get("/aggregate/by-day", response_model=list[AggregateByDayRow])
async def aggregate_by_day(
    start_at: datetime | None = Query(None, description="Inclusive window start (UTC or tz-aware)"),
    end_at: datetime | None = Query(None, description="Exclusive window end (UTC or tz-aware)"),
    tz: str = Query("UTC", description="IANA timezone for day-boundary computation"),
    category: str | None = Query(None, description="Optional category filter"),
    include_tombstoned: bool = Query(False),
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[AggregateByDayRow]:
    """Return time-bucketed episode durations grouped by (day, category).

    Day boundaries are resolved in the requested IANA timezone so that
    DST-extended (25 h) and DST-shortened (23 h) days are treated as
    single buckets with actual-duration semantics.  Each row includes
    ``day_start`` / ``day_end`` timestamps in the requested timezone so
    callers can verify bucket boundaries without re-deriving DST rules.

    Restricted episodes are excluded by default.  Sensitive episodes
    contribute to duration totals but their identifying fields are not
    surfaced.  Tombstoned episodes are excluded unless
    ``include_tombstoned=true`` is supplied.
    """
    # ── Parameter validation ───────────────────────────────────────────
    if start_at is None or end_at is None:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="missing_parameter",
                    message="start_at and end_at are required",
                    butler="chronicler",
                )
            ).model_dump(exclude_none=True),
        )

    if end_at <= start_at:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="invalid_time_range",
                    message="end_at must be strictly after start_at",
                    butler="chronicler",
                )
            ).model_dump(exclude_none=True),
        )

    try:
        tzinfo = zoneinfo.ZoneInfo(tz)
    except (zoneinfo.ZoneInfoNotFoundError, KeyError):
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="invalid_timezone",
                    message=f"Unrecognized IANA timezone: {tz!r}",
                    butler="chronicler",
                )
            ).model_dump(exclude_none=True),
        )

    pool = _pool(db)

    # ── Fetch raw episode rows from the corrected view ─────────────────
    # Only select relations from the chronicler schema (v_episodes_corrected).
    # No LLM. No cross-schema references.
    # Use the corrected start_at/end_at columns so user-submitted overrides
    # are honoured in window filtering and duration arithmetic.
    clauses: list[str] = [
        "start_at < $2",
        "(end_at IS NULL OR end_at > $1)",
    ]
    args: list[Any] = [start_at, end_at]

    if not include_tombstoned:
        clauses.append("tombstone_at IS NULL")

    # Exclude restricted episodes by default (use corrected privacy column).
    clauses.append("privacy != 'restricted'")

    where = "WHERE " + " AND ".join(clauses)

    rows = await pool.fetch(
        f"""
        SELECT
            source_name,
            episode_type,
            start_at,
            end_at,
            precision,
            privacy,
            retention_days,
            tombstone_at
        FROM v_episodes_corrected
        {where}
        """,
        *args,
    )

    # ── Enumerate calendar days in the requested timezone ──────────────
    # Compute the first and last local calendar day that overlaps the window.
    local_start = start_at.astimezone(tzinfo)
    local_end = end_at.astimezone(tzinfo)

    first_day = local_start.date()
    last_day = (local_end - timedelta(microseconds=1)).astimezone(tzinfo).date()

    # Build a mapping: day_str -> (day_start_utc, day_end_utc)
    day_bounds: dict[str, tuple[datetime, datetime]] = {}
    current_day = first_day
    while current_day <= last_day:
        ds = datetime(current_day.year, current_day.month, current_day.day, 0, 0, 0, tzinfo=tzinfo)
        next_day = current_day + timedelta(days=1)
        de = datetime(next_day.year, next_day.month, next_day.day, 0, 0, 0, tzinfo=tzinfo)
        day_bounds[current_day.isoformat()] = (ds, de)
        current_day = next_day

    if not day_bounds:
        return []

    # ── Aggregate in Python ────────────────────────────────────────────
    # Group by (day_str, category, source_name) and sum overlap seconds.
    # This avoids pushing category_for() logic into SQL and keeps it
    # in the Python layer where the category taxonomy lives.

    # day_cat_src[(day, category, source_name)] = {
    #   "total_seconds": float,
    #   "episode_count": int,
    #   "tombstoned": bool,
    #   "precision_values": list[str],
    #   "retention_days_values": list[int | None],
    # }
    day_cat_src: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "total_seconds": 0.0,
            "episode_count": 0,
            "tombstoned": False,
            "precision_values": [],
            "retention_days_values": [],
        }
    )

    for row in rows:
        ep_start: datetime = row["start_at"]
        ep_end: datetime | None = row["end_at"]
        source_name: str = row["source_name"]
        episode_type: str = row["episode_type"]
        precision: str = row["precision"]
        retention_days: int | None = row["retention_days"]
        is_tombstoned: bool = row["tombstone_at"] is not None

        ep_category = category_for(source_name, episode_type)
        if category is not None and ep_category != category:
            continue

        if ep_category == "other":
            logger.warning(
                "chronicler.aggregate.unmapped_source=%s episode_type=%s",
                source_name,
                episode_type,
            )

        if is_tombstoned:
            logger.warning(
                "chronicler.aggregate.tombstoned_episode source=%s episode_type=%s",
                source_name,
                episode_type,
            )

        # If end_at is NULL treat as open-ended — only count the portion
        # that falls within each day window.
        ep_end_resolved = ep_end if ep_end is not None else end_at

        for day_str, (ds, de) in day_bounds.items():
            # Overlap = max(0, min(ep_end, de) - max(ep_start, ds))
            overlap_start = max(ep_start, ds)
            overlap_end = min(ep_end_resolved, de)
            if overlap_end <= overlap_start:
                continue
            overlap_seconds = (overlap_end - overlap_start).total_seconds()

            bucket_key = (day_str, ep_category, source_name)
            bucket = day_cat_src[bucket_key]
            bucket["total_seconds"] += overlap_seconds
            bucket["episode_count"] += 1
            bucket["tombstoned"] = bucket["tombstoned"] or is_tombstoned
            bucket["precision_values"].append(precision)
            bucket["retention_days_values"].append(retention_days)

    # ── Build final response rows ──────────────────────────────────────
    # First group day_cat_src by (day, category) to produce per-bucket source breakdowns.
    day_cat: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "total_seconds": 0.0,
            "episode_count": 0,
            "source_breakdown": [],
            "precision_values": [],
            "retention_days_values": [],
        }
    )

    for (day_str, ep_category, source_name), src_data in day_cat_src.items():
        bucket = day_cat[(day_str, ep_category)]
        bucket["total_seconds"] += src_data["total_seconds"]
        bucket["episode_count"] += src_data["episode_count"]
        bucket["precision_values"].extend(src_data["precision_values"])
        bucket["retention_days_values"].extend(src_data["retention_days_values"])
        bucket["source_breakdown"].append(
            SourceBreakdownEntry(
                source_name=source_name,
                total_seconds=src_data["total_seconds"],
                episode_count=src_data["episode_count"],
                tombstoned=src_data["tombstoned"],
            )
        )

    result: list[AggregateByDayRow] = []
    for (day_str, ep_category), data in sorted(day_cat.items()):
        ds, de = day_bounds[day_str]
        non_null_retentions = [r for r in data["retention_days_values"] if r is not None]
        result.append(
            AggregateByDayRow(
                day=day_str,
                category=ep_category,
                total_seconds=data["total_seconds"],
                episode_count=data["episode_count"],
                day_start=ds,
                day_end=de,
                source_breakdown=data["source_breakdown"],
                precision=_least_precise(data["precision_values"]),
                retention_floor_days=min(non_null_retentions) if non_null_retentions else None,
            )
        )

    # Sort by (day ASC, category ASC) — already guaranteed by sorted() above.
    return result


# ── GET /api/chronicler/aggregate/day-close ───────────────────────────────


@router.get(
    "/aggregate/day-close",
    response_model=Annotated[
        DayCloseFreshResponse | DayCloseStaleResponse,
        "Fresh prose or stale marker",
    ],
)
async def get_day_close_cache(
    date_param: str | None = Query(
        None, alias="date", description="YYYY-MM-DD date for day-close window"
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> DayCloseFreshResponse | DayCloseStaleResponse:
    """Return cached day-close prose OR a stale marker.

    Looks up ``tier2_cache`` by ``cache_key=day_close:{YYYY-MM-DD}``.
    Returns 404 if no cache entry exists.

    If a cache entry exists, checks whether any episode, point_event, or
    override row in the cached window [start_at, end_at) has been modified
    (tombstoned, updated, or created) after ``cache_built_at``.

    - **Fresh:** returns ``{prose, provenance_refs, cache_built_at}``.
    - **Stale:** returns ``{stale: true, cache_built_at, last_invalidating_event_at}``.

    No LLM is invoked on this path.
    """
    # ── Parameter validation ───────────────────────────────────────────
    if date_param is None:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="missing_parameter",
                    message="date is required",
                    butler="chronicler",
                )
            ).model_dump(exclude_none=True),
        )

    try:
        parsed_date = date.fromisoformat(date_param)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="invalid_date_format",
                    message=f"date must be a valid YYYY-MM-DD date; got {date_param!r}",
                    butler="chronicler",
                )
            ).model_dump(exclude_none=True),
        )

    pool = _pool(db)

    cache_key = f"day_close:{parsed_date.isoformat()}"

    # ── Step 1: fetch the cache row ──────────────────────────────────────
    cache_row = await pool.fetchrow(
        """
        SELECT cache_key, start_at, end_at, cache_built_at, prose, provenance_refs
        FROM tier2_cache
        WHERE cache_key = $1
          AND superseded_at IS NULL
        """,
        cache_key,
    )

    if cache_row is None:
        raise HTTPException(status_code=404, detail=f"No day-close cache entry for {parsed_date}")

    start_at = cache_row["start_at"]
    end_at = cache_row["end_at"]
    cache_built_at = cache_row["cache_built_at"]

    # ── Step 2: query staleness signals in the cached window ─────────────
    # Seven signals:
    #   episodes.tombstone_at  > cache_built_at  (window-scoped)
    #   episodes.updated_at    > cache_built_at  (window-scoped)
    #   point_events.tombstone_at > cache_built_at  (window-scoped)
    #   point_events.updated_at   > cache_built_at  (window-scoped)
    #   overrides.created_at   > cache_built_at  (for window-overlapping overrides)
    #   episodes cited in provenance_refs but now outside the window (updated_at signal)
    #   point_events cited in provenance_refs but now outside the window (updated_at signal)
    #
    # Window condition for episodes / point_events (signals 1-4):
    #   rows whose time span overlaps [start_at, end_at)
    #   i.e. start_at_col < end_at AND (end_at_col IS NULL OR end_at_col > start_at)
    #
    # Signals 6-7 (provenance-ref staleness): an episode or point_event that was
    # cited when the cache was built may have been updated to move its time range
    # OUTSIDE the cached window.  The window filter above would then miss it.
    # Fix: join against the source_ref values stored in tier2_cache.provenance_refs
    # so updates to those specific rows trigger staleness regardless of their
    # current window position.
    #
    # For overrides we join back to the underlying episode/point_event to
    # scope them to the same window.  We use a single UNION query to get
    # the MAX timestamp in one round-trip.
    staleness_row = await pool.fetchrow(
        """
        SELECT MAX(ts) AS last_invalidating_event_at
        FROM (
            -- episodes.tombstone_at
            SELECT tombstone_at AS ts
            FROM episodes
            WHERE tombstone_at > $3
              AND start_at < $2
              AND (end_at IS NULL OR end_at > $1)

            UNION ALL

            -- episodes.updated_at
            SELECT updated_at AS ts
            FROM episodes
            WHERE updated_at > $3
              AND start_at < $2
              AND (end_at IS NULL OR end_at > $1)

            UNION ALL

            -- point_events.tombstone_at
            SELECT tombstone_at AS ts
            FROM point_events
            WHERE tombstone_at > $3
              AND occurred_at >= $1
              AND occurred_at < $2

            UNION ALL

            -- point_events.updated_at
            SELECT updated_at AS ts
            FROM point_events
            WHERE updated_at > $3
              AND occurred_at >= $1
              AND occurred_at < $2

            UNION ALL

            -- overrides scoped via episode window
            SELECT o.created_at AS ts
            FROM overrides o
            JOIN episodes e ON e.id = o.target_id AND o.target_kind = 'episode'
            WHERE o.created_at > $3
              AND e.start_at < $2
              AND (e.end_at IS NULL OR e.end_at > $1)

            UNION ALL

            -- overrides scoped via point_event window
            SELECT o.created_at AS ts
            FROM overrides o
            JOIN point_events p ON p.id = o.target_id AND o.target_kind = 'point_event'
            WHERE o.created_at > $3
              AND p.occurred_at >= $1
              AND p.occurred_at < $2

            UNION ALL

            -- provenance-ref staleness: episodes cited by this cache entry
            -- that were updated (possibly moving their window) after cache was built.
            -- Catches updates that push the episode outside the cached window.
            SELECT e.updated_at AS ts
            FROM tier2_cache c
            CROSS JOIN LATERAL jsonb_array_elements_text(c.provenance_refs) AS ref
            JOIN episodes e ON e.source_ref = ref
            WHERE c.cache_key = $4
              AND c.superseded_at IS NULL
              AND e.updated_at > $3

            UNION ALL

            -- provenance-ref staleness: point_events cited by this cache entry
            -- that were updated (possibly moving their window) after cache was built.
            SELECT p.updated_at AS ts
            FROM tier2_cache c
            CROSS JOIN LATERAL jsonb_array_elements_text(c.provenance_refs) AS ref
            JOIN point_events p ON p.source_ref = ref
            WHERE c.cache_key = $4
              AND c.superseded_at IS NULL
              AND p.updated_at > $3
        ) invalidators
        """,
        start_at,
        end_at,
        cache_built_at,
        cache_key,
    )

    last_invalidating_event_at = (
        staleness_row["last_invalidating_event_at"] if staleness_row else None
    )

    if last_invalidating_event_at is not None:
        return DayCloseStaleResponse(
            stale=True,
            cache_built_at=cache_built_at,
            last_invalidating_event_at=last_invalidating_event_at,
        )

    # Fresh path: return prose + provenance_refs
    raw_refs = cache_row["provenance_refs"]
    if isinstance(raw_refs, str):
        try:
            provenance_refs = json.loads(raw_refs)
        except json.JSONDecodeError:
            provenance_refs = []
    elif isinstance(raw_refs, list):
        provenance_refs = raw_refs
    else:
        provenance_refs = []

    return DayCloseFreshResponse(
        prose=cache_row["prose"],
        provenance_refs=provenance_refs,
        cache_built_at=cache_built_at,
    )


# ── POST /api/chronicler/aggregate/day-close/refresh ─────────────────────────

_REFRESH_RATE_LIMIT_HOURS = 24


@router.post(
    "/aggregate/day-close/refresh",
    response_model=DayCloseRefreshResponse,
    status_code=200,
)
async def refresh_day_close(
    body: DayCloseRefreshRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
    dispatch_fn: DayCloseDispatchCallable | None = Depends(_get_day_close_dispatch_fn),
) -> DayCloseRefreshResponse:
    """Re-invoke the day-close Tier-2 path on demand (rate-limited: 1 per 24 h per date/tz).

    Checks whether a ``tier2_cache`` row for ``day_close:{date}`` was built within
    the last 24 hours.  If so, returns 429 with ``code=day_close_rate_limited`` and
    ``details.retry_after_seconds``.

    Otherwise, re-dispatches the ``chronicler_day_close`` scheduled prompt via the
    injected dispatch callable and writes a fresh ``tier2_cache`` row via
    ``write_day_close_cache()``.

    Returns 503 when no dispatch callable is wired (standalone/test mode without spawner).
    """
    # ── Validate timezone ─────────────────────────────────────────────────────
    try:
        zoneinfo.ZoneInfo(body.tz)
    except (zoneinfo.ZoneInfoNotFoundError, KeyError):
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="invalid_timezone",
                    message=f"Unrecognized IANA timezone: {body.tz!r}",
                    butler="chronicler",
                )
            ).model_dump(exclude_none=True),
        )

    pool = _pool(db)
    cache_key = f"day_close:{body.date.isoformat()}"
    now = datetime.now(UTC)

    # ── Rate-limit check ──────────────────────────────────────────────────────
    existing_row = await pool.fetchrow(
        """
        SELECT cache_built_at
        FROM tier2_cache
        WHERE cache_key = $1
          AND superseded_at IS NULL
        """,
        cache_key,
    )

    if existing_row is not None:
        cache_built_at: datetime = existing_row["cache_built_at"]
        age = now - cache_built_at
        if age < timedelta(hours=_REFRESH_RATE_LIMIT_HOURS):
            retry_after = int((timedelta(hours=_REFRESH_RATE_LIMIT_HOURS) - age).total_seconds())
            return JSONResponse(
                status_code=429,
                content=ErrorResponse(
                    error=ErrorDetail(
                        code="day_close_rate_limited",
                        message=(
                            f"A day-close refresh for {cache_key!r} was performed recently. "
                            f"Retry after {retry_after} seconds."
                        ),
                        butler="chronicler",
                        details={"retry_after_seconds": retry_after},
                    )
                ).model_dump(exclude_none=True),
            )

    # ── Dispatch guard ────────────────────────────────────────────────────────
    if dispatch_fn is None:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="dispatch_unavailable",
                    message="Day-close dispatch is not available in this deployment mode.",
                    butler="chronicler",
                )
            ).model_dump(exclude_none=True),
        )

    # ── Look up the chronicler_day_close prompt from scheduled_tasks ──────────
    task_row = await pool.fetchrow(
        "SELECT prompt FROM scheduled_tasks WHERE name = $1 AND enabled = true",
        DAY_CLOSE_TASK_NAME,
    )
    if task_row is None or not task_row["prompt"]:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="task_not_found",
                    message=f"Scheduled task {DAY_CLOSE_TASK_NAME!r} not found or has no prompt.",
                    butler="chronicler",
                )
            ).model_dump(exclude_none=True),
        )

    # ── Dispatch — re-uses the same prompt as the cron schedule ───────────────
    result = await dispatch_fn(
        prompt=task_row["prompt"],
        trigger_source=f"api:day_close_refresh:{body.date.isoformat()}",
    )

    # ── Write the fresh cache row ─────────────────────────────────────────────
    # Anchor run_at to the requested date so _compute_day_window targets body.date.
    # _compute_day_window returns yesterday = run_at.date() - 1, so we pass
    # midnight of body.date + 1 day to ensure the computed window covers body.date.
    run_at = datetime.combine(body.date + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    await write_day_close_cache(
        pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=run_at,
    )

    # Fetch the freshly-written row to return the authoritative cache_built_at.
    new_row = await pool.fetchrow(
        "SELECT cache_built_at FROM tier2_cache WHERE cache_key = $1 AND superseded_at IS NULL",
        cache_key,
    )
    if new_row is None:
        # Dispatch succeeded but write was a no-op (e.g. result had no output).
        return JSONResponse(
            status_code=502,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="cache_write_failed",
                    message="Day-close dispatch completed but no cache row was written.",
                    butler="chronicler",
                )
            ).model_dump(exclude_none=True),
        )

    return DayCloseRefreshResponse(
        cache_key=cache_key,
        cache_built_at=new_row["cache_built_at"],
    )
