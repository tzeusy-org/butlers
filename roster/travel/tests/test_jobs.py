"""Tests for Travel butler scheduled job handlers."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _today() -> date:
    return date.today()


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Schema setup helpers (matches travel migration exactly)
# ---------------------------------------------------------------------------

CREATE_TRAVEL_SCHEMA = "CREATE SCHEMA IF NOT EXISTS travel"

CREATE_TRIPS_SQL = """
CREATE TABLE IF NOT EXISTS travel.trips (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    destination TEXT NOT NULL,
    start_date  DATE NOT NULL,
    end_date    DATE NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('planned', 'active', 'completed', 'cancelled')),
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_LEGS_SQL = """
CREATE TABLE IF NOT EXISTS travel.legs (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id                   UUID NOT NULL REFERENCES travel.trips(id) ON DELETE CASCADE,
    type                      TEXT NOT NULL CHECK (type IN ('flight', 'train', 'bus', 'ferry')),
    carrier                   TEXT,
    departure_airport_station TEXT,
    departure_city            TEXT,
    departure_at              TIMESTAMPTZ NOT NULL,
    arrival_airport_station   TEXT,
    arrival_city              TEXT,
    arrival_at                TIMESTAMPTZ NOT NULL,
    confirmation_number       TEXT,
    pnr                       TEXT,
    seat                      TEXT,
    metadata                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_DOCUMENTS_SQL = """
CREATE TABLE IF NOT EXISTS travel.documents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id     UUID NOT NULL REFERENCES travel.trips(id) ON DELETE CASCADE,
    type        TEXT NOT NULL
                    CHECK (type IN ('boarding_pass', 'visa', 'insurance', 'receipt')),
    blob_ref    TEXT,
    expiry_date DATE,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_ACCOMMODATIONS_SQL = """
CREATE TABLE IF NOT EXISTS travel.accommodations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id             UUID NOT NULL REFERENCES travel.trips(id) ON DELETE CASCADE,
    type                TEXT NOT NULL CHECK (type IN ('hotel', 'airbnb', 'hostel')),
    name                TEXT,
    address             TEXT,
    check_in            TIMESTAMPTZ,
    check_out           TIMESTAMPTZ,
    confirmation_number TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_RESERVATIONS_SQL = """
CREATE TABLE IF NOT EXISTS travel.reservations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id             UUID NOT NULL REFERENCES travel.trips(id) ON DELETE CASCADE,
    type                TEXT NOT NULL
                            CHECK (type IN ('car_rental', 'restaurant', 'activity', 'tour')),
    provider            TEXT,
    datetime            TIMESTAMPTZ,
    confirmation_number TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


async def _setup_travel_schema(pool) -> None:
    """Create the travel schema and all required tables."""
    await pool.execute(CREATE_TRAVEL_SCHEMA)
    await pool.execute(CREATE_TRIPS_SQL)
    await pool.execute(CREATE_LEGS_SQL)
    await pool.execute(CREATE_DOCUMENTS_SQL)
    await pool.execute(CREATE_ACCOMMODATIONS_SQL)
    await pool.execute(CREATE_RESERVATIONS_SQL)


# ---------------------------------------------------------------------------
# Helper insert functions
# ---------------------------------------------------------------------------


async def _insert_trip(
    pool,
    *,
    name: str = "Test Trip",
    destination: str = "Paris",
    start_date: date | None = None,
    end_date: date | None = None,
    status: str = "planned",
) -> str:
    """Insert a trip and return its UUID string."""
    if start_date is None:
        start_date = _today() + timedelta(days=3)
    if end_date is None:
        end_date = start_date + timedelta(days=7)
    trip_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO travel.trips (id, name, destination, start_date, end_date, status)
        VALUES ($1::uuid, $2, $3, $4, $5, $6)
        """,
        trip_id,
        name,
        destination,
        start_date,
        end_date,
        status,
    )
    return trip_id


async def _insert_leg(
    pool,
    *,
    trip_id: str,
    leg_type: str = "flight",
    departure_at: datetime | None = None,
    arrival_at: datetime | None = None,
    seat: str | None = None,
) -> str:
    """Insert a leg and return its UUID string."""
    if departure_at is None:
        departure_at = _utcnow() + timedelta(days=3)
    if arrival_at is None:
        arrival_at = departure_at + timedelta(hours=8)
    leg_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO travel.legs
            (id, trip_id, type, departure_at, arrival_at, seat)
        VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6)
        """,
        leg_id,
        trip_id,
        leg_type,
        departure_at,
        arrival_at,
        seat,
    )
    return leg_id


async def _insert_document(
    pool,
    *,
    trip_id: str,
    doc_type: str = "boarding_pass",
    expiry_date: date | None = None,
) -> str:
    """Insert a document and return its UUID string."""
    doc_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO travel.documents (id, trip_id, type, expiry_date)
        VALUES ($1::uuid, $2::uuid, $3, $4)
        """,
        doc_id,
        trip_id,
        doc_type,
        expiry_date,
    )
    return doc_id


# ---------------------------------------------------------------------------
# Tests: run_upcoming_travel_check
# ---------------------------------------------------------------------------


async def test_upcoming_travel_check_no_trips(provisioned_postgres_pool):
    """No-op: returns zeros when no upcoming trips exist."""
    from roster.travel.jobs.travel_jobs import run_upcoming_travel_check

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        result = await run_upcoming_travel_check(pool)

        assert result["trips_found"] == 0
        assert result["actions_found"] == 0
        assert result["pretrip_actions"] == []


async def test_upcoming_travel_check_no_trips_in_window(provisioned_postgres_pool):
    """Trips starting beyond 7 days are excluded."""
    from roster.travel.jobs.travel_jobs import run_upcoming_travel_check

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        # Trip starting in 10 days — outside the 7-day window
        await _insert_trip(pool, start_date=_today() + timedelta(days=10))

        result = await run_upcoming_travel_check(pool)

        assert result["trips_found"] == 0
        assert result["actions_found"] == 0


async def test_upcoming_travel_check_excludes_completed(provisioned_postgres_pool):
    """Completed or cancelled trips are excluded."""
    from roster.travel.jobs.travel_jobs import run_upcoming_travel_check

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        await _insert_trip(pool, start_date=_today() + timedelta(days=2), status="completed")
        await _insert_trip(pool, start_date=_today() + timedelta(days=2), status="cancelled")

        result = await run_upcoming_travel_check(pool)

        assert result["trips_found"] == 0


async def test_upcoming_travel_check_trip_found(provisioned_postgres_pool):
    """Planned trip within 7 days is detected."""
    from roster.travel.jobs.travel_jobs import run_upcoming_travel_check

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        await _insert_trip(pool, name="Tokyo Trip", start_date=_today() + timedelta(days=4))

        result = await run_upcoming_travel_check(pool)

        assert result["trips_found"] == 1


async def test_upcoming_travel_check_missing_boarding_pass(provisioned_postgres_pool):
    """Missing boarding pass is surfaced for flight trips."""
    from roster.travel.jobs.travel_jobs import run_upcoming_travel_check

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(
            pool, name="Paris Trip", start_date=_today() + timedelta(days=2)
        )
        # Add a flight leg, but no boarding pass document
        await _insert_leg(pool, trip_id=trip_id, leg_type="flight")

        result = await run_upcoming_travel_check(pool)

        assert result["trips_found"] == 1
        assert result["actions_found"] >= 1
        action_types = [a["type"] for a in result["pretrip_actions"]]
        assert "missing_boarding_pass" in action_types


async def test_upcoming_travel_check_no_missing_boarding_pass_when_attached(
    provisioned_postgres_pool,
):
    """Boarding pass action is not raised when document is already attached."""
    from roster.travel.jobs.travel_jobs import run_upcoming_travel_check

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool, start_date=_today() + timedelta(days=2))
        await _insert_leg(pool, trip_id=trip_id, leg_type="flight")
        # Attach a boarding pass — action should NOT be surfaced
        await _insert_document(pool, trip_id=trip_id, doc_type="boarding_pass")

        result = await run_upcoming_travel_check(pool)

        action_types = [a["type"] for a in result["pretrip_actions"]]
        assert "missing_boarding_pass" not in action_types


async def test_upcoming_travel_check_unassigned_seat(provisioned_postgres_pool):
    """Unassigned seat action is surfaced when flight leg has no seat."""
    from roster.travel.jobs.travel_jobs import run_upcoming_travel_check

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool, start_date=_today() + timedelta(days=3))
        # Flight leg with no seat
        await _insert_leg(pool, trip_id=trip_id, leg_type="flight", seat=None)

        result = await run_upcoming_travel_check(pool)

        action_types = [a["type"] for a in result["pretrip_actions"]]
        assert "unassigned_seat" in action_types


async def test_upcoming_travel_check_no_unassigned_seat_when_assigned(provisioned_postgres_pool):
    """Unassigned seat action is not raised when a seat is assigned."""
    from roster.travel.jobs.travel_jobs import run_upcoming_travel_check

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool, start_date=_today() + timedelta(days=3))
        # Flight leg WITH seat assigned
        await _insert_leg(pool, trip_id=trip_id, leg_type="flight", seat="14A")
        await _insert_document(pool, trip_id=trip_id, doc_type="boarding_pass")

        result = await run_upcoming_travel_check(pool)

        action_types = [a["type"] for a in result["pretrip_actions"]]
        assert "unassigned_seat" not in action_types


async def test_upcoming_travel_check_check_in_pending(provisioned_postgres_pool):
    """Check-in pending action is surfaced for flights departing within 24h."""
    from roster.travel.jobs.travel_jobs import run_upcoming_travel_check

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        # Trip departing today (start_date = today)
        trip_id = await _insert_trip(pool, start_date=_today())
        # Flight departing in 12 hours — within 24h check-in window
        departure_soon = _utcnow() + timedelta(hours=12)
        arrival_soon = departure_soon + timedelta(hours=8)
        await _insert_leg(
            pool,
            trip_id=trip_id,
            leg_type="flight",
            departure_at=departure_soon,
            arrival_at=arrival_soon,
        )

        result = await run_upcoming_travel_check(pool)

        action_types = [a["type"] for a in result["pretrip_actions"]]
        assert "check_in_pending" in action_types


async def test_upcoming_travel_check_no_check_in_for_future_flight(provisioned_postgres_pool):
    """Check-in pending action is NOT raised for flights departing beyond 24h."""
    from roster.travel.jobs.travel_jobs import run_upcoming_travel_check

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool, start_date=_today() + timedelta(days=3))
        # Flight departing in 3 days — outside 24h check-in window
        departure_future = _utcnow() + timedelta(days=3)
        arrival_future = departure_future + timedelta(hours=8)
        await _insert_leg(
            pool,
            trip_id=trip_id,
            leg_type="flight",
            departure_at=departure_future,
            arrival_at=arrival_future,
            seat="12B",
        )
        await _insert_document(pool, trip_id=trip_id, doc_type="boarding_pass")

        result = await run_upcoming_travel_check(pool)

        action_types = [a["type"] for a in result["pretrip_actions"]]
        assert "check_in_pending" not in action_types


async def test_upcoming_travel_check_non_flight_no_boarding_pass_check(provisioned_postgres_pool):
    """Trips with only non-flight legs do not generate a boarding pass action."""
    from roster.travel.jobs.travel_jobs import run_upcoming_travel_check

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool, start_date=_today() + timedelta(days=2))
        # Train leg — no boarding pass required
        await _insert_leg(pool, trip_id=trip_id, leg_type="train")

        result = await run_upcoming_travel_check(pool)

        action_types = [a["type"] for a in result["pretrip_actions"]]
        assert "missing_boarding_pass" not in action_types


async def test_upcoming_travel_check_multiple_trips(provisioned_postgres_pool):
    """Multiple upcoming trips are all scanned."""
    from roster.travel.jobs.travel_jobs import run_upcoming_travel_check

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        for i in range(3):
            trip_id = await _insert_trip(
                pool,
                name=f"Trip {i}",
                start_date=_today() + timedelta(days=i + 1),
            )
            await _insert_leg(pool, trip_id=trip_id, leg_type="flight")

        result = await run_upcoming_travel_check(pool)

        assert result["trips_found"] == 3


async def test_upcoming_travel_check_action_includes_trip_context(provisioned_postgres_pool):
    """Pretrip actions include trip_id, trip_name, and days_until_departure."""
    from roster.travel.jobs.travel_jobs import run_upcoming_travel_check

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(
            pool, name="Barcelona Trip", start_date=_today() + timedelta(days=5)
        )
        await _insert_leg(pool, trip_id=trip_id, leg_type="flight")

        result = await run_upcoming_travel_check(pool)

        assert result["actions_found"] >= 1
        action = result["pretrip_actions"][0]
        assert "trip_id" in action
        assert "trip_name" in action
        assert action["trip_name"] == "Barcelona Trip"
        assert "days_until_departure" in action
        assert action["days_until_departure"] == 5


# ---------------------------------------------------------------------------
# Tests: run_trip_document_expiry
# ---------------------------------------------------------------------------


async def test_trip_document_expiry_no_documents(provisioned_postgres_pool):
    """No-op: returns zeros when no documents exist."""
    from roster.travel.jobs.travel_jobs import run_trip_document_expiry

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        result = await run_trip_document_expiry(pool)

        assert result["documents_scanned"] == 0
        assert result["urgent"] == 0
        assert result["warning"] == 0
        assert result["informational"] == 0
        assert result["expiring_documents"] == []


async def test_trip_document_expiry_no_expiring_within_90_days(provisioned_postgres_pool):
    """Documents expiring beyond 90 days are excluded."""
    from roster.travel.jobs.travel_jobs import run_trip_document_expiry

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool)
        await _insert_document(
            pool,
            trip_id=trip_id,
            doc_type="visa",
            expiry_date=_today() + timedelta(days=120),
        )

        result = await run_trip_document_expiry(pool)

        assert result["documents_scanned"] == 0


async def test_trip_document_expiry_urgent_30_days(provisioned_postgres_pool):
    """Document expiring within 30 days is classified as urgent."""
    from roster.travel.jobs.travel_jobs import run_trip_document_expiry

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool)
        await _insert_document(
            pool,
            trip_id=trip_id,
            doc_type="visa",
            expiry_date=_today() + timedelta(days=20),
        )

        result = await run_trip_document_expiry(pool)

        assert result["documents_scanned"] == 1
        assert result["urgent"] == 1
        assert result["warning"] == 0
        assert result["informational"] == 0
        assert result["expiring_documents"][0]["urgency"] == "urgent"


async def test_trip_document_expiry_warning_60_days(provisioned_postgres_pool):
    """Document expiring within 31-60 days is classified as warning."""
    from roster.travel.jobs.travel_jobs import run_trip_document_expiry

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool)
        await _insert_document(
            pool,
            trip_id=trip_id,
            doc_type="insurance",
            expiry_date=_today() + timedelta(days=45),
        )

        result = await run_trip_document_expiry(pool)

        assert result["documents_scanned"] == 1
        assert result["urgent"] == 0
        assert result["warning"] == 1
        assert result["informational"] == 0
        assert result["expiring_documents"][0]["urgency"] == "warning"


async def test_trip_document_expiry_informational_90_days(provisioned_postgres_pool):
    """Document expiring within 61-90 days is classified as informational."""
    from roster.travel.jobs.travel_jobs import run_trip_document_expiry

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool)
        await _insert_document(
            pool,
            trip_id=trip_id,
            doc_type="visa",
            expiry_date=_today() + timedelta(days=75),
        )

        result = await run_trip_document_expiry(pool)

        assert result["documents_scanned"] == 1
        assert result["urgent"] == 0
        assert result["warning"] == 0
        assert result["informational"] == 1
        assert result["expiring_documents"][0]["urgency"] == "informational"


async def test_trip_document_expiry_graduated_urgency(provisioned_postgres_pool):
    """Multiple documents at different expiry windows are classified correctly."""
    from roster.travel.jobs.travel_jobs import run_trip_document_expiry

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool)
        # Urgent: expires in 15 days
        await _insert_document(
            pool, trip_id=trip_id, doc_type="visa", expiry_date=_today() + timedelta(days=15)
        )
        # Warning: expires in 50 days
        await _insert_document(
            pool,
            trip_id=trip_id,
            doc_type="insurance",
            expiry_date=_today() + timedelta(days=50),
        )
        # Informational: expires in 80 days
        await _insert_document(
            pool, trip_id=trip_id, doc_type="visa", expiry_date=_today() + timedelta(days=80)
        )

        result = await run_trip_document_expiry(pool)

        assert result["documents_scanned"] == 3
        assert result["urgent"] == 1
        assert result["warning"] == 1
        assert result["informational"] == 1


async def test_trip_document_expiry_excludes_receipt_type(provisioned_postgres_pool):
    """Receipt documents are excluded from expiry scans."""
    from roster.travel.jobs.travel_jobs import run_trip_document_expiry

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool)
        # Receipt with expiry date — should be excluded
        await _insert_document(
            pool,
            trip_id=trip_id,
            doc_type="receipt",
            expiry_date=_today() + timedelta(days=10),
        )

        result = await run_trip_document_expiry(pool)

        assert result["documents_scanned"] == 0


async def test_trip_document_expiry_excludes_null_expiry(provisioned_postgres_pool):
    """Documents without an expiry_date are excluded."""
    from roster.travel.jobs.travel_jobs import run_trip_document_expiry

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool)
        # Visa with no expiry date
        await _insert_document(pool, trip_id=trip_id, doc_type="visa", expiry_date=None)

        result = await run_trip_document_expiry(pool)

        assert result["documents_scanned"] == 0


async def test_trip_document_expiry_document_has_linked_trip_context(provisioned_postgres_pool):
    """Expiring document result includes trip_id, trip_name, and document_type."""
    from roster.travel.jobs.travel_jobs import run_trip_document_expiry

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool, name="Visa Trip")
        await _insert_document(
            pool,
            trip_id=trip_id,
            doc_type="visa",
            expiry_date=_today() + timedelta(days=25),
        )

        result = await run_trip_document_expiry(pool)

        assert result["documents_scanned"] == 1
        doc = result["expiring_documents"][0]
        assert doc["trip_id"] == trip_id
        assert doc["trip_name"] == "Visa Trip"
        assert doc["document_type"] == "visa"
        assert "expiry_date" in doc
        assert "days_until_expiry" in doc
        assert "message" in doc


async def test_trip_document_expiry_sorted_by_expiry_date(provisioned_postgres_pool):
    """Documents are returned sorted by expiry_date ascending."""
    from roster.travel.jobs.travel_jobs import run_trip_document_expiry

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool)
        # Insert in reverse order to ensure sorting works
        await _insert_document(
            pool, trip_id=trip_id, doc_type="visa", expiry_date=_today() + timedelta(days=80)
        )
        await _insert_document(
            pool,
            trip_id=trip_id,
            doc_type="insurance",
            expiry_date=_today() + timedelta(days=20),
        )
        await _insert_document(
            pool, trip_id=trip_id, doc_type="visa", expiry_date=_today() + timedelta(days=50)
        )

        result = await run_trip_document_expiry(pool)

        days_list = [d["days_until_expiry"] for d in result["expiring_documents"]]
        assert days_list == sorted(days_list)


async def test_trip_document_expiry_expiring_today_is_urgent(provisioned_postgres_pool):
    """Document expiring today is classified as urgent."""
    from roster.travel.jobs.travel_jobs import run_trip_document_expiry

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool)
        await _insert_document(
            pool,
            trip_id=trip_id,
            doc_type="insurance",
            expiry_date=_today(),
        )

        result = await run_trip_document_expiry(pool)

        assert result["documents_scanned"] == 1
        assert result["urgent"] == 1
        assert result["expiring_documents"][0]["urgency"] == "urgent"


async def test_trip_document_expiry_insurance_included(provisioned_postgres_pool):
    """Insurance documents are included in expiry scans."""
    from roster.travel.jobs.travel_jobs import run_trip_document_expiry

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool)
        await _insert_document(
            pool,
            trip_id=trip_id,
            doc_type="insurance",
            expiry_date=_today() + timedelta(days=15),
        )

        result = await run_trip_document_expiry(pool)

        assert result["documents_scanned"] == 1


async def test_trip_document_expiry_boarding_pass_included(provisioned_postgres_pool):
    """Boarding pass documents are included in expiry scans."""
    from roster.travel.jobs.travel_jobs import run_trip_document_expiry

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)

        trip_id = await _insert_trip(pool)
        await _insert_document(
            pool,
            trip_id=trip_id,
            doc_type="boarding_pass",
            expiry_date=_today() + timedelta(days=5),
        )

        result = await run_trip_document_expiry(pool)

        assert result["documents_scanned"] == 1


# ---------------------------------------------------------------------------
# Insight scan: helpers
# ---------------------------------------------------------------------------


async def _setup_insight_tables(pool) -> None:
    """Create insight_candidates and related tables for insight scan tests."""
    from butlers.tools.switchboard.insight.broker import create_insight_tables

    await create_insight_tables(pool)


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — no data / no-op paths
# ---------------------------------------------------------------------------


async def test_insight_scan_no_trips_no_documents(provisioned_postgres_pool):
    """No-op: returns zeros when no trips or documents exist."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        result = await run_insight_scan(pool)

        assert result["candidates_proposed"] == 0
        assert result["candidates_accepted"] == 0
        assert result["candidates_filtered"] == 0
        assert result["candidates_errored"] == 0
        assert result["early_exit"] is False


async def test_insight_scan_excludes_completed_trips(provisioned_postgres_pool):
    """Completed and cancelled trips do not generate pre-trip insights."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        await _insert_trip(pool, start_date=_today() + timedelta(days=3), status="completed")
        await _insert_trip(pool, start_date=_today() + timedelta(days=3), status="cancelled")

        result = await run_insight_scan(pool)

        assert result["candidates_proposed"] == 0


async def test_insight_scan_excludes_trips_beyond_window(provisioned_postgres_pool):
    """Trips departing beyond 7 days are excluded from pre-trip insights."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        await _insert_trip(pool, start_date=_today() + timedelta(days=10))

        result = await run_insight_scan(pool)

        assert result["candidates_proposed"] == 0


async def test_insight_scan_excludes_past_trips(provisioned_postgres_pool):
    """Trips with departure dates in the past are excluded."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        await _insert_trip(pool, start_date=_today() - timedelta(days=1))

        result = await run_insight_scan(pool)

        assert result["candidates_proposed"] == 0


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — pre-trip preparation priorities
# ---------------------------------------------------------------------------


async def test_insight_scan_pretrip_priority_critical_1_day(provisioned_postgres_pool):
    """Trip departing in 1 day gets priority 92 (time-critical)."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        await _insert_trip(
            pool, name="Tokyo", destination="Tokyo", start_date=_today() + timedelta(days=1)
        )

        result = await run_insight_scan(pool)

        assert result["candidates_proposed"] >= 1
        assert result["candidates_accepted"] >= 1

        rows = await pool.fetch(
            "SELECT priority, dedup_key, message FROM insight_candidates"
            " WHERE origin_butler = 'travel'"
        )
        pretrip_rows = [r for r in rows if "pre-trip" in r["dedup_key"]]
        assert len(pretrip_rows) == 1
        assert pretrip_rows[0]["priority"] == 92
        assert "Tokyo" in pretrip_rows[0]["message"]


async def test_insight_scan_pretrip_priority_urgent_3_days(provisioned_postgres_pool):
    """Trip departing in 3 days gets priority 78."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        await _insert_trip(
            pool, name="Paris", destination="Paris", start_date=_today() + timedelta(days=3)
        )

        result = await run_insight_scan(pool)

        assert result["candidates_accepted"] >= 1
        rows = await pool.fetch(
            "SELECT priority FROM insight_candidates"
            " WHERE origin_butler = 'travel' AND category = 'pre-trip'"
        )
        assert rows[0]["priority"] == 78


async def test_insight_scan_pretrip_priority_info_7_days(provisioned_postgres_pool):
    """Trip departing in 5-7 days gets priority 65."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        await _insert_trip(
            pool, name="London", destination="London", start_date=_today() + timedelta(days=6)
        )

        result = await run_insight_scan(pool)

        assert result["candidates_accepted"] >= 1
        rows = await pool.fetch(
            "SELECT priority FROM insight_candidates"
            " WHERE origin_butler = 'travel' AND category = 'pre-trip'"
        )
        assert rows[0]["priority"] == 65


async def test_insight_scan_pretrip_dedup_key_format(provisioned_postgres_pool):
    """Pre-trip dedup_key follows travel:pre-trip:{trip-id}:{departure-date} format."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        departure = _today() + timedelta(days=4)
        trip_id = await _insert_trip(pool, start_date=departure)

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT dedup_key FROM insight_candidates WHERE category = 'pre-trip'"
        )
        assert len(rows) == 1
        dedup_key = rows[0]["dedup_key"]
        assert dedup_key.startswith("travel:pre-trip:")
        assert trip_id in dedup_key
        assert departure.isoformat() in dedup_key


async def test_insight_scan_pretrip_message_references_destination(provisioned_postgres_pool):
    """Pre-trip message references the destination and pre-trip checklist."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        await _insert_trip(
            pool,
            name="Bali Trip",
            destination="Bali",
            start_date=_today() + timedelta(days=5),
        )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT message FROM insight_candidates WHERE category = 'pre-trip'"
        )
        assert len(rows) == 1
        message = rows[0]["message"]
        assert "Bali" in message
        assert "checklist" in message.lower()


async def test_insight_scan_pretrip_expires_at_is_departure_date(provisioned_postgres_pool):
    """Pre-trip candidate expires_at is set to the departure date."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        departure = _today() + timedelta(days=4)
        await _insert_trip(pool, start_date=departure)

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT expires_at FROM insight_candidates WHERE category = 'pre-trip'"
        )
        assert len(rows) == 1
        expires_at = rows[0]["expires_at"]
        assert expires_at.date() == departure


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — document expiry
# ---------------------------------------------------------------------------


async def test_insight_scan_document_expiry_30_days_priority_85(provisioned_postgres_pool):
    """Visa expiring within 30 days generates priority 85 insight."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        trip_id = await _insert_trip(pool)
        expiry = _today() + timedelta(days=20)
        await _insert_document(pool, trip_id=trip_id, doc_type="visa", expiry_date=expiry)

        result = await run_insight_scan(pool)

        assert result["candidates_accepted"] >= 1
        rows = await pool.fetch(
            "SELECT priority, cooldown_days FROM insight_candidates"
            " WHERE category = 'document-expiry'"
        )
        assert len(rows) == 1
        assert rows[0]["priority"] == 85
        assert rows[0]["cooldown_days"] == 3


async def test_insight_scan_document_expiry_60_days_priority_65(provisioned_postgres_pool):
    """Document expiring within 31-60 days generates priority 65 insight."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        trip_id = await _insert_trip(pool)
        expiry = _today() + timedelta(days=45)
        await _insert_document(pool, trip_id=trip_id, doc_type="insurance", expiry_date=expiry)

        result = await run_insight_scan(pool)

        assert result["candidates_accepted"] >= 1
        rows = await pool.fetch(
            "SELECT priority, cooldown_days FROM insight_candidates"
            " WHERE category = 'document-expiry'"
        )
        assert len(rows) == 1
        assert rows[0]["priority"] == 65
        assert rows[0]["cooldown_days"] == 7


async def test_insight_scan_document_expiry_90_days_priority_45(provisioned_postgres_pool):
    """Document expiring within 61-90 days generates priority 45 insight."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        trip_id = await _insert_trip(pool)
        expiry = _today() + timedelta(days=75)
        await _insert_document(pool, trip_id=trip_id, doc_type="visa", expiry_date=expiry)

        result = await run_insight_scan(pool)

        assert result["candidates_accepted"] >= 1
        rows = await pool.fetch(
            "SELECT priority, cooldown_days FROM insight_candidates"
            " WHERE category = 'document-expiry'"
        )
        assert len(rows) == 1
        assert rows[0]["priority"] == 45
        assert rows[0]["cooldown_days"] == 14


async def test_insight_scan_document_expiry_dedup_key_format(provisioned_postgres_pool):
    """Document expiry dedup_key follows travel:document-expiry:{type}:{expiry-date} format."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        trip_id = await _insert_trip(pool)
        expiry = _today() + timedelta(days=25)
        await _insert_document(pool, trip_id=trip_id, doc_type="visa", expiry_date=expiry)

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT dedup_key FROM insight_candidates WHERE category = 'document-expiry'"
        )
        assert len(rows) == 1
        dedup_key = rows[0]["dedup_key"]
        assert dedup_key == f"travel:document-expiry:visa:{expiry.isoformat()}"


async def test_insight_scan_document_excludes_receipt_type(provisioned_postgres_pool):
    """Receipt documents are excluded from document expiry insight scan."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        # Use trip outside pre-trip window so only document type matters
        trip_id = await _insert_trip(pool, start_date=_today() + timedelta(days=30))
        await _insert_document(
            pool,
            trip_id=trip_id,
            doc_type="receipt",
            expiry_date=_today() + timedelta(days=10),
        )

        result = await run_insight_scan(pool)

        doc_rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'document-expiry'"
        )
        assert len(doc_rows) == 0
        assert result["candidates_proposed"] == 0


async def test_insight_scan_document_excludes_beyond_90_days(provisioned_postgres_pool):
    """Documents expiring beyond 90 days are excluded from insight scan."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        # Use trip outside pre-trip window so only document expiry matters
        trip_id = await _insert_trip(pool, start_date=_today() + timedelta(days=30))
        await _insert_document(
            pool,
            trip_id=trip_id,
            doc_type="visa",
            expiry_date=_today() + timedelta(days=120),
        )

        result = await run_insight_scan(pool)

        doc_rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'document-expiry'"
        )
        assert len(doc_rows) == 0
        assert result["candidates_proposed"] == 0


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — verbosity=off early exit
# ---------------------------------------------------------------------------


async def test_insight_scan_verbosity_off_exits_early(provisioned_postgres_pool):
    """When verbosity=off, first candidate returns filtered and job exits early."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        await pool.execute(
            "INSERT INTO insight_settings (id, verbosity) VALUES (1, 'off') "
            "ON CONFLICT (id) DO UPDATE SET verbosity = 'off'"
        )

        await _insert_trip(pool, name="Trip A", start_date=_today() + timedelta(days=2))
        await _insert_trip(pool, name="Trip B", start_date=_today() + timedelta(days=4))

        result = await run_insight_scan(pool)

        assert result["early_exit"] is True
        assert result["candidates_proposed"] >= 1
        assert result["candidates_accepted"] == 0
        rows = await pool.fetch("SELECT id FROM insight_candidates WHERE status = 'pending'")
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — multiple candidates in one run
# ---------------------------------------------------------------------------


async def test_insight_scan_multiple_trips_generate_multiple_candidates(
    provisioned_postgres_pool,
):
    """Multiple trips within window each generate their own pre-trip candidate."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        await _insert_trip(pool, name="Trip A", start_date=_today() + timedelta(days=2))
        await _insert_trip(pool, name="Trip B", start_date=_today() + timedelta(days=5))

        result = await run_insight_scan(pool)

        assert result["candidates_proposed"] == 2
        assert result["candidates_accepted"] == 2
        rows = await pool.fetch("SELECT id FROM insight_candidates WHERE category = 'pre-trip'")
        assert len(rows) == 2


async def test_insight_scan_combined_pretrip_and_document_candidates(
    provisioned_postgres_pool,
):
    """Pre-trip and document expiry candidates coexist in one scan run."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        trip_id = await _insert_trip(pool, start_date=_today() + timedelta(days=4))
        await _insert_document(
            pool,
            trip_id=trip_id,
            doc_type="visa",
            expiry_date=_today() + timedelta(days=25),
        )

        result = await run_insight_scan(pool)

        assert result["candidates_proposed"] == 2
        assert result["candidates_accepted"] == 2

        pretrip = await pool.fetch("SELECT id FROM insight_candidates WHERE category = 'pre-trip'")
        doc_expiry = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'document-expiry'"
        )
        assert len(pretrip) == 1
        assert len(doc_expiry) == 1


async def test_insight_scan_result_keys_present(provisioned_postgres_pool):
    """Result dict always contains all expected keys."""
    from roster.travel.jobs.travel_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_travel_schema(pool)
        await _setup_insight_tables(pool)

        result = await run_insight_scan(pool)

        assert "candidates_proposed" in result
        assert "candidates_accepted" in result
        assert "candidates_filtered" in result
        assert "candidates_errored" in result
        assert "early_exit" in result
