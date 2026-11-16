"""The ~150 m spatial coarsening scheme location summaries keep after raw expiry (bu-s11n0s.7)."""

from __future__ import annotations

import math
import random

import pytest

from butlers.chronicler.adapters.owntracks_trail import haversine_meters
from butlers.chronicler.spatial_precision import (
    EARTH_MEAN_RADIUS_M,
    LATITUDE_BANDS,
    SPATIAL_PRECISION_M,
    SPATIAL_SCHEME_REVISION,
    SpatialCell,
    coarsen,
    coarsen_location_payload,
    longitude_cells,
)

pytestmark = pytest.mark.unit

# Precise, deliberately off-centre sentinels: none of these is a cell centre.
_SENTINELS = [
    (1.2345678, 103.8765432),
    (-33.8567844, 151.2152967),
    (51.5007292, -0.1246254),
    (64.1265206, -21.8174393),
]


def test_the_scheme_is_versioned_and_declares_nominal_precision() -> None:
    assert SPATIAL_SCHEME_REVISION == "eqcell-150m-v1"
    assert SPATIAL_PRECISION_M == 150
    assert EARTH_MEAN_RADIUS_M == 6_371_008.8
    assert math.ceil(math.pi * EARTH_MEAN_RADIUS_M / 150) == LATITUDE_BANDS


@pytest.mark.parametrize(("lat", "lon"), _SENTINELS)
def test_a_precise_fix_becomes_its_cell_centre(lat: float, lon: float) -> None:
    cell = coarsen(lat, lon)

    assert (cell.lat, cell.lon) != (lat, lon)
    # Within half a cell diagonal of the original (cells are ~150 m on a side).
    assert haversine_meters(lat, lon, cell.lat, cell.lon) < SPATIAL_PRECISION_M
    assert cell.precision_m == SPATIAL_PRECISION_M
    assert cell.scheme == SPATIAL_SCHEME_REVISION


def test_every_fix_in_a_cell_yields_the_same_output() -> None:
    cell = coarsen(*_SENTINELS[0])
    nudged = coarsen(_SENTINELS[0][0] + 1e-6, _SENTINELS[0][1] - 1e-6)
    assert nudged == cell


def test_coarsening_is_idempotent_everywhere() -> None:
    rng = random.Random(20261115)
    points = [(rng.uniform(-90, 90), rng.uniform(-180, 180)) for _ in range(2000)]
    points += [(90.0, 0.0), (-90.0, 0.0), (89.9999, 179.9999), (-89.9999, -180.0), (0.0, 180.0)]
    for lat, lon in points:
        cell = coarsen(lat, lon)
        assert coarsen(cell.lat, cell.lon) == cell, (lat, lon)


def test_latitude_band_boundaries_are_half_open() -> None:
    band = LATITUDE_BANDS // 3
    boundary = -90.0 + band * 180.0 / LATITUDE_BANDS
    assert coarsen(boundary + 1e-9, 10.0).band == band
    assert coarsen(boundary - 1e-9, 10.0).band == band - 1


def test_cells_are_about_150_m_on_a_side_at_the_equator() -> None:
    columns = longitude_cells(LATITUDE_BANDS // 2)
    assert columns == round(2 * math.pi * EARTH_MEAN_RADIUS_M / 150)
    a = coarsen(0.0001, 10.0)
    b = coarsen(0.0001, 10.0 + 360.0 / columns)
    assert b.column == a.column + 1
    assert haversine_meters(a.lat, a.lon, b.lat, b.lon) == pytest.approx(150, rel=0.01)


def test_the_polar_bands_follow_the_same_formula() -> None:
    north = coarsen(90.0, 123.0)
    south = coarsen(-90.0, -45.0)
    assert (north.band, south.band) == (LATITUDE_BANDS - 1, 0)
    # The polar band centre is ~75 m from the pole: a ~471 m circle, so three ~150 m cells.
    assert longitude_cells(north.band) == longitude_cells(south.band) == 3
    assert {coarsen(90.0, lon).column for lon in range(-180, 180, 10)} == {0, 1, 2}
    assert coarsen(90.0, 179.0) == coarsen(90.0, 130.0)


def test_a_band_narrower_than_one_cell_still_has_one_cell() -> None:
    # No band of this scheme is that narrow; the floor keeps the formula total anyway.
    assert all(longitude_cells(band) >= 1 for band in (0, 1, LATITUDE_BANDS - 1))
    with pytest.raises(ValueError):
        longitude_cells(LATITUDE_BANDS)


@pytest.mark.parametrize("lon", [180.0, -180.0, 540.0, -540.0])
def test_the_antimeridian_wraps_to_one_cell(lon: float) -> None:
    assert coarsen(12.0, lon) == coarsen(12.0, -180.0)


def test_cells_either_side_of_the_antimeridian_are_neighbours() -> None:
    east = coarsen(12.0, 179.99999)
    west = coarsen(12.0, -179.99999)
    columns = longitude_cells(east.band)
    assert (east.column, west.column) == (columns - 1, 0)
    assert -180.0 <= west.lon < 180.0 and -180.0 <= east.lon < 180.0
    assert haversine_meters(east.lat, east.lon, west.lat, west.lon) < 2 * SPATIAL_PRECISION_M


@pytest.mark.parametrize(
    ("lat", "lon"),
    [(math.nan, 0.0), (0.0, math.inf), (90.0001, 0.0), (-91.0, 0.0), (True, 0.0)],
)
def test_invalid_coordinates_refuse(lat: float, lon: float) -> None:
    with pytest.raises(ValueError):
        coarsen(lat, lon)


def test_the_cell_carries_no_input_precision() -> None:
    cell = coarsen(*_SENTINELS[1])
    assert isinstance(cell, SpatialCell)
    assert cell.as_payload() == {
        "lat": cell.lat,
        "lon": cell.lon,
        "spatial_precision_m": 150,
        "spatial_scheme": "eqcell-150m-v1",
    }


# ── Summary payloads (S4b-2) ─────────────────────────────────────────────────

_MOVEMENT = {
    "endpoint_identity": "owntracks:alice",
    "point_count": 4,
    "path_m": 812.4,
    "start_lat": 1.2345678,
    "start_lon": 103.8765432,
    "end_lat": 1.2401234,
    "end_lon": 103.8812345,
}
_PLACE = {
    "endpoint_identity": "owntracks:alice",
    "point_count": 9,
    "centroid_lat": 51.5007292,
    "centroid_lon": -0.1246254,
    "label": "Home",
    "label_source": "owner_reference",
    "bridged": False,
}
_AWAY = {"max_distance_km": 12.3, "farthest_lat": -33.8567844, "farthest_lon": 151.2152967}


@pytest.mark.parametrize("payload", [_MOVEMENT, _PLACE, _AWAY], ids=["movement", "place", "away"])
def test_every_coordinate_pair_in_a_summary_becomes_its_cell_centre(payload: dict) -> None:
    coarse = coarsen_location_payload(payload)
    pairs = [(k, k[: -len("lat")] + "lon") for k in payload if k.endswith("lat")]
    assert pairs
    for lat_key, lon_key in pairs:
        cell = coarsen(payload[lat_key], payload[lon_key])
        assert (coarse[lat_key], coarse[lon_key]) == (cell.lat, cell.lon)
    others = {k: v for k, v in payload.items() if not k.endswith(("lat", "lon"))}
    assert {k: coarse[k] for k in others} == others
    assert (coarse["spatial_precision_m"], coarse["spatial_scheme"]) == (150, "eqcell-150m-v1")
    assert payload is not coarse and "spatial_scheme" not in payload


def test_coarsening_a_summary_twice_changes_nothing() -> None:
    once = coarsen_location_payload(_MOVEMENT)
    assert coarsen_location_payload(once) == once


def test_an_absent_or_unknown_coordinate_stays_unknown() -> None:
    payload = {**_MOVEMENT, "end_lat": None, "end_lon": None}
    coarse = coarsen_location_payload(payload)
    assert (coarse["end_lat"], coarse["end_lon"]) == (None, None)
    assert coarse["start_lat"] == coarsen(_MOVEMENT["start_lat"], _MOVEMENT["start_lon"]).lat


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({**_PLACE, "anchor_lat": 1.0, "anchor_lon": 2.0}, id="an-unknown-pair"),
        pytest.param({**_PLACE, "latitude": 1.0}, id="an-unknown-name"),
        pytest.param({**_MOVEMENT, "start_lon": None}, id="half-a-pair"),
        pytest.param({**_MOVEMENT, "start_lat": "1.23"}, id="not-a-number"),
        pytest.param({**_MOVEMENT, "start_lat": 91.0}, id="out-of-range"),
        pytest.param({**_PLACE, "meta": {"lat": 1.0}}, id="nested-in-an-object"),
        pytest.param({**_PLACE, "path": [{"lon": 2.0}]}, id="nested-in-a-list"),
        pytest.param({**_PLACE, "a": [[{"b": {"geo_latitude": 1.0}}]]}, id="deeply-nested"),
    ],
)
def test_a_payload_it_cannot_fully_coarsen_is_refused(payload: dict) -> None:
    # Fail closed: an exact coordinate the scheme does not know must never survive silently.
    with pytest.raises(ValueError):
        coarsen_location_payload(payload)


def test_nested_values_without_coordinates_are_kept_as_they_are() -> None:
    payload = {**_PLACE, "meta": {"name": "home", "tags": ["a", {"kind": "stay"}]}}
    assert coarsen_location_payload(payload)["meta"] == payload["meta"]
