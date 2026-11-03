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
- Ingestion progress is an arrival-order cursor, not device time
  (bu-e6lrho). OwnTracks uploads offline-queued fixes late, so a fix with an
  older device ``ts`` can arrive after a newer one was projected; a ``ts``
  watermark would skip it forever. The adapter pages ``(recorded_at, id)``
  (server arrival time, UUID tie-break) and stores that cursor in the
  checkpoint carryover under ``_ingest_cursor``: the relational
  ``watermark_id`` column is an integer and cannot hold a UUID. Rows newer
  than ``now() - ingest_settle_seconds`` wait for the next run, so a
  slow-committing insert stamped with an earlier ``recorded_at`` cannot land
  behind an already-advanced cursor. Device ``ts`` still orders points and
  bounds episodes; the relational ``watermark`` reports the newest device
  ``ts`` projected, as before.
- Cutover from a legacy ``ts``-only checkpoint (no ``_ingest_cursor``) does not
  rescan history: the cursor starts at the earliest-arrived row with
  ``ts > watermark`` (inclusive), or after the newest settled row when every
  row is already covered. Only rows that arrived since then are re-read;
  their projections are idempotent by ``source_ref``.
- Late fixes and movement episodes: a fix whose device ``ts`` is at or before
  its endpoint's open-episode carryover ``end_at`` is *late* (an offline queue
  delivered after newer fixes, or a cutover re-read). Late fixes always get
  their ``location`` point event, but never extend, replace or reorder the
  endpoint's carryover; the carryover with the newest ``end_at`` stays. A
  late span within the movement gap (``movement_gap_minutes``) of the open
  carryover episode or of any stored movement episode for that endpoint
  produces no episode write, so a queue flush never splits or overlaps an
  already-emitted trip. Only a late span more than the movement gap away from
  every open or stored episode (with at least ``MIN_MOVEMENT_POINTS`` fixes)
  becomes its own standalone movement episode.
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
from dataclasses import dataclass
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
    TRAIL_STATE_CARRYOVER_KEY,
    TrailOutlier,
    classify_trail,
    dump_anchors,
    load_anchors,
    load_trail_state,
    trail_order_key,
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
    get_owntracks_carryover as get_carryover,
)
from butlers.chronicler.storage import (
    get_owntracks_point,
    link_event_to_episode,
    owntracks_connection,
    owntracks_transaction,
    retained_owntracks_rows,
    revise_owntracks_point,
    run_owntracks_projection,
    upsert_episode,
    upsert_point_event,
)
from butlers.chronicler.storage import (
    save_owntracks_carryover as save_carryover,
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

# Carryover key of the arrival-order ingestion cursor (see module docstring).
INGEST_CURSOR_CARRYOVER_KEY = "_ingest_cursor"

# Rows whose ``recorded_at`` is newer than ``now()`` minus this many seconds are
# left for the next run. ``recorded_at`` defaults to the inserting
# transaction's start time, so an insert that commits late can carry a stamp
# older than rows already visible; the settle window keeps the cursor behind it.
INGEST_SETTLE_SECONDS = 60


@dataclass(frozen=True)
class IngestCursor:
    """Position in ``connectors.owntracks_points`` arrival order.

    ``inclusive`` is only set by the legacy-checkpoint cutover, whose start row
    has not been projected yet; every persisted cursor is exclusive.
    """

    recorded_at: datetime
    id: UUID
    inclusive: bool = False

    def dump(self) -> dict[str, Any]:
        return {"version": 1, "recorded_at": self.recorded_at.isoformat(), "id": str(self.id)}

    @classmethod
    def load(cls, raw: object) -> IngestCursor | None:
        if not isinstance(raw, dict) or raw.get("version") != 1:
            return None
        try:
            recorded_at = datetime.fromisoformat(raw["recorded_at"])
            row_id = UUID(raw["id"])
        except (KeyError, TypeError, ValueError):
            return None
        if recorded_at.tzinfo is None:
            return None
        return cls(recorded_at=recorded_at, id=row_id)


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
        ingest_settle_seconds: int = INGEST_SETTLE_SECONDS,
    ) -> None:
        super().__init__(SOURCE_NAME)
        self.batch_limit = batch_limit
        if ingest_settle_seconds < 0:
            raise ValueError(
                f"ingest_settle_seconds must be non-negative, got {ingest_settle_seconds}"
            )
        self.ingest_settle_seconds = ingest_settle_seconds
        self.movement_gap_minutes = movement_gap_minutes
        if clock_skew_threshold_hours < 0:
            raise ValueError(
                f"clock_skew_threshold_hours must be non-negative, got {clock_skew_threshold_hours}"
            )
        self.clock_skew_threshold = timedelta(hours=clock_skew_threshold_hours)

    async def run(self, *, pool: asyncpg.Pool, chronicler_pool: asyncpg.Pool) -> AdapterResult:
        # Optional owner lookup keeps its original pool/error isolation.
        entity_id = await resolve_owner_entity_id(pool)

        async def project(
            conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
            since: datetime | None,
            since_id: int | None,
        ) -> AdapterResult:
            source = conn if pool is chronicler_pool else pool
            return await self._project(
                source, chronicler_pool=conn, since=since, since_id=since_id, entity_id=entity_id
            )

        return await run_owntracks_projection(
            chronicler_pool, self.source_name, project, self._llm_probe
        )

    async def project(
        self,
        pool: asyncpg.Pool | asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        *,
        chronicler_pool: asyncpg.Pool | asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        entity_id = await resolve_owner_entity_id(pool)
        async with owntracks_transaction(chronicler_pool, self.source_name) as conn:
            source = conn if pool is chronicler_pool else pool
            return await self._project(
                source, chronicler_pool=conn, since=since, since_id=since_id, entity_id=entity_id
            )

    async def _project(
        self,
        pool: asyncpg.Pool | asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        *,
        chronicler_pool: asyncpg.Pool | asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        since: datetime | None,
        since_id: int | None = None,
        entity_id: UUID | None,
    ) -> AdapterResult:
        result = AdapterResult(source_name=self.source_name)

        prior_carryover = await get_carryover(chronicler_pool, self.source_name)
        stored_cursor = IngestCursor.load(prior_carryover.get(INGEST_CURSOR_CARRYOVER_KEY))
        fetched = await self._fetch_points(pool, since, cursor=stored_cursor)
        if fetched is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_EVIDENCE_TABLE} not found; OwnTracks evidence surface unavailable"
            )
            return result
        rows, start_cursor = fetched

        if not rows:
            # A cutover cursor positioned after every settled row completes the
            # upgrade now; an inclusive one still points at an unread row.
            if (
                start_cursor is not None
                and not start_cursor.inclusive
                and start_cursor != stored_cursor
            ):
                await save_carryover(
                    chronicler_pool,
                    self.source_name,
                    {**prior_carryover, INGEST_CURSOR_CARRYOVER_KEY: start_cursor.dump()},
                )
            result.watermark = since
            return result

        latest_watermark = since
        new_carryover: dict = dict(prior_carryover)
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
            valid_rows.sort(key=trail_order_key)
            state = load_trail_state(prior_carryover)
            # Classification may reset this bounded state during replay. Keep
            # its historical identities before that mutation: a fetched overlap
            # is still a revision, never permission to resurrect expired data.
            historical_ids = {
                identity
                for entry in state.values()
                for identity in (entry["seed_id"], entry.get("anchor_id"), *entry["pending_ids"])
                if identity is not None
            }
            retained = await retained_owntracks_rows(pool, state)
            recovered = {}
            for raw in retained:
                normalized, _ = self._normalize_row(raw)
                if normalized is not None:
                    recovered[str(normalized["id"])] = normalized
            current_ids = {str(row["id"]) for row in valid_rows}
            pending_ids = {
                identity for entry in state.values() for identity in entry["pending_ids"]
            }
            if pending_ids - recovered.keys() - current_ids:
                result.warnings.append(
                    "OwnTracks candidate evidence unavailable; pending consensus restarts."
                )
            for entry in state.values():
                if (
                    entry["seed_id"] is not None
                    and entry["seed_id"] not in recovered
                    and entry["seed_id"] not in current_ids
                ):
                    entry["seed_id"] = None
                    result.warnings.append(
                        "OwnTracks bootstrap evidence unavailable; legacy anchor retained."
                    )
            pending_rows = [
                row
                for identity, row in recovered.items()
                if identity in pending_ids and identity not in current_ids
            ]
            valid_rows = sorted([*pending_rows, *valid_rows], key=trail_order_key)
            changes = {}
            rejected_seeds: set[str] = set()
            outliers, anchors = classify_trail(
                valid_rows,
                prior_anchors=load_anchors(prior_carryover),
                state=state,
                changes=changes,
                rejected_seeds=rejected_seeds,
            )
            for endpoint in rejected_seeds:
                prior_carryover.pop(endpoint, None)

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
                event = await self._project_point_event(
                    chronicler_pool,
                    normalized_row,
                    outlier=outlier,
                    existing_only=(
                        str(normalized_row["id"]) in historical_ids
                        or str(normalized_row["id"]) not in current_ids
                    ),
                )
                if event is not None and outlier is None:
                    accepted_rows.append(normalized_row)
                if event is not None and event.id is not None:
                    event_id_by_key[normalized_row["idempotency_key"]] = event.id
                result.rows_projected += 1
                result.point_events += 1

            for identity, verdict in changes.items():
                previous = recovered.get(identity)
                if previous is not None and identity not in current_ids:
                    await revise_owntracks_point(
                        chronicler_pool,
                        f"{_EVIDENCE_TABLE}:{previous['idempotency_key']}",
                        verdict.to_payload() if verdict else None,
                    )
            new_carryover = {
                key: value for key, value in prior_carryover.items() if not key.startswith("_")
            }
            if accepted_rows:
                episodes_closed, movement_carryover = await self._project_movement_episodes(
                    chronicler_pool,
                    accepted_rows,
                    prior_carryover,
                    event_id_by_key=event_id_by_key,
                    entity_id=entity_id,
                )
                result.episodes_closed += episodes_closed
                new_carryover.update(movement_carryover)
            new_carryover[TRAIL_ANCHORS_CARRYOVER_KEY] = dump_anchors(anchors)
            new_carryover[TRAIL_STATE_CARRYOVER_KEY] = {"version": 1, "endpoints": state}

        # Advance arrival-order progress past every fetched row, valid or not,
        # in the same transaction as the projections it covers.
        last_row = rows[-1]
        new_carryover[INGEST_CURSOR_CARRYOVER_KEY] = IngestCursor(
            recorded_at=last_row["recorded_at"], id=last_row["id"]
        ).dump()
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
        pool: asyncpg.Pool | asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        since: datetime | None,
        *,
        cursor: IngestCursor | None,
    ) -> tuple[list[asyncpg.Record], IngestCursor | None] | None:
        """Fetch the next settled rows in arrival order after ``cursor``.

        Returns ``(rows, start_cursor)``; ``start_cursor`` is ``cursor`` or, for
        a legacy ``ts``-only checkpoint, the derived cutover position. Returns
        ``None`` if the evidence table is missing — degrade gracefully per
        RFC 0014 optional-schema guard.
        """
        columns = (
            "id, idempotency_key, ts, lat, lon, accuracy, trigger, event, "
            "endpoint_identity, raw_payload, recorded_at"
        )
        settled = "recorded_at <= now() - make_interval(secs => $1)"
        try:
            async with owntracks_connection(pool) as conn:
                # Roll back a caught read error before updating projection status.
                async with conn.transaction():
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

                    if cursor is None and since is not None:
                        cursor = await self._cutover_cursor(conn, since)

                    if cursor is None:
                        rows = await conn.fetch(
                            f"""
                            SELECT {columns}
                            FROM {_EVIDENCE_TABLE}
                            WHERE {settled}
                            ORDER BY recorded_at ASC, id ASC
                            LIMIT $2
                            """,
                            float(self.ingest_settle_seconds),
                            self.batch_limit,
                        )
                    else:
                        op = ">=" if cursor.inclusive else ">"
                        rows = await conn.fetch(
                            f"""
                            SELECT {columns}
                            FROM {_EVIDENCE_TABLE}
                            WHERE {settled}
                              AND (recorded_at, id) {op} ($2, $3)
                            ORDER BY recorded_at ASC, id ASC
                            LIMIT $4
                            """,
                            float(self.ingest_settle_seconds),
                            cursor.recorded_at,
                            cursor.id,
                            self.batch_limit,
                        )
        except asyncpg.PostgresError:
            logger.exception("Failed reading %s", _EVIDENCE_TABLE)
            return None

        return list(rows), cursor

    async def _cutover_cursor(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        since: datetime,
    ) -> IngestCursor | None:
        """Translate a legacy device-``ts`` watermark without rescanning history.

        Rows with ``ts > since`` were never projected, so start at the
        earliest-arrived of them (inclusive). If none exist, every row is
        covered: start after the newest settled row. ``None`` (empty table)
        starts from the beginning.
        """
        first_unprojected = await conn.fetchrow(
            f"""
            SELECT recorded_at, id FROM {_EVIDENCE_TABLE}
            WHERE ts > $1
            ORDER BY recorded_at ASC, id ASC
            LIMIT 1
            """,
            since,
        )
        if first_unprojected is not None:
            return IngestCursor(
                recorded_at=first_unprojected["recorded_at"],
                id=first_unprojected["id"],
                inclusive=True,
            )
        newest_settled = await conn.fetchrow(
            f"""
            SELECT recorded_at, id FROM {_EVIDENCE_TABLE}
            WHERE recorded_at <= now() - make_interval(secs => $1)
            ORDER BY recorded_at DESC, id DESC
            LIMIT 1
            """,
            float(self.ingest_settle_seconds),
        )
        if newest_settled is None:
            return None
        return IngestCursor(recorded_at=newest_settled["recorded_at"], id=newest_settled["id"])

    async def _project_point_event(
        self,
        chronicler_pool: asyncpg.Pool | asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        row: dict[str, Any],
        *,
        outlier: TrailOutlier | None = None,
        existing_only: bool = False,
    ) -> PointEvent | None:
        idempotency_key = row["idempotency_key"]
        source_ref = f"{_EVIDENCE_TABLE}:{idempotency_key}"
        existing = await get_owntracks_point(chronicler_pool, source_ref)
        if existing is not None:
            if existing.tombstone_at is not None:
                return None
            revised = await revise_owntracks_point(
                chronicler_pool, source_ref, outlier.to_payload() if outlier else None
            )
            return existing if revised is not None else None
        if existing_only:
            return None
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

        async with owntracks_connection(chronicler_pool) as conn:
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
        chronicler_pool: asyncpg.Pool | asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
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

        # Late fixes: arrival-order paging can deliver fixes at or before an
        # endpoint's open-episode horizon (offline queues, legacy cutover
        # re-reads). They never extend or replace that carryover; see
        # ``_late_segments``.
        horizons = self._carryover_horizons(prior_carryover)
        late_rows = [
            r
            for r in rows
            if (h := horizons.get(r["endpoint_identity"])) is not None and r["ts"] <= h[1]
        ]
        late_keys = {id(r) for r in late_rows}
        rows = [r for r in rows if id(r) not in late_keys]
        gap = timedelta(minutes=self.movement_gap_minutes)
        episodes_upserted = 0
        segments: list[dict] = []
        if rows:
            # Segment in device-time order.  Sorting by upload time instead would
            # yield negative ts gaps for buffered points, which pass the gap check
            # and merge fixes taken hours (and airports) apart (bu-8r9q06).
            # Group by endpoint first so interleaved devices cannot fragment each
            # other's movement into single-fix segments.
            rows = sorted(rows, key=lambda r: (r["endpoint_identity"], *trail_order_key(r)))

            # Each segment: list of rows in the same movement episode.
            # Also tracks whether the segment continues a prior-batch open episode.
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

        segments.extend(self._late_segments(late_rows, gap))

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

            if seg.get("late"):
                # Point events only when the late span lies within the
                # movement gap of the open carryover episode or any stored
                # movement episode (the live path would have joined them);
                # otherwise a standalone episode. Either way the carryover
                # stays put.
                if point_count < MIN_MOVEMENT_POINTS:
                    continue
                carry_start, carry_end = horizons[endpoint_identity]
                if effective_start_at <= carry_end + gap and end_at >= carry_start - gap:
                    continue
                async with owntracks_connection(chronicler_pool) as pooled:
                    if await self._merge_overlapping_episode(
                        cast(asyncpg.Connection, pooled),
                        endpoint_identity=endpoint_identity,
                        start_at=effective_start_at - gap,
                        end_at=end_at + gap,
                    ):
                        continue
            # Record carryover for every live segment (keyed by endpoint, so
            # the last one wins) before deciding whether to emit: a segment
            # still below the minimum evidence may grow past it next batch.
            # A late segment's state is discarded with ``slot``.
            slot: dict[str, Any] = {
                "source_ref": seg_source_ref,
                "start_at": effective_start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "start_lat": effective_start_lat,
                "start_lon": effective_start_lon,
                "point_count": point_count,
            }
            if not seg.get("late"):
                new_carryover[endpoint_identity] = slot

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
                async with owntracks_connection(chronicler_pool) as pooled:
                    merged = await self._merge_overlapping_episode(
                        cast(asyncpg.Connection, pooled),
                        endpoint_identity=endpoint_identity,
                        start_at=effective_start_at,
                        end_at=end_at,
                    )
            terminal = (float(last["lat"]), float(last["lon"]))
            if merged is not None:
                stored_terminal = (merged["end_lat"], merged["end_lon"])
                valid_stored = all(value is not None for value in stored_terminal)
                if merged["end_at"] > end_at:
                    terminal = stored_terminal if valid_stored else None
                elif merged["end_at"] == end_at and valid_stored:
                    terminal = stored_terminal
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
                slot.update(
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
            }
            if terminal is not None:
                payload.update(end_lat=terminal[0], end_lon=terminal[1])
            slot.update(
                end_lat=terminal[0] if terminal is not None else None,
                end_lon=terminal[1] if terminal is not None else None,
            )

            evidence_refs = evidence_refs_from_event_ids(seg_event_ids)

            # Confidence: a movement episode rests on a single, weak evidence
            # kind — raw GPS fixes from one device. With no independent
            # corroboration it stays ``low`` (still counted, but flagged for
            # re-reconciliation).
            confidence = derive_confidence([EvidenceKind(name="gps")])

            async with owntracks_connection(chronicler_pool) as conn:
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

    def _carryover_horizons(self, prior_carryover: dict) -> dict[str, tuple[datetime, datetime]]:
        """Map endpoint → readable open-episode span ``(start_at, end_at)``."""
        horizons: dict[str, tuple[datetime, datetime]] = {}
        for endpoint, carry in prior_carryover.items():
            if endpoint.startswith("_") or not isinstance(carry, dict):
                continue
            try:
                start_at = datetime.fromisoformat(carry["start_at"])
                end_at = datetime.fromisoformat(carry["end_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if start_at.tzinfo is None or end_at.tzinfo is None:
                continue
            horizons[endpoint] = (min(start_at, end_at), max(start_at, end_at))
        return horizons

    @staticmethod
    def _late_segments(late_rows: list[dict[str, Any]], gap: timedelta) -> list[dict]:
        """Group late fixes by endpoint and gap into standalone segments."""
        segments: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for row in sorted(late_rows, key=lambda r: (r["endpoint_identity"], *trail_order_key(r))):
            if current and (
                row["endpoint_identity"] != current[-1]["endpoint_identity"]
                or row["ts"] - current[-1]["ts"] > gap
            ):
                segments.append(current)
                current = []
            current.append(row)
        if current:
            segments.append(current)
        return [
            {
                "rows": seg,
                "source_ref": None,
                "prior_start_at": None,
                "prior_start_lat": None,
                "prior_start_lon": None,
                "prior_point_count": 0,
                "late": True,
            }
            for seg in segments
        ]

    async def _merge_overlapping_episode(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
        *,
        endpoint_identity: str,
        start_at: datetime,
        end_at: datetime,
    ) -> dict[str, Any] | None:
        """Return a stored movement episode overlapping this span, if any.

        Include the same canonical source reference: a shorter replay must
        not shrink its terminal time/location. The earliest overlapping
        episode wins so repeated merges converge on one row.
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
              AND start_at <= $5
              AND end_at >= $4
            ORDER BY start_at ASC, source_ref ASC
            LIMIT 1
            """,
            self.source_name,
            EPISODE_TYPE_MOVEMENT,
            endpoint_identity,
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
            "end_lat": self._coerce_finite_float(payload.get("end_lat")),
            "end_lon": self._coerce_finite_float(payload.get("end_lon")),
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
    "INGEST_CURSOR_CARRYOVER_KEY",
    "INGEST_SETTLE_SECONDS",
    "IngestCursor",
    "MIN_MOVEMENT_POINTS",
    "MOVEMENT_GAP_MINUTES",
    "OwnTracksPointAdapter",
    "SOURCE_NAME",
]
