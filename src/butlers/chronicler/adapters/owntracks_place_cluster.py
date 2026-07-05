"""OwnTracks GPS place-cluster projection adapter (bu-ac2pg, epic bu-p2d0f).

Per the telemetry-distillation design (``docs/plans/2026-07-06-telemetry-
distillation-design.md`` §3.2), this adapter reads the same durable evidence
table as :mod:`butlers.chronicler.adapters.owntracks`
(``connectors.owntracks_points``) and clusters stationary point runs into
labeled ``place_episode`` rows — a complementary, independent signal to that
adapter's ``movement_episode`` rollups.

**Canonical identifiers (coordinate with bu-whhll.5 — Wi-Fi SSID presence
adapter — per design §6.2, so ``occupation.py``'s corroborator list can add
both cleanly without a naming collision):**

- ``source_name = "owntracks.place_cluster"``
- ``episode_type = "place_episode"``

Algorithm (deterministic, single pass over points sorted by ``ts``,
per-endpoint):

1. A contiguous run of points within :data:`DEFAULT_RADIUS_METERS` of the
   cluster's running centroid, with no inter-point gap exceeding
   :data:`DEFAULT_MAX_GAP_MINUTES`, forms one cluster candidate.
2. **Teleport-outlier tolerance:** a single point that briefly breaks the
   radius (GPS multipath/urban-canyon glitch) does not, by itself, close the
   cluster. It is treated as noise and excluded from the cluster's centroid
   whenever the *next* point resumes within radius of the pre-glitch
   centroid (and within the gap budget). Only when two consecutive points
   both fall outside the cluster's radius is the run treated as genuine
   departure — the cluster is closed and a new one starts at the first
   confirmed away-point.
3. A cluster is only upserted as a ``place_episode`` once its dwell
   (``end_at - start_at``) reaches :data:`DEFAULT_MIN_DWELL_MINUTES`. A
   still-short cluster is simply not emitted yet (no partial/fabricated
   episode) — it may still grow past the threshold on a later run via
   cross-batch carryover, exactly like ``owntracks.py``'s movement-episode
   carryover.
4. Labeling is a deterministic distance-threshold match against owner-
   declared reference points (see :func:`parse_place_references`) — no
   geocoding, no LLM. A recurring cluster that matches no reference point is
   labeled :data:`PLACE_UNKNOWN_LABEL` (honest, not discarded — design
   principle: evidence, not fabrication).

**Owner configuration** — reference points (e.g. home/work lat-lon) are
supplied via the ``OWNTRACKS_PLACE_REFERENCES`` environment variable, a JSON
list of ``{"label": ..., "lat": ..., "lon": ..., "radius_m"?: ...}``
objects, mirroring the ``HA_WELLNESS_RULES_EXTRA`` owner-extensibility
pattern (``connectors/home_assistant_wellness.py``) rather than requiring a
new migration/table for this bead. Unset/empty means every cluster surfaces
as ``place_unknown`` — honest, not silently mislabeled.

**Privacy note (verified 2026-07-06):** ``connectors.owntracks_points``
already stores exact lat/lon at rest (the "metadata tier" GPS-stripping in
``connectors/owntracks.py`` applies only to the ingestion *envelope*, a
different surface). The sibling ``movement_episode`` payload already stores
raw start/end lat/lon with ``privacy=normal`` (see ``owntracks.py``'s module
docstring — the Map/Travel lane needs real coordinates). This adapter follows
the same precedent: the episode payload stores the cluster centroid
lat/lon plus the derived label, not a raw point trail.

No LLM call — Tier-0 deterministic projection only (RFC 0014 §D5).
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import asyncpg

from butlers.chronicler.adapters._owner_entity import (
    resolve_owner_entity_id,
    upsert_owner_episode_entity,
)
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.confidence import (
    EvidenceKind,
    derive_confidence,
    evidence_refs_from_event_ids,
)
from butlers.chronicler.models import Episode, Layer, Precision, Privacy
from butlers.chronicler.storage import get_carryover, save_carryover, upsert_episode

logger = logging.getLogger(__name__)

SOURCE_NAME = "owntracks.place_cluster"
EPISODE_TYPE_PLACE = "place_episode"
_EVIDENCE_TABLE = "connectors.owntracks_points"

# The sibling point-projection adapter's identifiers — used only to look up
# already-projected location point events for this adapter's evidence_refs.
_POINT_SOURCE_NAME = "owntracks.points"
_POINT_EVENT_TYPE = "location"

DEFAULT_BATCH_LIMIT = 1000

# Stationary points within this radius of the cluster centroid belong to the
# same place cluster. ~150m per the design doc — wide enough to absorb
# ordinary GPS jitter while staying tight enough to distinguish adjacent
# places (e.g. home vs. a neighbor's unit).
DEFAULT_RADIUS_METERS = 150.0

# A cluster must span at least this long before it is upserted as a
# place_episode. 20 minutes per the design doc — long enough that passing
# through a place (e.g. waiting at a traffic light) never qualifies.
DEFAULT_MIN_DWELL_MINUTES = 20

# Points separated by more than this are never part of the same cluster,
# regardless of distance — prevents two unrelated visits to the same
# coordinate (e.g. home today and home next week) from being merged into one
# multi-day "dwell". Wider than MOVEMENT_GAP_MINUTES (30) since a stationary
# device can legitimately ping less often than a moving one.
DEFAULT_MAX_GAP_MINUTES = 60

# Points whose device timestamp deviates from server ingestion time by more
# than this threshold are treated as having an implausible device clock and
# are clamped to recorded_at — identical rationale/default to
# owntracks.py's CLOCK_SKEW_THRESHOLD_HOURS (same evidence rows, same defect
# class).
CLOCK_SKEW_THRESHOLD_HOURS = 4

# A cluster centroid within this distance of an owner-declared reference
# point is labeled with that reference's label. Wider than the clustering
# radius by default to tolerate centroid drift relative to the owner's
# declared point (which is typically a single lat/lon, not a centroid).
DEFAULT_LABEL_RADIUS_METERS = 200.0

PLACE_UNKNOWN_LABEL = "place_unknown"

_EARTH_RADIUS_METERS = 6_371_000.0


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in meters.

    Pure function — no I/O, deterministic. Accurate enough at the sub-city
    scale this adapter clusters at (haversine error vs. an ellipsoidal model
    is negligible below tens of kilometers).
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_METERS * c


@dataclass(frozen=True)
class PlaceReference:
    """One owner-declared reference point (e.g. home, work) for labeling."""

    label: str
    lat: float
    lon: float
    radius_m: float = DEFAULT_LABEL_RADIUS_METERS


def parse_place_references(raw: str) -> tuple[PlaceReference, ...]:
    """Parse the ``OWNTRACKS_PLACE_REFERENCES`` JSON into ``PlaceReference`` objects.

    The value is a JSON list of objects with keys
    ``{label, lat, lon, radius_m?}``. ``label``/``lat``/``lon`` are required;
    ``radius_m`` defaults to :data:`DEFAULT_LABEL_RADIUS_METERS` when absent.

    Mirrors ``home_assistant_wellness.py::parse_rules_extra``'s owner-
    extensibility convention (ADR-2): no migration, no new table for this
    bead — an env var an owner can set without a code change.

    Returns an empty tuple for an unset/blank env var (every cluster then
    surfaces as :data:`PLACE_UNKNOWN_LABEL` — honest, not fabricated).

    Raises:
        ValueError: On malformed JSON, wrong top-level type, or an entry
            missing a required key / carrying a non-finite lat/lon — with a
            clear, actionable message naming the env var.
    """
    if not raw.strip():
        return ()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OWNTRACKS_PLACE_REFERENCES is not valid JSON: {exc}") from exc

    if not isinstance(parsed, list):
        raise ValueError("OWNTRACKS_PLACE_REFERENCES must be a JSON list of reference objects")

    references: list[PlaceReference] = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise ValueError(
                f"OWNTRACKS_PLACE_REFERENCES[{i}] must be a JSON object, got {type(item).__name__}"
            )
        label = item.get("label")
        if not label or not isinstance(label, str):
            raise ValueError(
                f"OWNTRACKS_PLACE_REFERENCES[{i}] is missing a non-empty string 'label'"
            )

        lat = _require_finite_float(item.get("lat"), index=i, field_name="lat")
        lon = _require_finite_float(item.get("lon"), index=i, field_name="lon")

        radius_m = DEFAULT_LABEL_RADIUS_METERS
        if "radius_m" in item:
            radius_m = _require_finite_float(item.get("radius_m"), index=i, field_name="radius_m")
            if radius_m <= 0:
                raise ValueError(
                    f"OWNTRACKS_PLACE_REFERENCES[{i}].radius_m must be positive, got {radius_m}"
                )

        references.append(PlaceReference(label=label, lat=lat, lon=lon, radius_m=radius_m))

    return tuple(references)


def _require_finite_float(value: Any, *, index: int, field_name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"OWNTRACKS_PLACE_REFERENCES[{index}].{field_name} must be a number, got {value!r}"
        ) from exc
    if not math.isfinite(numeric):
        raise ValueError(
            f"OWNTRACKS_PLACE_REFERENCES[{index}].{field_name} must be finite, got {value!r}"
        )
    return numeric


@dataclass
class ClusterSpan:
    """A candidate (or confirmed) stationary place cluster.

    Tracks a running centroid via ``sum_lat``/``sum_lon``/``point_count``
    rather than a materialized point list, so cross-batch carryover stays a
    handful of scalar fields (see :func:`cluster_points`).
    """

    endpoint_identity: str
    start_at: datetime
    end_at: datetime
    sum_lat: float
    sum_lon: float
    point_count: int

    @property
    def centroid_lat(self) -> float:
        return self.sum_lat / self.point_count

    @property
    def centroid_lon(self) -> float:
        return self.sum_lon / self.point_count

    @property
    def dwell(self) -> timedelta:
        return self.end_at - self.start_at

    def source_ref(self) -> str:
        """Deterministic, idempotent source_ref derived from (endpoint, start_at).

        Recomputing this from scalar fields (rather than persisting it
        through carryover) means a resumed cluster always yields the exact
        same source_ref as long as ``start_at`` is preserved across batches
        — which :func:`cluster_points` guarantees.
        """
        start_tst = int(self.start_at.timestamp())
        return f"{_EVIDENCE_TABLE}:place:{self.endpoint_identity}:{start_tst}"


def cluster_points(
    rows: list[dict[str, Any]],
    *,
    radius_m: float,
    max_gap: timedelta,
    prior_carryover: dict | None = None,
) -> tuple[list[ClusterSpan], dict]:
    """Deterministic radius/dwell clustering over points sorted by ``ts``.

    ``rows`` must already be sorted by ``ts`` ascending (as returned by
    ``_fetch_points``) and each must carry ``ts`` (tz-aware datetime),
    ``lat``, ``lon``, ``endpoint_identity``.

    Returns ``(spans, new_carryover)``. ``spans`` includes every cluster
    formed in this batch, whether or not it has yet reached the dwell
    threshold — that decision belongs to the caller (see
    ``OwnTracksPlaceClusterAdapter._maybe_emit``), which also decides
    whether to upsert an episode. ``new_carryover`` captures the last
    cluster per endpoint so a stationary run spanning a batch boundary
    continues rather than fragmenting (mirrors ``owntracks.py``'s movement-
    episode carryover).

    Pure function — no I/O, no LLM, no side effects.
    """
    prior_carryover = prior_carryover or {}
    if not rows:
        return [], {}

    spans: list[ClusterSpan] = []
    n = len(rows)
    i = 0

    while i < n:
        endpoint = rows[i]["endpoint_identity"]
        cluster = _resume_from_carryover(
            prior_carryover.get(endpoint),
            endpoint=endpoint,
            row=rows[i],
            radius_m=radius_m,
            max_gap=max_gap,
        )
        if cluster is None:
            cluster = ClusterSpan(
                endpoint_identity=endpoint,
                start_at=rows[i]["ts"],
                end_at=rows[i]["ts"],
                sum_lat=rows[i]["lat"],
                sum_lon=rows[i]["lon"],
                point_count=1,
            )
        i += 1

        while i < n:
            row = rows[i]
            if row["endpoint_identity"] != cluster.endpoint_identity:
                break  # endpoint change always forces a boundary

            gap = row["ts"] - cluster.end_at
            if gap > max_gap:
                break

            dist = haversine_meters(
                cluster.centroid_lat, cluster.centroid_lon, row["lat"], row["lon"]
            )
            if dist <= radius_m:
                cluster.sum_lat += row["lat"]
                cluster.sum_lon += row["lon"]
                cluster.point_count += 1
                cluster.end_at = row["ts"]
                i += 1
                continue

            # Out-of-radius candidate. Tolerate a single transient glitch:
            # if the *next* point resumes within radius (and gap budget) of
            # this cluster's still-frozen centroid, treat `row` as noise —
            # exclude it entirely and absorb the point after it directly.
            if i + 1 < n:
                nxt = rows[i + 1]
                nxt_gap = nxt["ts"] - cluster.end_at
                if (
                    nxt["endpoint_identity"] == cluster.endpoint_identity
                    and nxt_gap <= max_gap
                    and haversine_meters(
                        cluster.centroid_lat, cluster.centroid_lon, nxt["lat"], nxt["lon"]
                    )
                    <= radius_m
                ):
                    cluster.sum_lat += nxt["lat"]
                    cluster.sum_lon += nxt["lon"]
                    cluster.point_count += 1
                    cluster.end_at = nxt["ts"]
                    i += 2
                    continue

            # Two consecutive out-of-radius points (or end of batch) confirms
            # genuine movement away — close this cluster here.
            break

        spans.append(cluster)

    new_carryover: dict = {}
    for span in spans:
        new_carryover[span.endpoint_identity] = {
            "start_at": span.start_at.isoformat(),
            "end_at": span.end_at.isoformat(),
            "sum_lat": span.sum_lat,
            "sum_lon": span.sum_lon,
            "point_count": span.point_count,
        }

    return spans, new_carryover


def _resume_from_carryover(
    carry: Any,
    *,
    endpoint: str,
    row: dict[str, Any],
    radius_m: float,
    max_gap: timedelta,
) -> ClusterSpan | None:
    """Validate ``carry`` and decide whether it extends into ``row``.

    Returns a ``ClusterSpan`` (already including ``row``) when the carryover
    is well-formed and chronologically/spatially continuous. Returns
    ``None`` when the carryover should be discarded (malformed, or simply
    not continuous — both cases start a fresh cluster from ``row``, mirroring
    ``owntracks.py``'s ``_resolve_carryover_segment`` convention).
    """
    if carry is None:
        return None
    if not isinstance(carry, dict):
        logger.warning("Discarding non-dict place-cluster carryover for %s: %r", endpoint, carry)
        return None

    try:
        raw_start_at = carry["start_at"]
        raw_end_at = carry["end_at"]
        sum_lat = float(carry["sum_lat"])
        sum_lon = float(carry["sum_lon"])
        point_count = int(carry["point_count"])
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "Discarding malformed place-cluster carryover for %s: %r (%s)", endpoint, carry, exc
        )
        return None

    if point_count <= 0:
        logger.warning(
            "Discarding place-cluster carryover for %s with non-positive point_count: %r",
            endpoint,
            carry,
        )
        return None

    try:
        start_at = datetime.fromisoformat(raw_start_at)
        end_at = datetime.fromisoformat(raw_end_at)
    except (TypeError, ValueError):
        logger.warning(
            "Discarding place-cluster carryover for %s with invalid ISO timestamps: %r",
            endpoint,
            carry,
        )
        return None

    if start_at.tzinfo is None or end_at.tzinfo is None:
        logger.warning(
            "Discarding naive (tz-less) place-cluster carryover for %s: %r", endpoint, carry
        )
        return None

    gap = row["ts"] - end_at
    if gap < timedelta(0) or gap > max_gap:
        return None  # not malformed — simply doesn't continue; start fresh

    centroid_lat = sum_lat / point_count
    centroid_lon = sum_lon / point_count
    if haversine_meters(centroid_lat, centroid_lon, row["lat"], row["lon"]) > radius_m:
        return None  # owner moved away before this batch started; start fresh

    return ClusterSpan(
        endpoint_identity=endpoint,
        start_at=start_at,
        end_at=row["ts"],
        sum_lat=sum_lat + row["lat"],
        sum_lon=sum_lon + row["lon"],
        point_count=point_count + 1,
    )


class OwnTracksPlaceClusterAdapter(ProjectionAdapter):
    """Project ``connectors.owntracks_points`` rows into ``place_episode`` rows.

    Independent of, and complementary to,
    :class:`butlers.chronicler.adapters.owntracks.OwnTracksPointAdapter`'s
    ``movement_episode`` rollups — both read the same evidence table but
    project different signal shapes (movement vs. stationary dwell).
    """

    def __init__(
        self,
        *,
        batch_limit: int = DEFAULT_BATCH_LIMIT,
        radius_m: float = DEFAULT_RADIUS_METERS,
        min_dwell_minutes: int = DEFAULT_MIN_DWELL_MINUTES,
        max_gap_minutes: int = DEFAULT_MAX_GAP_MINUTES,
        clock_skew_threshold_hours: int = CLOCK_SKEW_THRESHOLD_HOURS,
        reference_points: tuple[PlaceReference, ...] = (),
    ) -> None:
        super().__init__(SOURCE_NAME)
        if radius_m <= 0:
            raise ValueError(f"radius_m must be positive, got {radius_m}")
        if min_dwell_minutes < 0:
            raise ValueError(f"min_dwell_minutes must be non-negative, got {min_dwell_minutes}")
        if max_gap_minutes <= 0:
            raise ValueError(f"max_gap_minutes must be positive, got {max_gap_minutes}")
        if clock_skew_threshold_hours < 0:
            raise ValueError(
                f"clock_skew_threshold_hours must be non-negative, got {clock_skew_threshold_hours}"
            )
        self.batch_limit = batch_limit
        self.radius_m = radius_m
        self.min_dwell = timedelta(minutes=min_dwell_minutes)
        self.max_gap = timedelta(minutes=max_gap_minutes)
        self.clock_skew_threshold = timedelta(hours=clock_skew_threshold_hours)
        self.reference_points = tuple(reference_points)

    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        # owntracks_points.id is a UUID, not an integer serial — same
        # rationale as owntracks.py: watermark on ts only.
        del since_id
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

            normalized, warnings = self._normalize_row(row)
            for warning in warnings:
                logger.warning("%s", warning)
                result.warnings.append(warning)
            if normalized is not None:
                valid_rows.append(normalized)

        if valid_rows:
            entity_id = await resolve_owner_entity_id(pool)
            prior_carryover = await get_carryover(chronicler_pool, self.source_name)
            spans, new_carryover = cluster_points(
                valid_rows,
                radius_m=self.radius_m,
                max_gap=self.max_gap,
                prior_carryover=prior_carryover,
            )
            for span in spans:
                episode = await self._maybe_emit(chronicler_pool, span, entity_id=entity_id)
                if episode is not None:
                    result.rows_projected += 1
                    result.episodes_closed += 1
            await save_carryover(chronicler_pool, self.source_name, new_carryover)

        result.watermark = latest_watermark
        return result

    def _normalize_row(
        self,
        row: asyncpg.Record,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """Sanitize one raw row; see ``owntracks.py``'s twin method for rationale.

        Applies the same clock-skew clamp (ts -> recorded_at when device
        clock is implausible) and finite-float coordinate guard as the
        sibling point-projection adapter, since both read identical raw
        rows and are equally exposed to the same data-quality defects.
        """
        row_ref = str(row["id"]) if row["id"] is not None else "<unknown>"
        warnings: list[str] = []

        ts = row["ts"]
        if not isinstance(ts, datetime) or ts.tzinfo is None:
            return None, [
                f"Skipping malformed OwnTracks row {row_ref} for place clustering: "
                "ts must be timezone-aware"
            ]

        recorded_at = row["recorded_at"]
        if isinstance(recorded_at, datetime) and recorded_at.tzinfo is not None:
            delta = ts - recorded_at
            if abs(delta) > self.clock_skew_threshold:
                warnings.append(
                    f"OwnTracks row {row_ref} has implausible device timestamp "
                    f"(ts={ts.isoformat()}, recorded_at={recorded_at.isoformat()}, "
                    f"delta={delta}); clamping ts to recorded_at for place clustering."
                )
                ts = recorded_at

        endpoint_identity = row["endpoint_identity"]
        if not isinstance(endpoint_identity, str) or not endpoint_identity.strip():
            return None, [
                f"Skipping malformed OwnTracks row {row_ref} for place clustering: "
                "endpoint_identity missing"
            ]

        lat = self._coerce_finite_float(row["lat"])
        if lat is None:
            return None, [
                f"Skipping malformed OwnTracks row {row_ref} for place clustering: "
                "lat must be finite"
            ]

        lon = self._coerce_finite_float(row["lon"])
        if lon is None:
            return None, [
                f"Skipping malformed OwnTracks row {row_ref} for place clustering: "
                "lon must be finite"
            ]

        return (
            {
                "id": row["id"],
                "ts": ts,
                "lat": lat,
                "lon": lon,
                "endpoint_identity": endpoint_identity.strip(),
            },
            warnings,
        )

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

    async def _fetch_points(
        self,
        pool: asyncpg.Pool,
        since: datetime | None,
    ) -> list[asyncpg.Record] | None:
        """Fetch evidence rows since the watermark; ``None`` if table missing."""
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
                        SELECT id, ts, lat, lon, endpoint_identity, recorded_at
                        FROM {_EVIDENCE_TABLE}
                        ORDER BY ts ASC, id ASC
                        LIMIT $1
                        """,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, ts, lat, lon, endpoint_identity, recorded_at
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

    def _label_for(self, lat: float, lon: float) -> tuple[str, PlaceReference | None]:
        """Nearest owner-declared reference point within its labeling radius, or unknown."""
        best: PlaceReference | None = None
        best_dist: float = math.inf
        for ref in self.reference_points:
            dist = haversine_meters(lat, lon, ref.lat, ref.lon)
            if dist <= ref.radius_m and dist < best_dist:
                best = ref
                best_dist = dist
        if best is None:
            return PLACE_UNKNOWN_LABEL, None
        return best.label, best

    async def _maybe_emit(
        self,
        chronicler_pool: asyncpg.Pool,
        span: ClusterSpan,
        *,
        entity_id: Any,
    ) -> Episode | None:
        """Upsert ``span`` as a place_episode, unless it hasn't reached min dwell yet."""
        if span.dwell < self.min_dwell:
            return None

        label, matched = self._label_for(span.centroid_lat, span.centroid_lon)
        source_ref = span.source_ref()

        payload: dict[str, Any] = {
            "endpoint_identity": span.endpoint_identity,
            "point_count": span.point_count,
            "centroid_lat": span.centroid_lat,
            "centroid_lon": span.centroid_lon,
            "label": label,
        }
        if matched is not None:
            payload["matched_reference"] = matched.label

        confidence = derive_confidence([EvidenceKind(name="gps")])

        async with chronicler_pool.acquire() as conn:
            evidence_ids = await self._fetch_location_point_event_ids(conn, span)
            evidence_refs = evidence_refs_from_event_ids(evidence_ids)

            episode = await upsert_episode(
                conn,
                Episode(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    episode_type=EPISODE_TYPE_PLACE,
                    start_at=span.start_at,
                    end_at=span.end_at,
                    precision=Precision.EXACT,
                    title=f"Place: {label}",
                    payload=payload,
                    privacy=Privacy.NORMAL,
                    layer=Layer.ACTIVITY,
                    confidence=confidence,
                    evidence_refs=evidence_refs,
                ),
            )
            ep_id = episode.id if episode is not None else None
            await upsert_owner_episode_entity(conn, ep_id, owner_id=entity_id)
        return episode

    @staticmethod
    async def _fetch_location_point_event_ids(
        conn: asyncpg.Connection,
        span: ClusterSpan,
    ) -> list[Any]:
        """Look up already-projected ``location`` point events for evidence_refs.

        Reads ``chronicler.point_events`` (chronicler reading what chronicler
        wrote — the same convention ``occupation.py`` uses for its
        corroborator lookups), scoped to the same source rows
        :class:`~butlers.chronicler.adapters.owntracks.OwnTracksPointAdapter`
        already projects from this cluster's endpoint/time span.
        """
        rows = await conn.fetch(
            """
            SELECT id FROM point_events
            WHERE tombstone_at IS NULL
              AND source_name = $1
              AND event_type = $2
              AND occurred_at >= $3
              AND occurred_at <= $4
              AND payload ->> 'endpoint_identity' = $5
            ORDER BY occurred_at ASC, id ASC
            """,
            _POINT_SOURCE_NAME,
            _POINT_EVENT_TYPE,
            span.start_at,
            span.end_at,
            span.endpoint_identity,
        )
        return [r["id"] for r in rows]


__all__ = [
    "CLOCK_SKEW_THRESHOLD_HOURS",
    "DEFAULT_BATCH_LIMIT",
    "DEFAULT_LABEL_RADIUS_METERS",
    "DEFAULT_MAX_GAP_MINUTES",
    "DEFAULT_MIN_DWELL_MINUTES",
    "DEFAULT_RADIUS_METERS",
    "EPISODE_TYPE_PLACE",
    "PLACE_UNKNOWN_LABEL",
    "ClusterSpan",
    "OwnTracksPlaceClusterAdapter",
    "PlaceReference",
    "SOURCE_NAME",
    "cluster_points",
    "haversine_meters",
    "parse_place_references",
]
