"""Projected owner zone for future days: confirmed itinerary only, unknown otherwise."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from butlers.core.temporal.city_zones import (
    AIRPORT_ZONES,
    CITY_ZONES,
    normalise_place,
    zone_for_place,
)
from butlers.core.temporal.projected_zone import TripLeg, leg_is_confirmed, projected_owner_zone

pytestmark = pytest.mark.unit

HOME = "Asia/Singapore"


def at(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def leg(dep: datetime, arr: datetime, city: str | None, *, code=None, confirmed=True) -> TripLeg:
    return TripLeg(dep, arr, code, city, confirmed)


def zone(instant: datetime, legs, **kw):
    return projected_owner_zone(instant, legs=legs, home_zone=HOME, **kw)


def test_no_trips_is_the_home_zone_assumed():
    result = zone(at(2026, 7, 1, 12), [])
    assert (result.zone, result.basis) == (HOME, "home_assumed")


def test_before_the_first_departure_is_still_home():
    out = leg(at(2026, 7, 10, 1), at(2026, 7, 10, 8), "London", code="LHR")
    assert zone(at(2026, 7, 9, 12), [out]).basis == "home_assumed"


def test_a_confirmed_trip_uses_the_destination_zone_from_arrival():
    out = leg(at(2026, 7, 10, 1), at(2026, 7, 10, 8), "London", code="LHR")
    during = zone(at(2026, 7, 12, 12), [out])
    assert (during.zone, during.basis) == ("Europe/London", "confirmed_booking")
    # Arrival itself is inclusive; one second earlier the owner is still in the air.
    assert zone(at(2026, 7, 10, 8), [out]).zone == "Europe/London"
    assert zone(at(2026, 7, 10, 7, 59, 59), [out]).basis == "in_flight"


def test_the_flight_interval_is_unknown_with_no_zone():
    out = leg(at(2026, 7, 10, 1), at(2026, 7, 10, 8), "London", code="LHR")
    result = zone(at(2026, 7, 10, 4), [out])
    assert result.zone is None and result.basis == "in_flight" and not result.known


def test_a_return_leg_ends_the_trip_at_home_even_when_the_home_city_is_unmapped():
    out = leg(at(2026, 7, 10, 1), at(2026, 7, 10, 8), "London", code="LHR")
    back = leg(at(2026, 7, 20, 9), at(2026, 7, 21, 1), "Pulau Ujong")
    after = zone(at(2026, 7, 22, 12), [out, back], home_city="Pulau Ujong")
    assert (after.zone, after.basis) == (HOME, "confirmed_booking")
    # Without the home city the table cannot name it, and nothing is guessed.
    assert zone(at(2026, 7, 22, 12), [out, back]).basis == "unmapped_place"


def test_multi_leg_trip_follows_each_arrival_in_order_whatever_the_input_order():
    sin_lon = leg(at(2026, 7, 10, 1), at(2026, 7, 10, 8), "London", code="LHR")
    lon_nyc = leg(at(2026, 7, 14, 9), at(2026, 7, 14, 17), "New York", code="JFK")
    nyc_sin = leg(at(2026, 7, 18, 20), at(2026, 7, 19, 22), "Singapore", code="SIN")
    legs = [nyc_sin, sin_lon, lon_nyc]
    assert zone(at(2026, 7, 12, 12), legs).zone == "Europe/London"
    assert zone(at(2026, 7, 16, 12), legs).zone == "America/New_York"
    assert zone(at(2026, 7, 20, 12), legs).zone == HOME


def test_an_unconfirmed_booking_never_applies_silently():
    out = leg(at(2026, 7, 10, 1), at(2026, 7, 10, 8), "London", code="LHR", confirmed=False)
    result = zone(at(2026, 7, 12, 12), [out])
    assert result.zone is None and result.basis == "unconfirmed_booking"


def test_a_later_unconfirmed_leg_makes_its_days_unknown_not_the_earlier_zone():
    out = leg(at(2026, 7, 10, 1), at(2026, 7, 10, 8), "London", code="LHR")
    onward = leg(at(2026, 7, 14, 9), at(2026, 7, 14, 17), "Paris", confirmed=False)
    assert zone(at(2026, 7, 12, 12), [out, onward]).zone == "Europe/London"
    assert zone(at(2026, 7, 15, 12), [out, onward]).basis == "unconfirmed_booking"


def test_an_unnamed_arrival_is_unmapped_not_guessed():
    out = leg(at(2026, 7, 10, 1), at(2026, 7, 10, 8), "Atlantis")
    result = zone(at(2026, 7, 12, 12), [out])
    assert result.zone is None and result.basis == "unmapped_place"


def test_an_unmapped_airport_code_is_not_rescued_by_the_city_name():
    # London, Ontario (YXU) must not become Europe/London.
    out = leg(at(2026, 7, 10, 1), at(2026, 7, 10, 8), "London", code="YXU")
    result = zone(at(2026, 7, 12, 12), [out])
    assert result.zone is None and result.basis == "unmapped_place"
    assert zone_for_place("YXU", "London") is None
    assert zone_for_place("", "London") == "Europe/London"  # no code: the city is consulted


def test_the_leg_reached_is_the_one_that_arrived_last_not_the_one_that_departed_last():
    # A short hop that departs later but lands earlier than the long-haul leg.
    long_haul = leg(at(2026, 7, 10, 1), at(2026, 7, 10, 20), "London")
    short_hop = leg(at(2026, 7, 10, 2), at(2026, 7, 10, 4), "Tokyo")
    result = zone(at(2026, 7, 11, 0), [long_haul, short_hop])
    assert result.zone == "Europe/London"


def test_a_naive_leg_time_is_refused():
    naive = TripLeg(datetime(2026, 7, 10, 1), at(2026, 7, 10, 8), None, "London", True)
    with pytest.raises(ValueError, match="timezone-aware"):
        zone(at(2026, 7, 12), [naive])


def test_a_naive_instant_is_refused():
    with pytest.raises(ValueError):
        projected_owner_zone(datetime(2026, 7, 1), legs=[], home_zone=HOME)


def test_the_projected_zone_carries_dst_through_to_the_local_clock():
    out = leg(at(2026, 3, 1, 1), at(2026, 3, 1, 12), "New York", code="JFK")
    winter = zone(at(2026, 3, 5, 12), [out])  # before the 8 March spring-forward
    summer = zone(at(2026, 3, 20, 12), [out])
    assert winter.zone == summer.zone == "America/New_York"
    assert at(2026, 3, 5, 12).astimezone(ZoneInfo(winter.zone)).utcoffset() == timedelta(hours=-5)
    assert at(2026, 3, 20, 12).astimezone(ZoneInfo(summer.zone)).utcoffset() == timedelta(hours=-4)


def test_a_westward_crossing_can_land_on_the_previous_calendar_day():
    # Depart Tokyo 18:00 JST on the 10th, arrive Los Angeles 11:00 PDT on the 10th.
    flight = leg(at(2026, 7, 10, 9), at(2026, 7, 10, 18), "Los Angeles", code="LAX")
    arrival = at(2026, 7, 10, 18)
    result = zone(arrival, [flight])
    assert result.zone == "America/Los_Angeles"
    local = arrival.astimezone(ZoneInfo(result.zone))
    assert (local.date().isoformat(), local.hour) == ("2026-07-10", 11)
    # 09:00 Singapore time on the 11th is still the previous evening in Los Angeles.
    nine_sgt = datetime(2026, 7, 11, 9, tzinfo=ZoneInfo(HOME))
    assert nine_sgt.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat() == "2026-07-10"


def test_every_hour_of_a_trip_is_either_one_known_zone_or_unknown():
    out = leg(at(2026, 7, 10, 1), at(2026, 7, 10, 8), "London", code="LHR")
    back = leg(at(2026, 7, 20, 9), at(2026, 7, 21, 1), "Singapore", code="SIN")
    seen: set[tuple[str | None, str]] = set()
    t = at(2026, 7, 9)
    while t < at(2026, 7, 23):
        result = zone(t, [out, back])
        if result.zone is not None:
            ZoneInfo(result.zone)  # always a real zone
        seen.add((result.zone, result.basis))
        t += timedelta(hours=1)
    assert seen == {
        (HOME, "home_assumed"),
        (None, "in_flight"),
        ("Europe/London", "confirmed_booking"),
        (HOME, "confirmed_booking"),
    }


class TestPinnedTable:
    def test_every_entry_names_a_real_zone(self):
        for name in {*AIRPORT_ZONES.values(), *CITY_ZONES.values()}:
            ZoneInfo(name)

    def test_keys_are_in_lookup_form(self):
        assert all(len(code) == 3 and code.isupper() for code in AIRPORT_ZONES)
        assert all(key == normalise_place(key) for key in CITY_ZONES)

    def test_airport_wins_over_city_and_names_fold(self):
        assert zone_for_place("lhr", "Nowhere") == "Europe/London"
        assert zone_for_place(None, "  São   Paulo ") == "America/Sao_Paulo"
        assert zone_for_place(None, "New York, NY") is None  # not a pinned spelling: unknown
        assert zone_for_place(None, None) is None


class TestLegIsConfirmed:
    """The named stand-in for owner confirmation (to be replaced by OwnerConfirm evidence)."""

    def test_a_provider_confirmation_number_on_a_live_trip_counts(self):
        assert leg_is_confirmed(confirmation_number="ABC123", trip_status="planned")
        assert leg_is_confirmed(confirmation_number=" X1 ", trip_status="active")

    @pytest.mark.parametrize("number", [None, "", "   "])
    def test_a_parsed_itinerary_with_no_confirmation_number_stays_unknown(self, number):
        assert not leg_is_confirmed(confirmation_number=number, trip_status="planned")

    @pytest.mark.parametrize("status", ["cancelled", "Cancelled", "tentative"])
    def test_a_cancelled_or_tentative_trip_never_counts(self, status):
        assert not leg_is_confirmed(confirmation_number="ABC123", trip_status=status)

    @pytest.mark.parametrize("meta", [{"tentative": True}, {"status": "Tentative"}])
    def test_trip_metadata_can_mark_a_trip_tentative(self, meta):
        assert not leg_is_confirmed(
            confirmation_number="ABC123", trip_status="planned", trip_metadata=meta
        )
