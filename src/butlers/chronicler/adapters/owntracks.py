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
from butlers.chronicler.storage import upsert_episode, upsert_point_event

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
        episodes_closed = await self._project_movement_episodes(chronicler_pool, valid_rows)
        result.episodes_closed += episodes_closed

        result.watermark = latest_watermark
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
    ) -> list[asyncpg.Record] | None:
        """Fetch evidence rows since the watermark.

        Returns ``None`` if the evidence table is missing — degrade
        gracefully per RFC 0014 optional-schema guard.
        """
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
    ) -> int:
        """Collapse point sequences into movement episodes.

        Consecutive points with a gap <= ``movement_gap_minutes`` belong to
        the same episode.  A new episode starts when the gap exceeds the
        threshold or when the endpoint identity changes.

        Returns the number of episodes upserted.
        """
        if not rows:
            return 0

        gap = timedelta(minutes=self.movement_gap_minutes)
        episodes_upserted = 0

        # Each segment: list of rows in the same movement episode.
        segments: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = [rows[0]]

        for row in rows[1:]:
            prev = current[-1]
            same_identity = row["endpoint_identity"] == prev["endpoint_identity"]
            time_gap = row["ts"] - prev["ts"]
            if same_identity and time_gap <= gap:
                current.append(row)
            else:
                segments.append(current)
                current = [row]
        segments.append(current)

        for segment in segments:
            first = segment[0]
            last = segment[-1]
            start_at: datetime = first["ts"]
            end_at: datetime = last["ts"]
            endpoint_identity: str = first["endpoint_identity"]
            point_count = len(segment)

            # Source ref is keyed to (endpoint, start_ts) so it is stable
            # even if the batch boundary shifts on replay.
            start_tst = int(start_at.timestamp())
            source_ref = f"{_EVIDENCE_TABLE}:movement:{endpoint_identity}:{start_tst}"

            title = f"Movement ({point_count} points)"

            payload: dict = {
                "endpoint_identity": endpoint_identity,
                "point_count": point_count,
                "start_lat": float(first["lat"]),
                "start_lon": float(first["lon"]),
                "end_lat": float(last["lat"]),
                "end_lon": float(last["lon"]),
            }

            async with chronicler_pool.acquire() as conn:
                await upsert_episode(
                    conn,
                    Episode(
                        source_name=self.source_name,
                        source_ref=source_ref,
                        episode_type=EPISODE_TYPE_MOVEMENT,
                        start_at=start_at,
                        end_at=end_at,
                        precision=Precision.EXACT,
                        title=title,
                        payload=payload,
                        privacy=Privacy.SENSITIVE,
                    ),
                )
            episodes_upserted += 1

        return episodes_upserted


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EPISODE_TYPE_MOVEMENT",
    "EVENT_TYPE_LOCATION",
    "MOVEMENT_GAP_MINUTES",
    "OwnTracksPointAdapter",
    "SOURCE_NAME",
]
