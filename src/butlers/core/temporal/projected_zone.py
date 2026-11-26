"""The zone the owner will be in at a future instant, from confirmed itinerary (bu-5ii4gj.5).

Pure and deterministic: no model, no clock, no network. The owner clock (RFC 0038 S1) knows only
the present; this answers "which zone is the owner in on that trip day" so a day's load, quiet
hours and cards are read in the right zone.

The answer is one of:

``home_assumed``
    No leg has been reached by that instant, so the owner is taken to be at home.
``confirmed_booking``
    The latest leg reached by that instant is owner-confirmed and its arrival is a place the
    pinned table names (or the owner's home city): that place's zone.
``in_flight``
    The instant is between a leg's departure and arrival; no zone is claimed.
``unconfirmed_booking``
    The latest leg reached is not owner-confirmed. An unconfirmed booking never applies silently.
``unmapped_place``
    The latest confirmed leg arrives somewhere the table does not name. Never guessed.

Only ``home_assumed`` and ``confirmed_booking`` carry a zone; every other basis is unknown
(``zone is None``), and a consumer must not flag anything off-hours for it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from butlers.core.temporal.city_zones import normalise_place, zone_for_place

Basis = Literal[
    "home_assumed", "confirmed_booking", "in_flight", "unconfirmed_booking", "unmapped_place"
]


@dataclass(frozen=True, slots=True)
class TripLeg:
    """One leg of an itinerary, as the Travel butler stores it, plus the owner's confirmation."""

    departure_at: datetime
    arrival_at: datetime
    arrival_airport: str | None
    arrival_city: str | None
    #: Whether the owner confirmed this booking. Travel's rows alone do not say so.
    confirmed: bool


@dataclass(frozen=True, slots=True)
class ProjectedZone:
    """``zone`` is ``None`` when the owner's zone at that instant is unknown."""

    zone: str | None
    basis: Basis

    @property
    def known(self) -> bool:
        return self.zone is not None


def projected_owner_zone(
    instant: datetime,
    *,
    legs: Iterable[TripLeg],
    home_zone: str,
    home_city: str | None = None,
) -> ProjectedZone:
    """The owner's zone at ``instant`` (timezone-aware), walking legs in time order.

    ``home_city`` lets a return leg that arrives home read as the home zone even when the pinned
    table does not name that city.

    Caller contract: pass only the legs of current or future trips (a past trip's last leg would
    otherwise pin the owner to its destination forever), and always pass ``home_city`` when it is
    known, so a trip that ends at home reads as home. Datetimes must be timezone-aware.
    """
    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware")
    ordered = list(legs)
    for leg in ordered:
        if leg.departure_at.tzinfo is None or leg.arrival_at.tzinfo is None:
            raise ValueError("leg times must be timezone-aware")
    reached: TripLeg | None = None
    for leg in ordered:
        if leg.departure_at <= instant < leg.arrival_at:
            return ProjectedZone(None, "in_flight")
        if leg.arrival_at <= instant and (reached is None or leg.arrival_at > reached.arrival_at):
            reached = leg
    if reached is None:
        return ProjectedZone(home_zone, "home_assumed")
    if not reached.confirmed:
        return ProjectedZone(None, "unconfirmed_booking")
    if home_city and normalise_place(reached.arrival_city) == normalise_place(home_city):
        return ProjectedZone(home_zone, "confirmed_booking")
    zone = zone_for_place(reached.arrival_airport, reached.arrival_city)
    if zone is None:
        return ProjectedZone(None, "unmapped_place")
    return ProjectedZone(zone, "confirmed_booking")
