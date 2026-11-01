"""Shared OwnTracks trail-quality policy (bu-2tsgqb.6).

One policy decides which GPS fixes are physically plausible. The point
projection (:mod:`butlers.chronicler.adapters.owntracks`), the place-cluster
projection (:mod:`butlers.chronicler.adapters.owntracks_place_cluster`) and,
through the ``trail_outlier`` payload marker, the dashboard Map trail all
apply the same rule, so an impossible fix never reaches one surface while
being hidden from another.

Impossible-jump rule (deterministic, per endpoint, in device-``ts`` order):

- Each endpoint keeps an *anchor*: its last accepted fix.
- A fix is accepted when it lies within :data:`MIN_JUMP_METERS` of the anchor
  (GPS jitter is never judged on speed) or when the implied ground speed from
  the anchor is at most :data:`MAX_PLAUSIBLE_SPEED_KMH` (a commercial
  airliner's cruise, so a real flight never trips it).
- Otherwise the fix is a candidate outlier. Candidates that agree with each
  other (each within the speed limit of the previous candidate) accumulate;
  once :data:`REANCHOR_RUN` consecutive candidates agree, the anchor was the
  stale side, so those candidates are accepted and become the new anchor.
  This keeps a single bad anchor from rejecting a genuine trail forever.
- A rejected fix is never deleted. The evidence row stays verbatim in
  ``connectors.owntracks_points`` and its ``location`` point event is still
  projected, carrying :data:`OUTLIER_PAYLOAD_KEY` with the reason and implied
  speed, so retention and provenance keep the original observation.

Anchors persist across batches in each adapter's projection carryover under
:data:`TRAIL_ANCHORS_CARRYOVER_KEY`. Candidate runs do not: a run that spans a
batch boundary restarts in the next batch (its already-projected fixes stay
flagged), which is acceptable because batches are 1000 fixes, roughly days.
A batch whose first fix for an endpoint predates that endpoint's anchor is a
replay, so the anchor is dropped and the batch is judged from scratch:
replaying a batch reproduces its verdicts.

Pure functions only: no I/O, no LLM, no external geocoding.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Fastest plausible owner ground speed. 1000 km/h sits just above airliner
# cruise; a fix implying more than this from the last accepted fix is an
# impossible jump (the observed Beijing <-> Guangzhou flips implied ~100,000
# km/h, the landing-time eastern-Poland fix ~1,500 km/h).
MAX_PLAUSIBLE_SPEED_KMH = 1000.0

# Jumps shorter than this are GPS jitter, not teleports: never judge them on
# speed (two fixes 300 m apart one second apart would otherwise "exceed" the
# limit).
MIN_JUMP_METERS = 1000.0

# Consecutive mutually consistent candidates needed to conclude the anchor
# was stale and re-anchor on the candidates. Three so a pair of alternating
# glitches cannot flip the trail.
REANCHOR_RUN = 3

OUTLIER_PAYLOAD_KEY = "trail_outlier"
OUTLIER_REASON_IMPLIED_SPEED = "implied_speed"
TRAIL_ANCHORS_CARRYOVER_KEY = "_trail_anchors"

_EARTH_RADIUS_METERS = 6_371_000.0


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in meters."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_METERS * c


@dataclass(frozen=True)
class TrailAnchor:
    """An endpoint's last accepted fix."""

    ts: datetime
    lat: float
    lon: float

    def to_carryover(self) -> dict[str, Any]:
        return {"ts": self.ts.isoformat(), "lat": self.lat, "lon": self.lon}

    @classmethod
    def from_carryover(cls, raw: Any) -> TrailAnchor | None:
        """Parse a carryover entry; malformed entries are discarded, not raised."""
        if not isinstance(raw, dict):
            return None
        try:
            ts = datetime.fromisoformat(raw["ts"])
            lat = float(raw["lat"])
            lon = float(raw["lon"])
        except (KeyError, TypeError, ValueError):
            return None
        if ts.tzinfo is None or not (math.isfinite(lat) and math.isfinite(lon)):
            return None
        return cls(ts=ts, lat=lat, lon=lon)


@dataclass(frozen=True)
class TrailOutlier:
    """Why one fix was rejected as an impossible jump."""

    implied_speed_kmh: float
    distance_km: float
    reason: str = OUTLIER_REASON_IMPLIED_SPEED

    def to_payload(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "implied_speed_kmh": round(self.implied_speed_kmh),
            "distance_km": round(self.distance_km, 1),
        }


def load_anchors(carryover: dict | None) -> dict[str, TrailAnchor]:
    """Read per-endpoint anchors from an adapter's carryover blob."""
    raw = (carryover or {}).get(TRAIL_ANCHORS_CARRYOVER_KEY)
    if not isinstance(raw, dict):
        return {}
    anchors: dict[str, TrailAnchor] = {}
    for endpoint, entry in raw.items():
        anchor = TrailAnchor.from_carryover(entry)
        if anchor is None:
            logger.warning("Discarding malformed trail anchor for %s: %r", endpoint, entry)
            continue
        anchors[endpoint] = anchor
    return anchors


def dump_anchors(anchors: dict[str, TrailAnchor]) -> dict[str, Any]:
    """Serialize anchors for an adapter's carryover blob."""
    return {endpoint: anchor.to_carryover() for endpoint, anchor in anchors.items()}


def _jump(
    from_ts: datetime, from_lat: float, from_lon: float, row: dict[str, Any]
) -> tuple[float, float]:
    """Return ``(distance_m, implied_speed_kmh)`` from a fix to ``row``."""
    distance_m = haversine_meters(from_lat, from_lon, row["lat"], row["lon"])
    seconds = max((row["ts"] - from_ts).total_seconds(), 1.0)
    return distance_m, (distance_m / 1000.0) / (seconds / 3600.0)


def _plausible(distance_m: float, speed_kmh: float, max_speed_kmh: float) -> bool:
    return distance_m < MIN_JUMP_METERS or speed_kmh <= max_speed_kmh


def classify_trail(
    rows: list[dict[str, Any]],
    *,
    prior_anchors: dict[str, TrailAnchor] | None = None,
    max_speed_kmh: float = MAX_PLAUSIBLE_SPEED_KMH,
    reanchor_run: int = REANCHOR_RUN,
) -> tuple[dict[int, TrailOutlier], dict[str, TrailAnchor]]:
    """Flag impossible jumps in ``rows`` (sorted by ``ts``).

    Each row needs ``ts`` (tz-aware), ``lat``, ``lon`` and
    ``endpoint_identity``. Returns ``(outliers, anchors)``: ``outliers`` maps
    a row's index in ``rows`` to why it was rejected, and ``anchors`` is the
    per-endpoint last accepted fix to persist for the next batch (endpoints
    absent from this batch keep their prior anchor).
    """
    anchors: dict[str, TrailAnchor] = dict(prior_anchors or {})
    pending: dict[str, list[int]] = {}
    outliers: dict[int, TrailOutlier] = {}

    # A batch whose first fix predates the endpoint's anchor is a replay
    # (unsaved watermark, reset or backfill): the anchor came from these very
    # rows or later ones, so judging against it would wave every fix through.
    # Judge the batch from scratch instead, so the verdicts do not depend on
    # how many times it was projected.
    first_ts: dict[str, datetime] = {}
    for row in rows:
        first_ts.setdefault(row["endpoint_identity"], row["ts"])
    for endpoint, ts in first_ts.items():
        prior = anchors.get(endpoint)
        if prior is not None and ts < prior.ts:
            del anchors[endpoint]

    for index, row in enumerate(rows):
        endpoint = row["endpoint_identity"]
        anchor = anchors.get(endpoint)
        if anchor is None:
            # No history yet: nothing trustworthy to judge the first fix against.
            anchors[endpoint] = TrailAnchor(ts=row["ts"], lat=row["lat"], lon=row["lon"])
            continue

        distance_m, speed_kmh = _jump(anchor.ts, anchor.lat, anchor.lon, row)
        if _plausible(distance_m, speed_kmh, max_speed_kmh):
            anchors[endpoint] = TrailAnchor(ts=row["ts"], lat=row["lat"], lon=row["lon"])
            pending.pop(endpoint, None)
            continue

        run = pending.setdefault(endpoint, [])
        if run:
            prev = rows[run[-1]]
            prev_distance, prev_speed = _jump(prev["ts"], prev["lat"], prev["lon"], row)
            if not _plausible(prev_distance, prev_speed, max_speed_kmh):
                run.clear()
        run.append(index)
        outliers[index] = TrailOutlier(
            implied_speed_kmh=speed_kmh,
            distance_km=distance_m / 1000.0,
        )

        if len(run) >= reanchor_run:
            # The candidates agree with each other: the anchor was stale.
            for accepted in run:
                outliers.pop(accepted, None)
            anchors[endpoint] = TrailAnchor(ts=row["ts"], lat=row["lat"], lon=row["lon"])
            pending.pop(endpoint, None)

    return outliers, anchors


__all__ = [
    "MAX_PLAUSIBLE_SPEED_KMH",
    "MIN_JUMP_METERS",
    "OUTLIER_PAYLOAD_KEY",
    "OUTLIER_REASON_IMPLIED_SPEED",
    "REANCHOR_RUN",
    "TRAIL_ANCHORS_CARRYOVER_KEY",
    "TrailAnchor",
    "TrailOutlier",
    "classify_trail",
    "dump_anchors",
    "haversine_meters",
    "load_anchors",
]
