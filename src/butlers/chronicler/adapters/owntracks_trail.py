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

Anchors and bounded candidate identities persist across batches. A new first
fix is provisional until a plausible companion confirms it. Three mutually
consistent candidates reject an isolated provisional seed, but never retract
an established earlier trail. Retained candidate rows are read again by exact
source identity, so their verdicts and derived contributions can be revised.
Replay from before an anchor's timestamp/UUID position resets its bootstrap
state deterministically; starting at a mature anchor retains its trust.

Pure functions only: no I/O, no LLM, no external geocoding.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

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
TRAIL_STATE_CARRYOVER_KEY = "_trail_state"

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


def trail_order_key(row: dict[str, Any]) -> tuple[datetime, str]:
    """One effective-device-time/source-UUID ordering for every trail consumer."""
    return row["ts"], str(row["id"])


def load_trail_state(carryover: dict) -> dict[str, dict[str, Any]]:
    """Read only the bounded, versioned identity state; old anchors stay trusted."""
    raw = carryover.get(TRAIL_STATE_CARRYOVER_KEY)
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
        return {}
    endpoints = raw.get("endpoints")
    if not isinstance(endpoints, dict):
        return {}
    result = {}
    for endpoint, entry in endpoints.items():
        if not isinstance(endpoint, str) or not isinstance(entry, dict):
            continue
        seed = entry.get("seed_id")
        pending = entry.get("pending_ids", [])
        anchor_id = entry.get("anchor_id")
        if anchor_id is not None and not isinstance(anchor_id, str):
            continue
        if (seed is not None and not isinstance(seed, str)) or not isinstance(pending, list):
            continue
        if len(pending) > REANCHOR_RUN - 1 or not all(isinstance(i, str) for i in pending):
            continue
        try:
            if any(
                value is not None and str(UUID(value)) != value
                for value in (seed, anchor_id, *pending)
            ):
                continue
        except ValueError:
            continue
        result[endpoint] = {"seed_id": seed, "pending_ids": list(pending)}
        if "anchor_id" in entry:
            result[endpoint]["anchor_id"] = anchor_id
    return result


def classify_trail(
    rows: list[dict[str, Any]],
    *,
    prior_anchors: dict[str, TrailAnchor] | None = None,
    max_speed_kmh: float = MAX_PLAUSIBLE_SPEED_KMH,
    reanchor_run: int = REANCHOR_RUN,
    state: dict[str, dict[str, Any]] | None = None,
    changes: dict[str, TrailOutlier | None] | None = None,
    rejected_seeds: set[str] | None = None,
) -> tuple[dict[int, TrailOutlier], dict[str, TrailAnchor]]:
    """Classify canonically ordered rows, including retained pending candidates.

    Optional mutable outputs carry bounded bootstrap identities and revisions
    to earlier retained point events. No source payload is copied into state.
    Rows without identities remain supported by the pure policy API.
    """
    anchors = dict(prior_anchors or {})
    state = state if state is not None else {}
    changes = changes if changes is not None else {}
    rejected_seeds = rejected_seeds if rejected_seeds is not None else set()
    pending: dict[str, list[int]] = {}
    seeds: dict[str, int] = {}
    outliers: dict[int, TrailOutlier] = {}
    first_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        first_rows.setdefault(row["endpoint_identity"], row)
    for endpoint, row in first_rows.items():
        anchor = anchors.get(endpoint)
        if anchor is None:
            continue
        anchor_id = state.get(endpoint, {}).get("anchor_id")
        before_anchor = row["ts"] < anchor.ts or (
            row["ts"] == anchor.ts
            and isinstance(anchor_id, str)
            and "id" in row
            and str(row["id"]) < anchor_id
        )
        if before_anchor:
            del anchors[endpoint]
            state.pop(endpoint, None)
    for index, row in enumerate(rows):
        endpoint = row["endpoint_identity"]
        identity = str(row["id"]) if "id" in row else None
        entry = state.setdefault(endpoint, {"seed_id": None, "pending_ids": [], "anchor_id": None})
        anchor = anchors.get(endpoint)
        if anchor is None:
            anchors[endpoint] = TrailAnchor(row["ts"], row["lat"], row["lon"])
            entry["anchor_id"] = identity
            entry["seed_id"] = identity
            seeds[endpoint] = index
            continue
        if identity is not None and identity == entry["seed_id"]:
            seeds[endpoint] = index
            continue
        distance, speed = _jump(anchor.ts, anchor.lat, anchor.lon, row)
        if _plausible(distance, speed, max_speed_kmh):
            anchors[endpoint] = TrailAnchor(row["ts"], row["lat"], row["lon"])
            entry["anchor_id"] = identity
            entry["seed_id"] = None
            seeds.pop(endpoint, None)
            pending.pop(endpoint, None)
            continue
        run = pending.setdefault(endpoint, [])
        if run:
            previous = rows[run[-1]]
            d, v = _jump(previous["ts"], previous["lat"], previous["lon"], row)
            if not _plausible(d, v, max_speed_kmh):
                run.clear()
        run.append(index)
        verdict = TrailOutlier(speed, distance / 1000)
        outliers[index] = verdict
        if len(run) >= reanchor_run:
            if endpoint in seeds or entry["seed_id"] is not None:
                if endpoint in seeds:
                    outliers[seeds[endpoint]] = verdict
                if entry["seed_id"] is not None:
                    changes[entry["seed_id"]] = verdict
                rejected_seeds.add(endpoint)
            for accepted in run:
                outliers.pop(accepted, None)
                if "id" in rows[accepted]:
                    changes[str(rows[accepted]["id"])] = None
            anchors[endpoint] = TrailAnchor(row["ts"], row["lat"], row["lon"])
            entry["anchor_id"] = identity
            entry["seed_id"] = None
            seeds.pop(endpoint, None)
            pending.pop(endpoint, None)
    for endpoint, entry in state.items():
        entry["pending_ids"] = [
            str(rows[i]["id"]) for i in pending.get(endpoint, []) if "id" in rows[i]
        ]
    return outliers, anchors


__all__ = [
    "MAX_PLAUSIBLE_SPEED_KMH",
    "MIN_JUMP_METERS",
    "OUTLIER_PAYLOAD_KEY",
    "OUTLIER_REASON_IMPLIED_SPEED",
    "REANCHOR_RUN",
    "TRAIL_ANCHORS_CARRYOVER_KEY",
    "TRAIL_STATE_CARRYOVER_KEY",
    "TrailAnchor",
    "TrailOutlier",
    "classify_trail",
    "dump_anchors",
    "haversine_meters",
    "load_anchors",
    "load_trail_state",
    "trail_order_key",
]
