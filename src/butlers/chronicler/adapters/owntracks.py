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
- Privacy class is ``normal`` — the Chronicles dashboard is the owner's
  view of their own location history, so masking the envelope and excluding
  markers/trail from the map made the Travel lane and Map widget useless
  to the only viewer.  Any per-recipient masking (shared dashboards,
  screenshot views) should be reintroduced via an explicit user-toggle
  per the ``Map Render Privacy Contract`` requirement, not by blanket
  classification at the adapter layer.  The ``restricted`` class still
  exists for episodes that must be hidden entirely (bu-6c5i6).
- Watermark on ``ts`` only. ``connectors.owntracks_points.id`` is a UUID,
  not an integer serial, so this adapter must not use the integer
  ``watermark_id`` tuple-watermark path.
- Missing evidence table degrades gracefully (module not enabled /
  migration not run on this deployment).
- No LLM call per event — Tier-0 projection only (RFC 0014 §D5).

Clock-skew detection:
- When ``abs(ts - recorded_at) > CLOCK_SKEW_THRESHOLD_HOURS``, the device
  clock is implausible.  The adapter clamps ``ts`` to ``recorded_at``
  (server ingestion time) for that row before episode projection.  This
  prevents the skewed device timestamp from producing inverted episodes at
  the source, making the swap-bounds guard in ``_project_movement_episodes``
  a strictly redundant safety net for the residual cases it cannot reach
  (cross-batch carryover inversions).  The original device ``ts`` is
  preserved verbatim in the evidence table for forensic access.
- Threshold default: 4 hours — wider than legitimate timezone confusion,
  tighter than weeks-old buffered points.  Override via the
  ``clock_skew_threshold_hours`` constructor argument.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from butlers.chronicler.adapters._owner_entity import (
    resolve_owner_entity_id,
    upsert_owner_episode_entity,
)
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.models import Episode, Layer, PointEvent, Precision, Privacy
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

# Points whose device timestamp deviates from server ingestion time by more than
# this threshold are treated as having an implausible device clock.  The adapter
# clamps their ``ts`` to ``recorded_at`` (server ingestion time) so that episode
# projection never sees a pathologically skewed timestamp.
# 4 hours: wider than legitimate timezone confusion, tighter than weeks-old
# buffered points.  Override via ``OwnTracksPointAdapter(clock_skew_threshold_hours=...)``.
CLOCK_SKEW_THRESHOLD_HOURS = 4


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
        clock_skew_threshold_hours: int = CLOCK_SKEW_THRESHOLD_HOURS,
    ) -> None:
        super().__init__(SOURCE_NAME)
        self.batch_limit = batch_limit
        self.movement_gap_minutes = movement_gap_minutes
        if clock_skew_threshold_hours < 0:
            raise ValueError(
                f"clock_skew_threshold_hours must be non-negative, got {clock_skew_threshold_hours}"
            )
        self.clock_skew_threshold = timedelta(hours=clock_skew_threshold_hours)

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

            normalized_row, row_warnings = self._normalize_row(row)
            for warning in row_warnings:
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
            # Resolve owner entity_id once per adapter run (not per row).
            entity_id = await resolve_owner_entity_id(pool)
            prior_carryover = await get_carryover(chronicler_pool, self.source_name)
            episodes_closed, new_carryover = await self._project_movement_episodes(
                chronicler_pool, valid_rows, prior_carryover, entity_id=entity_id
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
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """Return a sanitized row dict (or None) and a list of warnings.

        The evidence table uses floating-point columns, so malformed upstream
        writes can persist non-finite values (NaN/Inf). Those values are legal
        in Postgres ``double precision`` but not in JSONB payloads, which would
        otherwise crash projection and poison the checkpoint.

        Clock-skew detection: when ``abs(ts - recorded_at) > clock_skew_threshold``,
        the device clock is considered implausible.  The row's ``ts`` is clamped
        to ``recorded_at`` (server ingestion time) so episode projection never
        sees a pathologically skewed timestamp.  The original device timestamp is
        preserved verbatim in the evidence table for forensic access.

        Returns:
            (normalized_dict, warnings) — normalized_dict is None when the row
            must be skipped entirely, otherwise a sanitized dict ready for
            projection.  warnings is a (possibly empty) list of warning strings
            for rows that were partially sanitized but still projected.
        """
        row_ref = self._row_reference(row)
        warnings: list[str] = []

        ts = row["ts"]
        if not isinstance(ts, datetime) or ts.tzinfo is None:
            return None, [f"Skipping malformed OwnTracks row {row_ref}: ts must be timezone-aware"]

        # Clock-skew detection: clamp implausible device timestamps to server
        # ingestion time (recorded_at) before episode projection.
        recorded_at = row["recorded_at"]
        if isinstance(recorded_at, datetime) and recorded_at.tzinfo is not None:
            delta = ts - recorded_at
            if abs(delta) > self.clock_skew_threshold:
                skew_warning = (
                    f"OwnTracks row {row_ref} has implausible device timestamp "
                    f"(ts={ts.isoformat()}, recorded_at={recorded_at.isoformat()}, "
                    f"delta={delta}); clamping ts to recorded_at for episode projection."
                )
                warnings.append(skew_warning)
                ts = recorded_at

        idempotency_key = row["idempotency_key"]
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            return None, [f"Skipping malformed OwnTracks row {row_ref}: idempotency_key missing"]

        endpoint_identity = row["endpoint_identity"]
        if not isinstance(endpoint_identity, str) or not endpoint_identity.strip():
            return (
                None,
                [f"Skipping malformed OwnTracks row {row_ref}: endpoint_identity missing"],
            )

        lat = self._coerce_finite_float(row["lat"])
        if lat is None:
            return None, [f"Skipping malformed OwnTracks row {row_ref}: lat must be finite"]

        lon = self._coerce_finite_float(row["lon"])
        if lon is None:
            return None, [f"Skipping malformed OwnTracks row {row_ref}: lon must be finite"]

        accuracy = self._coerce_finite_float(row["accuracy"])
        if row["accuracy"] is not None and accuracy is None:
            warnings.append(
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
        return normalized, warnings

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
                    privacy=Privacy.NORMAL,
                    layer=Layer.EVIDENCE,
                ),
            )
        return event

    async def _project_movement_episodes(
        self,
        chronicler_pool: asyncpg.Pool,
        rows: list[dict[str, Any]],
        prior_carryover: dict,
        *,
        entity_id: UUID | None = None,
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

        Implementation note — clock-skew safety:
        Device-reported ``ts`` values can arrive out of order due to clock skew
        or buffered delivery.  To produce monotonically ordered bounds, the
        buffer is re-sorted by ``recorded_at`` (server ingestion time) before
        segmenting.  A defensive guard rejects any episode whose computed
        ``end_at < effective_start_at`` by swapping the bounds, ensuring the
        ``episodes_check`` DB constraint is never violated.
        """
        if not rows:
            return 0, {}

        # Re-sort by server ingestion time so that device clock skew or
        # buffered delivery never produces a negative-duration segment.
        # ``recorded_at`` is set by the server on arrival and is
        # monotonically non-decreasing within a single evidence table scan.
        rows = sorted(rows, key=lambda r: r["recorded_at"])

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
            resolved = self._resolve_carryover_segment(
                endpoint=endpoint,
                carry=carry,
                row_ts=first_row["ts"],
                gap=gap,
            )
            if resolved is not None:
                existing_source_ref, prior_start_at, prior_start_lat, prior_start_lon = resolved

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
                    new_resolved = self._resolve_carryover_segment(
                        endpoint=new_endpoint,
                        carry=new_carry,
                        row_ts=row["ts"],
                        gap=gap,
                    )
                    if new_resolved is not None:
                        (
                            current_source_ref,
                            current_prior_start_at,
                            current_prior_start_lat,
                            current_prior_start_lon,
                        ) = new_resolved
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

            # Defensive guard: device clock skew can produce an inverted episode
            # even after recorded_at sorting (e.g. when the prior-batch carryover
            # start_at is later than the current batch's last ts).  Swapping the
            # bounds is always safe here — the episode still covers the same time
            # range and never violates the episodes_check constraint.
            if end_at < effective_start_at:
                logger.warning(
                    "Inverted movement episode for %s (start_at=%s > end_at=%s); "
                    "swapping bounds to satisfy episodes_check. "
                    "Likely cause: device clock skew or out-of-order delivery.",
                    endpoint_identity,
                    effective_start_at.isoformat(),
                    end_at.isoformat(),
                )
                effective_start_at, end_at = end_at, effective_start_at

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
                episode = await upsert_episode(
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
                        privacy=Privacy.NORMAL,
                        layer=Layer.ACTIVITY,
                    ),
                )
                # Write owner row into episode_entities join table (bu-4c1ks).
                ep_id = episode.id if episode is not None else None
                await upsert_owner_episode_entity(conn, ep_id, owner_id=entity_id)
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

    def _resolve_carryover_segment(
        self,
        *,
        endpoint: str,
        carry: Any,
        row_ts: datetime,
        gap: timedelta,
    ) -> tuple[str, datetime, float | None, float | None] | None:
        """Validate ``carry`` and decide whether it extends the prior episode.

        Returns ``(source_ref, prior_start_at, start_lat, start_lon)`` when the
        carryover is well-formed and chronologically continuous with ``row_ts``.
        Returns ``None`` when the carryover should be discarded (and the new
        batch should start a fresh episode); each discard reason logs a
        specific warning so QA log scanning can tell failure modes apart
        instead of seeing one generic "malformed carryover" line.

        Coordinates are optional for movement episodes: missing keys, JSON
        ``null``, and empty-string lat/lon are all treated as "not provided"
        (``start_lat``/``start_lon`` default to ``None`` so the segment falls
        back to the first row's coordinates).  Only an explicit non-empty
        value that fails finite-float coercion is treated as malformed.
        """
        if not isinstance(carry, dict):
            logger.warning("Discarding non-dict movement carryover for %s: %r", endpoint, carry)
            return None

        try:
            source_ref = carry["source_ref"]
            raw_start_at = carry["start_at"]
            raw_end_at = carry["end_at"]
        except KeyError as exc:
            logger.warning(
                "Discarding movement carryover for %s with missing key %s: %r",
                endpoint,
                exc.args[0],
                carry,
            )
            return None

        try:
            prior_start_at = datetime.fromisoformat(raw_start_at)
            prior_end_at = datetime.fromisoformat(raw_end_at)
        except (TypeError, ValueError):
            logger.warning(
                "Discarding movement carryover for %s with invalid ISO timestamps: %r",
                endpoint,
                carry,
            )
            return None

        if not isinstance(source_ref, str) or not source_ref.strip():
            logger.warning(
                "Discarding movement carryover for %s with invalid source_ref: %r",
                endpoint,
                carry,
            )
            return None

        if prior_start_at.tzinfo is None or prior_end_at.tzinfo is None:
            logger.warning(
                "Discarding naive (tz-less) movement carryover for %s: %r", endpoint, carry
            )
            return None

        # Coordinates are optional.  Treat missing key, JSON null, and
        # empty-string values the same as "not provided" (start_*=None) so
        # the segment falls back to the first row's lat/lon downstream.
        # Only refuse the carryover when an explicit non-empty value cannot
        # be coerced to a finite float — that is true malformed data.
        raw_lat = carry.get("start_lat")
        raw_lon = carry.get("start_lon")
        start_lat = self._coerce_finite_float(raw_lat)
        start_lon = self._coerce_finite_float(raw_lon)
        if (raw_lat not in (None, "") and start_lat is None) or (
            raw_lon not in (None, "") and start_lon is None
        ):
            logger.warning(
                "Discarding movement carryover for %s with non-finite coordinates: %r",
                endpoint,
                carry,
            )
            return None

        try:
            continues = self._carryover_continues(
                row_ts=row_ts,
                prior_start_at=prior_start_at,
                prior_end_at=prior_end_at,
                gap=gap,
            )
        except TypeError:
            logger.warning(
                "Discarding movement carryover for %s with incompatible end_at type: %r",
                endpoint,
                carry,
            )
            return None

        if not continues:
            return None

        return source_ref.strip(), prior_start_at, start_lat, start_lon


__all__ = [
    "CLOCK_SKEW_THRESHOLD_HOURS",
    "DEFAULT_BATCH_LIMIT",
    "EPISODE_TYPE_MOVEMENT",
    "EVENT_TYPE_LOCATION",
    "MOVEMENT_GAP_MINUTES",
    "OwnTracksPointAdapter",
    "SOURCE_NAME",
]
