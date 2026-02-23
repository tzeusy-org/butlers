"""Pydantic models for the travel butler API.

Provides models for trips, legs, accommodations, reservations, documents,
trip summaries, and upcoming travel used by the travel butler's dashboard endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel


class TripModel(BaseModel):
    """A travel trip container."""

    id: str
    name: str
    destination: str
    start_date: str
    end_date: str
    status: str
    metadata: dict = {}
    created_at: str
    updated_at: str


class LegModel(BaseModel):
    """A transport leg (flight, train, bus, ferry) within a trip."""

    id: str
    trip_id: str
    type: str
    carrier: str | None = None
    departure_airport_station: str | None = None
    departure_city: str | None = None
    departure_at: str
    arrival_airport_station: str | None = None
    arrival_city: str | None = None
    arrival_at: str
    confirmation_number: str | None = None
    pnr: str | None = None
    seat: str | None = None
    metadata: dict = {}
    created_at: str
    updated_at: str


class AccommodationModel(BaseModel):
    """An accommodation (hotel, airbnb, hostel) within a trip."""

    id: str
    trip_id: str
    type: str
    name: str | None = None
    address: str | None = None
    check_in: str | None = None
    check_out: str | None = None
    confirmation_number: str | None = None
    metadata: dict = {}
    created_at: str
    updated_at: str


class ReservationModel(BaseModel):
    """A reservation (car rental, restaurant, activity, tour) within a trip."""

    id: str
    trip_id: str
    type: str
    provider: str | None = None
    datetime: str | None = None
    confirmation_number: str | None = None
    metadata: dict = {}
    created_at: str
    updated_at: str


class DocumentModel(BaseModel):
    """A travel document (boarding pass, visa, insurance, receipt) attached to a trip."""

    id: str
    trip_id: str
    type: str
    blob_ref: str | None = None
    expiry_date: str | None = None
    metadata: dict = {}
    created_at: str


class TimelineEntryModel(BaseModel):
    """A single entry in a trip's chronological timeline."""

    entity_type: str
    entity_id: str
    sort_key: str | None = None
    summary: str


class AlertModel(BaseModel):
    """An alert or pre-trip action item for a trip."""

    type: str
    message: str
    severity: str


class TripSummaryModel(BaseModel):
    """Full trip summary with all linked entities and timeline."""

    trip: TripModel
    legs: list[LegModel] = []
    accommodations: list[AccommodationModel] = []
    reservations: list[ReservationModel] = []
    documents: list[DocumentModel] = []
    timeline: list[TimelineEntryModel] = []
    alerts: list[AlertModel] = []


class UpcomingTripModel(BaseModel):
    """An upcoming trip with legs, accommodations, and days until departure."""

    trip: TripModel
    legs: list[LegModel] = []
    accommodations: list[AccommodationModel] = []
    days_until_departure: int | None = None


class PreTripActionModel(BaseModel):
    """A pre-trip action item with urgency ranking across upcoming trips."""

    trip_id: str
    trip_name: str
    type: str
    message: str
    severity: str
    urgency_rank: int


class UpcomingTravelModel(BaseModel):
    """Upcoming travel overview with trips and urgency-ranked pre-trip actions."""

    upcoming_trips: list[UpcomingTripModel] = []
    actions: list[PreTripActionModel] = []
    window_start: str
    window_end: str
