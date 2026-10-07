"""Shared OwnTracks retention values; none of these values grants authority.

The connector owns raw birth and logical identity. Chronicler owns projection
coverage and policy. A digest binds stored bytes, never an authenticated source.
Spatial precision is independent of Chronicler's temporal Precision enum.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

POLICY_STATE_KEY = "chronicler/owntracks/retention"
DEFAULT_DAYS = 30
SPATIAL_SCHEME_VERSION = 1
SPATIAL_PRECISION_METRES = 150
EARTH_RADIUS_METRES = 6_371_008.8
DEVICE_SKEW = timedelta(hours=4)
ADAPTER_NAMES = (
    "owntracks.place_cluster",
    "owntracks.points",
    "owntracks.ssid_presence",
)


def strict_days(value: Any) -> int:
    """The raw policy is owner-shortenable, never larger than thirty days."""
    if type(value) is not int or not 1 <= value <= DEFAULT_DAYS:
        raise ValueError("retention days must be an integer from 1 to 30")
    return value


def utc(value: datetime) -> datetime:
    """Reject ambiguous naive times instead of assuming a machine timezone."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("retention timestamps must have a timezone")
    return value.astimezone(UTC)


def retention_birth(device_at: datetime, recorded_at: datetime) -> datetime:
    """Freeze the existing four-hour plausibility rule at original acceptance."""
    device, recorded = utc(device_at), utc(recorded_at)
    return device if abs(device - recorded) <= DEVICE_SKEW else recorded


def content_digest(value: Mapping[str, Any]) -> bytes:
    """Bind canonical JSON, refusing non-finite floats and non-JSON objects."""
    encoded = json.dumps(
        dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).digest()


def logical_digest(idempotency_key: str) -> bytes:
    """Opaque lifecycle key; its digest is neither anonymous nor authorization."""
    return hashlib.sha256(idempotency_key.encode("utf-8")).digest()


@dataclass(frozen=True)
class SpatialCell:
    latitude: float
    longitude: float
    scheme_version: int = SPATIAL_SCHEME_VERSION
    nominal_precision_m: int = SPATIAL_PRECISION_METRES


def spatial_cell(latitude: float, longitude: float) -> SpatialCell:
    """Return the accepted nominal-150m cell centre, including wrapped poles.

    This discloses a spatial cell and episode times; it is not anonymity and
    does not assert sensor accuracy. Cell centres are stable on repeated use.
    """
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        raise ValueError("invalid coordinate")
    lat, lon = float(latitude), float(longitude)
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90:
        raise ValueError("invalid coordinate")
    bands = math.ceil(math.pi * EARTH_RADIUS_METRES / SPATIAL_PRECISION_METRES)
    band = min(bands - 1, math.floor((lat + 90) * bands / 180))
    centre_lat = (band + 0.5) * 180 / bands - 90
    cells = max(
        1,
        round(
            2
            * math.pi
            * EARTH_RADIUS_METRES
            * math.cos(math.radians(centre_lat))
            / SPATIAL_PRECISION_METRES
        ),
    )
    wrapped = (lon + 180) % 360
    cell = min(cells - 1, math.floor(wrapped * cells / 360))
    return SpatialCell(centre_lat, (cell + 0.5) * 360 / cells - 180)


def path_increment(previous: tuple[float, float], current: tuple[float, float]) -> float:
    """Actual observed edge length, not straight-line reconstruction of a trip."""
    lat1, lon1 = map(math.radians, previous)
    lat2, lon2 = map(math.radians, current)
    a = math.sin((lat2 - lat1) / 2) ** 2 + (
        math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METRES * math.asin(math.sqrt(min(1.0, max(0.0, a))))


def reduced_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Minimize source-derived summary payload, preserving actual metrics.

    This function never invents missing metrics or copies an independently
    supplied static reference. That exception needs its own native provenance.
    Unknown nested metadata is discarded, since it may contain raw fixes.
    """
    result: dict[str, Any] = {}
    for key in ("point_count", "path_m", "duration_seconds"):
        if key not in payload:
            continue
        value = payload[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("summary metrics are unavailable")
        if key == "point_count" and type(value) is not int:
            raise ValueError("summary point count is unavailable")
        result[key] = value
    if isinstance(payload.get("place"), str) and payload["place"] in {"home", "work"}:
        result["place"] = payload["place"]
    for prefix in ("start", "end", "centroid"):
        lat_key, lon_key = f"{prefix}_lat", f"{prefix}_lon"
        if lat_key in payload and lon_key in payload:
            cell = spatial_cell(payload[lat_key], payload[lon_key])
            result[lat_key], result[lon_key] = cell.latitude, cell.longitude
    result["spatial_precision_m"] = SPATIAL_PRECISION_METRES
    result["spatial_scheme_version"] = SPATIAL_SCHEME_VERSION
    return result


def attempt_status(
    *,
    started_at: datetime,
    lease_until: datetime,
    completed_at: datetime | None,
    outcome: str | None,
    now: datetime,
) -> str:
    """An unfinished expired attempt cannot borrow an earlier success receipt."""
    started, deadline, current = utc(started_at), utc(lease_until), utc(now)
    if deadline < started or current < started:
        return "unknown"
    if completed_at is None:
        return "unknown" if current >= deadline else "pending"
    completed = utc(completed_at)
    if completed < started or completed > current:
        return "unknown"
    if outcome not in {"complete", "no_work", "failed", "cancelled", "unavailable"}:
        return "unknown"
    return "stale" if current - completed > timedelta(hours=7) else outcome
