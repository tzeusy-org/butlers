"""Situational activity precedence (bu-r3fmja, REQ-butler-chronicler-016)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from butlers.chronicler.aggregations import lane_for_activity
from butlers.chronicler.anchors import (
    KIND_AIRPORT,
    KIND_HOME,
    KIND_WORK,
    SOURCE_GRAPH,
    SOURCE_INFERRED,
    AnchorPlace,
)
from butlers.chronicler.models import Confidence
from butlers.chronicler.situation import (
    EPISODE_TYPE_AIRPORT,
    EPISODE_TYPE_HOME,
    EPISODE_TYPE_OUTING,
    EPISODE_TYPE_WORK,
    OWNER_ANSWER_SOURCE,
    PLACE_KIND_UNREGISTERED,
    SOURCE_NAME,
    ActivitySpan,
    Fix,
    WhereSegment,
    activity_span,
    derive_blocks,
    subtract,
    tail_hold,
)

_SGT = ZoneInfo("Asia/Singapore")
_DAY_START = datetime(2026, 11, 2, tzinfo=_SGT).astimezone(UTC)
_DAY_END = _DAY_START + timedelta(days=1)
_HOME = (1.2760, 103.8460)
_WORK = (1.2840, 103.8510)
_OUTING = (1.3500, 103.9900)  # ~17 km from home
_TRIP = (3.1400, 101.6900)  # Kuala Lumpur, > 50 km


def _at(hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 11, 2, hh, mm, tzinfo=_SGT).astimezone(UTC)


def _anchor(kind: str, lat_lon: tuple[float, float], source: str = SOURCE_GRAPH) -> AnchorPlace:
    return AnchorPlace(kind, kind, lat_lon[0], lat_lon[1], 150, source)


_ANCHORS = (_anchor(KIND_HOME, _HOME), _anchor(KIND_WORK, _WORK))


def _seg(start: datetime, end: datetime, lat_lon: tuple[float, float], **kw) -> WhereSegment:
    return WhereSegment(start, end, lat_lon[0], lat_lon[1], **kw)


def _span(start: datetime, end: datetime, source: str, episode_type: str) -> ActivitySpan:
    lane = lane_for_activity("activity", source, episode_type)
    return ActivitySpan(start, end, source, episode_type, lane)


def _derive(segments, activities=(), anchors=_ANCHORS):
    return derive_blocks(
        day_start=_DAY_START,
        day_end=_DAY_END,
        segments=segments,
        anchors=anchors,
        activities=list(activities),
    )


def _shape(blocks) -> list[tuple[str, datetime, datetime]]:
    return [(b.episode_type, b.start_at, b.end_at) for b in blocks]


# ── Scenario: At the work place is work ──────────────────────────────────────


def test_a_stay_at_the_work_place_is_work() -> None:
    (block,) = _derive([_seg(_at(9), _at(18), _WORK)])

    assert (block.episode_type, block.lane, block.place_kind) == (
        EPISODE_TYPE_WORK,
        "work",
        KIND_WORK,
    )
    assert block.confidence == Confidence.MEDIUM


def test_office_wifi_corroboration_raises_work_to_high() -> None:
    wifi = _span(_at(9), _at(12), "owntracks.ssid_presence", "occupation_presence_episode")

    (block,) = _derive([_seg(_at(9), _at(18), _WORK)], [wifi])

    assert block.confidence == Confidence.HIGH


def test_lunch_inside_a_work_stay_does_not_split_it() -> None:
    # eat ranks below work, so a meal never carves the work block.
    meal = _span(_at(12), _at(13), "health.meals", "eating_event")

    assert _shape(_derive([_seg(_at(9), _at(18), _WORK)], [meal])) == [
        (EPISODE_TYPE_WORK, _at(9), _at(18))
    ]


# ── Scenario: At home with a game running is play ────────────────────────────


def test_at_home_with_a_game_running_is_play_and_the_rest_is_rest() -> None:
    sleep = _span(_at(0), _at(7), "google_health.measurements", "sleep_episode")
    game = _span(_at(20), _at(22), "steam.play_history", "play_episode")

    blocks = _derive([_seg(_at(0), _at(23, 59), _HOME)], [sleep, game])

    assert _shape(blocks) == [
        (EPISODE_TYPE_HOME, _at(7), _at(20)),
        (EPISODE_TYPE_HOME, _at(22), _at(23, 59)),
    ]
    assert {b.lane for b in blocks} == {"rest"}


def test_music_never_displaces_a_place_block() -> None:
    music = _span(_at(10), _at(12), "spotify.session_summary", "listening_episode")

    assert (
        activity_span(
            layer="activity",
            source_name=music.source_name,
            episode_type=music.episode_type,
            start_at=music.start_at,
            end_at=music.end_at,
        )
        is None
    )


@pytest.mark.parametrize(
    ("source", "episode_type", "minutes"),
    [
        ("core.sessions", "work", 60),
        ("comms.message_bursts", "social_episode", 60),
        (SOURCE_NAME, EPISODE_TYPE_HOME, 60),
        ("steam.play_history", "play_episode", 9),
    ],
    ids=["butler-session", "comms-burst", "own-row", "under-10-min"],
)
def test_point_like_own_and_short_episodes_never_preempt(
    source: str, episode_type: str, minutes: int
) -> None:
    span = activity_span(
        layer="activity",
        source_name=source,
        episode_type=episode_type,
        start_at=_at(10),
        end_at=_at(10) + timedelta(minutes=minutes),
    )

    assert span is None


def test_evidence_layer_rows_never_preempt() -> None:
    span = activity_span(
        layer="evidence",
        source_name="steam.play_history",
        episode_type="play_episode",
        start_at=_at(10),
        end_at=_at(12),
    )

    assert span is None


def test_an_owner_answer_preempts_every_lane() -> None:
    answer = ActivitySpan(_at(12), _at(14), OWNER_ANSWER_SOURCE, "owner_rest_block", "rest")

    assert _shape(_derive([_seg(_at(9), _at(18), _WORK)], [answer])) == [
        (EPISODE_TYPE_WORK, _at(9), _at(12)),
        (EPISODE_TYPE_WORK, _at(14), _at(18)),
    ]


# ── Scenario: A stay at an unregistered place is social ──────────────────────


def test_a_stay_at_an_unregistered_place_is_social() -> None:
    (block,) = _derive([_seg(_at(13), _at(14), _OUTING)])

    assert (block.episode_type, block.lane, block.place_kind) == (
        EPISODE_TYPE_OUTING,
        "social",
        PLACE_KIND_UNREGISTERED,
    )
    assert block.confidence == Confidence.LOW
    assert block.distance_from_home_m is not None
    assert block.distance_from_home_m % 100 == 0
    assert 15_000 < block.distance_from_home_m < 20_000


def test_a_trip_beyond_fifty_km_gets_no_block() -> None:
    assert _derive([_seg(_at(9), _at(18), _TRIP)]) == []


def test_without_a_home_anchor_every_unregistered_stay_is_social() -> None:
    (block,) = _derive([_seg(_at(9), _at(18), _TRIP)], anchors=())

    assert (block.episode_type, block.distance_from_home_m) == (EPISODE_TYPE_OUTING, None)


def test_an_airport_anchor_is_travel() -> None:
    anchors = (*_ANCHORS, _anchor(KIND_AIRPORT, (1.3644, 103.9915)))

    (block,) = _derive([_seg(_at(6), _at(8), (1.3644, 103.9915))], anchors=anchors)

    assert (block.episode_type, block.lane) == (EPISODE_TYPE_AIRPORT, "travel")


def test_inferred_home_is_low_confidence() -> None:
    anchors = (_anchor(KIND_HOME, _HOME, source=SOURCE_INFERRED),)

    (block,) = _derive([_seg(_at(9), _at(12), _HOME)], anchors=anchors)

    assert (block.episode_type, block.confidence) == (EPISODE_TYPE_HOME, Confidence.LOW)


# ── Scenario: Hold at the last known place ───────────────────────────────────


def test_hold_at_the_last_known_place_covers_an_open_stay() -> None:
    fixes = [
        Fix(_at(12, 59), *_OUTING),
        Fix(_at(15, 2), *_HOME),
        Fix(_at(23, 30), _HOME[0] + 0.0004, _HOME[1]),
    ]

    hold = tail_hold(fixes, after=_at(14, 6), now=_at(23, 45), tz=_SGT)

    assert hold is not None
    assert (hold.start_at, hold.end_at, hold.provisional, hold.bridged) == (
        _at(15, 2),
        _at(23, 45),
        True,
        True,
    )


def test_hold_is_capped_twelve_hours_after_the_last_fix() -> None:
    hold = tail_hold([Fix(_at(8), *_HOME)], after=None, now=_at(23), tz=_SGT)

    assert hold is not None
    assert hold.end_at == _at(20)


def test_no_hold_once_a_stay_covers_the_last_fix() -> None:
    assert tail_hold([Fix(_at(8), *_HOME)], after=_at(9), now=_at(23), tz=_SGT) is None


def test_hold_continues_an_emitted_open_stay_from_its_end() -> None:
    # The clusterer emitted the open home stay up to the latest fix (18:30);
    # the phone then went silent, so the hold covers the evening.
    stay = _seg(_at(18, 30), _at(18, 30), _HOME, place_episode_ids=("p1",))
    fixes = [Fix(_at(18), *_HOME), Fix(_at(18, 30), *_HOME)]

    hold = tail_hold(fixes, after=_at(18, 30), now=_at(23), tz=_SGT, last_stay=stay)

    assert hold is not None
    assert (hold.start_at, hold.end_at, hold.provisional) == (_at(18, 30), _at(23), True)
    assert hold.place_episode_ids == ("p1",)


def test_no_hold_from_a_stay_the_latest_fix_has_left() -> None:
    stay = _seg(_at(18), _at(18), _HOME)
    fixes = [Fix(_at(18), *_OUTING)]

    assert tail_hold(fixes, after=_at(18), now=_at(23), tz=_SGT, last_stay=stay) is None


def test_hold_ends_at_the_latest_fixs_local_midnight() -> None:
    hold = tail_hold([Fix(_at(22), *_HOME)], after=None, now=_at(22) + timedelta(hours=5), tz=_SGT)

    assert hold is not None
    assert hold.end_at == _DAY_END


def test_corrected_spans_of_own_blocks_are_never_overlapped() -> None:
    blocks = _derive_corrected([_seg(_at(9), _at(18), _WORK)], [(_at(9), _at(12))])

    assert _shape(blocks) == [(EPISODE_TYPE_WORK, _at(12), _at(18))]


def _derive_corrected(segments, corrected):
    return derive_blocks(
        day_start=_DAY_START,
        day_end=_DAY_END,
        segments=segments,
        anchors=_ANCHORS,
        activities=[],
        corrected=corrected,
    )


def test_a_provisional_block_is_never_above_low() -> None:
    hold = _seg(_at(9), _at(12), _WORK, provisional=True)

    (block,) = _derive([hold])

    assert (block.provisional, block.confidence) == (True, Confidence.LOW)


# ── Scenario: Re-derivation replaces stale blocks ────────────────────────────


def test_source_ref_follows_the_block_start_so_reshaping_changes_it() -> None:
    sleep = _span(_at(0), _at(7), "google_health.measurements", "sleep_episode")
    stay = _seg(_at(0), _at(12), _HOME)

    (before,) = _derive([stay])
    (after,) = _derive([stay], [sleep])

    assert before.source_ref("2026-11-02") != after.source_ref("2026-11-02")
    assert after.source_ref("2026-11-02") == (
        f"chronicler.situation:2026-11-02:home:{int(_at(7).timestamp())}"
    )


def test_blocks_are_clipped_to_the_day() -> None:
    stay = _seg(_at(0) - timedelta(hours=3), _at(6), _HOME)

    assert _shape(_derive([stay])) == [(EPISODE_TYPE_HOME, _DAY_START, _at(6))]


# ── Scenario: Short pieces are dropped ───────────────────────────────────────


def test_short_pieces_are_dropped() -> None:
    sleep = _span(_at(0), _at(7, 50), "google_health.measurements", "sleep_episode")

    assert _derive([_seg(_at(0), _at(8), _HOME)], [sleep]) == []


def test_subtract_returns_ordered_remainders() -> None:
    assert subtract(_at(9), _at(18), [(_at(12), _at(13)), (_at(8), _at(10))]) == [
        (_at(10), _at(12)),
        (_at(13), _at(18)),
    ]
