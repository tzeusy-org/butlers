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
from butlers.tools.travel._helpers import _row_to_dict
from butlers.tools.travel.connections import connection_reason

# Dynamically load models module from the same directory
_models_path = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("travel_api_models", _models_path)
if _spec is not None and _spec.loader is not None:
    _models = importlib.util.module_from_spec(_spec)
    sys.modules["travel_api_models"] = _models
    _spec.loader.exec_module(_models)

    AccommodationModel = _models.AccommodationModel
    AlertModel = _models.AlertModel
    ConnectionModel = _models.ConnectionModel
    DocumentModel = _models.DocumentModel
    ExpiringDocumentModel = _models.ExpiringDocumentModel
    ExpiringDocumentsResponse = _models.ExpiringDocumentsResponse
    LegModel = _models.LegModel
    PreTripActionModel = _models.PreTripActionModel
    ReservationModel = _models.ReservationModel
    TimelineEntryModel = _models.TimelineEntryModel
    TripModel = _models.TripModel
    TripSummaryModel = _models.TripSummaryModel
    TravellerModel = _models.TravellerModel
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
    return TripModel(**_row_to_dict(r))


def _row_to_leg(r: dict) -> LegModel:
    return LegModel(**_row_to_dict(r))


def _row_to_accommodation(r: dict) -> AccommodationModel:
    return AccommodationModel(**_row_to_dict(r))


def _row_to_reservation(r: dict) -> ReservationModel:
    return ReservationModel(**_row_to_dict(r))


def _row_to_document(r: dict) -> DocumentModel:
    return DocumentModel(**_row_to_dict(r))


def _row_to_connection(r: dict) -> ConnectionModel:
    d = _row_to_dict(r)
    for extra in ("id", "trip_id", "created_at", "updated_at"):
        d.pop(extra, None)
    return ConnectionModel(**d)


def _row_to_traveller(r: dict) -> TravellerModel:
    return TravellerModel(**_row_to_dict(r))


def _rows_to_models(rows: list, converter, entity_label: str) -> tuple[list, list[str]]:
    """Convert rows to models, excluding-and-disclosing any that fail to convert.

    Mirrors /upcoming's per-row degraded-mode pattern: a single corrupt row
    (e.g. unparseable metadata) must not fail the whole sub-collection.
    Returns (converted_models, unreadable_row_ids).
    """
    models = []
    unreadable_ids: list[str] = []
    for r in rows:
        raw_id = str(r["id"])
        try:
            models.append(converter(dict(r)))
        except Exception:
            logger.warning(
                "get_trip_summary: excluding unreadable %s %s", entity_label, raw_id, exc_info=True
            )
            unreadable_ids.append(raw_id)
    return models, unreadable_ids


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

    data: list[TripModel] = []
    unreadable_trip_ids: list[str] = []
    for r in rows:
        raw_trip_id = str(r["id"])
        try:
            data.append(_row_to_trip(dict(r)))
        except Exception:
            # A single unreadable trip (e.g. corrupt metadata) must not 500 the
            # whole list; exclude it and disclose the id, mirroring /upcoming's
            # degraded-mode envelope.
            logger.warning("list_trips: excluding unreadable trip %s", raw_trip_id, exc_info=True)
            unreadable_trip_ids.append(raw_trip_id)

    return PaginatedResponse[TripModel](
        data=data,
        meta=PaginationMeta(
            total=total,
            offset=offset,
            limit=limit,
            unreadable_trip_ids=unreadable_trip_ids,
        ),
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

    try:
        trip = _row_to_trip(dict(trip_row))
    except Exception:
        # The top-level trip row is the entire response's subject -- there is
        # no sub-collection to exclude-and-continue over. Fail with an honest,
        # documented error instead of an unhandled 500.
        logger.warning(
            "get_trip_summary: trip %s row is corrupt and unreadable", trip_id, exc_info=True
        )
        raise HTTPException(
            status_code=502,
            detail=f"Trip {trip_id} data is corrupt and could not be read",
        )

    legs_rows = await pool.fetch(
        "SELECT id, trip_id, type, carrier, departure_airport_station, departure_city,"
        " departure_at, arrival_airport_station, arrival_city, arrival_at,"
        " confirmation_number, pnr, seat, metadata, created_at, updated_at"
        " FROM travel.legs WHERE trip_id = $1::uuid ORDER BY departure_at ASC",
        trip_id,
    )
    legs, unreadable_leg_ids = _rows_to_models(legs_rows, _row_to_leg, "leg")

    acc_rows = await pool.fetch(
        "SELECT id, trip_id, type, name, address, check_in, check_out,"
        " confirmation_number, metadata, created_at, updated_at"
        " FROM travel.accommodations WHERE trip_id = $1::uuid ORDER BY check_in ASC",
        trip_id,
    )
    accommodations, unreadable_accommodation_ids = _rows_to_models(
        acc_rows, _row_to_accommodation, "accommodation"
    )

    res_rows = await pool.fetch(
        "SELECT id, trip_id, type, provider, datetime, confirmation_number,"
        " metadata, created_at, updated_at"
        " FROM travel.reservations WHERE trip_id = $1::uuid ORDER BY datetime ASC",
        trip_id,
    )
    reservations, unreadable_reservation_ids = _rows_to_models(
        res_rows, _row_to_reservation, "reservation"
    )

    documents: list[DocumentModel] = []
    unreadable_document_ids: list[str] = []
    if include_documents:
        doc_rows = await pool.fetch(
            "SELECT id, trip_id, type, blob_ref, expiry_date, metadata, created_at"
            " FROM travel.documents WHERE trip_id = $1::uuid ORDER BY created_at ASC",
            trip_id,
        )
        documents, unreadable_document_ids = _rows_to_models(doc_rows, _row_to_document, "document")

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

    party_rows = await pool.fetch(
        "SELECT id, entity_id, display_name FROM travel.travellers "
        "WHERE trip_id = $1::uuid ORDER BY display_name NULLS LAST, id",
        trip_id,
    )
    party, unreadable_party_ids = _rows_to_models(party_rows, _row_to_traveller, "traveller")

    connection_rows = await pool.fetch(
        "SELECT id, inbound_leg_id, outbound_leg_id, verdict, available_minutes,"
        " evidence, computed_at"
        " FROM travel.connections WHERE trip_id = $1::uuid ORDER BY computed_at ASC",
        trip_id,
    )
    connections, unreadable_connection_ids = _rows_to_models(
        connection_rows, _row_to_connection, "connection"
    )

    return TripSummaryModel(
        trip=trip,
        legs=legs,
        accommodations=accommodations,
        reservations=reservations,
        documents=documents,
        timeline=timeline,
        alerts=alerts,
        party=party,
        connections=connections,
        connection_reason=connection_reason(
            trip.metadata,
            has_connections=bool(connections),
            has_unreadable_connections=bool(unreadable_connection_ids),
        ),
        unreadable_leg_ids=unreadable_leg_ids,
        unreadable_accommodation_ids=unreadable_accommodation_ids,
        unreadable_reservation_ids=unreadable_reservation_ids,
        unreadable_document_ids=unreadable_document_ids,
        unreadable_party_ids=unreadable_party_ids,
        unreadable_connection_ids=unreadable_connection_ids,
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

    today = date.today()
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
    unreadable_trip_ids: list[str] = []
    all_actions: list[dict] = []

    for row in rows:
        raw_trip_id = str(row["id"])
        try:
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
        except Exception:
            # A single unreadable trip (e.g. corrupt metadata) must not 500
            # the whole upcoming-travel response. Exclude it and disclose the
            # id (named-list degraded-mode envelope) instead of either.
            logger.warning(
                "get_upcoming_travel: excluding unreadable trip %s", raw_trip_id, exc_info=True
            )
            unreadable_trip_ids.append(raw_trip_id)
            continue

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
        unreadable_trip_ids=unreadable_trip_ids,
    )


# ---------------------------------------------------------------------------
# GET /documents/expiring — cross-trip expiring document aggregation
# ---------------------------------------------------------------------------

_VALID_DAYS = {30, 60, 90, 180, 365}


@router.get("/documents/expiring", response_model=ExpiringDocumentsResponse)
async def get_expiring_documents(
    days: int = Query(
        180,
        description=("Look-ahead window in days. Allowed values: 30, 60, 90, 180, 365."),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ExpiringDocumentsResponse:
    """Return documents expiring within the given look-ahead window, sorted by expiry_date asc.

    Aggregates across all trips. Only documents with a non-null expiry_date that falls on or
    before today + `days` days are returned.
    """
    if days not in _VALID_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid days value '{days}'. Must be one of: {sorted(_VALID_DAYS)}",
        )

    pool = _pool(db)

    today = date.today()
    cutoff = today + timedelta(days=days)

    rows = await pool.fetch(
        "SELECT id, trip_id, type, metadata, expiry_date"
        " FROM travel.documents"
        " WHERE expiry_date IS NOT NULL"
        "   AND expiry_date >= $1"
        "   AND expiry_date <= $2"
        " ORDER BY expiry_date ASC",
        today,
        cutoff,
    )

    documents: list[ExpiringDocumentModel] = []
    for r in rows:
        d = dict(r)
        expiry = d["expiry_date"]
        if isinstance(expiry, date):
            days_until = (expiry - today).days
            expiry_str = expiry.isoformat()
        else:
            # Fallback for string values
            expiry_date_parsed = date.fromisoformat(str(expiry))
            days_until = (expiry_date_parsed - today).days
            expiry_str = str(expiry)

        # Extract a human-readable name from metadata if present
        metadata = dict(d["metadata"]) if d["metadata"] else {}
        name: str | None = metadata.get("name") or metadata.get("title")

        documents.append(
            ExpiringDocumentModel(
                id=str(d["id"]),
                trip_id=str(d["trip_id"]),
                type=d["type"],
                name=name,
                expiry_date=expiry_str,
                days_until_expiry=days_until,
            )
        )

    return ExpiringDocumentsResponse(documents=documents)
