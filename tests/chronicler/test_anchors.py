"""Owner anchor-place resolution (bu-r3fmja, REQ-butler-chronicler-015)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from butlers.chronicler.adapters.owntracks_place_cluster import PlaceReference
from butlers.chronicler.anchors import (
    GRAPH_HOME_RADIUS_M,
    GRAPH_WORK_RADIUS_M,
    KIND_HOME,
    KIND_OTHER,
    KIND_WORK,
    SOURCE_ENV,
    SOURCE_GRAPH,
    SOURCE_INFERRED,
    SOURCE_REGISTRY,
    AnchorPlace,
    GraphAddress,
    StaySample,
    address_sha256,
    anchors_from_graph,
    anchors_from_references,
    anchors_from_registry,
    centroid_of,
    infer_overnight_home,
    load_graph_addresses,
    nearest_anchor,
    resolve_anchors,
)

_SGT = ZoneInfo("Asia/Singapore")
_HOME = (1.2760, 103.8460)
_WORK = (1.2840, 103.8510)


def _address(
    via: str,
    *,
    address: str = "1 Example Road, Singapore 012345",
    label: str | None = None,
    target_name: str | None = None,
    lat_lon: tuple[float, float] | None = _WORK,
    status: str = "resolved",
    sha: str | None = None,
    is_primary: bool = False,
    updated_at: datetime | None = None,
) -> GraphAddress:
    return GraphAddress(
        via=via,
        label=label,
        target_name=target_name,
        address=address,
        is_primary=is_primary,
        updated_at=updated_at,
        geocode_sha256=sha if sha is not None else address_sha256(address),
        geocode_status=status,
        lat=lat_lon[0] if lat_lon else None,
        lon=lat_lon[1] if lat_lon else None,
    )


# ── Scenario: Work comes from the employer's geocoded address ────────────────


def test_work_comes_from_the_employers_geocoded_address() -> None:
    anchors = anchors_from_graph([_address("works-at", target_name="Acme Pte Ltd")])

    assert anchors == [
        AnchorPlace(
            kind=KIND_WORK,
            label="Acme Pte Ltd",
            lat=_WORK[0],
            lon=_WORK[1],
            radius_m=GRAPH_WORK_RADIUS_M,
            source=SOURCE_GRAPH,
        )
    ]


def test_owner_address_labelled_office_counts_as_work() -> None:
    anchors = anchors_from_graph([_address("self", label="Office")])

    assert [a.kind for a in anchors] == [KIND_WORK]


def test_primary_then_latest_employer_address_wins() -> None:
    old = datetime(2026, 1, 1, tzinfo=UTC)
    new = datetime(2026, 6, 1, tzinfo=UTC)
    anchors = anchors_from_graph(
        [
            _address("works-at", target_name="Newer", updated_at=new, lat_lon=(1.30, 103.80)),
            _address("works-at", target_name="Primary", is_primary=True, updated_at=old),
        ]
    )

    assert anchors[0].label == "Primary"


# ── Scenario: Home comes from the owner's geocoded address ───────────────────


def test_home_comes_from_the_owners_own_address() -> None:
    anchors = anchors_from_graph([_address("self", lat_lon=_HOME)])

    assert anchors == [
        AnchorPlace(
            kind=KIND_HOME,
            label="home",
            lat=_HOME[0],
            lon=_HOME[1],
            radius_m=GRAPH_HOME_RADIUS_M,
            source=SOURCE_GRAPH,
        )
    ]


def test_home_falls_back_to_the_rented_place_address() -> None:
    anchors = anchors_from_graph([_address("rental-location", lat_lon=_HOME)])

    assert [(a.kind, a.lat) for a in anchors] == [(KIND_HOME, _HOME[0])]


# ── Scenario: A stale geocode is not an anchor ───────────────────────────────


@pytest.mark.parametrize(
    "address",
    [
        _address("works-at", sha="0" * 64),
        _address("works-at", status="not_found", lat_lon=None),
        _address("works-at", status="failed"),
        _address("works-at", lat_lon=None),
    ],
    ids=["hash-mismatch", "not-found", "failed", "no-coordinates"],
)
def test_a_stale_or_unresolved_geocode_is_not_an_anchor(address: GraphAddress) -> None:
    assert anchors_from_graph([address]) == []


def test_address_hash_ignores_surrounding_whitespace() -> None:
    assert address_sha256("  1 Example Road \n") == address_sha256("1 Example Road")


def test_address_hash_matches_the_shared_test_vector() -> None:
    # Relationship stores the same hash (REQ-butler-relationship-006).
    assert address_sha256(" abc\n") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


# ── Scenario: Registry overrides the graph ───────────────────────────────────


def _anchor(kind: str, source: str, lat: float = 1.0) -> AnchorPlace:
    return AnchorPlace(kind=kind, label=kind, lat=lat, lon=103.0, radius_m=150, source=source)


def test_registry_overrides_the_graph_per_kind() -> None:
    registry = [_anchor(KIND_WORK, SOURCE_REGISTRY)]
    graph = [_anchor(KIND_WORK, SOURCE_GRAPH), _anchor(KIND_HOME, SOURCE_GRAPH)]

    resolved = resolve_anchors(registry=registry, graph=graph)

    assert {(a.kind, a.source) for a in resolved} == {
        (KIND_WORK, SOURCE_REGISTRY),
        (KIND_HOME, SOURCE_GRAPH),
    }


def test_env_references_are_the_last_declared_source() -> None:
    refs = anchors_from_references(
        [PlaceReference("Home", 1.0, 103.0), PlaceReference("gym", 1.1, 103.1)]
    )

    resolved = resolve_anchors(graph=[_anchor(KIND_HOME, SOURCE_GRAPH)], references=refs)

    assert {(a.kind, a.source) for a in resolved} == {
        (KIND_HOME, SOURCE_GRAPH),
        (KIND_OTHER, SOURCE_ENV),
    }


def test_registry_rows_keep_their_kind_radius_and_ssids() -> None:
    place = SimpleNamespace(
        label="HQ", kind="work", lat=1.2, lon=103.8, radius_m=80, ssids=["corp"]
    )

    (anchor,) = anchors_from_registry([place])

    assert (anchor.kind, anchor.radius_m, anchor.ssids, anchor.source) == (
        KIND_WORK,
        80.0,
        ("corp",),
        SOURCE_REGISTRY,
    )


def test_inferred_home_is_used_only_without_a_declared_home() -> None:
    inferred = _anchor(KIND_HOME, SOURCE_INFERRED)

    assert resolve_anchors(inferred_home=inferred) == (inferred,)
    declared = resolve_anchors(graph=[_anchor(KIND_HOME, SOURCE_GRAPH)], inferred_home=inferred)
    assert [a.source for a in declared] == [SOURCE_GRAPH]


def test_work_is_never_inferred() -> None:
    resolved = resolve_anchors(inferred_home=_anchor(KIND_HOME, SOURCE_INFERRED))

    assert all(a.kind != KIND_WORK for a in resolved)


# ── Scenario: Home inferred from overnight stays ─────────────────────────────


def _night(day: int, lat: float = _HOME[0], lon: float = _HOME[1]) -> StaySample:
    start = datetime(2026, 10, day, 23, 0, tzinfo=_SGT)
    return StaySample(start, start + timedelta(hours=8), lat, lon)


def test_home_inferred_from_three_overnight_stays() -> None:
    stays = [_night(1), _night(2), _night(3, lat=_HOME[0] + 0.0003), _night(4, *_WORK)]

    home = infer_overnight_home(stays, tz=_SGT)

    assert home is not None
    assert (home.kind, home.source) == (KIND_HOME, SOURCE_INFERRED)
    assert home.contains(*_HOME)


def test_two_nights_are_not_enough_to_infer_home() -> None:
    assert infer_overnight_home([_night(1), _night(2)], tz=_SGT) is None


def test_daytime_stays_never_infer_home() -> None:
    day = datetime(2026, 10, 1, 9, 0, tzinfo=_SGT)
    stays = [
        StaySample(day + timedelta(days=i), day + timedelta(days=i, hours=8), *_WORK)
        for i in range(5)
    ]

    assert infer_overnight_home(stays, tz=_SGT) is None


# ── Containment and payload parsing ──────────────────────────────────────────


def test_nearest_containing_anchor_wins() -> None:
    near = AnchorPlace(KIND_OTHER, "cafe", 1.2800, 103.8500, 300, SOURCE_REGISTRY)
    nearer = AnchorPlace(KIND_WORK, "work", 1.2801, 103.8501, 300, SOURCE_REGISTRY)

    assert nearest_anchor([near, nearer], 1.2801, 103.8501) is nearer
    assert nearest_anchor([near, nearer], 1.40, 103.90) is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"centroid_lat": 1.5, "centroid_lon": 103.5}, (1.5, 103.5)),
        ('{"centroid_lat": 1.5, "centroid_lon": 103.5}', (1.5, 103.5)),
        ({"centroid_lat": "nan", "centroid_lon": 103.5}, (None, None)),
        ({}, (None, None)),
        ("not json", (None, None)),
    ],
)
def test_centroid_of_tolerates_payload_shapes(payload: object, expected: tuple) -> None:
    assert centroid_of(payload) == expected


# ── Scenario: the graph surface degrades to no anchor ────────────────────────


class _FailingPool:
    async def fetch(self, *_: object) -> list:
        raise asyncpg.UndefinedTableError("relation does not exist")


async def test_absent_geocode_table_degrades_to_no_graph_anchor() -> None:
    assert await load_graph_addresses(_FailingPool()) == []


class _FlakyPool:
    async def fetch(self, *_: object) -> list:
        raise asyncpg.QueryCanceledError("canceling statement due to statement timeout")


async def test_a_transient_failure_fails_the_run_instead_of_dropping_anchors() -> None:
    # Degrading here would re-lane every block for one run, then back again.
    with pytest.raises(asyncpg.QueryCanceledError):
        await load_graph_addresses(_FlakyPool())
