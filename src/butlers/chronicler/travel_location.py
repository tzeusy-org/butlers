"""Location checks for one-way travel intents (bu-2tsgqb.7).

Pure, deterministic helpers used by :func:`butlers.chronicler.reconciliation.reconcile_day`
to weigh OwnTracks location fixes against a calendar travel block (a flight, or
a block written as a route such as ``SIN -> Aomori``). No I/O, no LLM and no
geocoding: the check never resolves an airport or city name to coordinates
(the telemetry-distillation contract forbids external location lookup, and a
bundled gazetteer is an open owner decision, see the clean-owntracks-location-trail
proposal). It only asks whether the owner's own fixes moved across the
scheduled window.

The rule, per travel intent:

- **Origin anchor**: the latest accepted fix in
  ``[start - ANCHOR_LOOKBACK, start + DEPARTURE_SLACK]`` plus the fixes just
  before it that sit within :data:`SAME_PLACE_KM` of it. The slack lets a
  delayed departure still read as "at the origin".
- **Destination anchor**: the earliest accepted fix in
  ``[end - DEPARTURE_SLACK, end + ANCHOR_LOOKBACK]`` plus the fixes just after
  it within :data:`SAME_PLACE_KM`. Both windows are clipped at the intent's
  midpoint so a short trip cannot share a fix between them.
- An anchor needs :data:`MIN_ANCHOR_FIXES` fixes. One stray fix is not a place.
- Accepted fixes exclude anything the shared trail policy flagged as an
  impossible jump (``payload.trail_outlier``), anything with accuracy worse
  than :data:`MAX_FIX_ACCURACY_METERS`, rows without finite coordinates, and
  rows with naive timestamps (their instant is unknown).

Verdicts:

- ``consistent``: the anchors are at least :data:`TRAVEL_DISPLACEMENT_KM`
  apart and the implied speed between them is physically plausible. This is
  only the claim "the owner's location moved this far across the scheduled
  window". It never asserts that the owner took the planned flight.
- ``contradicted``: the anchors are within :data:`SAME_PLACE_KM` of each
  other. The owner was at the same place before and after a one-way trip, so
  the trip did not happen as planned.
- ``unknown``: anything else (missing or thin fixes, a middling distance, an
  implausible speed). Unknown is never narrated as either outcome.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from butlers.chronicler.adapters.owntracks_trail import (
    MAX_PLAUSIBLE_SPEED_KMH,
    OUTLIER_PAYLOAD_KEY,
    haversine_meters,
)

# A trip whose anchors are at least this far apart moved between cities, not
# within one metro area.
TRAVEL_DISPLACEMENT_KM = 300.0

# Two fixes within this distance are the same place for travel purposes. Matches
# the away-from-home radius of the place-cluster projection (bu-2tsgqb.6).
SAME_PLACE_KM = 50.0

# How far outside the scheduled window an anchor fix may be.
ANCHOR_LOOKBACK = timedelta(hours=6)

# How far inside the scheduled window an anchor fix may be (delayed departure,
# early arrival).
DEPARTURE_SLACK = timedelta(hours=1)

MIN_ANCHOR_FIXES = 2

# Fixes with a worse reported accuracy are too coarse to anchor a place.
MAX_FIX_ACCURACY_METERS = 1000.0

# A single travel block shorter or longer than this is not one transit (a
# week-long "Japan trip" block is a stay, not a flight).
MIN_TRAVEL_WINDOW = timedelta(minutes=30)
MAX_TRAVEL_WINDOW = timedelta(hours=24)

# Calendar module title prefix for butler-written events
# (``butlers.modules.calendar.constants.BUTLER_EVENT_TITLE_PREFIX``). Kept as a
# literal: importing the calendar package from the pure reconciliation path
# would pull its whole runtime in.
BUTLER_EVENT_TITLE_PREFIX = "BUTLER:"

PLAN_PRODUCER_BUTLER = "butler"
PLAN_PRODUCER_CALENDAR = "calendar"

LOCATION_CONSISTENT = "consistent"
LOCATION_CONTRADICTED = "contradicted"
LOCATION_UNKNOWN = "unknown"

_FLIGHT_WORD = re.compile(r"\bflights?\b|✈", re.IGNORECASE)
_ROUTE_ARROW = re.compile(r"\S\s*(?:->|=>|→|⟶|➔)\s*\S")


@dataclass(frozen=True)
class Fix:
    """One accepted location fix."""

    at: datetime
    lat: float
    lon: float


@dataclass(frozen=True)
class TravelLocationCheck:
    """Outcome of :func:`check_travel_intent`."""

    verdict: str
    reason: str
    displacement_km: float | None = None
    origin_last_at: datetime | None = None
    destination_first_at: datetime | None = None

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe summary for the day-close bundle."""
        payload: dict[str, Any] = {"verdict": self.verdict, "reason": self.reason}
        if self.displacement_km is not None:
            payload["displacement_km"] = round(self.displacement_km)
        if self.origin_last_at is not None:
            payload["origin_last_fix_at"] = self.origin_last_at.isoformat()
        if self.destination_first_at is not None:
            payload["destination_first_fix_at"] = self.destination_first_at.isoformat()
        return payload


def _text(ep: Mapping[str, Any]) -> str:
    payload = ep.get("payload")
    payload_title = payload.get("title") if isinstance(payload, Mapping) else None
    parts = (ep.get("canonical_title"), ep.get("title"), payload_title)
    return " ".join(part for part in parts if isinstance(part, str))


def plan_producer(intent: Mapping[str, Any]) -> str:
    """Return who wrote a calendar intent: ``butler`` or ``calendar``.

    ``butler`` comes from the calendar module's ``butler_generated`` flag (copied
    into the episode payload by the calendar adapter) or the ``BUTLER:`` title
    prefix (rows projected before the adapter recorded the flag). ``calendar`` means
    only "an event on a synced calendar": it does not assert the owner wrote it.
    """
    payload = intent.get("payload")
    if isinstance(payload, Mapping):
        if payload.get("butler_generated") is True:
            return PLAN_PRODUCER_BUTLER
    for title in (intent.get("canonical_title"), intent.get("title")):
        if isinstance(title, str) and title.lstrip().upper().startswith(BUTLER_EVENT_TITLE_PREFIX):
            return PLAN_PRODUCER_BUTLER
    return PLAN_PRODUCER_CALENDAR


def is_travel_intent(intent: Mapping[str, Any], start: datetime, end: datetime) -> bool:
    """True for a one-way transit block: a flight, or a titled route ``A -> B``."""
    if not MIN_TRAVEL_WINDOW <= end - start <= MAX_TRAVEL_WINDOW:
        return False
    text = _text(intent)
    return bool(_FLIGHT_WORD.search(text) or _ROUTE_ARROW.search(text))


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def accepted_fixes(rows: Sequence[Mapping[str, Any]]) -> list[Fix]:
    """Filter location point-event rows down to fixes fit to anchor a place."""
    fixes: list[Fix] = []
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, Mapping) or payload.get(OUTLIER_PAYLOAD_KEY):
            continue
        accuracy = _finite(payload.get("accuracy"))
        if payload.get("accuracy") is not None and (
            accuracy is None or accuracy > MAX_FIX_ACCURACY_METERS
        ):
            continue
        lat = _finite(payload.get("lat"))
        lon = _finite(payload.get("lon"))
        at = row.get("canonical_occurred_at") or row.get("occurred_at")
        if lat is None or lon is None or not isinstance(at, datetime) or at.tzinfo is None:
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue
        fixes.append(Fix(at=at, lat=lat, lon=lon))
    fixes.sort(key=lambda fix: fix.at)
    return fixes


def _km(a: Fix, b: Fix) -> float:
    return haversine_meters(a.lat, a.lon, b.lat, b.lon) / 1000.0


def _anchor(candidates: list[Fix]) -> list[Fix] | None:
    """The first candidate plus the following ones at the same place, or None."""
    if not candidates:
        return None
    head = candidates[0]
    same_place = [fix for fix in candidates if _km(head, fix) <= SAME_PLACE_KM]
    return same_place if len(same_place) >= MIN_ANCHOR_FIXES else None


def check_travel_intent(
    start: datetime, end: datetime, fixes: Sequence[Fix]
) -> TravelLocationCheck:
    """Judge one travel window against accepted fixes (see the module docstring)."""
    if start.tzinfo is None or end.tzinfo is None:
        return TravelLocationCheck(LOCATION_UNKNOWN, "intent window has no timezone")
    midpoint = start + (end - start) / 2
    origin_until = min(start + DEPARTURE_SLACK, midpoint)
    destination_from = max(end - DEPARTURE_SLACK, midpoint)
    before = [f for f in fixes if start - ANCHOR_LOOKBACK <= f.at <= origin_until]
    after = [f for f in fixes if destination_from < f.at <= end + ANCHOR_LOOKBACK]

    # Latest origin fix first, so the anchor is the last place before leaving.
    origin = _anchor(list(reversed(before)))
    destination = _anchor(after)
    if origin is None or destination is None:
        return TravelLocationCheck(
            LOCATION_UNKNOWN, "not enough reliable location fixes around the window"
        )

    last_origin = origin[0]
    first_destination = destination[0]
    distance_km = _km(last_origin, first_destination)
    common = {
        "displacement_km": distance_km,
        "origin_last_at": last_origin.at,
        "destination_first_at": first_destination.at,
    }
    if distance_km <= SAME_PLACE_KM:
        return TravelLocationCheck(
            LOCATION_CONTRADICTED,
            "location fixes show the same place before and after the scheduled travel",
            **common,
        )
    if distance_km < TRAVEL_DISPLACEMENT_KM:
        return TravelLocationCheck(
            LOCATION_UNKNOWN, "location moved, but not far enough to indicate travel", **common
        )
    hours = (first_destination.at - last_origin.at).total_seconds() / 3600.0
    if hours <= 0 or distance_km / hours > MAX_PLAUSIBLE_SPEED_KMH:
        return TravelLocationCheck(
            LOCATION_UNKNOWN, "location jump is faster than any plausible travel", **common
        )
    return TravelLocationCheck(
        LOCATION_CONSISTENT,
        "location moved a long distance across the scheduled window; "
        "this does not confirm the planned trip itself",
        **common,
    )


__all__ = [
    "LOCATION_CONSISTENT",
    "LOCATION_CONTRADICTED",
    "LOCATION_UNKNOWN",
    "PLAN_PRODUCER_BUTLER",
    "PLAN_PRODUCER_CALENDAR",
    "Fix",
    "TravelLocationCheck",
    "accepted_fixes",
    "check_travel_intent",
    "is_travel_intent",
    "plan_producer",
]
