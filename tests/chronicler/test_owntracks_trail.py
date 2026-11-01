"""Shared OwnTracks trail-quality policy (bu-2tsgqb.6).

Synthetic traces modelled on the Oct 16-18 Beijing trail: a landing-time
eastern-Poland fix, Beijing <-> Guangzhou flips within a minute, GPS jitter,
a genuine flight, and a stale anchor that must not reject a real trail.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from butlers.chronicler.adapters.owntracks_trail import (
    TRAIL_ANCHORS_CARRYOVER_KEY,
    TrailAnchor,
    classify_trail,
    dump_anchors,
    load_anchors,
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
