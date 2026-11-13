"""The owner clock, its time basis and the brief's local-date idempotence (bu-dhloi1.21 PR3).

Every test uses an injected instant; nothing reads the wall clock. The clock
auto-switches only on a phone fix (with a notice and an undo), asks once on an
unconfirmed booking, and holds an owner-set zone. The brief keys on the local
date with a watermark, so east/west jumps, DST, undo and missed ticks can never
send the same day's brief twice, and no stale watermark suppresses it forever.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from butlers.perception import flags
from butlers.perception.clock import (
    UNDO_HOLD,
    Booking,
    ClockState,
    ClockValue,
    OwnerClear,
    OwnerConfirm,
    OwnerSet,
    PhoneFix,
    Undo,
    brief_due,
    clock_event,
    clock_parity,
    owner_time_basis,
    reduce_clock,
    resolve_owner_clock,
)
from butlers.perception.model import EventRequest

pytestmark = pytest.mark.unit

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
HOME = "Asia/Singapore"
OWNER = UUID("00000000-0000-4000-8000-000000000001")
REF = UUID("55555555-5555-4555-8555-555555555555")


def _home() -> ClockState:
    return ClockState.initial(HOME)


def _step(state, evidence, now=None):
    """Apply evidence with the reducer's injected instant (the evidence time by default)."""
    return reduce_clock(state, evidence, now=evidence.at if now is None else now)


# -- explicit owner/home/UTC policy -------------------------------------------------


def test_the_initial_clock_is_home_else_utc_fallback_never_a_hardcoded_zone():
    assert _home().current == ClockValue(HOME, "home")
    assert ClockState.initial(None).current == ClockValue("UTC", "utc_fallback")
    assert ClockState.initial("Not/AZone").current == ClockValue("UTC", "utc_fallback")


@pytest.mark.parametrize(
    ("state", "value", "home", "expected"),
    [
        ("known", {"zone": "Europe/Paris", "basis": "phone"}, HOME, ("Europe/Paris", "phone")),
        ("unknown", None, HOME, (HOME, "home")),
        ("unconfigured", None, HOME, (HOME, "home")),
        ("unmeasurable", None, None, ("UTC", "utc_fallback")),
        ("known", {"zone": "Not/AZone", "basis": "phone"}, HOME, (HOME, "home")),
        ("known", {"zone": "Europe/Paris", "basis": "guess"}, None, ("UTC", "utc_fallback")),
    ],
)
def test_resolving_the_owner_clock_from_a_read(state, value, home, expected):
    resolved = resolve_owner_clock(state, value, home)
    assert (resolved.zone, resolved.basis) == expected


# -- phone fixes auto-switch with a notice and an undo ------------------------------


def test_a_phone_fix_in_a_new_zone_switches_with_a_notice_and_undo():
    step = _step(_home(), PhoneFix("Asia/Tokyo", T0))
    assert step.effect == "switch"
    assert step.notice is True
    assert step.state.current == ClockValue("Asia/Tokyo", "phone")
    assert step.state.previous == ClockValue(HOME, "home")


def test_a_phone_fix_in_the_current_zone_changes_nothing():
    step = _step(_home(), PhoneFix(HOME, T0))
    assert step.effect == "none"
    assert step.state == _home()


def test_undo_reverts_and_holds_the_undone_zone_then_releases_it():
    moved = _step(_home(), PhoneFix("Asia/Tokyo", T0)).state
    undone = _step(moved, Undo(T0 + timedelta(minutes=5)))
    assert undone.effect == "revert"
    assert undone.state.current == ClockValue(HOME, "home")
    assert undone.state.previous is None

    # Inside the hold the same phone zone does not switch again ...
    held = _step(undone.state, PhoneFix("Asia/Tokyo", T0 + timedelta(hours=1)))
    assert held.effect == "none"
    # ... but a different zone still does, and the hold is not forever.
    other = _step(undone.state, PhoneFix("Asia/Seoul", T0 + timedelta(hours=1)))
    assert other.effect == "switch"
    later = T0 + timedelta(minutes=5) + UNDO_HOLD + timedelta(seconds=1)
    again = _step(undone.state, PhoneFix("Asia/Tokyo", later))
    assert again.effect == "switch"


def _undone_tokyo(at=T0):
    moved = _step(_home(), PhoneFix("Asia/Tokyo", at)).state
    return _step(moved, Undo(at)).state


def test_an_automatic_switch_elsewhere_keeps_the_undone_zone_held():
    # Border bounce: undo Tokyo, a Seoul fix switches, Tokyo again within 24h is held.
    seoul = _step(_undone_tokyo(), PhoneFix("Asia/Seoul", T0 + timedelta(minutes=10)))
    assert seoul.effect == "switch"
    back = _step(seoul.state, PhoneFix("Asia/Tokyo", T0 + timedelta(minutes=20)))
    assert back.effect == "none"
    assert back.state.current == ClockValue("Asia/Seoul", "phone")


def test_a_border_bounce_gives_one_notice_per_zone_not_one_per_fix():
    state, notices = _undone_tokyo(), 0
    for minute in range(1, 21):
        zone = "Asia/Seoul" if minute % 2 else "Asia/Tokyo"
        step = _step(state, PhoneFix(zone, T0 + timedelta(minutes=minute)))
        state, notices = step.state, notices + step.notice
    assert notices == 1


def test_a_second_undo_keeps_the_first_zones_hold():
    seoul = _step(_undone_tokyo(), PhoneFix("Asia/Seoul", T0 + timedelta(minutes=10))).state
    both = _step(seoul, Undo(T0 + timedelta(minutes=11))).state
    later = T0 + timedelta(hours=1)
    assert _step(both, PhoneFix("Asia/Tokyo", later)).effect == "none"
    assert _step(both, PhoneFix("Asia/Seoul", later)).effect == "none"


def test_a_future_skewed_fix_cannot_bypass_the_hold():
    now = T0 + timedelta(hours=1)
    skewed = PhoneFix("Asia/Tokyo", T0 + UNDO_HOLD + timedelta(days=3))
    assert _step(_undone_tokyo(), skewed, now=now).effect == "none"


def test_the_undo_hold_boundary_is_exactly_undo_hold():
    undone = _undone_tokyo()
    edge = T0 + UNDO_HOLD
    assert _step(undone, PhoneFix("Asia/Tokyo", edge)).effect == "none"
    after = edge + timedelta(seconds=1)
    assert _step(undone, PhoneFix("Asia/Tokyo", after)).effect == "switch"


@pytest.mark.parametrize(
    "owner_act",
    [
        OwnerSet("Europe/Paris", T0 + timedelta(minutes=1)),
        OwnerConfirm("Europe/Paris", T0 + timedelta(minutes=1), "hotel-1"),
    ],
)
def test_only_an_owner_act_clears_the_undo_hold(owner_act):
    acted = _step(_undone_tokyo(), owner_act).state
    cleared = _step(acted, OwnerClear(T0 + timedelta(minutes=2))).state
    assert _step(cleared, PhoneFix("Asia/Tokyo", T0 + timedelta(minutes=3))).effect == "switch"


def test_owner_clear_alone_clears_the_undo_hold():
    # Undo returns to home, so OwnerClear is a no-op move but still the owner's act.
    cleared = _step(_undone_tokyo(), OwnerClear(T0 + timedelta(minutes=1))).state
    assert _step(cleared, PhoneFix("Asia/Tokyo", T0 + timedelta(minutes=2))).effect == "switch"


def test_undo_without_an_auto_switch_does_nothing():
    assert _step(_home(), Undo(T0)).effect == "none"
    owner = _step(_home(), OwnerSet("Europe/Paris", T0)).state
    assert _step(owner, Undo(T0)).effect == "none"


def test_an_owner_set_zone_holds_against_phone_fixes_until_cleared():
    owner = _step(_home(), OwnerSet("Europe/Paris", T0))
    assert owner.effect == "switch" and owner.notice is False
    assert owner.state.current == ClockValue("Europe/Paris", "owner")
    assert _step(owner.state, PhoneFix("Asia/Tokyo", T0)).effect == "none"
    cleared = _step(owner.state, OwnerClear(T0))
    assert cleared.state.current == ClockValue(HOME, "home")
    assert _step(cleared.state, PhoneFix("Asia/Tokyo", T0)).effect == "switch"


# -- booking-only evidence asks once ------------------------------------------------


def test_an_unconfirmed_booking_asks_once_and_never_switches():
    first = _step(_home(), Booking("America/New_York", T0, "flight-1"))
    assert first.effect == "ask"
    assert first.ask_ref == "flight-1"
    assert first.state.current == ClockValue(HOME, "home")
    repeat = _step(first.state, Booking("America/New_York", T0, "flight-1"))
    assert repeat.effect == "none"
    other = _step(first.state, Booking("Europe/Paris", T0, "hotel-2"))
    assert other.effect == "ask"


def test_a_confirmed_booking_switches_and_a_later_phone_fix_wins():
    confirmed = _step(_home(), OwnerConfirm("America/New_York", T0, "flight-1"))
    assert confirmed.effect == "switch" and confirmed.notice is False
    assert confirmed.state.current == ClockValue("America/New_York", "owner_confirmed_booking")
    landed = _step(confirmed.state, PhoneFix("America/Chicago", T0 + timedelta(hours=8)))
    assert landed.state.current == ClockValue("America/Chicago", "phone")


def test_an_invalid_zone_is_ignored():
    for event in (
        PhoneFix("Mars/Base", T0),
        Booking("Mars/Base", T0, "x"),
        OwnerConfirm("Mars/Base", T0, "x"),
        OwnerSet("Mars/Base", T0),
    ):
        step = _step(_home(), event)
        assert (step.effect, step.state) == ("none", _home())


# -- the brief's local date: no duplicate, no forever-suppression -------------------

BRIEF = time(7, 0)


def _sends(zone_at: list[tuple[str, datetime]], at_local: time = BRIEF) -> list[date]:
    """Simulate scheduler ticks; return the local-date keys actually sent."""
    watermark: date | None = None
    sent: list[date] = []
    for zone, now in zone_at:
        key = brief_due(zone=ZoneInfo(zone), now=now, at_local=at_local, watermark=watermark)
        if key is not None:
            sent.append(key)
            watermark = key
    return sent


def _hourly(zone, start, hours):
    return [(zone, start + timedelta(hours=h)) for h in range(hours)]


def test_one_brief_per_local_day_on_hourly_ticks():
    # 08:00 SGT on 1 March through 06:00 SGT on 4 March.
    sent = _sends(_hourly(HOME, datetime(2026, 3, 1, 0, 0, tzinfo=UTC), 71))
    assert sent == [date(2026, 3, 1), date(2026, 3, 2), date(2026, 3, 3)]


def test_before_brief_time_nothing_is_due():
    now = datetime(2026, 3, 1, 22, 0, tzinfo=UTC)  # 06:00 SGT on 2 March
    assert brief_due(zone=ZoneInfo(HOME), now=now, at_local=BRIEF, watermark=None) is None


def test_a_missed_tick_catches_up_later_the_same_day():
    # Ticks stop at 05:00 local and resume at 15:00 local: still sent that day.
    ticks = [
        (HOME, datetime(2026, 3, 1, 21, 0, tzinfo=UTC)),  # 05:00 SGT 2 Mar
        (HOME, datetime(2026, 3, 2, 7, 0, tzinfo=UTC)),  # 15:00 SGT 2 Mar
    ]
    assert _sends(ticks) == [date(2026, 3, 2)]


def test_a_missed_whole_day_resumes_the_next_day():
    ticks = [
        (HOME, datetime(2026, 3, 1, 0, 0, tzinfo=UTC)),  # 08:00 SGT 1 Mar
        (HOME, datetime(2026, 3, 3, 0, 0, tzinfo=UTC)),  # 08:00 SGT 3 Mar
    ]
    assert _sends(ticks) == [date(2026, 3, 1), date(2026, 3, 3)]


def test_flying_west_never_repeats_a_day():
    # Brief sent in Tokyo on 2 March; land in Los Angeles where it is 1 March again.
    tokyo = ("Asia/Tokyo", datetime(2026, 3, 1, 23, 0, tzinfo=UTC))  # 08:00 JST 2 Mar
    la = _hourly("America/Los_Angeles", datetime(2026, 3, 2, 2, 0, tzinfo=UTC), 48)
    sent = _sends([tokyo, *la])
    assert sent == [date(2026, 3, 2), date(2026, 3, 3)]
    assert len(sent) == len(set(sent))


def test_flying_east_sends_the_new_day_once():
    la = ("America/Los_Angeles", datetime(2026, 3, 1, 16, 0, tzinfo=UTC))  # 08:00 PST 1 Mar
    tokyo = _hourly("Asia/Tokyo", datetime(2026, 3, 2, 0, 0, tzinfo=UTC), 30)
    assert _sends([la, *tokyo]) == [date(2026, 3, 1), date(2026, 3, 2), date(2026, 3, 3)]


def test_dst_spring_forward_gap_still_sends():
    # Europe/London skips 01:00-02:00 on 29 March 2026; a 01:30 brief still goes.
    # Ticks at 00:00 GMT, then 02:00 BST: 01:30 never appears on the wall clock.
    ticks = _hourly("Europe/London", datetime(2026, 3, 29, 0, 0, tzinfo=UTC), 6)
    assert _sends(ticks, time(1, 30)) == [date(2026, 3, 29)]


def test_dst_fall_back_repeated_hour_sends_once():
    # Europe/London repeats 01:00-02:00 on 25 October 2026.
    ticks = [
        ("Europe/London", datetime(2026, 10, 24, 23, 0, tzinfo=UTC) + timedelta(minutes=m))
        for m in range(0, 240, 15)
    ]
    assert _sends(ticks, time(1, 0)) == [date(2026, 10, 25)]


def test_a_phone_switch_then_undo_mid_day_never_repeats():
    moved = _step(_home(), PhoneFix("Asia/Tokyo", T0)).state
    undone = _step(moved, Undo(T0)).state
    ticks = [
        (_home().current.zone, datetime(2026, 3, 1, 23, 30, tzinfo=UTC)),  # 07:30 SGT 2 Mar
        (moved.current.zone, datetime(2026, 3, 2, 0, 0, tzinfo=UTC)),  # 09:00 JST 2 Mar
        (undone.current.zone, datetime(2026, 3, 2, 1, 0, tzinfo=UTC)),  # 09:00 SGT 2 Mar
    ]
    assert _sends(ticks) == [date(2026, 3, 2)]


def test_a_booking_ask_does_not_move_the_brief():
    asked = _step(_home(), Booking("America/New_York", T0, "flight-1")).state
    now = datetime(2026, 3, 1, 23, 30, tzinfo=UTC)  # 07:30 SGT, 18:30 EST previous day
    due = brief_due(zone=ZoneInfo(asked.current.zone), now=now, at_local=BRIEF, watermark=None)
    assert due == date(2026, 3, 2)


def test_a_watermark_beyond_any_real_local_date_cannot_suppress_forever():
    now = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)  # 08:00 SGT 2 Mar
    corrupt = date(2026, 3, 9)
    assert brief_due(zone=ZoneInfo(HOME), now=now, at_local=BRIEF, watermark=corrupt) == date(
        2026, 3, 2
    )
    # The largest legitimate watermark (UTC date + 1, the furthest-east zone) still holds.
    edge = date(2026, 3, 3)
    assert brief_due(zone=ZoneInfo(HOME), now=now, at_local=BRIEF, watermark=edge) is None
    # One day past that edge is already not a real local date: the ceiling is exactly +1.
    past = date(2026, 3, 4)
    assert brief_due(zone=ZoneInfo(HOME), now=now, at_local=BRIEF, watermark=past) == date(
        2026, 3, 2
    )


def test_brief_due_requires_an_aware_instant():
    with pytest.raises(ValueError):
        brief_due(zone=ZoneInfo(HOME), now=datetime(2026, 3, 1), at_local=BRIEF, watermark=None)


# -- shadow parity against the legacy basis -----------------------------------------


def test_parity_agrees_when_the_clock_is_home_and_home_is_the_legacy_zone():
    receipt = clock_parity(HOME, ClockValue(HOME, "home"), T0)
    assert receipt.same_zone and receipt.same_local_date
    assert receipt.legacy_zone == HOME and receipt.clock_basis == "home"


def test_parity_records_a_disagreement_with_both_local_dates():
    at = datetime(2026, 3, 1, 20, 0, tzinfo=UTC)  # 04:00 SGT 2 Mar, 15:00 EST 1 Mar
    receipt = clock_parity(HOME, ClockValue("America/New_York", "phone"), at)
    assert not receipt.same_zone and not receipt.same_local_date
    assert receipt.legacy_local_date == date(2026, 3, 2)
    assert receipt.clock_local_date == date(2026, 3, 1)


def test_parity_names_the_legacy_sgt_fallback_when_nothing_is_saved():
    receipt = clock_parity(None, ClockValue(HOME, "home"), T0)
    assert receipt.legacy_zone == "Asia/Singapore"
    assert receipt.legacy_fallback is True


def test_the_time_basis_stays_legacy_while_dark_and_always_carries_a_receipt(monkeypatch):
    assert flags.OWNER_CLOCK_LIVE is False
    zone, receipt = owner_time_basis("Europe/London", ClockValue("Asia/Tokyo", "phone"), T0)
    assert zone == ZoneInfo("Europe/London")
    assert receipt.clock_zone == "Asia/Tokyo" and not receipt.same_zone

    monkeypatch.setattr(flags, "OWNER_CLOCK_LIVE", True)
    zone, _ = owner_time_basis("Europe/London", ClockValue("Asia/Tokyo", "phone"), T0)
    assert zone == ZoneInfo("Asia/Tokyo")


# -- the owner.clock record ---------------------------------------------------------


def test_clock_event_is_a_valid_owner_clock_request():
    body = clock_event(
        ClockValue("Asia/Tokyo", "phone"),
        subject=OWNER,
        evidence_ref=REF,
        generation=3,
        at=T0,
        observed_at=T0 - timedelta(minutes=1),
    )
    payload = EventRequest(body).payload()
    assert payload["kind"] == "owner.clock" and payload["source"] == "context.clock"
    assert payload["typed_value_or_ref"] == {
        "zone": "Asia/Tokyo",
        "basis": "phone",
        "evidence_ref": str(REF),
    }
    assert payload["expected_generation"] == 3


def test_clock_event_keys_are_stable_per_assertion():
    def body(at):
        return clock_event(
            ClockValue("Asia/Tokyo", "phone"),
            subject=OWNER,
            evidence_ref=REF,
            generation=0,
            at=at,
            observed_at=at,
        )

    assert body(T0)["event_key"] == body(T0)["event_key"]
    assert body(T0)["event_key"] != body(T0 + timedelta(seconds=1))["event_key"]
