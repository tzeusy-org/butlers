"""Versioned ~150 m spatial coarsening for location summaries (bu-s11n0s.7 S3).

When raw OwnTracks fixes expire, the summaries they fed (place centroids, movement endpoints)
survive at reduced spatial precision instead of the exact observed coordinates. This module is
that reduction: a deterministic, idempotent snap to the centre of an approximately 150 m cell.

Scheme ``eqcell-150m-v1``:

- Earth is a sphere of mean radius 6 371 008.8 m.
- Latitude is cut into ``ceil(pi * R / 150)`` equal bands (~150 m tall).
- Each band is cut into ``max(1, round(2 * pi * R * cos(band centre) / 150))`` equal longitude
  cells (~150 m wide at the band centre), never fewer than one. The polar bands, centred
  ~75 m from the pole, have three.
- Bands and cells are half-open (``[lower, upper)``); latitude 90 joins the top band. Longitude
  wraps at the antimeridian, so 180 and -180 are the same cell.
- A point becomes its cell's centre. The centre lies in its own cell, so coarsening a coarsened
  point returns it unchanged.

Spatial precision is separate from the temporal :class:`~butlers.chronicler.models.Precision`
enum. ``spatial_precision_m = 150`` is the nominal cell size, not a sensor accuracy, and the
cells make no anonymity claim: times, counts and distances can still reveal routines.

Pure functions only: no I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

__all__ = [
    "EARTH_MEAN_RADIUS_M",
    "LATITUDE_BANDS",
    "SPATIAL_PRECISION_M",
    "SPATIAL_SCHEME_REVISION",
    "SpatialCell",
    "coarsen",
    "longitude_cells",
]

SPATIAL_SCHEME_REVISION = "eqcell-150m-v1"
SPATIAL_PRECISION_M = 150
EARTH_MEAN_RADIUS_M = 6_371_008.8
LATITUDE_BANDS = math.ceil(math.pi * EARTH_MEAN_RADIUS_M / SPATIAL_PRECISION_M)

_BAND_DEGREES = 180.0 / LATITUDE_BANDS


@dataclass(frozen=True)
class SpatialCell:
    """One ~150 m cell: its indices and the centre that stands in for every point inside it."""

    band: int
    column: int
    lat: float
    lon: float
    precision_m: int = SPATIAL_PRECISION_M
    scheme: str = SPATIAL_SCHEME_REVISION

    def as_payload(self) -> dict[str, Any]:
        """The cell as summary payload fields; nothing of the original point survives."""
        return {
            "lat": self.lat,
            "lon": self.lon,
            "spatial_precision_m": self.precision_m,
            "spatial_scheme": self.scheme,
        }


def _band_centre(band: int) -> float:
    return -90.0 + (band + 0.5) * _BAND_DEGREES


def longitude_cells(band: int) -> int:
    """How many equal longitude cells latitude band ``band`` is cut into."""
    if not 0 <= band < LATITUDE_BANDS:
        raise ValueError(f"latitude band out of range: {band}")
    circumference = 2 * math.pi * EARTH_MEAN_RADIUS_M * math.cos(math.radians(_band_centre(band)))
    return max(1, round(circumference / SPATIAL_PRECISION_M))


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def coarsen(lat: float, lon: float) -> SpatialCell:
    """Snap ``(lat, lon)`` to the centre of its ~150 m cell. Refuses non-finite or |lat| > 90."""
    lat = _finite(lat, "lat")
    lon = _finite(lon, "lon")
    if not -90.0 <= lat <= 90.0:
        raise ValueError("lat must lie in [-90, 90]")

    band = min(math.floor((lat + 90.0) / _BAND_DEGREES), LATITUDE_BANDS - 1)
    columns = longitude_cells(band)
    column_degrees = 360.0 / columns
    wrapped = (lon + 180.0) % 360.0  # [0, 360): the antimeridian is one seam
    column = min(math.floor(wrapped / column_degrees), columns - 1)
    centre_lon = -180.0 + (column + 0.5) * column_degrees
    return SpatialCell(band=band, column=column, lat=_band_centre(band), lon=centre_lon)
