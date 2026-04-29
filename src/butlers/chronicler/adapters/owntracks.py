"""OwnTracks location-point projection adapter.

Projects location points from ``connectors.owntracks_points`` into two
Chronicler output layers:

1. **``location`` point events** — one per row in the evidence table.
   Each point event records the GPS fix at a single instant.
   ``source_ref`` = ``connectors.owntracks_points:{idempotency_key}``
   so replays are idempotent.

2. **``movement_episode`` rollups** — contiguous sequences of points
   within ``MOVEMENT_GAP_MINUTES`` of each other are collapsed into a
   single episode whose (start_at, end_at) span the sequence.

Semantics:
- Boundary precision is ``exact`` — OwnTracks device timestamps carry
  second resolution.
- Privacy class is ``sensitive`` — GPS tracks are personally identifying
  retrospective location data.
- Watermark on ``ts`` only. ``connectors.owntracks_points.id`` is a UUID,
  not an integer serial, so this adapter must not use the integer
  ``watermark_id`` tuple-watermark path.
- Missing evidence table degrades gracefully (module not enabled /
  migration not run on this deployment).
- No LLM call per event — Tier-0 projection only (RFC 0014 §D5).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

import asyncpg

from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.models import Episode, PointEvent, Precision, Privacy
from butlers.chronicler.storage import (
    get_carryover,
    save_carryover,
    upsert_episode,
    upsert_point_event,
)

logger = logging.getLogger(__name__)

SOURCE_NAME = "owntracks.points"
EVENT_TYPE_LOCATION = "location"
EPISODE_TYPE_MOVEMENT = "movement_episode"
_EVIDENCE_TABLE = "connectors.owntracks_points"
DEFAULT_BATCH_LIMIT = 1000

# Consecutive points separated by more than this threshold start a new episode.
MOVEMENT_GAP_MINUTES = 30


class OwnTracksPointAdapter(ProjectionAdapter):
    """Project ``connectors.owntracks_points`` rows into Chronicler.

    Each row → one ``location`` point event.
    Contiguous sequences of points within ``MOVEMENT_GAP_MINUTES`` are
    collapsed into ``movement_episode`` spans.
    """

    def __init__(
        self,
        *,
        batch_limit: int = DEFAULT_BATCH_LIMIT,
        movement_gap_minutes: int = MOVEMENT_GAP_MINUTES,
    ) -> None:
        super().__init__(SOURCE_NAME)
        self.batch_limit = batch_limit
        self.movement_gap_minutes = movement_gap_minutes

    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        result = AdapterResult(source_name=self.source_name)

        rows = await self._fetch_points(pool, since)
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_EVIDENCE_TABLE} not found; OwnTracks evidence surface unavailable"
            )
            return result

        if not rows:
            result.watermark = since
            return result

        # Project each row as a point event.
        latest_watermark = since
        valid_rows: list[dict[str, Any]] = []
        for row in rows:
            ts = row["ts"]
            if isinstance(ts, datetime) and ts.tzinfo is not None:
                if latest_watermark is None or ts > latest_watermark:
                    latest_watermark = ts

            normalized_row, warning = self._normalize_row(row)
            if warning is not None:
                logger.warning("%s", warning)
                result.warnings.append(warning)
            if normalized_row is None:
                continue

            valid_rows.append(normalized_row)
            await self._project_point_event(chronicler_pool, normalized_row)
            result.rows_projected += 1
            result.point_events += 1

        # Build movement episodes from the sorted point sequence.
        # Load prior-batch carryover for cross-batch stitching.
        if valid_rows:
            prior_carryover = await get_carryover(chronicler_pool, self.source_name)
            episodes_closed, new_carryover = await self._project_movement_episodes(
                chronicler_pool, valid_rows, prior_carryover
            )
            result.episodes_closed += episodes_closed
            await save_carryover(chronicler_pool, self.source_name, new_carryover)

        result.watermark = latest_watermark
        # Leave watermark_id unset: owntracks_points.id is UUID, while the
        # checkpoint column stores integer tie-breakers for serial-id sources.
        return result

    def _normalize_row(
        self,
        row: asyncpg.Record,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Return a sanitized row dict or a warning when the row is unusable.

        The evidence table uses floating-point columns, so malformed upstream
        writes can persist non-finite values (NaN/Inf). Those values are legal
        in Postgres ``double precision`` but not in JSONB payloads, which would
        otherwise crash projection and poison the checkpoint.
        """
        row_ref = self._row_reference(row)

        ts = row["ts"]
        if not isinstance(ts, datetime) or ts.tzinfo is None:
            return None, f"Skipping malformed OwnTracks row {row_ref}: ts must be timezone-aware"

        idempotency_key = row["idempotency_key"]
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            return None, f"Skipping malformed OwnTracks row {row_ref}: idempotency_key missing"

        endpoint_identity = row["endpoint_identity"]
        if not isinstance(endpoint_identity, str) or not endpoint_identity.strip():
            return None, f"Skipping malformed OwnTracks row {row_ref}: endpoint_identity missing"

        lat = self._coerce_finite_float(row["lat"])
        if lat is None:
            return None, f"Skipping malformed OwnTracks row {row_ref}: lat must be finite"

        lon = self._coerce_finite_float(row["lon"])
        if lon is None:
            return None, f"Skipping malformed OwnTracks row {row_ref}: lon must be finite"

        accuracy = self._coerce_finite_float(row["accuracy"])
        accuracy_warning: str | None = None
        if row["accuracy"] is not None and accuracy is None:
            accuracy_warning = (
                f"OwnTracks row {row_ref} has non-finite accuracy; "
                "omitting accuracy from projection"
            )

        normalized = {
            "id": row["id"],
            "idempotency_key": idempotency_key.strip(),
            "ts": ts,
            "lat": lat,
            "lon": lon,
            "accuracy": accuracy,
            "trigger": row["trigger"],
            "event": row["event"],
            "endpoint_identity": endpoint_identity.strip(),
            "raw_payload": row["raw_payload"],
            "recorded_at": row["recorded_at"],
        }
        return normalized, accuracy_warning

    @staticmethod
    def _coerce_finite_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric):
            return None
        return numeric

    @staticmethod
    def _row_reference(row: asyncpg.Record) -> str:
        idempotency_key = row["idempotency_key"]
        if isinstance(idempotency_key, str) and idempotency_key.strip():
            return idempotency_key.strip()
        row_id = row["id"]
        if row_id is not None:
            return str(row_id)
        return "<unknown>"

    async def _fetch_points(
        self,
        pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> list[asyncpg.Record] | None:
        """Fetch evidence rows since the watermark.

        ``since_id`` is intentionally ignored. The evidence table primary key
        is UUID, while ``projection_checkpoints.watermark_id`` is an integer
        field for serial-id sources. Use single-column ``WHERE ts > $1`` so a
        stale integer checkpoint cannot make Postgres compare UUIDs to ints.

        Returns ``None`` if the evidence table is missing — degrade
        gracefully per RFC 0014 optional-schema guard.
        """
        del since_id
        try:
            async with pool.acquire() as conn:
                exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'connectors'
                          AND table_name = 'owntracks_points'
                    )
                    """
                )
                if not exists:
                    return None

                if since is None:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, idempotency_key, ts, lat, lon,
                               accuracy, trigger, event, endpoint_identity,
                               raw_payload, recorded_at
                        FROM {_EVIDENCE_TABLE}
                        ORDER BY ts ASC, id ASC
                        LIMIT $1
                        """,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, idempotency_key, ts, lat, lon,
                               accuracy, trigger, event, endpoint_identity,
                               raw_payload, recorded_at
                        FROM {_EVIDENCE_TABLE}
                        WHERE ts > $1
                        ORDER BY ts ASC, id ASC
                        LIMIT $2
                        """,
                        since,
                        self.batch_limit,
                    )
        except asyncpg.PostgresError:
            logger.exception("Failed reading %s", _EVIDENCE_TABLE)
            return None

        return list(rows)

    async def _project_point_event(
        self,
        chronicler_pool: asyncpg.Pool,
        row: dict[str, Any],
    ) -> PointEvent:
        idempotency_key = row["idempotency_key"]
        source_ref = f"{_EVIDENCE_TABLE}:{idempotency_key}"
        endpoint_identity = row["endpoint_identity"]

        lat = row["lat"]
        lon = row["lon"]
        accuracy = row["accuracy"]
        trigger = row["trigger"]

        title = f"Location: {lat:.5f}, {lon:.5f}"
        if accuracy is not None:
            title += f" (±{accuracy:.0f}m)"

        payload: dict = {
            "idempotency_key": idempotency_key,
            "endpoint_identity": endpoint_identity,
            "lat": lat,
            "lon": lon,
        }
        if accuracy is not None:
            payload["accuracy"] = accuracy
        if trigger is not None:
            payload["trigger"] = trigger

        async with chronicler_pool.acquire() as conn:
            event = await upsert_point_event(
                conn,
                PointEvent(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    event_type=EVENT_TYPE_LOCATION,
                    occurred_at=row["ts"],
                    precision=Precision.EXACT,
                    title=title,
                    payload=payload,
                    privacy=Privacy.SENSITIVE,
                ),
            )
        return event

    async def _project_movement_episodes(
        self,
        chronicler_pool: asyncpg.Pool,
        rows: list[dict[str, Any]],
        prior_carryover: dict,
    ) -> tuple[int, dict]:
        """Collapse point sequences into movement episodes.

        Consecutive points with a gap <= ``movement_gap_minutes`` belong to
        the same episode.  A new episode starts when the gap exceeds the
        threshold or when the endpoint identity changes.

        ``prior_carryover`` maps ``endpoint_identity`` → carryover dict from
        the prior batch.  When the first point of a new batch is within
        ``movement_gap_minutes`` of the last point of the prior batch's open
        movement episode (same endpoint), the episode is extended rather than
        starting a fresh fragmented one.

        Returns ``(episodes_upserted, new_carryover)`` where ``new_carryover``
        captures any open movement episode at the end of this batch.
        """
        if not rows:
            return 0, {}

        gap = timedelta(minutes=self.movement_gap_minutes)
        episodes_upserted = 0

        # Each segment: list of rows in the same movement episode.
        # Also tracks whether the segment continues a prior-batch open episode.
        segments: list[dict] = []
        first_row = rows[0]
        endpoint = first_row["endpoint_identity"]

        # Check if the first row continues a prior-batch open episode.
        carry = prior_carryover.get(endpoint)
        existing_source_ref: str | None = None
        prior_start_at: datetime | None = None
        prior_start_lat: float | None = None
        prior_start_lon: float | None = None

        if carry:
            try:
                parsed_prior_start_at = datetime.fromisoformat(carry["start_at"])
                prior_end_at = datetime.fromisoformat(carry["end_at"])
                if self._carryover_continues(
                    row_ts=first_row["ts"],
                    prior_start_at=parsed_prior_start_at,
                    prior_end_at=prior_end_at,
                    gap=gap,
                ):
                    # Continue the prior episode.
                    existing_source_ref = carry["source_ref"]
                    prior_start_at = parsed_prior_start_at
                    prior_start_lat = carry.get("start_lat")
                    prior_start_lon = carry.get("start_lon")
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "Discarding malformed movement carryover for %s: %r", endpoint, carry
                )

        current: list[dict[str, Any]] = [first_row]
        current_source_ref: str | None = existing_source_ref
        current_prior_start_at: datetime | None = prior_start_at
        current_prior_start_lat: float | None = prior_start_lat
        current_prior_start_lon: float | None = prior_start_lon

        for row in rows[1:]:
            prev = current[-1]
            same_identity = row["endpoint_identity"] == prev["endpoint_identity"]
            time_gap = row["ts"] - prev["ts"]
            if same_identity and time_gap <= gap:
                current.append(row)
            else:
                segments.append(
                    {
                        "rows": current,
                        "source_ref": current_source_ref,
                        "prior_start_at": current_prior_start_at,
                        "prior_start_lat": current_prior_start_lat,
                        "prior_start_lon": current_prior_start_lon,
                    }
                )
                current = [row]
                current_source_ref = None
                current_prior_start_at = None
                current_prior_start_lat = None
                current_prior_start_lon = None
                # Check carryover for this new segment's endpoint too.
                new_endpoint = row["endpoint_identity"]
                new_carry = prior_carryover.get(new_endpoint)
                if new_carry:
                    try:
                        parsed_new_prior_start_at = datetime.fromisoformat(new_carry["start_at"])
                        new_prior_end_at = datetime.fromisoformat(new_carry["end_at"])
                        if self._carryover_continues(
                            row_ts=row["ts"],
                            prior_start_at=parsed_new_prior_start_at,
                            prior_end_at=new_prior_end_at,
                            gap=gap,
                        ):
                            current_source_ref = new_carry["source_ref"]
                            current_prior_start_at = parsed_new_prior_start_at
                            current_prior_start_lat = new_carry.get("start_lat")
                            current_prior_start_lon = new_carry.get("start_lon")
                    except (KeyError, TypeError, ValueError):
                        logger.warning(
                            "Discarding malformed movement carryover for %s: %r",
                            new_endpoint,
                            new_carry,
                        )
        segments.append(
            {
                "rows": current,
                "source_ref": current_source_ref,
                "prior_start_at": current_prior_start_at,
                "prior_start_lat": current_prior_start_lat,
                "prior_start_lon": current_prior_start_lon,
            }
        )

        new_carryover: dict = {}

        for seg in segments:
            seg_rows: list[dict[str, Any]] = seg["rows"]
            seg_source_ref: str | None = seg["source_ref"]
            seg_prior_start_at: datetime | None = seg["prior_start_at"]
            seg_prior_start_lat: float | None = seg["prior_start_lat"]
            seg_prior_start_lon: float | None = seg["prior_start_lon"]

            first = seg_rows[0]
            last = seg_rows[-1]

            # Effective start: use prior batch's start when extending.
            effective_start_at: datetime = seg_prior_start_at if seg_prior_start_at else first["ts"]
            end_at: datetime = last["ts"]
            endpoint_identity: str = first["endpoint_identity"]
            point_count = len(seg_rows)

            if seg_source_ref is None:
                # New episode starting this batch.
                start_tst = int(effective_start_at.timestamp())
                seg_source_ref = f"{_EVIDENCE_TABLE}:movement:{endpoint_identity}:{start_tst}"

            title = f"Movement ({point_count} points)"

            raw_start_lat = seg_prior_start_lat if seg_prior_start_lat is not None else first["lat"]
            raw_start_lon = seg_prior_start_lon if seg_prior_start_lon is not None else first["lon"]
            effective_start_lat = float(raw_start_lat)
            effective_start_lon = float(raw_start_lon)

            payload: dict = {
                "endpoint_identity": endpoint_identity,
                "point_count": point_count,
                "start_lat": effective_start_lat,
                "start_lon": effective_start_lon,
                "end_lat": float(last["lat"]),
                "end_lon": float(last["lon"]),
            }

            async with chronicler_pool.acquire() as conn:
                await upsert_episode(
                    conn,
                    Episode(
                        source_name=self.source_name,
                        source_ref=seg_source_ref,
                        episode_type=EPISODE_TYPE_MOVEMENT,
                        start_at=effective_start_at,
                        end_at=end_at,
                        precision=Precision.EXACT,
                        title=title,
                        payload=payload,
                        privacy=Privacy.SENSITIVE,
                    ),
                )
            episodes_upserted += 1

            # The last segment may be open (continues into the next batch).
            # Record carryover for all segments; only the last one matters in
            # practice, but we key by endpoint so multi-identity batches work.
            new_carryover[endpoint_identity] = {
                "source_ref": seg_source_ref,
                "start_at": effective_start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "start_lat": effective_start_lat,
                "start_lon": effective_start_lon,
            }

        return episodes_upserted, new_carryover

    @staticmethod
    def _carryover_continues(
        *,
        row_ts: datetime,
        prior_start_at: datetime,
        prior_end_at: datetime,
        gap: timedelta,
    ) -> bool:
        """Return True only when carryover is chronologically continuous."""
        if prior_start_at > prior_end_at:
            return False
        if prior_end_at > row_ts:
            return False
        return (row_ts - prior_end_at) <= gap


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EPISODE_TYPE_MOVEMENT",
    "EVENT_TYPE_LOCATION",
    "MOVEMENT_GAP_MINUTES",
    "OwnTracksPointAdapter",
    "SOURCE_NAME",
]
