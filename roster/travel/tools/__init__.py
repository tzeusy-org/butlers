"""Travel butler tools — trips, bookings, documents, and itinerary management.

Re-exports all public symbols from the travel tool sub-modules so that
``from butlers.tools.travel import X`` works as a stable public API.

Sub-modules implemented by parallel branches are imported with try/except
guards so that this package remains importable during staged roll-out. Once
all branches are merged the guards can be removed.
"""

from __future__ import annotations

# --- Bookings and itinerary mutations: implemented on this branch ---
from .bookings import (
    record_booking,
    update_itinerary,
)
from .documents import (
    add_document,
)

try:
    from .trips import (  # type: ignore[attr-defined]
        list_trips,
        trip_summary,
        upcoming_travel,
    )
except (ImportError, AttributeError):
    list_trips = None  # type: ignore[assignment]
    trip_summary = None  # type: ignore[assignment]
    upcoming_travel = None  # type: ignore[assignment]

__all__ = [
    # bookings (butlers-9a3l.6)
    "record_booking",
    "update_itinerary",
    # documents (butlers-9a3l.6)
    "add_document",
    # trips (butlers-9a3l.5)
    "list_trips",
    "trip_summary",
    "upcoming_travel",
]
