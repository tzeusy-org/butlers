"""Scheduled job handlers for the Travel butler.

Each job handler:
- Takes db_pool: asyncpg.Pool as first parameter
- Returns a dict with a summary of work done
- Uses async with db_pool.acquire() as conn for queries
- Uses the travel schema prefix (travel.trips, travel.documents, etc.)
- Is a no-op (returns early with zeros) when no matching data exists
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Urgency windows for document expiry (days from today)
_EXPIRY_URGENT_DAYS = 30
_EXPIRY_WARNING_DAYS = 60
_EXPIRY_INFO_DAYS = 90

# Document types that warrant expiry alerts (travel-relevant only)
_EXPIRY_RELEVANT_TYPES = {"visa", "insurance", "boarding_pass"}


async def run_upcoming_travel_check(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Check for imminent departures within 7 days and surface pre-trip actions.

    Calls the equivalent of upcoming_travel(within_days=7, include_pretrip_actions=True)
    directly on the database, generating reminders for:
    - Imminent departures and check-in windows
    - Unresolved pre-trip actions: missing boarding pass, online check-in pending,
      unassigned seat

    Args:
        db_pool: Database connection pool.

    Returns:
        Dictionary with keys: trips_found, actions_found, pretrip_actions.
    """
    logger.info("Running upcoming travel check job")

    today = datetime.now(UTC).date()
    window_end = today + timedelta(days=7)

    async with db_pool.acquire() as conn:
        trip_rows = await conn.fetch(
            """
            SELECT *
            FROM travel.trips
            WHERE status IN ('planned', 'active')
              AND start_date >= $1
              AND start_date <= $2
            ORDER BY start_date ASC
            """,
            today,
            window_end,
        )

    if not trip_rows:
        logger.info("Upcoming travel check: no upcoming trips found within 7 days")
        return {
            "trips_found": 0,
            "actions_found": 0,
            "pretrip_actions": [],
        }

    all_actions: list[dict[str, Any]] = []

    for trip_row in trip_rows:
        trip_id = str(trip_row["id"])
        trip_name = trip_row["name"]
        start_date_val = trip_row["start_date"]

        # Compute days until departure
        days_until: int | None = None
        if start_date_val is not None:
            if isinstance(start_date_val, str):
                start_d = date.fromisoformat(start_date_val)
            elif isinstance(start_date_val, date):
                start_d = start_date_val
            else:
                start_d = None
            if start_d is not None:
                days_until = (start_d - today).days

        async with db_pool.acquire() as conn:
            leg_rows = await conn.fetch(
                """
                SELECT * FROM travel.legs
                WHERE trip_id = $1::uuid
                ORDER BY departure_at ASC
                """,
                trip_id,
            )
            doc_rows = await conn.fetch(
                "SELECT type FROM travel.documents WHERE trip_id = $1::uuid",
                trip_id,
            )

        flight_legs = [row for row in leg_rows if row["type"] == "flight"]
        doc_types = {row["type"] for row in doc_rows}

        # Pre-trip action: missing boarding pass for flight trips
        if flight_legs and "boarding_pass" not in doc_types:
            all_actions.append(
                {
                    "trip_id": trip_id,
                    "trip_name": trip_name,
                    "days_until_departure": days_until,
                    "type": "missing_boarding_pass",
                    "message": (
                        "No boarding pass attached — upload or link boarding pass before departure"
                    ),
                    "severity": "high",
                }
            )

        # Pre-trip action: unassigned seat on flight legs
        unseated = [leg for leg in flight_legs if not leg["seat"]]
        if unseated:
            all_actions.append(
                {
                    "trip_id": trip_id,
                    "trip_name": trip_name,
                    "days_until_departure": days_until,
                    "type": "unassigned_seat",
                    "message": (
                        f"{len(unseated)} flight leg(s) have no seat assigned — "
                        "consider selecting seats"
                    ),
                    "severity": "low",
                }
            )

        # Pre-trip action: online check-in window (within 24h of departure)
        now = datetime.now(UTC)
        checkin_legs = []
        for leg in flight_legs:
            dep_val = leg["departure_at"]
            if dep_val is not None:
                try:
                    dep_dt: datetime
                    if isinstance(dep_val, datetime):
                        dep_dt = dep_val
                    else:
                        dep_dt = datetime.fromisoformat(str(dep_val))
                    if dep_dt.tzinfo is None:
                        dep_dt = dep_dt.replace(tzinfo=UTC)
                    time_to_dep = dep_dt - now
                    if timedelta(0) <= time_to_dep <= timedelta(hours=24):
                        checkin_legs.append(leg)
                except (ValueError, TypeError):
                    pass

        if checkin_legs:
            all_actions.append(
                {
                    "trip_id": trip_id,
                    "trip_name": trip_name,
                    "days_until_departure": days_until,
                    "type": "check_in_pending",
                    "message": (
                        f"{len(checkin_legs)} flight(s) within check-in window — "
                        "online check-in may be available"
                    ),
                    "severity": "medium",
                }
            )

    trips_found = len(trip_rows)
    actions_found = len(all_actions)

    logger.info(
        "Upcoming travel check complete: %d trips found, %d pre-trip actions surfaced",
        trips_found,
        actions_found,
    )

    return {
        "trips_found": trips_found,
        "actions_found": actions_found,
        "pretrip_actions": all_actions,
    }


async def run_trip_document_expiry(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Scan travel documents for expiry within 30, 60, and 90-day windows.

    Focuses on visa, insurance, and boarding pass documents. Emits actionable
    reminders with linked trip_id and document type using graduated urgency:
    - 30 days → urgent
    - 60 days → warning
    - 90 days → informational

    Args:
        db_pool: Database connection pool.

    Returns:
        Dictionary with keys: documents_scanned, urgent, warning, informational,
        expiring_documents.
    """
    logger.info("Running trip document expiry check job")

    today = datetime.now(UTC).date()
    window_end = today + timedelta(days=_EXPIRY_INFO_DAYS)

    async with db_pool.acquire() as conn:
        doc_rows = await conn.fetch(
            """
            SELECT d.id, d.trip_id, d.type, d.expiry_date, t.name AS trip_name
            FROM travel.documents d
            JOIN travel.trips t ON t.id = d.trip_id
            WHERE d.expiry_date IS NOT NULL
              AND d.expiry_date <= $1
              AND d.expiry_date >= $2
              AND d.type = ANY($3::text[])
            ORDER BY d.expiry_date ASC
            """,
            window_end,
            today,
            list(_EXPIRY_RELEVANT_TYPES),
        )

    if not doc_rows:
        logger.info("Trip document expiry check: no expiring documents found within 90 days")
        return {
            "documents_scanned": 0,
            "urgent": 0,
            "warning": 0,
            "informational": 0,
            "expiring_documents": [],
        }

    urgent_count = 0
    warning_count = 0
    info_count = 0
    expiring_docs: list[dict[str, Any]] = []

    for row in doc_rows:
        expiry_date_val = row["expiry_date"]
        if isinstance(expiry_date_val, str):
            expiry_d = date.fromisoformat(expiry_date_val)
        elif isinstance(expiry_date_val, date):
            expiry_d = expiry_date_val
        else:
            continue

        days_until_expiry = (expiry_d - today).days
        doc_type = row["type"]
        trip_id = str(row["trip_id"])
        trip_name = row["trip_name"]

        if days_until_expiry <= _EXPIRY_URGENT_DAYS:
            urgency = "urgent"
            urgent_count += 1
        elif days_until_expiry <= _EXPIRY_WARNING_DAYS:
            urgency = "warning"
            warning_count += 1
        else:
            urgency = "informational"
            info_count += 1

        expiring_docs.append(
            {
                "trip_id": trip_id,
                "trip_name": trip_name,
                "document_type": doc_type,
                "expiry_date": expiry_d.isoformat(),
                "days_until_expiry": days_until_expiry,
                "urgency": urgency,
                "message": (
                    f"{doc_type.replace('_', ' ').title()} for '{trip_name}' "
                    f"expires in {days_until_expiry} days ({expiry_d.isoformat()})"
                ),
            }
        )

    documents_scanned = len(doc_rows)

    logger.info(
        "Trip document expiry check complete: %d documents scanned "
        "(urgent=%d, warning=%d, informational=%d)",
        documents_scanned,
        urgent_count,
        warning_count,
        info_count,
    )

    return {
        "documents_scanned": documents_scanned,
        "urgent": urgent_count,
        "warning": warning_count,
        "informational": info_count,
        "expiring_documents": expiring_docs,
    }
