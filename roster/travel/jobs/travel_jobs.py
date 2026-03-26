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

# --------------------------------------------------------------------------
# Insight scan constants
# --------------------------------------------------------------------------

# Pre-trip preparation: days-until-departure thresholds and priorities
_PRETRIP_CRITICAL_DAYS = 1  # priority 92
_PRETRIP_URGENT_DAYS = 3  # priority 78
_PRETRIP_WINDOW_DAYS = 7  # priority 65 (outermost window)

_PRETRIP_PRIORITY_CRITICAL = 92
_PRETRIP_PRIORITY_URGENT = 78
_PRETRIP_PRIORITY_INFO = 65

# Document expiry insight priorities and cooldowns
_DOC_EXPIRY_URGENT_PRIORITY = 85  # within 30 days
_DOC_EXPIRY_WARNING_PRIORITY = 65  # within 60 days
_DOC_EXPIRY_INFO_PRIORITY = 45  # within 90 days

_DOC_EXPIRY_COOLDOWN_URGENT = 3  # days (30-day warnings)
_DOC_EXPIRY_COOLDOWN_WARNING = 7  # days (60-day warnings)
_DOC_EXPIRY_COOLDOWN_INFO = 14  # days (90-day warnings)

# expires_at for document expiry candidates: 14 days from now (re-check periodically)
_DOC_EXPIRY_CANDIDATE_EXPIRES_DAYS = 14

# Medication prep priorities and thresholds
_MEDICATION_URGENT_DAYS = 7  # trips within 7 days → priority 75
_MEDICATION_WINDOW_DAYS = 14  # trips within 14 days → priority 55
_MEDICATION_MIN_TRIP_DAYS = 3  # only generate if trip duration > 3 days

_MEDICATION_PRIORITY_URGENT = 75
_MEDICATION_PRIORITY_INFO = 55

# Document types scanned for insight expiry warnings
_INSIGHT_EXPIRY_DOC_TYPES = {"visa", "insurance"}


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


async def run_insight_scan(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Generate proactive insight candidates for the travel domain.

    Covers three categories:
    1. Pre-trip preparation — trips departing within 7 days (planned status only)
    2. Document expiry — visa/insurance expiring within 90 days
    3. Medication prep — trips >3 days within 14 days (if active meds exist)

    Each candidate is submitted via ``propose_insight_candidate()`` from the
    shared insight broker.  If the broker returns ``{"status": "filtered"}``
    (verbosity=off), no further candidates are submitted and the job exits early.

    Args:
        db_pool: Database connection pool.

    Returns:
        Dictionary with keys: candidates_proposed, candidates_accepted,
        candidates_filtered, candidates_errored, early_exit.
    """
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    logger.info("Running travel insight scan job")

    today = datetime.now(UTC).date()
    now_utc = datetime.now(UTC)
    stats: dict[str, Any] = {
        "candidates_proposed": 0,
        "candidates_accepted": 0,
        "candidates_filtered": 0,
        "candidates_errored": 0,
        "early_exit": False,
    }

    async def _submit(
        *,
        priority: int,
        category: str,
        dedup_key: str,
        message: str,
        expires_at: datetime,
        cooldown_days: int | None = None,
    ) -> bool:
        """Submit one candidate; return False if verbosity=off (early exit signal)."""
        stats["candidates_proposed"] += 1
        result = await propose_insight_candidate(
            db_pool,
            origin_butler="travel",
            priority=priority,
            category=category,
            dedup_key=dedup_key,
            message=message,
            expires_at=expires_at,
            cooldown_days=cooldown_days,
        )
        status = result.get("status", "error")
        if status == "accepted":
            stats["candidates_accepted"] += 1
        elif status == "filtered":
            stats["candidates_filtered"] += 1
            reason = result.get("reason", "")
            if "verbosity is off" in reason:
                return False  # signal early exit
        else:
            stats["candidates_errored"] += 1
            logger.warning(
                "Travel insight scan: propose_insight_candidate error: %s",
                result.get("reason", "unknown"),
            )
        return True  # continue submitting

    # -----------------------------------------------------------------------
    # 1. Pre-trip preparation insights (departing within 7 days)
    # -----------------------------------------------------------------------
    window_end = today + timedelta(days=_PRETRIP_WINDOW_DAYS)
    trip_rows = await db_pool.fetch(
        """
        SELECT id, name, destination, start_date, end_date, status
        FROM travel.trips
        WHERE status = 'planned'
          AND start_date > $1
          AND start_date <= $2
        ORDER BY start_date ASC
        """,
        today,
        window_end,
    )

    for row in trip_rows:
        start_date_val = row["start_date"]
        if isinstance(start_date_val, str):
            start_d = date.fromisoformat(start_date_val)
        elif isinstance(start_date_val, date):
            start_d = start_date_val
        else:
            continue

        days_until = (start_d - today).days
        trip_id = str(row["id"])
        destination = row["destination"] or row["name"]
        trip_name = row["name"]

        if days_until <= _PRETRIP_CRITICAL_DAYS:
            priority = _PRETRIP_PRIORITY_CRITICAL
        elif days_until <= _PRETRIP_URGENT_DAYS:
            priority = _PRETRIP_PRIORITY_URGENT
        else:
            priority = _PRETRIP_PRIORITY_INFO

        dedup_key = f"travel:pre-trip:{trip_id}:{start_d.isoformat()}"
        expires_at = datetime(start_d.year, start_d.month, start_d.day, tzinfo=UTC)
        message = (
            f"Trip to {destination} departs in {days_until} day(s) — "
            "review your pre-trip checklist to ensure you're ready."
        )
        if days_until == 0:
            message = (
                f"Trip to {destination} departs today — "
                "review your pre-trip checklist to ensure you're ready."
            )

        should_continue = await _submit(
            priority=priority,
            category="pre-trip",
            dedup_key=dedup_key,
            message=message,
            expires_at=expires_at,
        )
        if not should_continue:
            logger.info("Travel insight scan: verbosity=off, exiting early after pre-trip check")
            stats["early_exit"] = True
            return stats

    # -----------------------------------------------------------------------
    # 2. Document expiry insights (visa, insurance within 90 days)
    # -----------------------------------------------------------------------
    doc_window_end = today + timedelta(days=_EXPIRY_INFO_DAYS)
    doc_rows = await db_pool.fetch(
        """
        SELECT d.id, d.type, d.expiry_date, t.name AS trip_name
        FROM travel.documents d
        JOIN travel.trips t ON t.id = d.trip_id
        WHERE d.expiry_date IS NOT NULL
          AND d.expiry_date >= $1
          AND d.expiry_date <= $2
          AND d.type = ANY($3::text[])
        ORDER BY d.expiry_date ASC
        """,
        today,
        doc_window_end,
        list(_INSIGHT_EXPIRY_DOC_TYPES),
    )

    for row in doc_rows:
        expiry_val = row["expiry_date"]
        if isinstance(expiry_val, str):
            expiry_d = date.fromisoformat(expiry_val)
        elif isinstance(expiry_val, date):
            expiry_d = expiry_val
        else:
            continue

        days_until_expiry = (expiry_d - today).days
        doc_type = row["type"]
        trip_name = row["trip_name"]

        if days_until_expiry <= _EXPIRY_URGENT_DAYS:
            priority = _DOC_EXPIRY_URGENT_PRIORITY
            cooldown = _DOC_EXPIRY_COOLDOWN_URGENT
        elif days_until_expiry <= _EXPIRY_WARNING_DAYS:
            priority = _DOC_EXPIRY_WARNING_PRIORITY
            cooldown = _DOC_EXPIRY_COOLDOWN_WARNING
        else:
            priority = _DOC_EXPIRY_INFO_PRIORITY
            cooldown = _DOC_EXPIRY_COOLDOWN_INFO

        dedup_key = f"travel:document-expiry:{doc_type}:{expiry_d.isoformat()}"
        expires_at = now_utc + timedelta(days=_DOC_EXPIRY_CANDIDATE_EXPIRES_DAYS)
        doc_label = doc_type.replace("_", " ").title()
        message = (
            f"Your {doc_label} linked to '{trip_name}' expires on "
            f"{expiry_d.isoformat()} ({days_until_expiry} days). "
            "Renew soon to avoid travel disruptions."
        )

        should_continue = await _submit(
            priority=priority,
            category="document-expiry",
            dedup_key=dedup_key,
            message=message,
            expires_at=expires_at,
            cooldown_days=cooldown,
        )
        if not should_continue:
            logger.info(
                "Travel insight scan: verbosity=off, exiting early after document-expiry check"
            )
            stats["early_exit"] = True
            return stats

    # -----------------------------------------------------------------------
    # 3. Medication prep insights (trips >3 days within 14 days, active meds)
    # -----------------------------------------------------------------------
    # Check if there are any active medications tracked via the shared schema.
    # The health butler owns medications — the travel butler checks via the
    # shared.medications view if available, or falls back to a graceful no-op.
    has_active_meds = False
    try:
        med_row = await db_pool.fetchrow(
            """
            SELECT EXISTS (
                SELECT 1 FROM shared.medications
                WHERE status = 'active'
            ) AS has_meds
            """
        )
        if med_row is not None:
            has_active_meds = bool(med_row["has_meds"])
    except Exception:
        # shared.medications may not exist on all deployments — graceful no-op
        logger.debug(
            "Travel insight scan: shared.medications not available, "
            "skipping medication prep insights"
        )

    if has_active_meds:
        med_window_end = today + timedelta(days=_MEDICATION_WINDOW_DAYS)
        med_trip_rows = await db_pool.fetch(
            """
            SELECT id, name, destination, start_date, end_date, status
            FROM travel.trips
            WHERE status = 'planned'
              AND start_date > $1
              AND start_date <= $2
            ORDER BY start_date ASC
            """,
            today,
            med_window_end,
        )

        for row in med_trip_rows:
            start_date_val = row["start_date"]
            end_date_val = row["end_date"]
            if isinstance(start_date_val, str):
                start_d = date.fromisoformat(start_date_val)
            elif isinstance(start_date_val, date):
                start_d = start_date_val
            else:
                continue
            if isinstance(end_date_val, str):
                end_d = date.fromisoformat(end_date_val)
            elif isinstance(end_date_val, date):
                end_d = end_date_val
            else:
                continue

            trip_duration = (end_d - start_d).days
            if trip_duration <= _MEDICATION_MIN_TRIP_DAYS:
                continue

            days_until = (start_d - today).days
            trip_id = str(row["id"])
            destination = row["destination"] or row["name"]
            trip_name = row["name"]

            if days_until <= _MEDICATION_URGENT_DAYS:
                priority = _MEDICATION_PRIORITY_URGENT
            else:
                priority = _MEDICATION_PRIORITY_INFO

            dedup_key = f"travel:medication-prep:{trip_id}"
            expires_at = datetime(start_d.year, start_d.month, start_d.day, tzinfo=UTC)
            message = (
                f"Your trip to {destination} is {trip_duration} days long — "
                "ensure you have enough medication supply before departure."
            )

            should_continue = await _submit(
                priority=priority,
                category="medication-prep",
                dedup_key=dedup_key,
                message=message,
                expires_at=expires_at,
            )
            if not should_continue:
                logger.info(
                    "Travel insight scan: verbosity=off, exiting early after medication-prep check"
                )
                stats["early_exit"] = True
                return stats

    logger.info(
        "Travel insight scan complete: proposed=%d, accepted=%d, "
        "filtered=%d, errored=%d, early_exit=%s",
        stats["candidates_proposed"],
        stats["candidates_accepted"],
        stats["candidates_filtered"],
        stats["candidates_errored"],
        stats["early_exit"],
    )
    return stats
