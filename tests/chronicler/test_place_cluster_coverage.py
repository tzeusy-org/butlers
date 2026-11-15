"""Projection coverage of the OwnTracks place-cluster adapter (bu-s11n0s.7, slice 2, PR-B).

Each test runs the adapter's own pure clustering (``cluster_points``, ``detect_away_spans``)
and its coverage bookkeeping (``record_stay_coverage``, ``record_trip_coverage``) over
consecutive runs, flushing into the in-memory stand-in for the coverage tables. Every hold path
has a test: the open stay, the held departure fix (both resolutions), the open away trip, a fix
in a stay and a trip at once, a rejected trail seed, and an endpoint not read this run. The
real-Postgres proofs live in ``tests/integration/test_owntracks_retention_coverage_integration.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest

from butlers.chronicler.adapters.owntracks_place_cluster import (
    AWAY_CARRYOVER_KEY,
    DEPARTURE_CARRY_PREFIX,
    STAY_CARRY_PREFIX,
    TRIP_CARRY_PREFIX,
    PlaceReference,
    cluster_points,
    detect_away_spans,
    hold_trail,
    record_stay_coverage,
    record_trip_coverage,
)
from butlers.chronicler.projection_coverage import ProjectionCoverage
from tests.chronicler.test_projection_coverage import PLACE_REVISION, _Tables, digest_for

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 3, 26, 8, 0, tzinfo=UTC)
_EP = "owntracks:alice"
_HOME = (1.30000, 103.80000)
_CAFE = (1.31000, 103.80000)  # ~1.1 km: another stay, still near home
_TRIP = (2.30000, 103.80000)  # ~111 km: away from home
_HOMES = (PlaceReference(label="home", lat=_HOME[0], lon=_HOME[1], radius_m=200),)
_MIN_DWELL = timedelta(minutes=20)
_MIN_AWAY = timedelta(minutes=60)
_STAY = f"{STAY_CARRY_PREFIX}{_EP}"
_TRIP_KEY = f"{TRIP_CARRY_PREFIX}{_EP}"
_DEPARTURE = f"{DEPARTURE_CARRY_PREFIX}{_EP}"


def _fix(minutes: float, at: tuple[float, float] = _HOME) -> dict[str, Any]:
    raw_id = uuid4()
    return {
        "id": raw_id,
        "idempotency_key": f"owntracks:alice:{raw_id.int % 10**9}:location",
        "retention_at": _T0 + timedelta(minutes=minutes),
        "content_digest": digest_for(raw_id),
        "ts": _T0 + timedelta(minutes=minutes),
        "lat": at[0],
        "lon": at[1],
        "endpoint_identity": _EP,
        "regions": (),
    }


def _episode(source_ref: str) -> UUID:
    # upsert_episode is idempotent by source_ref: a re-emitted span keeps its id.
    return uuid5(NAMESPACE_URL, source_ref)


class _Adapter:
    """The coverage half of one adapter run per call, over the adapter's pure functions."""

    def __init__(self, *, homes: tuple[PlaceReference, ...] = ()) -> None:
        self.tables = _Tables()
        self.carry: dict[str, Any] = {}
        self.homes = homes
        self.stays: dict[str, UUID] = {}
        self.trips: dict[str, UUID] = {}

    async def run(self, *fixes: dict[str, Any], rejected: frozenset[str] = frozenset()) -> None:
        rows = sorted(fixes, key=lambda row: row["ts"])
        prior_stays = dict(self.carry)
        prior_trips = dict(self.carry.get(AWAY_CARRYOVER_KEY) or {})
        carry = {key: value for key, value in self.carry.items() if key not in rejected}
        spans, stays = cluster_points(
            rows,
            radius_m=150.0,
            max_gap=timedelta(minutes=60),
            prior_carryover=carry,
            bridge_max=timedelta(hours=16),
        )
        trips_in = {k: v for k, v in prior_trips.items() if k not in rejected}
        trips, open_trips = detect_away_spans(
            rows,
            homes=self.homes,
            away_radius_m=50_000.0,
            max_gap=timedelta(hours=36),
            prior_carryover=trips_in,
        )
        stay_episodes = {
            s.source_ref(): _episode(s.source_ref()) for s in spans if s.dwell >= _MIN_DWELL
        }
        trip_episodes = {
            t.source_ref(): _episode(t.source_ref())
            for t in trips
            if t.point_count >= 2 and t.duration >= _MIN_AWAY
        }
        self.stays.update(stay_episodes)
        self.trips.update(trip_episodes)

        ledger = ProjectionCoverage(
            "owntracks.place_cluster", config_revision=PLACE_REVISION, evaluates_every_row=True
        )
        for row in rows:
            ledger.observe(row)
        present = {row["endpoint_identity"] for row in rows}
        record_stay_coverage(
            ledger,
            spans=spans,
            episodes=stay_episodes,
            prior_carryover=prior_stays,
            present=present,
            rejected=set(rejected),
        )
        record_trip_coverage(
            ledger,
            spans=trips,
            episodes=trip_episodes,
            open_trips=open_trips,
            prior_trips=prior_trips,
            present=present,
            rejected=set(rejected),
        )
        await ledger.flush(self.tables)
        self.carry = {
            **{k: v for k, v in carry.items() if not k.startswith("_")},
            **stays,
            AWAY_CARRYOVER_KEY: {
                **{k: v for k, v in trips_in.items() if k not in present},
                **open_trips,
            },
        }

    def keys(self, fix: dict[str, Any]) -> list[str] | None:
        member = self.tables.open.get(fix["id"])
        return member["carry_keys"] if member is not None else None

    def record(self, fix: dict[str, Any]) -> dict[str, Any] | None:
        return self.tables.coverage.get(fix["id"])


async def test_an_open_stay_holds_its_members_and_a_closed_stay_covers_them() -> None:
    adapter = _Adapter()
    stay = [_fix(m) for m in (0, 5, 10, 25)]
    await adapter.run(*stay)
    episode = _episode(f"connectors.owntracks_points:place:{_EP}:{int(_T0.timestamp())}")
    for fix in stay:
        assert adapter.keys(fix) == [_STAY]
        assert adapter.tables.open[fix["id"]]["output_episode_ids"] == [episode]
        assert adapter.record(fix) is None

    # Two fixes elsewhere: the owner left. The carried stay closes; a new stay opens.
    cafe = [_fix(30, _CAFE), _fix(35, _CAFE)]
    await adapter.run(*cafe)
    for fix in stay:
        record = adapter.record(fix)
        assert record is not None and record["disposition"] == "projected"
        assert record["output_episode_ids"] == [episode]
        assert adapter.keys(fix) is None
    for fix in cafe:
        assert adapter.keys(fix) == [_STAY] and adapter.record(fix) is None


async def test_a_stay_that_closes_below_the_minimum_dwell_is_evaluated_without_output() -> None:
    adapter = _Adapter()
    short = [_fix(0), _fix(5)]
    await adapter.run(*short, _fix(10, _CAFE), _fix(15, _CAFE))
    for fix in short:
        record = adapter.record(fix)
        assert record is not None and record["disposition"] == "evaluated_no_output"


async def test_an_open_stay_episode_reaches_members_stored_by_earlier_runs() -> None:
    adapter = _Adapter()
    early = [_fix(0), _fix(5)]
    await adapter.run(*early)
    assert adapter.tables.open[early[0]["id"]]["output_episode_ids"] == []

    await adapter.run(_fix(15), _fix(25))  # the same stay reaches the minimum dwell, still open
    episode = _episode(f"connectors.owntracks_points:place:{_EP}:{int(_T0.timestamp())}")
    for fix in early:
        assert adapter.keys(fix) == [_STAY]
        assert adapter.tables.open[fix["id"]]["output_episode_ids"] == [episode]


async def test_a_single_fix_glitch_inside_a_stay_is_evaluated_without_output() -> None:
    adapter = _Adapter()
    glitch = _fix(10, _CAFE)
    await adapter.run(_fix(0), _fix(5), glitch, _fix(15), _fix(25))
    record = adapter.record(glitch)
    assert record is not None and record["disposition"] == "evaluated_no_output"
    assert adapter.keys(glitch) is None


async def test_a_lone_fix_after_a_bridged_silence_is_held_until_the_next_run_decides() -> None:
    adapter = _Adapter()
    stay = [_fix(0), _fix(25)]
    lone = _fix(180, _CAFE)  # 2.5 h of silence, then one fix elsewhere ends the batch
    await adapter.run(*stay, lone)
    assert adapter.keys(lone) == [_DEPARTURE]
    assert all(adapter.keys(fix) == [_STAY] for fix in stay)

    # The owner is back home: the lone fix was a stray, and the stay continues.
    back = _fix(190)
    await adapter.run(back)
    record = adapter.record(lone)
    assert record is not None and record["disposition"] == "evaluated_no_output"
    assert adapter.keys(lone) is None
    assert adapter.keys(back) == [_STAY]
    assert all(adapter.keys(fix) == [_STAY] for fix in stay)


async def test_a_held_departure_fix_starts_the_next_stay_when_the_owner_did_leave() -> None:
    adapter = _Adapter()
    stay = [_fix(0), _fix(25)]
    lone = _fix(180, _CAFE)
    await adapter.run(*stay, lone)

    there = _fix(190, _CAFE)
    await adapter.run(there)
    # The stay ended at the departure fix and is covered with its (re-emitted) episode.
    episode = _episode(f"connectors.owntracks_points:place:{_EP}:{int(_T0.timestamp())}")
    for fix in stay:
        record = adapter.record(fix)
        assert record is not None and record["output_episode_ids"] == [episode]
    # The departure fix moved from its own carry into the new open stay.
    assert adapter.keys(lone) == [_STAY]
    assert adapter.keys(there) == [_STAY]
    assert adapter.record(lone) is None


async def test_a_departure_fix_whose_next_stay_closes_gets_that_stays_episode() -> None:
    adapter = _Adapter()
    lone = _fix(180, _CAFE)
    await adapter.run(_fix(0), _fix(25), lone)
    # At the cafe for 25 minutes, then home again: the cafe stay closes with an episode.
    await adapter.run(_fix(195, _CAFE), _fix(205, _CAFE), _fix(215, _HOME), _fix(220, _HOME))
    cafe_episode = _episode(
        f"connectors.owntracks_points:place:{_EP}:{int(lone['ts'].timestamp())}"
    )
    record = adapter.record(lone)
    assert record is not None and record["output_episode_ids"] == [cafe_episode]


async def test_a_fix_in_an_open_stay_and_an_open_trip_waits_for_both() -> None:
    adapter = _Adapter(homes=_HOMES)
    away = [_fix(0, _TRIP), _fix(30, _TRIP), _fix(70, _TRIP)]
    await adapter.run(*away)
    for fix in away:
        assert adapter.keys(fix) == sorted([_STAY, _TRIP_KEY])

    # A second stay on the same trip: the first stay closes, the trip stays open.
    elsewhere = (2.31000, 103.80000)
    later = [_fix(80, elsewhere), _fix(85, elsewhere)]
    await adapter.run(*later)
    stay_episode = _episode(f"connectors.owntracks_points:place:{_EP}:{int(_T0.timestamp())}")
    trip_episode = _episode(f"connectors.owntracks_points:away:{_EP}:{int(_T0.timestamp())}")
    for fix in away:
        assert adapter.keys(fix) == [_TRIP_KEY]
        assert adapter.record(fix) is None
        member = adapter.tables.open[fix["id"]]
        assert set(member["output_episode_ids"]) == {stay_episode, trip_episode}
    for fix in later:
        assert adapter.keys(fix) == sorted([_STAY, _TRIP_KEY])

    # Home again: the trip and the second stay close; every trip fix is now covered. The
    # bridged silence ends the second stay at the first fix home, long enough to emit.
    await adapter.run(_fix(200), _fix(205))
    second_stay = _episode(
        f"connectors.owntracks_points:place:{_EP}:{int(later[0]['ts'].timestamp())}"
    )
    for fix in away:
        record = adapter.record(fix)
        assert record is not None
        assert set(record["output_episode_ids"]) == {stay_episode, trip_episode}
    for fix in later:
        record = adapter.record(fix)
        assert record is not None
        assert set(record["output_episode_ids"]) == {second_stay, trip_episode}


async def test_an_endpoint_not_read_this_run_keeps_its_carries() -> None:
    adapter = _Adapter(homes=_HOMES)
    away = [_fix(0, _TRIP), _fix(30, _TRIP)]
    await adapter.run(*away)
    other = _fix(40) | {"endpoint_identity": "owntracks:bob"}
    await adapter.run(other)
    for fix in away:
        assert adapter.keys(fix) == sorted([_STAY, _TRIP_KEY])


async def test_a_rejected_trail_seed_closes_the_endpoints_carries() -> None:
    adapter = _Adapter(homes=_HOMES)
    away = [_fix(0, _TRIP), _fix(10, _TRIP)]  # below both thresholds so far
    await adapter.run(*away)
    assert all(adapter.keys(fix) == sorted([_STAY, _TRIP_KEY]) for fix in away)
    await adapter.run(_fix(40, _TRIP), rejected=frozenset({_EP}))
    for fix in away:
        assert adapter.keys(fix) is None
        record = adapter.record(fix)
        assert record is not None and record["disposition"] == "evaluated_no_output"


async def test_trail_seed_and_pending_candidates_are_held_and_released_when_resolved() -> None:
    tables = _Tables()
    seed, pending, settled = _fix(0), _fix(5), _fix(10)
    ledger = ProjectionCoverage(
        "owntracks.place_cluster", config_revision=PLACE_REVISION, evaluates_every_row=True
    )
    for fix in (seed, pending, settled):
        ledger.observe(fix)
    hold_trail(ledger, {_EP: {"seed_id": str(seed["id"]), "pending_ids": [str(pending["id"])]}})
    await ledger.flush(tables)
    assert tables.open[seed["id"]]["carry_keys"] == [f"trail:{_EP}"]
    assert tables.open[pending["id"]]["carry_keys"] == [f"trail:{_EP}"]
    assert tables.coverage[settled["id"]]["disposition"] == "evaluated_no_output"

    resolved = ProjectionCoverage(
        "owntracks.place_cluster", config_revision=PLACE_REVISION, evaluates_every_row=True
    )
    hold_trail(resolved, {_EP: {"seed_id": None, "pending_ids": []}})
    await resolved.flush(tables)
    assert tables.open == {}
    assert {tables.coverage[f["id"]]["disposition"] for f in (seed, pending)} == {
        "evaluated_no_output"
    }


def test_the_place_revision_moves_with_a_home_but_not_with_a_relabelled_place() -> None:
    from butlers.chronicler.adapters.owntracks_place_cluster import OwnTracksPlaceClusterAdapter

    cafe = PlaceReference(label="cafe", lat=_CAFE[0], lon=_CAFE[1], radius_m=100)
    base = OwnTracksPlaceClusterAdapter(reference_points=_HOMES).coverage_revision
    assert base == OwnTracksPlaceClusterAdapter(reference_points=_HOMES).coverage_revision
    moved_home = (PlaceReference(label="home", lat=_CAFE[0], lon=_CAFE[1], radius_m=200),)
    assert base != OwnTracksPlaceClusterAdapter(reference_points=moved_home).coverage_revision
    assert base != OwnTracksPlaceClusterAdapter(reference_points=()).coverage_revision
    assert (
        base
        != OwnTracksPlaceClusterAdapter(
            reference_points=_HOMES, min_dwell_minutes=30
        ).coverage_revision
    )
    assert base == OwnTracksPlaceClusterAdapter(reference_points=(*_HOMES, cafe)).coverage_revision
