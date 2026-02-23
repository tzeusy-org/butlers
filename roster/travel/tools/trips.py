"""Travel butler trip query tools — list_trips, trip_summary, upcoming_travel."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg

from butlers.tools.travel._helpers import _build_timeline, _row_to_dict

logger = logging.getLogger(__name__)

VALID_TRIP_STATUSES = {"planned", "active", "completed", "cancelled"}

# Actions checked for upcoming_travel pre-trip action detection.
# Each entry: (action_key, description, severity)
_PRETRIP_ACTION_CHECKS = [
    ("missing_boarding_pass", "No boarding pass attached for this trip", "high"),
    ("check_in_pending", "Online check-in may be available (within 24h of departure)", "medium"),
    ("unassigned_seat", "One or more flight legs have no seat assigned", "low"),
]


async def list_trips(
    pool: asyncpg.Pool,
    status: str | None = None,
    from_date: date | str | None = None,
    to_date: date | str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Query trip containers by lifecycle status and date windows.

    Parameters
    ----------
    pool:
        Database connection pool (schema must include ``travel``).
    status:
        Filter by trip status. One of ``planned``, ``active``, ``completed``,
        ``cancelled``. If *None*, all statuses are returned.
    from_date:
        Inclusive lower bound on ``start_date`` (ISO-8601 string or ``date``).
    to_date:
        Inclusive upper bound on ``start_date`` (ISO-8601 string or ``date``).
    limit:
        Maximum number of rows to return (default 20).
    offset:
        Row offset for pagination (default 0).

    Returns
    -------
    dict
        A ``ListTripsResult``-shaped dict::

            {
                "items": [...],
                "total": 5,
                "limit": 20,
                "offset": 0,
            }

    Raises
    ------
    ValueError
        If ``status`` is not a valid trip status.
    """
    if status is not None and status not in VALID_TRIP_STATUSES:
        raise ValueError(
            f"Unsupported status value: {status!r}. "
            f"Must be one of: {', '.join(sorted(VALID_TRIP_STATUSES))}"
        )

    # Normalise dates
    if isinstance(from_date, str):
        from_date = date.fromisoformat(from_date)
    if isinstance(to_date, str):
        to_date = date.fromisoformat(to_date)

    conditions: list[str] = []
    params: list[Any] = []
    idx = 1

    if status is not None:
        conditions.append(f"status = ${idx}")
        params.append(status)
        idx += 1

    if from_date is not None:
        conditions.append(f"start_date >= ${idx}")
        params.append(from_date)
        idx += 1

    if to_date is not None:
        conditions.append(f"start_date <= ${idx}")
        params.append(to_date)
        idx += 1

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # Total count
    count_row = await pool.fetchrow(
        f"SELECT COUNT(*) AS total FROM travel.trips {where_clause}",
        *params,
    )
    total: int = count_row["total"] if count_row else 0

    # Paginated results — default sort: start_date DESC
    rows = await pool.fetch(
        f"""
        SELECT *
        FROM travel.trips
        {where_clause}
        ORDER BY start_date DESC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *params,
        limit,
        offset,
    )

    return {
        "items": [_row_to_dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def trip_summary(
    pool: asyncpg.Pool,
    trip_id: str,
    include_documents: bool = True,
    include_timeline: bool = True,
) -> dict[str, Any]:
    """Return full normalized trip with all linked entities.

    Parameters
    ----------
    pool:
        Database connection pool (schema must include ``travel``).
    trip_id:
        UUID string of the trip to summarize.
    include_documents:
        If *True*, include document pointers in the result.
    include_timeline:
        If *True*, build and include a chronological timeline.

    Returns
    -------
    dict
        A ``TripSummaryResult``-shaped dict::

            {
                "trip": {...},
                "legs": [...],
                "accommodations": [...],
                "reservations": [...],
                "documents": [...],   # empty list if include_documents=False
                "timeline": [...],    # empty list if include_timeline=False
                "alerts": [...],
            }

    Raises
    ------
    ValueError
        If no trip is found for the given ``trip_id``.
    """
    trip_row = await pool.fetchrow(
        "SELECT * FROM travel.trips WHERE id = $1::uuid",
        trip_id,
    )
    if trip_row is None:
        raise ValueError(f"Trip not found: {trip_id!r}")

    trip = _row_to_dict(trip_row)

    # Fetch all linked entities in parallel-ish fashion
    legs_rows = await pool.fetch(
        "SELECT * FROM travel.legs WHERE trip_id = $1::uuid ORDER BY departure_at ASC",
        trip_id,
    )
    legs = [_row_to_dict(r) for r in legs_rows]

    acc_rows = await pool.fetch(
        "SELECT * FROM travel.accommodations WHERE trip_id = $1::uuid ORDER BY check_in ASC",
        trip_id,
    )
    accommodations = [_row_to_dict(r) for r in acc_rows]

    res_rows = await pool.fetch(
        "SELECT * FROM travel.reservations WHERE trip_id = $1::uuid ORDER BY datetime ASC",
        trip_id,
    )
    reservations = [_row_to_dict(r) for r in res_rows]

    documents: list[dict[str, Any]] = []
    if include_documents:
        doc_rows = await pool.fetch(
            "SELECT * FROM travel.documents WHERE trip_id = $1::uuid ORDER BY created_at ASC",
            trip_id,
        )
        documents = [_row_to_dict(r) for r in doc_rows]

    timeline: list[dict[str, Any]] = []
    if include_timeline:
        timeline = _build_timeline(legs, accommodations, reservations)

    # Generate alerts for pre-trip action items
    alerts = _compute_trip_alerts(trip, legs, accommodations, reservations, documents)

    return {
        "trip": trip,
        "legs": legs,
        "accommodations": accommodations,
        "reservations": reservations,
        "documents": documents,
        "timeline": timeline,
        "alerts": alerts,
    }


def _compute_trip_alerts(
    trip: dict[str, Any],
    legs: list[dict[str, Any]],
    accommodations: list[dict[str, Any]],
    reservations: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compute alert items for unresolved pre-trip action requirements.

    Returns a list of alert dicts, each with ``type``, ``message``, and
    ``severity`` keys.
    """
    alerts: list[dict[str, Any]] = []
    doc_types = {d.get("type") for d in documents}

    # Check for missing boarding passes when trip has flight legs
    flight_legs = [leg for leg in legs if leg.get("type") == "flight"]
    if flight_legs and "boarding_pass" not in doc_types:
        alerts.append(
            {
                "type": "missing_boarding_pass",
                "message": (
                    "No boarding pass attached — upload or link boarding pass before departure"
                ),
                "severity": "high",
            }
        )

    # Check for flight legs without an assigned seat
    unseated_legs = [leg for leg in flight_legs if not leg.get("seat")]
    if unseated_legs:
        alerts.append(
            {
                "type": "unassigned_seat",
                "message": (
                    f"{len(unseated_legs)} flight leg(s) have no seat assigned — "
                    "consider selecting seats"
                ),
                "severity": "low",
            }
        )

    # Check for online check-in window (within 24h of any flight departure)
    now = datetime.now(UTC)
    checkin_window_legs = []
    for leg in flight_legs:
        dep_str = leg.get("departure_at")
        if dep_str:
            try:
                dep_dt = datetime.fromisoformat(dep_str)
                # Ensure timezone-aware comparison
                if dep_dt.tzinfo is None:
                    dep_dt = dep_dt.replace(tzinfo=UTC)
                time_to_dep = dep_dt - now
                if timedelta(0) <= time_to_dep <= timedelta(hours=24):
                    checkin_window_legs.append(leg)
            except (ValueError, TypeError):
                pass

    if checkin_window_legs:
        alerts.append(
            {
                "type": "check_in_pending",
                "message": (
                    f"{len(checkin_window_legs)} flight(s) within check-in window — "
                    "online check-in may be available"
                ),
                "severity": "medium",
            }
        )

    return alerts


async def upcoming_travel(
    pool: asyncpg.Pool,
    within_days: int = 14,
    include_pretrip_actions: bool = True,
) -> dict[str, Any]:
    """Find trips starting within the given window and surface urgency-ranked actions.

    Parameters
    ----------
    pool:
        Database connection pool (schema must include ``travel``).
    within_days:
        Number of days ahead to look for upcoming trips (default 14).
    include_pretrip_actions:
        If *True*, compute per-trip pre-trip action items.

    Returns
    -------
    dict
        An ``UpcomingTravelResult``-shaped dict::

            {
                "upcoming_trips": [
                    {
                        "trip": {...},
                        "legs": [...],
                        "accommodations": [...],
                        "days_until_departure": 3,
                    },
                    ...
                ],
                "actions": [
                    {
                        "trip_id": "...",
                        "trip_name": "...",
                        "type": "missing_boarding_pass",
                        "message": "...",
                        "severity": "high",
                        "urgency_rank": 1,
                    },
                    ...
                ],
                "window_start": "2026-02-23",
                "window_end": "2026-03-09",
            }
    """
    today = datetime.now(UTC).date()
    window_end = today + timedelta(days=within_days)

    # Find planned/active trips with start_date within window
    rows = await pool.fetch(
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

    upcoming_trips: list[dict[str, Any]] = []
    all_actions: list[dict[str, Any]] = []

    for row in rows:
        trip = _row_to_dict(row)
        trip_id = trip["id"]

        legs_rows = await pool.fetch(
            "SELECT * FROM travel.legs WHERE trip_id = $1::uuid ORDER BY departure_at ASC",
            trip_id,
        )
        legs = [_row_to_dict(r) for r in legs_rows]

        acc_rows = await pool.fetch(
            "SELECT * FROM travel.accommodations WHERE trip_id = $1::uuid ORDER BY check_in ASC",
            trip_id,
        )
        accommodations = [_row_to_dict(r) for r in acc_rows]

        # Compute days until first departure
        start_date_val = trip.get("start_date")
        days_until: int | None = None
        if start_date_val:
            try:
                if isinstance(start_date_val, str):
                    start_d = date.fromisoformat(start_date_val)
                elif isinstance(start_date_val, date):
                    start_d = start_date_val
                else:
                    start_d = None
                if start_d is not None:
                    days_until = (start_d - today).days
            except (ValueError, TypeError):
                pass

        upcoming_trips.append(
            {
                "trip": trip,
                "legs": legs,
                "accommodations": accommodations,
                "days_until_departure": days_until,
            }
        )

        if include_pretrip_actions:
            doc_rows = await pool.fetch(
                "SELECT * FROM travel.documents WHERE trip_id = $1::uuid",
                trip_id,
            )
            documents = [_row_to_dict(r) for r in doc_rows]

            res_rows = await pool.fetch(
                "SELECT * FROM travel.reservations WHERE trip_id = $1::uuid",
                trip_id,
            )
            reservations = [_row_to_dict(r) for r in res_rows]

            trip_alerts = _compute_trip_alerts(trip, legs, accommodations, reservations, documents)
            for alert in trip_alerts:
                all_actions.append(
                    {
                        "trip_id": trip_id,
                        "trip_name": trip.get("name", ""),
                        "type": alert["type"],
                        "message": alert["message"],
                        "severity": alert["severity"],
                    }
                )

    # Urgency-rank: high=1, medium=2, low=3, then by days_until_departure
    _severity_rank = {"high": 1, "medium": 2, "low": 3}

    def _action_sort_key(a: dict[str, Any]) -> tuple[int, str]:
        return (_severity_rank.get(a.get("severity", "low"), 99), a.get("trip_id", ""))

    all_actions.sort(key=_action_sort_key)

    # Assign urgency_rank after sorting
    for i, action in enumerate(all_actions):
        action["urgency_rank"] = i + 1

    return {
        "upcoming_trips": upcoming_trips,
        "actions": all_actions,
        "window_start": today.isoformat(),
        "window_end": window_end.isoformat(),
    }
