"""Shared OwnTracks trail-quality policy (bu-2tsgqb.6).

Synthetic traces modelled on the Oct 16-18 Beijing trail: a landing-time
eastern-Poland fix, Beijing <-> Guangzhou flips within a minute, GPS jitter,
a genuine flight, and a stale anchor that must not reject a real trail.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from butlers.chronicler.adapters.owntracks_trail import (
    TRAIL_ANCHORS_CARRYOVER_KEY,
    TRAIL_STATE_CARRYOVER_KEY,
    TrailAnchor,
    classify_trail,
    dump_anchors,
    load_anchors,
    load_trail_state,
)

_T0 = datetime(2026, 10, 16, 23, 0, tzinfo=UTC)
_EP = "owntracks:owner"
BEIJING = (39.90, 116.40)
GUANGZHOU = (23.10, 113.34)
POLAND = (52.12, 22.06)
SINGAPORE = (1.36, 103.99)


def _fix(minutes: float, where: tuple[float, float], endpoint: str = _EP) -> dict:
    return {
        "ts": _T0 + timedelta(minutes=minutes),
        "lat": where[0],
        "lon": where[1],
        "endpoint_identity": endpoint,
    }


def test_single_teleport_spike_is_rejected_with_reason() -> None:
    rows = [_fix(0, BEIJING), _fix(4, POLAND), _fix(10, BEIJING)]
    outliers, anchors = classify_trail(rows)
    assert set(outliers) == {1}
    assert outliers[1].to_payload()["reason"] == "implied_speed"
    assert outliers[1].implied_speed_kmh > 10_000
    assert anchors[_EP].ts == rows[2]["ts"]


def test_alternating_far_city_flips_are_all_rejected() -> None:
    rows = [
        _fix(0.0, BEIJING),
        _fix(0.5, GUANGZHOU),
        _fix(1.0, BEIJING),
        _fix(1.5, GUANGZHOU),
        _fix(2.0, BEIJING),
        _fix(2.5, GUANGZHOU),
    ]
    outliers, _ = classify_trail(rows)
    assert set(outliers) == {1, 3, 5}


def test_genuine_flight_and_jitter_are_accepted() -> None:
    jitter = (BEIJING[0] + 0.004, BEIJING[1])  # ~450 m, one second later
    rows = [
        _fix(0, SINGAPORE),
        _fix(6 * 60 + 20, BEIJING),  # ~4,470 km in 6h20 ~ 705 km/h
        {**_fix(6 * 60 + 20, jitter), "ts": _T0 + timedelta(minutes=380, seconds=1)},
    ]
    outliers, _ = classify_trail(rows)
    assert outliers == {}


def test_landing_spike_after_long_offline_gap_is_rejected() -> None:
    rows = [_fix(0, SINGAPORE), _fix(6 * 60 + 14, POLAND), _fix(6 * 60 + 20, BEIJING)]
    outliers, _ = classify_trail(rows)
    assert set(outliers) == {1}


def test_three_consistent_candidates_reanchor_away_from_a_stale_anchor() -> None:
    # A bad anchor (cross-batch) in Poland; the real trail is in Beijing.
    prior = {_EP: TrailAnchor(ts=_T0 - timedelta(minutes=5), lat=POLAND[0], lon=POLAND[1])}
    rows = [_fix(0, BEIJING), _fix(5, BEIJING), _fix(10, BEIJING), _fix(15, BEIJING)]
    outliers, anchors = classify_trail(rows, prior_anchors=prior)
    assert outliers == {}
    assert (anchors[_EP].lat, anchors[_EP].lon) == BEIJING

    # A cold seed is weaker than an established anchor: later consensus must
    # reject the isolated first fix, rather than accept both distant cities.
    cold = [_fix(0, POLAND), _fix(5, BEIJING), _fix(10, BEIJING), _fix(15, BEIJING)]
    rejected, _ = classify_trail(cold)
    assert set(rejected) == {0}

    # Same evidence, split 1+1+2, survives a serialized restart after every batch.
    cold = [{**row, "id": UUID(int=i + 1)} for i, row in enumerate(cold)]
    state = {}
    anchors = {}
    revisions = {}
    by_id = {str(row["id"]): row for row in cold}
    for batch in [cold[:1], cold[1:2], cold[2:]]:
        pending = [by_id[i] for entry in state.values() for i in entry["pending_ids"]]
        _, anchors = classify_trail(
            [*pending, *batch], prior_anchors=anchors, state=state, changes=revisions
        )
        carry = json.loads(
            json.dumps({TRAIL_STATE_CARRYOVER_KEY: {"version": 1, "endpoints": state}})
        )
        state = load_trail_state(carry)
        assert all(len(entry["pending_ids"]) <= 2 for entry in state.values())
    assert revisions.get(str(cold[0]["id"])) is not None
    assert all(revisions.get(str(row["id"]), "not-revised") is None for row in cold[1:])
    assert anchors[_EP].ts == cold[-1]["ts"]

    # Confirmed history is never retracted merely because a later trip reanchors.
    confirmed = [_fix(-5, POLAND), *[{k: v for k, v in row.items() if k != "id"} for row in cold]]
    verdicts, _ = classify_trail(confirmed)
    assert verdicts == {}


def test_endpoints_are_judged_independently() -> None:
    rows = [_fix(0, BEIJING, "a"), _fix(1, GUANGZHOU, "b"), _fix(2, BEIJING, "a")]
    outliers, anchors = classify_trail(rows)
    assert outliers == {}
    assert set(anchors) == {"a", "b"}


def test_anchor_carryover_round_trip_and_malformed_entries_dropped() -> None:
    anchors = {_EP: TrailAnchor(ts=_T0, lat=BEIJING[0], lon=BEIJING[1])}
    carry = {TRAIL_ANCHORS_CARRYOVER_KEY: {**dump_anchors(anchors), "bad": {"ts": "x"}}}
    assert load_anchors(carry) == anchors
    assert load_anchors({}) == {}

    state = {_EP: {"seed_id": str(UUID(int=1)), "pending_ids": [str(UUID(int=2))]}}
    encoded = json.loads(
        json.dumps({TRAIL_STATE_CARRYOVER_KEY: {"version": 1, "endpoints": state}})
    )
    assert load_trail_state(encoded) == state
    for version in (True, 2, "1"):
        assert (
            load_trail_state({TRAIL_STATE_CARRYOVER_KEY: {"version": version, "endpoints": state}})
            == {}
        )
    assert (
        load_trail_state(
            {
                TRAIL_STATE_CARRYOVER_KEY: {
                    "version": 1,
                    "endpoints": {_EP: {"seed_id": "x", "pending_ids": ["a", "b", "c"]}},
                }
            }
        )
        == {}
    )


def test_replaying_a_batch_reproduces_its_outlier_verdicts() -> None:
    # A replay (watermark not checkpointed, reset, or backfill) sees the
    # anchors the first run saved; it must flag the same fixes, every time.
    rows = [
        _fix(0, BEIJING),
        _fix(5, BEIJING),
        _fix(10, POLAND),
        _fix(15, BEIJING),
        _fix(20, BEIJING),
    ]
    first, anchors = classify_trail(rows)
    assert set(first) == {2}
    for _ in range(2):
        replayed, anchors = classify_trail(rows, prior_anchors=anchors)
        assert set(replayed) == set(first)

    cold = [
        {**_fix(m, where), "id": UUID(int=i + 1)}
        for i, (m, where) in enumerate([(0, POLAND), (5, BEIJING), (10, BEIJING), (15, BEIJING)])
    ]
    state = {}
    rejected, anchors = classify_trail(cold, state=state)
    assert set(rejected) == {0}
    for _ in range(2):
        replayed, anchors = classify_trail(cold, prior_anchors=anchors, state=state)
        assert set(replayed) == {0}
