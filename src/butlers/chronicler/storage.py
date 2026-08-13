"""Chronicler storage and query helpers.

Idempotent upserts, correction overlays, overlap queries, checkpoint
updates, and source registration. All operations scope to the
``chronicler`` schema via the runtime search_path (set by the butler
daemon through ``butler_chronicler_rw``).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from butlers.chronicler.confidence import evidence_refs_from_event_ids
from butlers.chronicler.models import (
    Compatibility,
    Confidence,
    CorrectedEpisode,
    CorrectedPointEvent,
    DailyRollup,
    DailyRollupFlag,
    Episode,
    Layer,
    LinkRelation,
    Override,
    OverrideTarget,
    PointEvent,
    Precision,
    Privacy,
    ProjectionCheckpoint,
    Routine,
    RoutineOrigin,
    SourceAdapterState,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


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


def _coerce_ref_list(value: Any) -> list[str]:
    """Coerce a jsonb ``evidence_refs`` column into a list of strings.

    The jsonb codec decodes to a Python list already; this guards the edge
    cases (NULL, a raw JSON string, a non-list payload) defensively.
    """
    if value is None:
        return []
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        value = loaded
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _row_layer(row: asyncpg.Record, keys: Any) -> Layer:
    return Layer(row["layer"]) if "layer" in keys else Layer.EVIDENCE


def _row_to_point_event(row: asyncpg.Record) -> PointEvent:
    keys = set(row.keys())
    return PointEvent(
        id=row["id"],
        source_name=row["source_name"],
        source_ref=row["source_ref"],
        event_type=row["event_type"],
        occurred_at=row["occurred_at"],
        precision=Precision(row["precision"]),
        title=row["title"],
        payload=_coerce_payload(row["payload"]),
        privacy=Privacy(row["privacy"]),
        retention_days=row["retention_days"],
        tombstone_at=row["tombstone_at"],
        tombstone_reason=row["tombstone_reason"],
        entity_id=row["entity_id"] if "entity_id" in keys else None,
        layer=_row_layer(row, keys),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_episode(row: asyncpg.Record) -> Episode:
    keys = set(row.keys())
    return Episode(
        id=row["id"],
        source_name=row["source_name"],
        source_ref=row["source_ref"],
        episode_type=row["episode_type"],
        start_at=row["start_at"],
        end_at=row["end_at"],
        precision=Precision(row["precision"]),
        title=row["title"],
        payload=_coerce_payload(row["payload"]),
        privacy=Privacy(row["privacy"]),
        retention_days=row["retention_days"],
        tombstone_at=row["tombstone_at"],
        tombstone_reason=row["tombstone_reason"],
        participant_entity_ids=(
            list(row["participant_entity_ids"])
            if "participant_entity_ids" in keys and row["participant_entity_ids"] is not None
            else []
        ),
        layer=_row_layer(row, keys),
        confidence=Confidence(row["confidence"]) if "confidence" in keys else Confidence.LOW,
        evidence_refs=_coerce_ref_list(row["evidence_refs"]) if "evidence_refs" in keys else [],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_corrected_episode(row: asyncpg.Record) -> CorrectedEpisode:
    keys = set(row.keys())
    return CorrectedEpisode(
        id=row["id"],
        source_name=row["source_name"],
        source_ref=row["source_ref"],
        episode_type=row["episode_type"],
        start_at=row["start_at"],
        end_at=row["end_at"],
        precision=Precision(row["precision"]),
        title=row["title"],
        payload=_coerce_payload(row["payload"]),
        privacy=Privacy(row["privacy"]),
        retention_days=row["retention_days"],
        tombstone_at=row["tombstone_at"],
        canonical_start_at=row["canonical_start_at"],
        canonical_end_at=row["canonical_end_at"],
        canonical_title=row["canonical_title"],
        canonical_privacy=Privacy(row["canonical_privacy"]),
        corrected_at=row["corrected_at"],
        correction_note=row["correction_note"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        participant_entity_ids=(
            list(row["participant_entity_ids"])
            if "participant_entity_ids" in keys and row["participant_entity_ids"] is not None
            else []
        ),
        layer=_row_layer(row, keys),
        confidence=Confidence(row["confidence"]) if "confidence" in keys else Confidence.LOW,
        evidence_refs=_coerce_ref_list(row["evidence_refs"]) if "evidence_refs" in keys else [],
    )


def _row_to_corrected_point_event(row: asyncpg.Record) -> CorrectedPointEvent:
    keys = set(row.keys())
    return CorrectedPointEvent(
        id=row["id"],
        source_name=row["source_name"],
        source_ref=row["source_ref"],
        event_type=row["event_type"],
        occurred_at=row["occurred_at"],
        precision=Precision(row["precision"]),
        title=row["title"],
        payload=_coerce_payload(row["payload"]),
        privacy=Privacy(row["privacy"]),
        retention_days=row["retention_days"],
        tombstone_at=row["tombstone_at"],
        canonical_occurred_at=row["canonical_occurred_at"],
        canonical_title=row["canonical_title"],
        canonical_privacy=Privacy(row["canonical_privacy"]),
        corrected_at=row["corrected_at"],
        correction_note=row["correction_note"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        entity_id=row["entity_id"] if "entity_id" in keys else None,
        layer=_row_layer(row, keys),
    )


# ── Source registration ────────────────────────────────────────────────────


async def register_source(
    conn: asyncpg.Connection | asyncpg.Pool,
    state: SourceAdapterState,
) -> None:
    """Upsert a source adapter registration.

    Does not flip ``active`` — adapters activate themselves on first
    successful run via :func:`mark_source_active`.
    """
    await conn.execute(
        """
        INSERT INTO source_adapter_state (
            source_name, chronicler_compatibility, read_surface,
            boundary_semantics, optional_schema, schema_version,
            registered_at, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7, now()), now())
        ON CONFLICT (source_name) DO UPDATE SET
            chronicler_compatibility = EXCLUDED.chronicler_compatibility,
            read_surface = EXCLUDED.read_surface,
            boundary_semantics = EXCLUDED.boundary_semantics,
            optional_schema = EXCLUDED.optional_schema,
            schema_version = EXCLUDED.schema_version,
            updated_at = now()
        """,
        state.source_name,
        state.chronicler_compatibility.value,
        state.read_surface,
        state.boundary_semantics,
        state.optional_schema,
        state.schema_version,
        state.registered_at,
    )


async def mark_source_active(
    conn: asyncpg.Connection | asyncpg.Pool,
    source_name: str,
    *,
    active: bool,
    inactive_reason: str | None = None,
) -> None:
    """Toggle a source adapter's active flag."""
    await conn.execute(
        """
        UPDATE source_adapter_state
        SET active = $2,
            inactive_reason = $3,
            updated_at = now()
        WHERE source_name = $1
        """,
        source_name,
        active,
        None if active else inactive_reason,
    )


async def get_source_state(
    conn: asyncpg.Connection | asyncpg.Pool,
    source_name: str,
) -> SourceAdapterState | None:
    row = await conn.fetchrow(
        "SELECT * FROM source_adapter_state WHERE source_name = $1",
        source_name,
    )
    if row is None:
        return None
    return SourceAdapterState(
        source_name=row["source_name"],
        chronicler_compatibility=Compatibility(row["chronicler_compatibility"]),
        read_surface=row["read_surface"],
        boundary_semantics=row["boundary_semantics"],
        optional_schema=row["optional_schema"],
        active=row["active"],
        inactive_reason=row["inactive_reason"],
        schema_version=row["schema_version"],
        registered_at=row["registered_at"],
        updated_at=row["updated_at"],
    )


# ── Checkpoints ────────────────────────────────────────────────────────────


async def upsert_checkpoint(
    conn: asyncpg.Connection | asyncpg.Pool,
    source_name: str,
    *,
    watermark: datetime | None = None,
    watermark_id: int | None = None,
    success: bool,
    rows_projected: int = 0,
    error: str | None = None,
) -> None:
    """Record a projection run result on ``projection_checkpoints``.

    Operates on the **global** checkpoint row (``subsource = ''``).
    For per-schema checkpoints use :func:`upsert_checkpoint_subsource`.

    On success: updates watermark, watermark_id, last_success_at, rows_projected,
    clears last_error. On failure: records last_error and last_run_at but does
    NOT advance the watermark.

    ``watermark_id`` is the ``id`` of the last-projected source row, forming a
    tuple watermark ``(watermark, watermark_id)`` that eliminates the
    batch-boundary missed-row edge case when multiple rows share the same
    timestamp.  It may be ``None`` for adapters that do not yet carry the id
    through their result.
    """
    await _upsert_checkpoint_row(
        conn,
        source_name=source_name,
        subsource="",
        watermark=watermark,
        watermark_id=watermark_id,
        success=success,
        rows_projected=rows_projected,
        error=error,
    )


async def upsert_checkpoint_subsource(
    conn: asyncpg.Connection | asyncpg.Pool,
    source_name: str,
    subsource: str,
    *,
    watermark: datetime | None = None,
    watermark_id: int | None = None,
    success: bool,
    rows_projected: int = 0,
    error: str | None = None,
) -> None:
    """Record a projection run result for a specific sub-source.

    ``subsource`` is a non-empty string identifying the sub-source within
    the adapter (e.g. a butler schema name for ``CoreSessionsAdapter``).
    Per-sub-source rows are keyed on ``(source_name, subsource)`` and
    tracked independently of the global checkpoint.

    ``watermark_id`` is the ``id`` of the last-projected source row; see
    :func:`upsert_checkpoint` for full semantics.
    """
    if not subsource:
        raise ValueError("subsource must be a non-empty string")
    await _upsert_checkpoint_row(
        conn,
        source_name=source_name,
        subsource=subsource,
        watermark=watermark,
        watermark_id=watermark_id,
        success=success,
        rows_projected=rows_projected,
        error=error,
    )


async def _upsert_checkpoint_row(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    source_name: str,
    subsource: str,
    watermark: datetime | None,
    watermark_id: int | None,
    success: bool,
    rows_projected: int,
    error: str | None,
) -> None:
    """Internal upsert shared by global and per-subsource checkpoint writes.

    After migration 002 the primary key is ``(source_name, subsource)``
    where ``subsource = ''`` denotes the global (adapter-level) row.
    After migration 005, ``watermark_id`` tracks the source row ``id`` of
    the last-projected row alongside the timestamp watermark, enabling
    tuple-comparison ``WHERE (ts, id) > ($1, $2)`` in adapters.
    """
    now = _utcnow()
    if success:
        await conn.execute(
            """
            INSERT INTO projection_checkpoints (
                source_name, subsource, watermark, watermark_id,
                last_run_at, last_success_at,
                last_error, rows_projected, run_count, updated_at
            )
            VALUES ($1, $2, $3, $6, $4, $4, NULL, $5, 1, $4)
            ON CONFLICT (source_name, subsource) DO UPDATE SET
                watermark = COALESCE(EXCLUDED.watermark, projection_checkpoints.watermark),
                watermark_id = CASE
                    WHEN EXCLUDED.watermark IS NOT NULL THEN EXCLUDED.watermark_id
                    ELSE COALESCE(EXCLUDED.watermark_id, projection_checkpoints.watermark_id)
                END,
                last_run_at = EXCLUDED.last_run_at,
                last_success_at = EXCLUDED.last_success_at,
                last_error = NULL,
                rows_projected = projection_checkpoints.rows_projected + EXCLUDED.rows_projected,
                run_count = projection_checkpoints.run_count + 1,
                updated_at = EXCLUDED.updated_at
            """,
            source_name,
            subsource,
            watermark,
            now,
            rows_projected,
            watermark_id,
        )
    else:
        await conn.execute(
            """
            INSERT INTO projection_checkpoints (
                source_name, subsource, watermark, watermark_id,
                last_run_at, last_success_at,
                last_error, rows_projected, run_count, updated_at
            )
            VALUES ($1, $2, NULL, NULL, $3, NULL, $4, 0, 1, $3)
            ON CONFLICT (source_name, subsource) DO UPDATE SET
                last_run_at = EXCLUDED.last_run_at,
                last_error = EXCLUDED.last_error,
                run_count = projection_checkpoints.run_count + 1,
                updated_at = EXCLUDED.updated_at
            """,
            source_name,
            subsource,
            now,
            error,
        )


def _row_to_checkpoint(row: asyncpg.Record) -> ProjectionCheckpoint:
    raw_subsource = row["subsource"] if "subsource" in row.keys() else ""
    keys = set(row.keys())
    watermark_id: int | None = row["watermark_id"] if "watermark_id" in keys else None
    return ProjectionCheckpoint(
        source_name=row["source_name"],
        subsource=raw_subsource if raw_subsource else None,
        watermark=row["watermark"],
        watermark_id=watermark_id,
        last_run_at=row["last_run_at"],
        last_success_at=row["last_success_at"],
        last_error=row["last_error"],
        rows_projected=row["rows_projected"],
        run_count=row["run_count"],
        updated_at=row["updated_at"],
    )


async def get_checkpoint(
    conn: asyncpg.Connection | asyncpg.Pool,
    source_name: str,
) -> ProjectionCheckpoint | None:
    """Fetch the global checkpoint (``subsource = ''``) for a source."""
    row = await conn.fetchrow(
        "SELECT * FROM projection_checkpoints WHERE source_name = $1 AND subsource = ''",
        source_name,
    )
    if row is None:
        return None
    return _row_to_checkpoint(row)


async def get_checkpoint_subsource(
    conn: asyncpg.Connection | asyncpg.Pool,
    source_name: str,
    subsource: str,
) -> ProjectionCheckpoint | None:
    """Fetch the per-subsource checkpoint for ``(source_name, subsource)``."""
    if not subsource:
        raise ValueError("subsource must be a non-empty string")
    row = await conn.fetchrow(
        "SELECT * FROM projection_checkpoints WHERE source_name = $1 AND subsource = $2",
        source_name,
        subsource,
    )
    if row is None:
        return None
    return _row_to_checkpoint(row)


# ── Point events ──────────────────────────────────────────────────────────


async def upsert_point_event(
    conn: asyncpg.Connection | asyncpg.Pool,
    event: PointEvent,
) -> PointEvent:
    """Idempotent upsert on ``(source_name, source_ref)``.

    Updates mutable fields (title, payload, precision, privacy,
    retention, tombstone, occurred_at, entity_id) so replays with
    corrected source data are reflected.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO point_events (
            source_name, source_ref, event_type, occurred_at, precision,
            title, payload, privacy, retention_days, tombstone_at, tombstone_reason,
            entity_id, layer
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        ON CONFLICT (source_name, source_ref) DO UPDATE SET
            event_type = EXCLUDED.event_type,
            occurred_at = EXCLUDED.occurred_at,
            precision = EXCLUDED.precision,
            title = EXCLUDED.title,
            payload = EXCLUDED.payload,
            privacy = EXCLUDED.privacy,
            retention_days = EXCLUDED.retention_days,
            tombstone_at = EXCLUDED.tombstone_at,
            tombstone_reason = EXCLUDED.tombstone_reason,
            entity_id = EXCLUDED.entity_id,
            layer = EXCLUDED.layer,
            updated_at = now()
        RETURNING *
        """,
        event.source_name,
        event.source_ref,
        event.event_type,
        event.occurred_at,
        event.precision.value,
        event.title,
        event.payload,
        event.privacy.value,
        event.retention_days,
        event.tombstone_at,
        event.tombstone_reason,
        event.entity_id,
        event.layer.value,
    )
    return _row_to_point_event(row)


# ── Episodes ──────────────────────────────────────────────────────────────


async def upsert_episode(
    conn: asyncpg.Connection | asyncpg.Pool,
    episode: Episode,
) -> Episode:
    """Idempotent upsert on ``(source_name, source_ref)``.

    Open-ended episodes (``end_at IS NULL``) are permitted. Replays that
    close an open episode update ``end_at`` in place.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO episodes (
            source_name, source_ref, episode_type, start_at, end_at,
            precision, title, payload, privacy, retention_days, tombstone_at, tombstone_reason,
            layer, confidence, evidence_refs
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        ON CONFLICT (source_name, source_ref) DO UPDATE SET
            episode_type = EXCLUDED.episode_type,
            start_at = EXCLUDED.start_at,
            end_at = EXCLUDED.end_at,
            precision = EXCLUDED.precision,
            title = EXCLUDED.title,
            payload = EXCLUDED.payload,
            privacy = EXCLUDED.privacy,
            retention_days = EXCLUDED.retention_days,
            tombstone_at = EXCLUDED.tombstone_at,
            tombstone_reason = EXCLUDED.tombstone_reason,
            layer = EXCLUDED.layer,
            confidence = EXCLUDED.confidence,
            evidence_refs = EXCLUDED.evidence_refs,
            updated_at = now()
        RETURNING *
        """,
        episode.source_name,
        episode.source_ref,
        episode.episode_type,
        episode.start_at,
        episode.end_at,
        episode.precision.value,
        episode.title,
        episode.payload,
        episode.privacy.value,
        episode.retention_days,
        episode.tombstone_at,
        episode.tombstone_reason,
        episode.layer.value,
        episode.confidence.value,
        list(episode.evidence_refs),
    )
    return _row_to_episode(row)


# ── Episode-event links ───────────────────────────────────────────────────


async def link_event_to_episode(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    episode_id: UUID,
    event_id: UUID,
    relation: LinkRelation = LinkRelation.SUPPORTS,
) -> None:
    await conn.execute(
        """
        INSERT INTO episode_event_links (episode_id, event_id, relation)
        VALUES ($1, $2, $3)
        ON CONFLICT (episode_id, event_id, relation) DO NOTHING
        """,
        episode_id,
        event_id,
        relation.value,
    )


async def list_episode_events(
    conn: asyncpg.Connection | asyncpg.Pool,
    episode_id: UUID,
) -> list[CorrectedPointEvent]:
    rows = await conn.fetch(
        """
        SELECT v.*
        FROM episode_event_links l
        JOIN v_point_events_corrected v ON v.id = l.event_id
        WHERE l.episode_id = $1
        ORDER BY v.occurred_at ASC
        """,
        episode_id,
    )
    return [_row_to_corrected_point_event(r) for r in rows]


async def refresh_episode_evidence_refs(
    conn: asyncpg.Connection | asyncpg.Pool,
    episode_id: UUID,
) -> list[str]:
    """Populate ``episodes.evidence_refs`` from ``episode_event_links``.

    The canonical evidence chain lives in ``episode_event_links``; this
    denormalizes it onto the ``episodes.evidence_refs`` convenience surface as an
    ordered (by occurrence), de-duplicated list of point-event id strings so
    consumers can read an activity's evidence without a join. Idempotent: it
    overwrites ``evidence_refs`` with the current link set, so re-running after
    links are added/removed keeps the surface in sync. Returns the refs written.
    """
    rows = await conn.fetch(
        """
        SELECT l.event_id
        FROM episode_event_links l
        JOIN point_events p ON p.id = l.event_id
        WHERE l.episode_id = $1
        ORDER BY p.occurred_at ASC, l.event_id ASC
        """,
        episode_id,
    )
    refs = evidence_refs_from_event_ids(r["event_id"] for r in rows)
    await conn.execute(
        "UPDATE episodes SET evidence_refs = $2, updated_at = now() WHERE id = $1",
        episode_id,
        refs,
    )
    return refs


# ── Read queries (corrected views by default) ─────────────────────────────


async def get_episode(
    conn: asyncpg.Connection | asyncpg.Pool,
    episode_id: UUID,
    *,
    include_tombstoned: bool = False,
) -> CorrectedEpisode | None:
    clause = "" if include_tombstoned else "AND tombstone_at IS NULL"
    row = await conn.fetchrow(
        f"SELECT * FROM v_episodes_corrected WHERE id = $1 {clause}",
        episode_id,
    )
    if row is None:
        return None
    return _row_to_corrected_episode(row)


async def list_episodes(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    start_from: datetime | None = None,
    start_to: datetime | None = None,
    source_name: str | None = None,
    episode_type: str | None = None,
    participant_entity_id: UUID | None = None,
    overlaps_with: tuple[datetime, datetime] | None = None,
    include_tombstoned: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[CorrectedEpisode]:
    clauses: list[str] = []
    args: list[Any] = []
    if not include_tombstoned:
        clauses.append("tombstone_at IS NULL")
    if start_from is not None:
        args.append(start_from)
        clauses.append(f"start_at >= ${len(args)}")
    if start_to is not None:
        args.append(start_to)
        clauses.append(f"start_at < ${len(args)}")
    if source_name is not None:
        args.append(source_name)
        clauses.append(f"source_name = ${len(args)}")
    if episode_type is not None:
        args.append(episode_type)
        clauses.append(f"episode_type = ${len(args)}")
    if participant_entity_id is not None:
        args.append(participant_entity_id)
        clauses.append(f"${len(args)}::uuid = ANY(participant_entity_ids)")
    if overlaps_with is not None:
        window_start, window_end = overlaps_with
        args.append(window_end)
        clauses.append(f"start_at < ${len(args)}")
        args.append(window_start)
        clauses.append(f"(end_at IS NULL OR end_at > ${len(args)})")
    where_clause = " AND ".join(clauses) if clauses else "TRUE"
    args.append(limit)
    args.append(offset)
    rows = await conn.fetch(
        f"""
        SELECT * FROM v_episodes_corrected
        WHERE {where_clause}
        ORDER BY start_at DESC
        LIMIT ${len(args) - 1} OFFSET ${len(args)}
        """,
        *args,
    )
    return [_row_to_corrected_episode(r) for r in rows]


async def list_point_events(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    source_name: str | None = None,
    event_type: str | None = None,
    entity_id: UUID | None = None,
    include_tombstoned: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[CorrectedPointEvent]:
    clauses: list[str] = []
    args: list[Any] = []
    if not include_tombstoned:
        clauses.append("tombstone_at IS NULL")
    if occurred_from is not None:
        args.append(occurred_from)
        clauses.append(f"occurred_at >= ${len(args)}")
    if occurred_to is not None:
        args.append(occurred_to)
        clauses.append(f"occurred_at < ${len(args)}")
    if source_name is not None:
        args.append(source_name)
        clauses.append(f"source_name = ${len(args)}")
    if event_type is not None:
        args.append(event_type)
        clauses.append(f"event_type = ${len(args)}")
    if entity_id is not None:
        args.append(entity_id)
        clauses.append(f"entity_id = ${len(args)}")
    where_clause = " AND ".join(clauses) if clauses else "TRUE"
    args.append(limit)
    args.append(offset)
    rows = await conn.fetch(
        f"""
        SELECT * FROM v_point_events_corrected
        WHERE {where_clause}
        ORDER BY occurred_at DESC
        LIMIT ${len(args) - 1} OFFSET ${len(args)}
        """,
        *args,
    )
    return [_row_to_corrected_point_event(r) for r in rows]


async def list_overlapping_episodes(
    conn: asyncpg.Connection | asyncpg.Pool,
    episode_id: UUID,
) -> list[CorrectedEpisode]:
    """Return corrected episodes whose time span overlaps the target.

    Includes the target episode's own row; callers may filter it out
    client-side if undesired.
    """
    row = await conn.fetchrow(
        "SELECT start_at, end_at FROM v_episodes_corrected WHERE id = $1",
        episode_id,
    )
    if row is None:
        return []
    start = row["start_at"]
    end = row["end_at"] or start
    rows = await conn.fetch(
        """
        SELECT * FROM v_episodes_corrected
        WHERE tombstone_at IS NULL
          AND start_at <= $2
          AND (end_at IS NULL OR end_at >= $1)
        ORDER BY start_at DESC
        """,
        start,
        end,
    )
    return [_row_to_corrected_episode(r) for r in rows]


# ── Corrections / overrides ───────────────────────────────────────────────


async def insert_override(
    conn: asyncpg.Connection | asyncpg.Pool,
    override: Override,
) -> Override:
    row = await conn.fetchrow(
        """
        INSERT INTO overrides (
            target_kind, target_id, corrected_start_at, corrected_end_at,
            corrected_title, corrected_privacy, corrected_tombstone_at,
            note, submitted_by
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id, created_at
        """,
        override.target_kind.value,
        override.target_id,
        override.corrected_start_at,
        override.corrected_end_at,
        override.corrected_title,
        override.corrected_privacy.value if override.corrected_privacy else None,
        override.corrected_tombstone_at,
        override.note,
        override.submitted_by,
    )
    override.id = row["id"]
    override.created_at = row["created_at"]
    return override


async def list_overrides_for(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    target_kind: OverrideTarget,
    target_id: UUID,
) -> list[Override]:
    rows = await conn.fetch(
        """
        SELECT * FROM overrides
        WHERE target_kind = $1 AND target_id = $2
        ORDER BY created_at DESC
        """,
        target_kind.value,
        target_id,
    )
    result: list[Override] = []
    for r in rows:
        result.append(
            Override(
                id=r["id"],
                target_kind=OverrideTarget(r["target_kind"]),
                target_id=r["target_id"],
                corrected_start_at=r["corrected_start_at"],
                corrected_end_at=r["corrected_end_at"],
                corrected_title=r["corrected_title"],
                corrected_privacy=(
                    Privacy(r["corrected_privacy"]) if r["corrected_privacy"] is not None else None
                ),
                corrected_tombstone_at=r["corrected_tombstone_at"],
                note=r["note"],
                submitted_by=r["submitted_by"],
                created_at=r["created_at"],
            )
        )
    return result


# ── Idempotency key registry (optional batch-level dedup) ─────────────────


async def record_idempotency(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    source_name: str,
    key: str,
) -> bool:
    """Record a batch-level idempotency key. Returns True iff it was new."""
    row = await conn.fetchrow(
        """
        INSERT INTO idempotency_keys (source_name, key)
        VALUES ($1, $2)
        ON CONFLICT (source_name, key) DO UPDATE SET
            last_seen_at = now(),
            hit_count = idempotency_keys.hit_count + 1
        RETURNING (xmax = 0) AS inserted
        """,
        source_name,
        key,
    )
    return bool(row["inserted"])


# ── Tier 2 cache (day-close prose summaries) ──────────────────────────────


async def upsert_tier2_cache(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    cache_key: str,
    start_at: datetime,
    end_at: datetime,
    prose: str,
    provenance_refs: list[Any],
    cache_built_at: datetime | None = None,
    date_label: str | None = None,
    invalid_reason: str | None = None,
) -> None:
    """Idempotent INSERT-or-UPDATE for a Tier 2 day-close cache entry.

    Idempotency key is ``cache_key`` (PRIMARY KEY on the table).  When a row
    already exists for the key it is replaced with the new prose, window, and
    provenance.  ``superseded_at`` is left NULL so the row remains "active"
    (queries filter ``WHERE superseded_at IS NULL``).  The column is reserved
    for a future multi-row versioning scheme per §D8; setting it on the live
    row would silently drop it from all active-entry indexes.

    Args:
        conn: asyncpg connection or pool.
        cache_key: Primary key string, e.g.
            ``day_close:2026-04-25:tz:Asia/Singapore``.
        start_at: Start of the window covered by this summary.
        end_at: End of the window covered by this summary.
        prose: LLM-generated prose summary.
        provenance_refs: List of source_ref strings cited in the prose.
        cache_built_at: Override for the build timestamp (defaults to ``now()``
            inside the DB, useful for testing).
        date_label: Structured local-day label the candidate claims (day-close
            admission contract; ``None`` for non-day-close cache keys such as
            ``episode_explain:*``).
        invalid_reason: ``inadmissible_prose`` | ``date_mismatch`` when the
            row failed the deterministic day-close admission predicate.  A
            non-``None`` row is retained for audit but is never returned as
            renderable prose by the reader.
    """
    await conn.execute(
        """
        INSERT INTO tier2_cache
            (cache_key, start_at, end_at, prose, provenance_refs, cache_built_at,
             date_label, invalid_reason)
        VALUES ($1, $2, $3, $4, $5, COALESCE($6, now()), $7, $8)
        ON CONFLICT (cache_key) DO UPDATE
            SET prose            = EXCLUDED.prose,
                start_at         = EXCLUDED.start_at,
                end_at           = EXCLUDED.end_at,
                provenance_refs  = EXCLUDED.provenance_refs,
                cache_built_at   = EXCLUDED.cache_built_at,
                date_label       = EXCLUDED.date_label,
                invalid_reason   = EXCLUDED.invalid_reason,
                superseded_at    = NULL
        """,
        cache_key,
        start_at,
        end_at,
        prose,
        provenance_refs,
        cache_built_at,
        date_label,
        invalid_reason,
    )


# ── Carryover (cross-batch open-episode state) ────────────────────────────


async def get_carryover(
    conn: asyncpg.Connection | asyncpg.Pool,
    source_name: str,
) -> dict:
    """Return the carryover JSONB blob for the global (subsource='') checkpoint.

    Adapters persist open-episode state here so the next batch can continue
    stitching across the boundary.  Returns ``{}`` when the column is NULL,
    the row does not exist, or the DB predates migration ``chronicler_006``.
    """
    try:
        raw = await conn.fetchval(
            """
            SELECT carryover
            FROM projection_checkpoints
            WHERE source_name = $1 AND subsource = ''
            """,
            source_name,
        )
    except asyncpg.PostgresError:
        return {}
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    if isinstance(raw, dict):
        return raw
    return {}


async def save_carryover(
    conn: asyncpg.Connection | asyncpg.Pool,
    source_name: str,
    carryover: dict,
) -> None:
    """Persist open-episode carryover state for the next batch run.

    Writes to the global (``subsource = ''``) checkpoint row.  The row is
    created (with ``carryover`` only) if it does not yet exist, but the
    normal ``upsert_checkpoint`` path is expected to create it first.
    """
    try:
        await conn.execute(
            """
            INSERT INTO projection_checkpoints (source_name, subsource, carryover)
            VALUES ($1, '', $2)
            ON CONFLICT (source_name, subsource) DO UPDATE
            SET carryover = EXCLUDED.carryover
            """,
            source_name,
            carryover,
        )
    except asyncpg.PostgresError:
        return


# ── Routines (bu-whhll.9) ──────────────────────────────────────────────────


def _row_to_routine(row: asyncpg.Record) -> Routine:
    keys = set(row.keys())
    return Routine(
        id=row["id"],
        dow_mask=row["dow_mask"],
        window_start_local=row["window_start_local"],
        window_end_local=row["window_end_local"],
        timezone=row["timezone"],
        label=row["label"],
        support_count=row["support_count"],
        confidence=row["confidence"],
        evidence_summary=_coerce_payload(row["evidence_summary"]),
        origin=RoutineOrigin(row["origin"]),
        enabled=row["enabled"],
        last_confirmed_at=row["last_confirmed_at"] if "last_confirmed_at" in keys else None,
        missed_mine_cycles=row["missed_mine_cycles"] if "missed_mine_cycles" in keys else 0,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@dataclass(frozen=True)
class RoutineStalenessResult:
    """Outcome of one evidence-backed missed-pattern reconciliation."""

    routines_marked_stale: int = 0
    routines_auto_disabled: int = 0


async def upsert_mined_routine(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    dow_mask: int,
    window_start_local: Any,
    window_end_local: Any,
    label: str,
    support_count: int,
    confidence: float,
    evidence_summary: dict[str, Any],
    timezone: str = "Asia/Singapore",
    confirmed_at: datetime | None = None,
) -> Routine:
    """Idempotent upsert of a mined routine, keyed on ``dow_mask``.

    Targets the partial unique index ``routines_mined_dow_mask_idx``
    (``dow_mask`` WHERE ``origin = 'mined'``) added by migration
    ``chronicler_018``: at most one mined row exists per exact
    day-of-week combination. A re-run refreshes the mining-derived columns
    (window bounds, timezone, support_count, confidence, evidence_summary)
    but deliberately never touches ``label`` or ``enabled`` — those are the
    owner's to edit via ``PATCH /api/chronicler/routines/{id}`` and must
    survive every subsequent re-mine. Re-detection does reset the mining-owned
    missed-cycle state and records the supplied confirmation time.
    """
    confirmed_at = confirmed_at or _utcnow()
    if confirmed_at.tzinfo is None:
        raise ValueError("confirmed_at must be timezone-aware")
    row = await conn.fetchrow(
        """
        INSERT INTO routines (
            dow_mask, window_start_local, window_end_local, timezone,
            label, support_count, confidence, evidence_summary, origin,
            last_confirmed_at, missed_mine_cycles, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'mined', $9, 0, $9)
        ON CONFLICT (dow_mask) WHERE origin = 'mined' DO UPDATE SET
            window_start_local = EXCLUDED.window_start_local,
            window_end_local = EXCLUDED.window_end_local,
            timezone = EXCLUDED.timezone,
            support_count = EXCLUDED.support_count,
            confidence = EXCLUDED.confidence,
            evidence_summary = EXCLUDED.evidence_summary,
            last_confirmed_at = EXCLUDED.last_confirmed_at,
            missed_mine_cycles = 0,
            updated_at = EXCLUDED.updated_at
        RETURNING *
        """,
        dow_mask,
        window_start_local,
        window_end_local,
        timezone,
        label,
        support_count,
        confidence,
        evidence_summary,
        confirmed_at,
    )
    return _row_to_routine(row)


async def record_missing_mined_routines(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    detected_dow_masks: Sequence[int],
    missed_at: datetime,
    auto_disable_after_misses: int,
) -> RoutineStalenessResult:
    """Record one evidence-backed missed mining cycle for absent mined rows.

    The caller decides whether its completed miner run carried enough primary
    evidence to justify a miss. This helper only reconciles rows with
    ``origin='mined'``; owner-declared schedules are never selected. Existing
    owner disables remain false, and re-detection is responsible for resetting
    the count without ever re-enabling a row.
    """
    if not isinstance(auto_disable_after_misses, int) or auto_disable_after_misses <= 0:
        raise ValueError("auto_disable_after_misses must be a positive integer")
    if missed_at.tzinfo is None:
        raise ValueError("missed_at must be timezone-aware")

    rows = await conn.fetch(
        """
        WITH candidates AS (
            SELECT id, enabled AS was_enabled
            FROM routines
            WHERE origin = 'mined'
              AND dow_mask <> ALL($1::smallint[])
            FOR UPDATE
        )
        UPDATE routines AS routine
        SET missed_mine_cycles = routine.missed_mine_cycles + 1,
            enabled = CASE
                WHEN routine.enabled
                     AND routine.missed_mine_cycles + 1 >= $3
                    THEN false
                ELSE routine.enabled
            END,
            updated_at = $2
        FROM candidates
        WHERE routine.id = candidates.id
        RETURNING routine.*, candidates.was_enabled
        """,
        list(detected_dow_masks),
        missed_at,
        auto_disable_after_misses,
    )
    routines_auto_disabled = sum(
        1 for row in rows if bool(row["was_enabled"]) and not bool(row["enabled"])
    )
    return RoutineStalenessResult(
        routines_marked_stale=len(rows),
        routines_auto_disabled=routines_auto_disabled,
    )


async def create_declared_routine(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    dow_mask: int,
    window_start_local: Any,
    window_end_local: Any,
    label: str,
    timezone: str = "Asia/Singapore",
    enabled: bool = True,
    evidence_summary: dict[str, Any] | None = None,
) -> Routine:
    """Insert an owner-declared routine (``origin='declared'``, bu-whhll.11).

    Declared rows are the owner's bootstrap schedule ("I work Mon-Fri
    09:30-19:30 at <label>"). They carry no mining statistics
    (``support_count``/``confidence`` stay 0) and are deliberately NOT
    constrained by the mined ``routines_mined_dow_mask_idx`` partial-unique
    index — the owner may declare any number of them, even overlapping a
    mined row on the same ``dow_mask``. The occupation-inference adapter reads
    enabled rows on its next run, so declaring a schedule makes inference work
    immediately rather than waiting weeks for the miner to accrue support.

    ``evidence_summary`` is written as a JSONB **dict** (never ``json.dumps``
    — the pool's JSONB codec encodes the dict; a pre-serialized string would
    double-encode). Defaults to a small marker recording the declared origin.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO routines (
            dow_mask, window_start_local, window_end_local, timezone,
            label, evidence_summary, origin, enabled
        )
        VALUES ($1, $2, $3, $4, $5, $6, 'declared', $7)
        RETURNING *
        """,
        dow_mask,
        window_start_local,
        window_end_local,
        timezone,
        label,
        evidence_summary if evidence_summary is not None else {"origin": "owner-declared"},
        enabled,
    )
    return _row_to_routine(row)


async def list_routines(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    enabled_only: bool = False,
) -> list[Routine]:
    """List routines ordered by dow_mask then window start.

    Set ``enabled_only=True`` for inference consumers (bu-whhll.10/.11) that
    must skip owner-disabled rows; the dashboard review surface wants both.
    """
    where = "WHERE enabled" if enabled_only else ""
    rows = await conn.fetch(
        f"""
        SELECT * FROM routines
        {where}
        ORDER BY dow_mask ASC, window_start_local ASC
        """
    )
    return [_row_to_routine(r) for r in rows]


async def get_routine(
    conn: asyncpg.Connection | asyncpg.Pool,
    routine_id: UUID,
) -> Routine | None:
    row = await conn.fetchrow("SELECT * FROM routines WHERE id = $1", routine_id)
    if row is None:
        return None
    return _row_to_routine(row)


async def update_routine(
    conn: asyncpg.Connection | asyncpg.Pool,
    routine_id: UUID,
    *,
    enabled: bool | None = None,
    label: str | None = None,
    dow_mask: int | None = None,
    window_start_local: Any = None,
    window_end_local: Any = None,
    timezone: str | None = None,
) -> Routine | None:
    """Owner-review / owner-declare PATCH: update fields in place.

    All fields are optional; only the provided ones are updated. Returns
    ``None`` when ``routine_id`` does not exist.

    ``enabled``/``label`` are editable on any routine (the owner-review
    surface). The schedule columns (``dow_mask``, ``window_start_local``,
    ``window_end_local``, ``timezone``) are only ever passed for
    ``origin='declared'`` rows — the API layer rejects a schedule edit on a
    mined routine, whose windows the weekly miner owns (``upsert_mined_routine``).
    ``support_count``/``confidence``/``evidence_summary`` are never touched here.
    """
    updates: list[str] = []
    args: list[Any] = []
    if enabled is not None:
        args.append(enabled)
        updates.append(f"enabled = ${len(args)}")
    if label is not None:
        args.append(label)
        updates.append(f"label = ${len(args)}")
    if dow_mask is not None:
        args.append(dow_mask)
        updates.append(f"dow_mask = ${len(args)}")
    if window_start_local is not None:
        args.append(window_start_local)
        updates.append(f"window_start_local = ${len(args)}")
    if window_end_local is not None:
        args.append(window_end_local)
        updates.append(f"window_end_local = ${len(args)}")
    if timezone is not None:
        args.append(timezone)
        updates.append(f"timezone = ${len(args)}")

    if not updates:
        return await get_routine(conn, routine_id)

    updates.append("updated_at = now()")
    args.append(routine_id)
    row = await conn.fetchrow(
        f"""
        UPDATE routines
        SET {", ".join(updates)}
        WHERE id = ${len(args)}
        RETURNING *
        """,
        *args,
    )
    if row is None:
        return None
    return _row_to_routine(row)


async def adjust_routine_confidence(
    conn: asyncpg.Connection | asyncpg.Pool,
    routine_id: UUID,
    *,
    confidence_delta: float,
    support_delta: int = 0,
) -> Routine | None:
    """Reinforce or decay a routine's mined ``confidence`` in place.

    The weekly miner (``upsert_mined_routine``) fully recomputes ``confidence``
    from observed support, but a one-tap owner signal (the day-close gap
    interview, bu-whhll.12) should be able to nudge that statistic between
    mines: a confirmed workday reinforces (positive ``confidence_delta`` and a
    ``support_delta`` of +1), a corrected one decays (negative
    ``confidence_delta``). The result is clamped to the ``[0, 1]`` range the
    ``chronicler_018`` CHECK constraint enforces, so an accumulation of taps can
    never push the row outside its domain. ``support_count`` is floored at 0 for
    the same reason.

    Returns the updated :class:`~butlers.chronicler.models.Routine`, or ``None``
    when ``routine_id`` does not exist. Only these two mining-statistic columns
    are touched — window/label/enabled are left to their own owners.
    """
    row = await conn.fetchrow(
        """
        UPDATE routines
        SET confidence = LEAST(1.0, GREATEST(0.0, confidence + $2)),
            support_count = GREATEST(0, support_count + $3),
            updated_at = now()
        WHERE id = $1
        RETURNING *
        """,
        routine_id,
        confidence_delta,
        support_delta,
    )
    if row is None:
        return None
    return _row_to_routine(row)


async def delete_routine(
    conn: asyncpg.Connection | asyncpg.Pool,
    routine_id: UUID,
) -> bool:
    """Hard-delete a routine row. Returns ``True`` when a row was removed.

    Only owner-declared routines are deletable (the API layer enforces this):
    a mined routine deleted here would simply be re-created on the next weekly
    mine, so those are disabled rather than deleted.
    """
    row = await conn.fetchrow(
        "DELETE FROM routines WHERE id = $1 RETURNING id",
        routine_id,
    )
    return row is not None


# ── Daily rollups (bu-u30as, telemetry-distillation bead 3) ────────────────


def _row_to_daily_rollup(row: asyncpg.Record) -> DailyRollup:
    return DailyRollup(
        id=row["id"],
        local_date=row["local_date"],
        timezone=row["timezone"],
        lane=row["lane"],
        seconds=row["seconds"],
        episode_count=row["episode_count"],
        distinct_place_count=row["distinct_place_count"],
        narrative=row["narrative"],
        computed_at=row["computed_at"],
    )


async def upsert_daily_rollup(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    local_date: Any,
    lane: str,
    seconds: int,
    episode_count: int,
    timezone: str = "Asia/Singapore",
    distinct_place_count: int | None = None,
) -> DailyRollup:
    """Idempotent upsert of a per-(local_date, lane) rollup row.

    Targets the ``UNIQUE (local_date, lane)`` constraint added by migration
    ``chronicler_019``: re-running the materializer for a date it already
    rolled up (e.g. after a late-arriving correction/override) recomputes the
    row in place rather than creating a duplicate.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO daily_rollups (
            local_date, timezone, lane, seconds, episode_count, distinct_place_count
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (local_date, lane) DO UPDATE SET
            timezone = EXCLUDED.timezone,
            seconds = EXCLUDED.seconds,
            episode_count = EXCLUDED.episode_count,
            distinct_place_count = EXCLUDED.distinct_place_count,
            computed_at = now()
        RETURNING *
        """,
        local_date,
        timezone,
        lane,
        seconds,
        episode_count,
        distinct_place_count,
    )
    return _row_to_daily_rollup(row)


async def list_daily_rollups(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    local_date: Any | None = None,
) -> list[DailyRollup]:
    """List rollup rows, optionally filtered to a single ``local_date``.

    Ordered by ``local_date DESC, lane ASC`` for deterministic output.
    """
    if local_date is not None:
        rows = await conn.fetch(
            "SELECT * FROM daily_rollups WHERE local_date = $1 ORDER BY lane ASC",
            local_date,
        )
    else:
        rows = await conn.fetch("SELECT * FROM daily_rollups ORDER BY local_date DESC, lane ASC")
    return [_row_to_daily_rollup(r) for r in rows]


async def list_daily_rollups_range(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    start_date: Any,
    end_date: Any,
) -> list[DailyRollup]:
    """List rollup rows for every ``local_date`` in ``[start_date, end_date]`` inclusive.

    Ordered by ``local_date ASC, lane ASC``. Used by
    ``GET /api/chronicler/rollups`` to fetch a multi-day window in a single
    round trip instead of one ``list_daily_rollups`` call per day.
    """
    rows = await conn.fetch(
        "SELECT * FROM daily_rollups WHERE local_date BETWEEN $1 AND $2"
        " ORDER BY local_date ASC, lane ASC",
        start_date,
        end_date,
    )
    return [_row_to_daily_rollup(r) for r in rows]


# ── Daily rollup flags (bu-v76a7, telemetry-distillation bead 4) ───────────


def _row_to_daily_rollup_flag(row: asyncpg.Record) -> DailyRollupFlag:
    return DailyRollupFlag(
        id=row["id"],
        local_date=row["local_date"],
        flag_type=row["flag_type"],
        severity=row["severity"],
        detail=row["detail"] or {},
        narrative=row["narrative"],
        created_at=row["created_at"],
    )


async def upsert_daily_rollup_flag(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    local_date: Any,
    flag_type: str,
    severity: str = "info",
    detail: dict[str, Any] | None = None,
) -> DailyRollupFlag:
    """Idempotent upsert of a per-(local_date, flag_type) anomaly flag.

    Targets the ``UNIQUE (local_date, flag_type)`` constraint added by
    migration ``chronicler_019``: re-evaluating a day the flag rules already
    ran against (e.g. after a late-arriving correction) recomputes the row in
    place rather than creating a duplicate. Pair with
    :func:`delete_daily_rollup_flag` when a rule no longer holds on a re-run
    — the row must be removed, not left stale, or a fixed condition would
    keep reading as "still flagged" forever.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO daily_rollup_flags (local_date, flag_type, severity, detail)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (local_date, flag_type) DO UPDATE SET
            severity = EXCLUDED.severity,
            detail = EXCLUDED.detail
        RETURNING *
        """,
        local_date,
        flag_type,
        severity,
        detail or {},
    )
    return _row_to_daily_rollup_flag(row)


async def delete_daily_rollup_flag(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    local_date: Any,
    flag_type: str,
) -> None:
    """Remove a ``(local_date, flag_type)`` flag row if present.

    A no-op when no such row exists — callers use this to reconcile a day's
    flags to "this rule no longer holds" without checking existence first.
    """
    await conn.execute(
        "DELETE FROM daily_rollup_flags WHERE local_date = $1 AND flag_type = $2",
        local_date,
        flag_type,
    )


async def list_daily_rollup_flags(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    local_date: Any | None = None,
) -> list[DailyRollupFlag]:
    """List anomaly-flag rows, optionally filtered to a single ``local_date``.

    Ordered by ``local_date DESC, flag_type ASC`` for deterministic output.
    """
    if local_date is not None:
        rows = await conn.fetch(
            "SELECT * FROM daily_rollup_flags WHERE local_date = $1 ORDER BY flag_type ASC",
            local_date,
        )
    else:
        rows = await conn.fetch(
            "SELECT * FROM daily_rollup_flags ORDER BY local_date DESC, flag_type ASC"
        )
    return [_row_to_daily_rollup_flag(r) for r in rows]


async def list_daily_rollup_flags_range(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    start_date: Any,
    end_date: Any,
) -> list[DailyRollupFlag]:
    """List flag rows for every ``local_date`` in ``[start_date, end_date]`` inclusive.

    Ordered by ``local_date ASC, flag_type ASC``. Used by
    ``GET /api/chronicler/rollups`` alongside
    :func:`list_daily_rollups_range` so a multi-day window is two queries
    total, not two-per-day.
    """
    rows = await conn.fetch(
        "SELECT * FROM daily_rollup_flags WHERE local_date BETWEEN $1 AND $2"
        " ORDER BY local_date ASC, flag_type ASC",
        start_date,
        end_date,
    )
    return [_row_to_daily_rollup_flag(r) for r in rows]


# ── Daily rollup narration (bu-v9y18, telemetry-distillation bead 6) ───────


async def set_daily_rollup_day_narrative(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    local_date: Any,
    narrative: str,
) -> int:
    """Write the once-daily LLM day summary to every lane row for *local_date*.

    ``daily_rollups`` has no single per-day row (one row per ``(local_date,
    lane)``), so the day-level summary is broadcast identically to every
    existing lane row for that date. A no-op (returns ``0``) if no rollup
    rows exist yet for *local_date* — the narration orchestrator only calls
    this after confirming rollup rows exist (see
    ``narration.narrate_daily_rollup``), so a ``0`` here would indicate a
    caller bug, not a normal path.

    Returns the number of rows updated.
    """
    result = await conn.execute(
        "UPDATE daily_rollups SET narrative = $2 WHERE local_date = $1",
        local_date,
        narrative,
    )
    # asyncpg command tags look like "UPDATE 3"; parse the trailing count.
    return int(result.rsplit(" ", 1)[-1])


async def set_daily_rollup_flag_narrative(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    local_date: Any,
    flag_type: str,
    narrative: str,
) -> DailyRollupFlag | None:
    """Write a short LLM label for one already-existing ``(local_date,
    flag_type)`` flag row.

    Returns the updated row, or ``None`` if no such flag row exists (the
    narration pass never fabricates a flag — it only labels flags the
    deterministic evaluator in ``flags.py`` already wrote).
    """
    row = await conn.fetchrow(
        """
        UPDATE daily_rollup_flags SET narrative = $3
        WHERE local_date = $1 AND flag_type = $2
        RETURNING *
        """,
        local_date,
        flag_type,
        narrative,
    )
    if row is None:
        return None
    return _row_to_daily_rollup_flag(row)


__all__: Sequence[str] = (
    "create_declared_routine",
    "delete_daily_rollup_flag",
    "delete_routine",
    "get_carryover",
    "get_checkpoint",
    "get_checkpoint_subsource",
    "get_episode",
    "get_routine",
    "get_source_state",
    "insert_override",
    "link_event_to_episode",
    "list_daily_rollup_flags",
    "list_daily_rollup_flags_range",
    "list_daily_rollups",
    "list_daily_rollups_range",
    "list_episode_events",
    "list_episodes",
    "list_overlapping_episodes",
    "list_overrides_for",
    "list_point_events",
    "list_routines",
    "mark_source_active",
    "record_idempotency",
    "register_source",
    "save_carryover",
    "set_daily_rollup_day_narrative",
    "set_daily_rollup_flag_narrative",
    "update_routine",
    "upsert_checkpoint",
    "upsert_checkpoint_subsource",
    "upsert_daily_rollup",
    "upsert_daily_rollup_flag",
    "upsert_episode",
    "upsert_mined_routine",
    "upsert_point_event",
    "upsert_tier2_cache",
)
