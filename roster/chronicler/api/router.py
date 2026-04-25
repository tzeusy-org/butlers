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
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta

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
else:  # pragma: no cover — defensive
    raise RuntimeError("Failed to load chronicler API models module")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chronicler", tags=["chronicler"])

BUTLER_DB = "chronicler"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


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
    from collections import defaultdict

    # Group checkpoint rows by source_name.
    checkpoints_by_source: dict[str, list[Any]] = defaultdict(list)
    for cp in checkpoint_rows:
        checkpoints_by_source[cp["source_name"]].append(cp)

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
