"""Travel butler endpoints.

Provides read-only endpoints for trips, legs, accommodations, reservations,
documents, and upcoming travel. All data is queried directly from the travel
butler's PostgreSQL database via asyncpg.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import PaginatedResponse, PaginationMeta

# Dynamically load models module from the same directory
_models_path = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("travel_api_models", _models_path)
if _spec is not None and _spec.loader is not None:
    _models = importlib.util.module_from_spec(_spec)
    sys.modules["travel_api_models"] = _models
    _spec.loader.exec_module(_models)

    AccommodationModel = _models.AccommodationModel
    AlertModel = _models.AlertModel
    DocumentModel = _models.DocumentModel
    LegModel = _models.LegModel
    PreTripActionModel = _models.PreTripActionModel
    ReservationModel = _models.ReservationModel
    TimelineEntryModel = _models.TimelineEntryModel
    TripModel = _models.TripModel
    TripSummaryModel = _models.TripSummaryModel
    UpcomingTravelModel = _models.UpcomingTravelModel
    UpcomingTripModel = _models.UpcomingTripModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/travel", tags=["travel"])

BUTLER_DB = "travel"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _pool(db: DatabaseManager):
    """Retrieve the travel butler's connection pool.

    Raises HTTPException 503 if the pool is not available.
    """
    try:
        return db.pool(BUTLER_DB)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Travel butler database is not available",
        )


# ---------------------------------------------------------------------------
# Helper: row → model converters
# ---------------------------------------------------------------------------


def _row_to_trip(r: dict) -> TripModel:
    return TripModel(
        id=str(r["id"]),
        name=r["name"],
        destination=r["destination"],
        start_date=str(r["start_date"]),
        end_date=str(r["end_date"]),
        status=r["status"],
        metadata=dict(r["metadata"]) if r["metadata"] else {},
        created_at=str(r["created_at"]),
        updated_at=str(r["updated_at"]),
    )


def _row_to_leg(r: dict) -> LegModel:
    return LegModel(
        id=str(r["id"]),
        trip_id=str(r["trip_id"]),
        type=r["type"],
        carrier=r["carrier"],
        departure_airport_station=r["departure_airport_station"],
        departure_city=r["departure_city"],
        departure_at=str(r["departure_at"]),
        arrival_airport_station=r["arrival_airport_station"],
        arrival_city=r["arrival_city"],
        arrival_at=str(r["arrival_at"]),
        confirmation_number=r["confirmation_number"],
        pnr=r["pnr"],
        seat=r["seat"],
        metadata=dict(r["metadata"]) if r["metadata"] else {},
        created_at=str(r["created_at"]),
        updated_at=str(r["updated_at"]),
    )


def _row_to_accommodation(r: dict) -> AccommodationModel:
    return AccommodationModel(
        id=str(r["id"]),
        trip_id=str(r["trip_id"]),
        type=r["type"],
        name=r["name"],
        address=r["address"],
        check_in=str(r["check_in"]) if r["check_in"] else None,
        check_out=str(r["check_out"]) if r["check_out"] else None,
        confirmation_number=r["confirmation_number"],
        metadata=dict(r["metadata"]) if r["metadata"] else {},
        created_at=str(r["created_at"]),
        updated_at=str(r["updated_at"]),
    )


def _row_to_reservation(r: dict) -> ReservationModel:
    return ReservationModel(
        id=str(r["id"]),
        trip_id=str(r["trip_id"]),
        type=r["type"],
        provider=r["provider"],
        datetime=str(r["datetime"]) if r["datetime"] else None,
        confirmation_number=r["confirmation_number"],
        metadata=dict(r["metadata"]) if r["metadata"] else {},
        created_at=str(r["created_at"]),
        updated_at=str(r["updated_at"]),
    )


def _row_to_document(r: dict) -> DocumentModel:
    return DocumentModel(
        id=str(r["id"]),
        trip_id=str(r["trip_id"]),
        type=r["type"],
        blob_ref=r["blob_ref"],
        expiry_date=str(r["expiry_date"]) if r["expiry_date"] else None,
        metadata=dict(r["metadata"]) if r["metadata"] else {},
        created_at=str(r["created_at"]),
    )


# ---------------------------------------------------------------------------
# GET /trips — filtered, paginated trip listing
# ---------------------------------------------------------------------------


@router.get("/trips", response_model=PaginatedResponse[TripModel])
async def list_trips(
    status: str | None = Query(
        None, description="Filter by status (planned, active, completed, cancelled)"
    ),
    from_date: str | None = Query(
        None, description="Filter from this start_date (inclusive, YYYY-MM-DD)"
    ),
    to_date: str | None = Query(
        None, description="Filter up to this start_date (inclusive, YYYY-MM-DD)"
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[TripModel]:
    """List trips with optional status and date range filters."""
    pool = _pool(db)

    valid_statuses = {"planned", "active", "completed", "cancelled"}
    if status is not None and status not in valid_statuses:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{status}'. Must be one of: {sorted(valid_statuses)}",
        )

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if status is not None:
        conditions.append(f"status = ${idx}")
        args.append(status)
        idx += 1

    if from_date is not None:
        conditions.append(f"start_date >= ${idx}")
        args.append(date.fromisoformat(from_date))
        idx += 1

    if to_date is not None:
        conditions.append(f"start_date <= ${idx}")
        args.append(date.fromisoformat(to_date))
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(f"SELECT count(*) FROM travel.trips{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, name, destination, start_date, end_date, status, metadata,"
        f" created_at, updated_at"
        f" FROM travel.trips{where}"
        f" ORDER BY start_date DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [_row_to_trip(dict(r)) for r in rows]

    return PaginatedResponse[TripModel](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /trips/{trip_id} — full trip summary with nested entities
# ---------------------------------------------------------------------------


@router.get("/trips/{trip_id}", response_model=TripSummaryModel)
async def get_trip_summary(
    trip_id: str,
    include_documents: bool = Query(True, description="Include document pointers"),
    include_timeline: bool = Query(True, description="Include chronological timeline"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> TripSummaryModel:
    """Return full trip summary with all linked entities."""
    pool = _pool(db)

    trip_row = await pool.fetchrow(
        "SELECT id, name, destination, start_date, end_date, status, metadata,"
        " created_at, updated_at"
        " FROM travel.trips WHERE id = $1::uuid",
        trip_id,
    )
    if trip_row is None:
        raise HTTPException(status_code=404, detail=f"Trip not found: {trip_id}")

    trip = _row_to_trip(dict(trip_row))

    legs_rows = await pool.fetch(
        "SELECT id, trip_id, type, carrier, departure_airport_station, departure_city,"
        " departure_at, arrival_airport_station, arrival_city, arrival_at,"
        " confirmation_number, pnr, seat, metadata, created_at, updated_at"
        " FROM travel.legs WHERE trip_id = $1::uuid ORDER BY departure_at ASC",
        trip_id,
    )
    legs = [_row_to_leg(dict(r)) for r in legs_rows]

    acc_rows = await pool.fetch(
        "SELECT id, trip_id, type, name, address, check_in, check_out,"
        " confirmation_number, metadata, created_at, updated_at"
        " FROM travel.accommodations WHERE trip_id = $1::uuid ORDER BY check_in ASC",
        trip_id,
    )
    accommodations = [_row_to_accommodation(dict(r)) for r in acc_rows]

    res_rows = await pool.fetch(
        "SELECT id, trip_id, type, provider, datetime, confirmation_number,"
        " metadata, created_at, updated_at"
        " FROM travel.reservations WHERE trip_id = $1::uuid ORDER BY datetime ASC",
        trip_id,
    )
    reservations = [_row_to_reservation(dict(r)) for r in res_rows]

    documents: list[DocumentModel] = []
    if include_documents:
        doc_rows = await pool.fetch(
            "SELECT id, trip_id, type, blob_ref, expiry_date, metadata, created_at"
            " FROM travel.documents WHERE trip_id = $1::uuid ORDER BY created_at ASC",
            trip_id,
        )
        documents = [_row_to_document(dict(r)) for r in doc_rows]

    # Build timeline
    timeline: list[TimelineEntryModel] = []
    if include_timeline:
        entries: list[dict] = []

        for leg in legs:
            entries.append(
                {
                    "entity_type": "leg",
                    "entity_id": leg.id,
                    "sort_key": leg.departure_at,
                    "summary": _leg_summary(leg),
                }
            )
        for acc in accommodations:
            entries.append(
                {
                    "entity_type": "accommodation",
                    "entity_id": acc.id,
                    "sort_key": acc.check_in,
                    "summary": _accommodation_summary(acc),
                }
            )
        for res in reservations:
            entries.append(
                {
                    "entity_type": "reservation",
                    "entity_id": res.id,
                    "sort_key": res.datetime,
                    "summary": _reservation_summary(res),
                }
            )

        def _sort_key(e: dict) -> tuple[int, str, str]:
            sk = e.get("sort_key")
            if sk is not None:
                return (0, str(sk), e["entity_id"])
            return (1, "", e["entity_id"])

        entries.sort(key=_sort_key)
        timeline = [TimelineEntryModel(**e) for e in entries]

    # Compute alerts
    alerts = _compute_alerts(legs, documents)

    return TripSummaryModel(
        trip=trip,
        legs=legs,
        accommodations=accommodations,
        reservations=reservations,
        documents=documents,
        timeline=timeline,
        alerts=alerts,
    )


def _leg_summary(leg: LegModel) -> str:
    parts = []
    if leg.type:
        parts.append(leg.type.capitalize())
    dep = leg.departure_city or leg.departure_airport_station
    arr = leg.arrival_city or leg.arrival_airport_station
    if dep:
        parts.append(dep)
    if arr:
        parts.append(f"→ {arr}")
    if leg.carrier:
        parts.append(f"({leg.carrier})")
    return " ".join(parts) if parts else "Transport leg"


def _accommodation_summary(acc: AccommodationModel) -> str:
    parts = []
    if acc.type:
        parts.append(acc.type.capitalize())
    if acc.name:
        parts.append(acc.name)
    return " ".join(parts) if parts else "Accommodation"


def _reservation_summary(res: ReservationModel) -> str:
    parts = []
    if res.type:
        parts.append(res.type.replace("_", " ").capitalize())
    if res.provider:
        parts.append(f"— {res.provider}")
    return " ".join(parts) if parts else "Reservation"


def _compute_alerts(legs: list[LegModel], documents: list[DocumentModel]) -> list[AlertModel]:
    """Compute alert items for pre-trip action requirements."""
    alerts: list[AlertModel] = []
    doc_types = {d.type for d in documents}

    flight_legs = [leg for leg in legs if leg.type == "flight"]
    if flight_legs and "boarding_pass" not in doc_types:
        alerts.append(
            AlertModel(
                type="missing_boarding_pass",
                message="No boarding pass attached — upload or link boarding pass before departure",
                severity="high",
            )
        )

    unseated_legs = [leg for leg in flight_legs if not leg.seat]
    if unseated_legs:
        alerts.append(
            AlertModel(
                type="unassigned_seat",
                message=(
                    f"{len(unseated_legs)} flight leg(s) have no seat assigned — "
                    "consider selecting seats"
                ),
                severity="low",
            )
        )

    now = datetime.now(UTC)
    checkin_window_legs = []
    for leg in flight_legs:
        if leg.departure_at:
            try:
                dep_dt = datetime.fromisoformat(leg.departure_at)
                if dep_dt.tzinfo is None:
                    dep_dt = dep_dt.replace(tzinfo=UTC)
                time_to_dep = dep_dt - now
                if timedelta(0) <= time_to_dep <= timedelta(hours=24):
                    checkin_window_legs.append(leg)
            except (ValueError, TypeError):
                pass

    if checkin_window_legs:
        alerts.append(
            AlertModel(
                type="check_in_pending",
                message=(
                    f"{len(checkin_window_legs)} flight(s) within check-in window — "
                    "online check-in may be available"
                ),
                severity="medium",
            )
        )

    return alerts


# ---------------------------------------------------------------------------
# GET /trips/{trip_id}/legs — legs for a specific trip
# ---------------------------------------------------------------------------


@router.get("/trips/{trip_id}/legs", response_model=list[LegModel])
async def list_trip_legs(
    trip_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[LegModel]:
    """List all transport legs for a specific trip."""
    pool = _pool(db)

    # Verify trip exists
    trip_exists = await pool.fetchval(
        "SELECT 1 FROM travel.trips WHERE id = $1::uuid",
        trip_id,
    )
    if trip_exists is None:
        raise HTTPException(status_code=404, detail=f"Trip not found: {trip_id}")

    rows = await pool.fetch(
        "SELECT id, trip_id, type, carrier, departure_airport_station, departure_city,"
        " departure_at, arrival_airport_station, arrival_city, arrival_at,"
        " confirmation_number, pnr, seat, metadata, created_at, updated_at"
        " FROM travel.legs WHERE trip_id = $1::uuid ORDER BY departure_at ASC",
        trip_id,
    )
    return [_row_to_leg(dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# GET /trips/{trip_id}/accommodations — accommodations for a trip
# ---------------------------------------------------------------------------


@router.get("/trips/{trip_id}/accommodations", response_model=list[AccommodationModel])
async def list_trip_accommodations(
    trip_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[AccommodationModel]:
    """List all accommodations for a specific trip."""
    pool = _pool(db)

    trip_exists = await pool.fetchval(
        "SELECT 1 FROM travel.trips WHERE id = $1::uuid",
        trip_id,
    )
    if trip_exists is None:
        raise HTTPException(status_code=404, detail=f"Trip not found: {trip_id}")

    rows = await pool.fetch(
        "SELECT id, trip_id, type, name, address, check_in, check_out,"
        " confirmation_number, metadata, created_at, updated_at"
        " FROM travel.accommodations WHERE trip_id = $1::uuid ORDER BY check_in ASC",
        trip_id,
    )
    return [_row_to_accommodation(dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# GET /trips/{trip_id}/reservations — reservations for a trip
# ---------------------------------------------------------------------------


@router.get("/trips/{trip_id}/reservations", response_model=list[ReservationModel])
async def list_trip_reservations(
    trip_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[ReservationModel]:
    """List all reservations for a specific trip."""
    pool = _pool(db)

    trip_exists = await pool.fetchval(
        "SELECT 1 FROM travel.trips WHERE id = $1::uuid",
        trip_id,
    )
    if trip_exists is None:
        raise HTTPException(status_code=404, detail=f"Trip not found: {trip_id}")

    rows = await pool.fetch(
        "SELECT id, trip_id, type, provider, datetime, confirmation_number,"
        " metadata, created_at, updated_at"
        " FROM travel.reservations WHERE trip_id = $1::uuid ORDER BY datetime ASC",
        trip_id,
    )
    return [_row_to_reservation(dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# GET /trips/{trip_id}/documents — documents for a trip
# ---------------------------------------------------------------------------


@router.get("/trips/{trip_id}/documents", response_model=list[DocumentModel])
async def list_trip_documents(
    trip_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[DocumentModel]:
    """List all documents attached to a specific trip."""
    pool = _pool(db)

    trip_exists = await pool.fetchval(
        "SELECT 1 FROM travel.trips WHERE id = $1::uuid",
        trip_id,
    )
    if trip_exists is None:
        raise HTTPException(status_code=404, detail=f"Trip not found: {trip_id}")

    rows = await pool.fetch(
        "SELECT id, trip_id, type, blob_ref, expiry_date, metadata, created_at"
        " FROM travel.documents WHERE trip_id = $1::uuid ORDER BY created_at ASC",
        trip_id,
    )
    return [_row_to_document(dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# GET /upcoming — upcoming travel with pre-trip actions
# ---------------------------------------------------------------------------


@router.get("/upcoming", response_model=UpcomingTravelModel)
async def get_upcoming_travel(
    within_days: int = Query(14, ge=1, le=365, description="Look-ahead window in days"),
    include_pretrip_actions: bool = Query(True, description="Include pre-trip action items"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> UpcomingTravelModel:
    """List upcoming trips with urgency-ranked pre-trip action items."""
    pool = _pool(db)

    today = datetime.now(UTC).date()
    window_end = today + timedelta(days=within_days)

    rows = await pool.fetch(
        "SELECT id, name, destination, start_date, end_date, status, metadata,"
        " created_at, updated_at"
        " FROM travel.trips"
        " WHERE status IN ('planned', 'active')"
        "   AND start_date >= $1"
        "   AND start_date <= $2"
        " ORDER BY start_date ASC",
        today,
        window_end,
    )

    upcoming_trips: list[UpcomingTripModel] = []
    all_actions: list[dict] = []

    for row in rows:
        trip = _row_to_trip(dict(row))
        trip_id = trip.id

        legs_rows = await pool.fetch(
            "SELECT id, trip_id, type, carrier, departure_airport_station, departure_city,"
            " departure_at, arrival_airport_station, arrival_city, arrival_at,"
            " confirmation_number, pnr, seat, metadata, created_at, updated_at"
            " FROM travel.legs WHERE trip_id = $1::uuid ORDER BY departure_at ASC",
            trip_id,
        )
        legs = [_row_to_leg(dict(r)) for r in legs_rows]

        acc_rows = await pool.fetch(
            "SELECT id, trip_id, type, name, address, check_in, check_out,"
            " confirmation_number, metadata, created_at, updated_at"
            " FROM travel.accommodations WHERE trip_id = $1::uuid ORDER BY check_in ASC",
            trip_id,
        )
        accommodations = [_row_to_accommodation(dict(r)) for r in acc_rows]

        # Calculate days until departure
        days_until: int | None = None
        start_date_val = trip.start_date
        if start_date_val:
            try:
                start_d = date.fromisoformat(start_date_val)
                days_until = (start_d - today).days
            except (ValueError, TypeError):
                pass

        upcoming_trips.append(
            UpcomingTripModel(
                trip=trip,
                legs=legs,
                accommodations=accommodations,
                days_until_departure=days_until,
            )
        )

        if include_pretrip_actions:
            doc_rows = await pool.fetch(
                "SELECT id, trip_id, type, blob_ref, expiry_date, metadata, created_at"
                " FROM travel.documents WHERE trip_id = $1::uuid",
                trip_id,
            )
            documents = [_row_to_document(dict(r)) for r in doc_rows]

            trip_alerts = _compute_alerts(legs, documents)
            for alert in trip_alerts:
                all_actions.append(
                    {
                        "trip_id": trip_id,
                        "trip_name": trip.name,
                        "type": alert.type,
                        "message": alert.message,
                        "severity": alert.severity,
                    }
                )

    # Urgency-rank: high=1, medium=2, low=3
    _severity_rank = {"high": 1, "medium": 2, "low": 3}

    def _action_sort_key(a: dict) -> tuple[int, str]:
        return (_severity_rank.get(a.get("severity", "low"), 99), a.get("trip_id", ""))

    all_actions.sort(key=_action_sort_key)

    actions = [
        PreTripActionModel(
            trip_id=a["trip_id"],
            trip_name=a["trip_name"],
            type=a["type"],
            message=a["message"],
            severity=a["severity"],
            urgency_rank=i + 1,
        )
        for i, a in enumerate(all_actions)
    ]

    return UpcomingTravelModel(
        upcoming_trips=upcoming_trips,
        actions=actions,
        window_start=today.isoformat(),
        window_end=window_end.isoformat(),
    )
