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
- When ``ts - recorded_at > CLOCK_SKEW_THRESHOLD_HOURS`` (the device claims a
  fix taken after the server received it), the device clock is implausible.
  The adapter clamps ``ts`` to ``recorded_at`` (server ingestion time) for
  that row before projection.  The original device ``ts`` is preserved
  verbatim in the evidence table for forensic access.
- A ``ts`` far *behind* ``recorded_at`` is normal: OwnTracks queues fixes
  while offline (e.g. in flight) and uploads them later.  Those rows keep
  their device ``ts``; re-stamping them to upload time would place the owner
  where they were hours ago at the time they came back online (bu-8r9q06).
- Movement episodes are segmented in device ``ts`` order, so out-of-order
  delivery cannot invert bounds within a batch.  The swap-bounds guard in
  ``_project_movement_episodes`` remains for cross-batch carryover cases.
- Threshold default: 4 hours — wider than legitimate timezone confusion.
  Override via the ``clock_skew_threshold_hours`` constructor argument.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

import asyncpg

from butlers.chronicler.adapters._owner_entity import (
    owner_episode_transaction,
    resolve_owner_entity_id,
    upsert_owner_episode_entity,
)
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.adapters.owntracks_trail import (
    OUTLIER_PAYLOAD_KEY,
    TRAIL_ANCHORS_CARRYOVER_KEY,
    TrailOutlier,
    classify_trail,
    dump_anchors,
    load_anchors,
)
from butlers.chronicler.confidence import (
    EvidenceKind,
    derive_confidence,
    evidence_refs_from_event_ids,
)
from butlers.chronicler.models import (
    Episode,
    Layer,
    LinkRelation,
    PointEvent,
    Precision,
    Privacy,
)
from butlers.chronicler.storage import (
    get_carryover,
    link_event_to_episode,
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

# A movement episode needs at least this many accepted fixes (bu-2tsgqb.6).
MIN_MOVEMENT_POINTS = 2

# Points whose device timestamp runs ahead of server ingestion time by more than
# this threshold are treated as having an implausible device clock.  The adapter
# clamps their ``ts`` to ``recorded_at`` (server ingestion time) so that
# projection never sees a future-dated fix.  Late (buffered) points are not
# clamped.  4 hours: wider than legitimate timezone confusion.  Override via
# ``OwnTracksPointAdapter(clock_skew_threshold_hours=...)``.
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
            if normalized_row is not None:
                valid_rows.append(normalized_row)

        if valid_rows:
            # Judge plausibility in device-time order with the shared trail
            # policy (owntracks_trail.py); anchors persist across batches.
            valid_rows.sort(key=lambda r: (r["ts"], r["recorded_at"] or r["ts"]))
            prior_carryover = await get_carryover(chronicler_pool, self.source_name)
            outliers, anchors = classify_trail(
                valid_rows, prior_anchors=load_anchors(prior_carryover)
            )

            # Map each location point event's idempotency_key → its chronicler
            # point-event id, so movement episodes can record their corroborating
            # GPS fixes as ``evidence_refs`` (and link them in episode_event_links).
            event_id_by_key: dict[str, UUID] = {}
            accepted_rows: list[dict[str, Any]] = []
            for index, normalized_row in enumerate(valid_rows):
                outlier = outliers.get(index)
                if outlier is not None:
                    result.warnings.append(
                        f"OwnTracks row {normalized_row['idempotency_key']} rejected as an "
                        f"impossible jump (~{outlier.implied_speed_kmh:.0f} km/h); kept as "
                        "flagged evidence, excluded from episodes."
                    )
                else:
                    accepted_rows.append(normalized_row)
                event = await self._project_point_event(
                    chronicler_pool, normalized_row, outlier=outlier
                )
                if event is not None and event.id is not None:
                    event_id_by_key[normalized_row["idempotency_key"]] = event.id
                result.rows_projected += 1
                result.point_events += 1

            new_carryover: dict = {}
            if accepted_rows:
                # Resolve owner entity_id once per adapter run (not per row).
                entity_id = await resolve_owner_entity_id(pool)
                episodes_closed, new_carryover = await self._project_movement_episodes(
                    chronicler_pool,
                    accepted_rows,
                    prior_carryover,
                    event_id_by_key=event_id_by_key,
                    entity_id=entity_id,
                )
                result.episodes_closed += episodes_closed
            new_carryover[TRAIL_ANCHORS_CARRYOVER_KEY] = dump_anchors(anchors)
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

        Clock-skew detection: when ``ts - recorded_at > clock_skew_threshold``,
        the device clock is considered implausible.  The row's ``ts`` is clamped
        to ``recorded_at`` (server ingestion time) so projection never sees a
        future-dated fix.  A ``ts`` behind ``recorded_at`` is a buffered offline
        fix and is kept.  The original device timestamp is preserved verbatim in
        the evidence table for forensic access.

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

        # Clock-skew detection: clamp future-dated device timestamps to server
        # ingestion time (recorded_at).  Late delivery (ts behind recorded_at)
        # is OwnTracks' offline queue draining and keeps the device ts.
        recorded_at = row["recorded_at"]
        if isinstance(recorded_at, datetime) and recorded_at.tzinfo is not None:
            delta = ts - recorded_at
            if delta > self.clock_skew_threshold:
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
        *,
        outlier: TrailOutlier | None = None,
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
        if outlier is not None:
            # Kept as evidence (provenance), marked so episodes and the Map
            # trail exclude it (shared policy: owntracks_trail.py).
            payload[OUTLIER_PAYLOAD_KEY] = outlier.to_payload()
            title += " (rejected: impossible jump)"

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
        event_id_by_key: dict[str, UUID] | None = None,
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
        Buffered delivery means upload order (``recorded_at``) can differ from
        device order.  Segmenting is done in device ``ts`` order (after the
        future-skew clamp in ``_normalize_row``) so gaps are never negative and
        bounds are monotonic within a batch.  A defensive guard rejects any
        episode whose computed ``end_at < effective_start_at`` by swapping the
        bounds, ensuring the ``episodes_check`` DB constraint is never violated.
        """
        if not rows:
            return 0, {}

        event_id_by_key = event_id_by_key or {}

        # Segment in device-time order.  Sorting by upload time instead would
        # yield negative ts gaps for buffered points, which pass the gap check
        # and merge fixes taken hours (and airports) apart (bu-8r9q06).
        # Group by endpoint first so interleaved devices cannot fragment each
        # other's movement into single-fix segments.
        rows = sorted(
            rows, key=lambda r: (r["endpoint_identity"], r["ts"], r["recorded_at"] or r["ts"])
        )

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
        prior_point_count = 0

        if carry:
            resolved = self._resolve_carryover_segment(
                endpoint=endpoint,
                carry=carry,
                row_ts=first_row["ts"],
                gap=gap,
            )
            if resolved is not None:
                (
                    existing_source_ref,
                    prior_start_at,
                    prior_start_lat,
                    prior_start_lon,
                    prior_point_count,
                ) = resolved

        current: list[dict[str, Any]] = [first_row]
        current_source_ref: str | None = existing_source_ref
        current_prior_start_at: datetime | None = prior_start_at
        current_prior_start_lat: float | None = prior_start_lat
        current_prior_start_lon: float | None = prior_start_lon
        current_prior_point_count = prior_point_count

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
                        "prior_point_count": current_prior_point_count,
                    }
                )
                current = [row]
                current_source_ref = None
                current_prior_start_at = None
                current_prior_start_lat = None
                current_prior_start_lon = None
                current_prior_point_count = 0
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
                            current_prior_point_count,
                        ) = new_resolved
        segments.append(
            {
                "rows": current,
                "source_ref": current_source_ref,
                "prior_start_at": current_prior_start_at,
                "prior_start_lat": current_prior_start_lat,
                "prior_start_lon": current_prior_start_lon,
                "prior_point_count": current_prior_point_count,
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
            # Count the whole episode, not just this batch's slice: an
            # extension carrying one new fix is not a "1 point" movement.
            point_count = seg["prior_point_count"] + len(seg_rows)

            # Defensive guard: device clock skew can produce an inverted episode
            # even after ts sorting (e.g. when the prior-batch carryover
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

            raw_start_lat = seg_prior_start_lat if seg_prior_start_lat is not None else first["lat"]
            raw_start_lon = seg_prior_start_lon if seg_prior_start_lon is not None else first["lon"]
            effective_start_lat = float(raw_start_lat)
            effective_start_lon = float(raw_start_lon)

            # Record carryover for every segment (keyed by endpoint, so the
            # last one wins) before deciding whether to emit: a segment still
            # below the minimum evidence may grow past it next batch.
            new_carryover[endpoint_identity] = {
                "source_ref": seg_source_ref,
                "start_at": effective_start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "start_lat": effective_start_lat,
                "start_lon": effective_start_lon,
                "point_count": point_count,
            }

            # Minimum movement evidence: one fix is a location, not a movement.
            if point_count < MIN_MOVEMENT_POINTS:
                continue

            # Evidence chain: the GPS location point events that make up this
            # segment (those projected in THIS batch — cross-batch carryover
            # points were linked on their own run). De-duplicated, ordered by
            # occurrence via the row order.
            seg_event_ids = [
                event_id_by_key[r["idempotency_key"]]
                for r in seg_rows
                if r["idempotency_key"] in event_id_by_key
            ]

            merged = None
            if seg["source_ref"] is None:
                # Only a segment that starts fresh this batch can duplicate a
                # stored episode; a carryover extension already targets one.
                async with chronicler_pool.acquire() as pooled:
                    merged = await self._merge_overlapping_episode(
                        cast(asyncpg.Connection, pooled),
                        endpoint_identity=endpoint_identity,
                        source_ref=seg_source_ref,
                        start_at=effective_start_at,
                        end_at=end_at,
                    )
            if merged is not None:
                # Never emit a second chunk over the same span: widen the
                # already-stored episode instead (late or replayed fixes).
                seg_source_ref = cast(str, merged["source_ref"])
                if merged["start_at"] < effective_start_at:
                    effective_start_at = merged["start_at"]
                    effective_start_lat = merged["start_lat"]
                    effective_start_lon = merged["start_lon"]
                end_at = max(end_at, merged["end_at"])
                # Count distinct evidence, never a sum: a replayed batch's
                # fixes are already linked to the stored episode.
                linked = merged["linked_event_ids"]
                if linked:
                    point_count = len(linked | set(seg_event_ids))
                else:
                    # Legacy episode without linked evidence: extend its stored count.
                    point_count += merged["point_count"]
                new_carryover[endpoint_identity].update(
                    source_ref=seg_source_ref,
                    start_at=effective_start_at.isoformat(),
                    end_at=end_at.isoformat(),
                    start_lat=effective_start_lat,
                    start_lon=effective_start_lon,
                    point_count=point_count,
                )

            title = f"Movement ({point_count} points)"

            payload: dict = {
                "endpoint_identity": endpoint_identity,
                "point_count": point_count,
                "start_lat": effective_start_lat,
                "start_lon": effective_start_lon,
                "end_lat": float(last["lat"]),
                "end_lon": float(last["lon"]),
            }

            evidence_refs = evidence_refs_from_event_ids(seg_event_ids)

            # Confidence: a movement episode rests on a single, weak evidence
            # kind — raw GPS fixes from one device. With no independent
            # corroboration it stays ``low`` (still counted, but flagged for
            # re-reconciliation).
            confidence = derive_confidence([EvidenceKind(name="gps")])

            async with chronicler_pool.acquire() as conn:
                async with owner_episode_transaction(conn):
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
                            confidence=confidence,
                            evidence_refs=evidence_refs,
                        ),
                    )
                    # Write owner row into episode_entities join table (bu-4c1ks).
                    ep_id = episode.id if episode is not None else None
                    await upsert_owner_episode_entity(conn, ep_id, owner_id=entity_id)
                # Link each GPS fix into the canonical evidence chain.
                if ep_id is not None:
                    for event_id in seg_event_ids:
                        await link_event_to_episode(
                            conn,
                            episode_id=ep_id,
                            event_id=event_id,
                            relation=LinkRelation.EVIDENCE,
                        )
            episodes_upserted += 1

        return episodes_upserted, new_carryover

    async def _merge_overlapping_episode(
        self,
        conn: asyncpg.Connection,
        *,
        endpoint_identity: str,
        source_ref: str,
        start_at: datetime,
        end_at: datetime,
    ) -> dict[str, Any] | None:
        """Return a stored movement episode overlapping this span, if any.

        Only a *different* episode (another ``source_ref``) for the same
        endpoint counts: re-projecting the same segment is an ordinary
        idempotent upsert. The earliest overlapping episode wins so repeated
        merges converge on one row.
        """
        row = await conn.fetchrow(
            """
            SELECT source_ref, start_at, end_at, payload,
                   ARRAY(
                       SELECT l.event_id FROM episode_event_links l
                       WHERE l.episode_id = episodes.id
                   ) AS linked_event_ids
            FROM episodes
            WHERE source_name = $1
              AND episode_type = $2
              AND tombstone_at IS NULL
              AND payload ->> 'endpoint_identity' = $3
              AND source_ref <> $4
              AND start_at <= $6
              AND end_at >= $5
            ORDER BY start_at ASC, source_ref ASC
            LIMIT 1
            """,
            self.source_name,
            EPISODE_TYPE_MOVEMENT,
            endpoint_identity,
            source_ref,
            start_at,
            end_at,
        )
        if row is None:
            return None
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            payload = {}
        stored_end = row["end_at"] or row["start_at"]
        start_lat = self._coerce_finite_float(payload.get("start_lat"))
        start_lon = self._coerce_finite_float(payload.get("start_lon"))
        try:
            stored_count = max(int(payload.get("point_count", 0)), 0)
        except (TypeError, ValueError):
            stored_count = 0
        return {
            "source_ref": row["source_ref"],
            "start_at": row["start_at"],
            "end_at": stored_end,
            "start_lat": start_lat if start_lat is not None else 0.0,
            "start_lon": start_lon if start_lon is not None else 0.0,
            "point_count": stored_count,
            "linked_event_ids": set(row["linked_event_ids"] or ()),
        }

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
    ) -> tuple[str, datetime, float | None, float | None, int] | None:
        """Validate ``carry`` and decide whether it extends the prior episode.

        Returns ``(source_ref, prior_start_at, start_lat, start_lon, point_count)`` when the
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

        # Carryover written before point counts were tracked held only
        # emitted episodes, so it represents at least one fix.
        try:
            point_count = max(int(carry.get("point_count", 1)), 1)
        except (TypeError, ValueError):
            point_count = 1

        return source_ref.strip(), prior_start_at, start_lat, start_lon, point_count


__all__ = [
    "CLOCK_SKEW_THRESHOLD_HOURS",
    "DEFAULT_BATCH_LIMIT",
    "EPISODE_TYPE_MOVEMENT",
    "EVENT_TYPE_LOCATION",
    "MIN_MOVEMENT_POINTS",
    "MOVEMENT_GAP_MINUTES",
    "OwnTracksPointAdapter",
    "SOURCE_NAME",
]
