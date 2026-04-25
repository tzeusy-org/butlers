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
from datetime import datetime, timedelta

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
        for row in rows:
            await self._project_point_event(chronicler_pool, row)
            result.rows_projected += 1
            result.point_events += 1

            ts = row["ts"]
            if ts is not None and (latest_watermark is None or ts > latest_watermark):
                latest_watermark = ts

        # Build movement episodes from the sorted point sequence.
        episodes_closed = await self._project_movement_episodes(chronicler_pool, rows)
        result.episodes_closed += episodes_closed

        result.watermark = latest_watermark
        return result

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
                        ORDER BY ts ASC
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
                        ORDER BY ts ASC
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
        row: asyncpg.Record,
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
        rows: list[asyncpg.Record],
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
        segments: list[list[asyncpg.Record]] = []
        current: list[asyncpg.Record] = [rows[0]]

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
