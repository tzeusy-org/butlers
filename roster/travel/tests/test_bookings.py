"""Integration tests for roster/travel/tools/bookings.py and documents.py.

Uses provisioned_postgres_pool fixture for a real PostgreSQL schema, following
the same pattern as roster/travel/tests/test_trips.py and
roster/finance/tests/test_tools.py.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest

_docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# Schema creation helpers
# ---------------------------------------------------------------------------

CREATE_TRAVEL_SCHEMA = "CREATE SCHEMA IF NOT EXISTS travel"

CREATE_TRIPS_SQL = """
CREATE TABLE IF NOT EXISTS travel.trips (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    destination TEXT NOT NULL,
    start_date  DATE NOT NULL,
    end_date    DATE NOT NULL CHECK (end_date >= start_date),
    status      TEXT NOT NULL
                    CHECK (status IN ('planned', 'active', 'completed', 'cancelled')),
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
    arrival_at                TIMESTAMPTZ NOT NULL CHECK (arrival_at >= departure_at),
    confirmation_number       TEXT,
    pnr                       TEXT,
    seat                      TEXT,
    metadata                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
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


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with travel schema tables."""
    async with provisioned_postgres_pool() as p:
        await p.execute(CREATE_TRAVEL_SCHEMA)
        await p.execute(CREATE_TRIPS_SQL)
        await p.execute(CREATE_LEGS_SQL)
        await p.execute(CREATE_ACCOMMODATIONS_SQL)
        await p.execute(CREATE_RESERVATIONS_SQL)
        await p.execute(CREATE_DOCUMENTS_SQL)
        yield p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _insert_trip(pool, destination: str = "Tokyo", days_ahead: int = 7) -> str:
    """Insert a minimal trip and return its trip_id."""
    start = (_utcnow() + timedelta(days=days_ahead)).date()
    end = (_utcnow() + timedelta(days=days_ahead + 5)).date()
    row = await pool.fetchrow(
        """
        INSERT INTO travel.trips (name, destination, start_date, end_date, status)
        VALUES ($1, $2, $3, $4, 'planned')
        RETURNING id
        """,
        f"Trip to {destination}",
        destination,
        start,
        end,
    )
    return str(row["id"])


# ---------------------------------------------------------------------------
# record_booking — leg entity
# ---------------------------------------------------------------------------


class TestRecordBookingLeg:
    """Tests for record_booking with entity_type='leg'."""

    async def test_creates_new_trip_and_leg(self, pool):
        """record_booking auto-creates a trip when no existing trip matches."""
        from roster.travel.tools.bookings import record_booking

        dep_at = (_utcnow() + timedelta(days=10)).isoformat()
        arr_at = (_utcnow() + timedelta(days=10, hours=12)).isoformat()

        result = await record_booking(
            pool=pool,
            payload={
                "provider": "United Airlines",
                "entity_type": "leg",
                "departure": "SFO",
                "arrival": "NRT",
                "departure_at": dep_at,
                "arrival_at": arr_at,
                "pnr": "K9X4TZ",
                "confirmation_number": "UA123456",
                "source_message_id": "email-001",
            },
        )

        assert result["trip_id"] is not None
        assert result["entity_type"] == "leg"
        assert result["entity_id"] is not None
        assert result["created"] is True
        assert result["deduped"] is False
        assert result["warnings"] == []

        # Verify leg exists in DB
        leg_row = await pool.fetchrow(
            "SELECT * FROM travel.legs WHERE id = $1::uuid",
            result["entity_id"],
        )
        assert leg_row is not None
        assert leg_row["pnr"] == "K9X4TZ"
        assert leg_row["confirmation_number"] == "UA123456"
        assert leg_row["type"] == "flight"

    async def test_matches_existing_trip_by_date_and_destination(self, pool):
        """record_booking matches an existing trip by date/destination overlap."""
        from roster.travel.tools.bookings import record_booking

        trip_id = await _insert_trip(pool, destination="Tokyo", days_ahead=5)

        dep_at = (_utcnow() + timedelta(days=6)).isoformat()
        arr_at = (_utcnow() + timedelta(days=6, hours=14)).isoformat()

        result = await record_booking(
            pool=pool,
            payload={
                "provider": "ANA",
                "entity_type": "leg",
                "arrival": "Tokyo",
                "departure_at": dep_at,
                "arrival_at": arr_at,
                "source_message_id": "email-002",
            },
        )

        # Should reuse existing trip
        assert result["trip_id"] == trip_id
        assert result["created"] is True

    async def test_deduplicates_on_confirmation_and_source_message_id(self, pool):
        """Duplicate record_booking calls with same confirmation+source return deduped=True."""
        from roster.travel.tools.bookings import record_booking

        dep_at = (_utcnow() + timedelta(days=15)).isoformat()
        arr_at = (_utcnow() + timedelta(days=15, hours=10)).isoformat()

        payload = {
            "entity_type": "leg",
            "departure": "LAX",
            "arrival": "LHR",
            "departure_at": dep_at,
            "arrival_at": arr_at,
            "confirmation_number": "DEDUP123",
            "source_message_id": "email-dup-001",
        }

        first = await record_booking(pool=pool, payload=payload)
        second = await record_booking(pool=pool, payload=payload)

        assert first["created"] is True
        assert first["deduped"] is False
        assert second["deduped"] is True
        assert second["entity_id"] == first["entity_id"]

    async def test_invalid_entity_type_falls_back_to_leg(self, pool):
        """Unknown entity_type defaults to 'leg' with a warning."""
        from roster.travel.tools.bookings import record_booking

        dep_at = (_utcnow() + timedelta(days=20)).isoformat()
        arr_at = (_utcnow() + timedelta(days=20, hours=5)).isoformat()

        result = await record_booking(
            pool=pool,
            payload={
                "entity_type": "spaceship",
                "departure_at": dep_at,
                "arrival_at": arr_at,
            },
        )

        assert result["entity_type"] == "leg"
        assert any("Unknown entity_type" in w for w in result["warnings"])

    async def test_missing_departure_at_returns_warning(self, pool):
        """record_booking returns warnings when required leg field is missing."""
        from roster.travel.tools.bookings import record_booking

        result = await record_booking(
            pool=pool,
            payload={
                "entity_type": "leg",
                "arrival": "CDG",
                # departure_at is missing — should trigger warning
            },
        )

        assert result["entity_id"] is None
        assert result["created"] is False
        assert len(result["warnings"]) > 0


# ---------------------------------------------------------------------------
# record_booking — accommodation entity
# ---------------------------------------------------------------------------


class TestRecordBookingAccommodation:
    """Tests for record_booking with entity_type='accommodation'."""

    async def test_creates_accommodation(self, pool):
        """record_booking creates an accommodation linked to the trip."""
        from roster.travel.tools.bookings import record_booking

        check_in = (_utcnow() + timedelta(days=8)).isoformat()
        check_out = (_utcnow() + timedelta(days=12)).isoformat()

        result = await record_booking(
            pool=pool,
            payload={
                "entity_type": "accommodation",
                "provider": "Marriott",
                "name": "Tokyo Marriott",
                "type": "hotel",
                "check_in": check_in,
                "check_out": check_out,
                "confirmation_number": "HOTEL-001",
                "source_message_id": "hotel-email-001",
            },
        )

        assert result["entity_type"] == "accommodation"
        assert result["created"] is True
        assert result["entity_id"] is not None

        row = await pool.fetchrow(
            "SELECT * FROM travel.accommodations WHERE id = $1::uuid",
            result["entity_id"],
        )
        assert row is not None
        assert row["name"] == "Tokyo Marriott"
        assert row["confirmation_number"] == "HOTEL-001"

    async def test_deduplicates_accommodation(self, pool):
        """Duplicate accommodation booking with same confirmation+source returns deduped."""
        from roster.travel.tools.bookings import record_booking

        check_in = (_utcnow() + timedelta(days=9)).isoformat()
        check_out = (_utcnow() + timedelta(days=11)).isoformat()
        payload = {
            "entity_type": "accommodation",
            "check_in": check_in,
            "check_out": check_out,
            "confirmation_number": "HOTEL-DUP",
            "source_message_id": "hotel-dup-001",
        }

        first = await record_booking(pool=pool, payload=payload)
        second = await record_booking(pool=pool, payload=payload)

        assert first["deduped"] is False
        assert second["deduped"] is True


# ---------------------------------------------------------------------------
# record_booking — reservation entity
# ---------------------------------------------------------------------------


class TestRecordBookingReservation:
    """Tests for record_booking with entity_type='reservation'."""

    async def test_creates_reservation(self, pool):
        """record_booking creates a reservation linked to a trip."""
        from roster.travel.tools.bookings import record_booking

        event_dt = (_utcnow() + timedelta(days=9)).isoformat()

        result = await record_booking(
            pool=pool,
            payload={
                "entity_type": "reservation",
                "type": "restaurant",
                "provider": "Nobu Tokyo",
                "datetime": event_dt,
                "confirmation_number": "RES-001",
                "source_message_id": "res-email-001",
            },
        )

        assert result["entity_type"] == "reservation"
        assert result["created"] is True
        row = await pool.fetchrow(
            "SELECT * FROM travel.reservations WHERE id = $1::uuid",
            result["entity_id"],
        )
        assert row is not None
        assert row["provider"] == "Nobu Tokyo"


# ---------------------------------------------------------------------------
# update_itinerary — trip-level mutations
# ---------------------------------------------------------------------------


class TestUpdateItineraryTripLevel:
    """Tests for update_itinerary applied at the trip level."""

    async def test_update_trip_status_planned_to_active(self, pool):
        """update_itinerary advances status from planned to active."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool)
        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"status": "active"},
            reason="trip started",
        )

        assert result["new_trip_status"] == "active"
        assert result["conflicts"] == []
        assert any(e["entity_type"] == "trip" for e in result["updated_entities"])

        row = await pool.fetchrow("SELECT status FROM travel.trips WHERE id = $1::uuid", trip_id)
        assert row["status"] == "active"

    async def test_status_change_preserves_history(self, pool):
        """update_itinerary stores prior status in metadata.change_history."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool)
        await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"status": "active"},
            reason="trip commenced",
        )

        import json

        row = await pool.fetchrow("SELECT metadata FROM travel.trips WHERE id = $1::uuid", trip_id)
        meta_raw = row["metadata"]
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        history = meta.get("change_history", [])
        assert len(history) == 1
        entry = history[0]
        assert entry["prior_values"]["status"] == "planned"
        assert entry["updated_by"] == "update_itinerary"
        assert entry["reason"] == "trip commenced"

    async def test_invalid_backward_status_transition_adds_conflict(self, pool):
        """update_itinerary rejects backward status transitions and adds conflict."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool)
        # Set to completed first
        await pool.execute(
            "UPDATE travel.trips SET status = 'completed' WHERE id = $1::uuid",
            trip_id,
        )

        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"status": "active"},
        )

        assert result["new_trip_status"] == "completed"
        assert len(result["conflicts"]) == 1
        assert "Cannot transition" in result["conflicts"][0]["reason"]

    async def test_trip_not_found_raises_value_error(self, pool):
        """update_itinerary raises ValueError for unknown trip_id."""
        from roster.travel.tools.bookings import update_itinerary

        with pytest.raises(ValueError, match="not found"):
            await update_itinerary(
                pool=pool,
                trip_id=str(uuid.uuid4()),
                patch={"status": "active"},
            )

    async def test_update_trip_destination_field(self, pool):
        """update_itinerary updates a trip destination string field."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool, destination="Paris")
        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"destination": "Lyon"},
            reason="destination corrected",
        )

        assert result["conflicts"] == []
        row = await pool.fetchrow(
            "SELECT destination FROM travel.trips WHERE id = $1::uuid", trip_id
        )
        assert row["destination"] == "Lyon"


# ---------------------------------------------------------------------------
# update_itinerary — entity-level mutations
# ---------------------------------------------------------------------------


class TestUpdateItineraryEntityLevel:
    """Tests for update_itinerary applied to legs and accommodations."""

    async def _create_leg(self, pool, trip_id: str) -> str:
        dep_at = _utcnow() + timedelta(days=5)
        arr_at = _utcnow() + timedelta(days=5, hours=10)
        row = await pool.fetchrow(
            """
            INSERT INTO travel.legs (
                trip_id, type, departure_at, arrival_at
            ) VALUES ($1::uuid, 'flight', $2, $3)
            RETURNING id
            """,
            trip_id,
            dep_at,
            arr_at,
        )
        return str(row["id"])

    async def test_update_leg_departure_time(self, pool):
        """update_itinerary patches a leg's departure_at and stores prior value."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool)
        leg_id = await self._create_leg(pool, trip_id)

        new_dep = (_utcnow() + timedelta(days=5, hours=3)).isoformat()
        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={
                "leg_id": leg_id,
                "departure_at": new_dep,
            },
            reason="delay notification",
        )

        assert result["conflicts"] == []
        updated = [e for e in result["updated_entities"] if e["entity_type"] == "leg"]
        assert len(updated) == 1
        assert "departure_at" in updated[0]["fields"]

    async def test_change_history_stored_in_leg_metadata(self, pool):
        """update_itinerary stores prior departure_at in leg metadata.change_history."""
        import json

        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool)
        leg_id = await self._create_leg(pool, trip_id)

        # Read current departure_at
        orig_row = await pool.fetchrow(
            "SELECT departure_at FROM travel.legs WHERE id = $1::uuid", leg_id
        )
        orig_dep = orig_row["departure_at"].isoformat()

        new_dep = (_utcnow() + timedelta(days=5, hours=4)).isoformat()
        await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"leg_id": leg_id, "departure_at": new_dep},
            reason="gate change",
        )

        leg_row = await pool.fetchrow(
            "SELECT metadata FROM travel.legs WHERE id = $1::uuid", leg_id
        )
        meta_raw = leg_row["metadata"]
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        history = meta.get("change_history", [])
        assert len(history) == 1
        assert history[0]["prior_values"]["departure_at"] == orig_dep
        assert history[0]["reason"] == "gate change"

    async def test_entity_not_found_adds_conflict(self, pool):
        """update_itinerary adds conflict when entity_id does not exist in trip."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool)
        fake_leg_id = str(uuid.uuid4())

        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"leg_id": fake_leg_id, "seat": "15A"},
        )

        assert len(result["conflicts"]) == 1
        assert "not found" in result["conflicts"][0]["reason"]

    async def test_optimistic_concurrency_conflict(self, pool):
        """update_itinerary returns conflict when version_token mismatches."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool)
        leg_id = await self._create_leg(pool, trip_id)

        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={
                "leg_id": leg_id,
                "seat": "12C",
                "version_token": "stale-token-xyz",
            },
        )

        assert len(result["conflicts"]) == 1
        assert "version_token mismatch" in result["conflicts"][0]["reason"]

    async def test_seat_update_via_leg_id_shortcut(self, pool):
        """update_itinerary supports leg_id shortcut without explicit entity_type."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool)
        leg_id = await self._create_leg(pool, trip_id)

        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"leg_id": leg_id, "seat": "3F"},
            reason="seat upgrade",
        )

        assert result["conflicts"] == []
        leg_row = await pool.fetchrow("SELECT seat FROM travel.legs WHERE id = $1::uuid", leg_id)
        assert leg_row["seat"] == "3F"


# ---------------------------------------------------------------------------
# add_document
# ---------------------------------------------------------------------------


class TestAddDocument:
    """Tests for add_document."""

    async def test_attach_boarding_pass(self, pool):
        """add_document attaches a boarding_pass to an existing trip."""
        from roster.travel.tools.documents import add_document

        trip_id = await _insert_trip(pool)
        result = await add_document(
            pool=pool,
            trip_id=trip_id,
            type="boarding_pass",
            blob_ref="s3://bucket/boarding-pass-001.pdf",
            metadata={"flight": "UA 837", "gate": "B12"},
        )

        assert result["document_id"] is not None
        assert result["trip_id"] == trip_id
        assert result["type"] == "boarding_pass"
        assert result["blob_ref"] == "s3://bucket/boarding-pass-001.pdf"
        assert result["created_at"] is not None

    async def test_attach_visa_with_expiry(self, pool):
        """add_document stores expiry_date for visa documents."""
        from roster.travel.tools.documents import add_document

        trip_id = await _insert_trip(pool)
        result = await add_document(
            pool=pool,
            trip_id=trip_id,
            type="visa",
            expiry_date="2028-06-14",
        )

        assert result["type"] == "visa"
        assert result["expiry_date"] == "2028-06-14"

    async def test_invalid_document_type_raises(self, pool):
        """add_document raises ValueError for unknown document type."""
        from roster.travel.tools.documents import add_document

        trip_id = await _insert_trip(pool)
        with pytest.raises(ValueError, match="Invalid document type"):
            await add_document(
                pool=pool,
                trip_id=trip_id,
                type="passport",  # not in allowed types
            )

    async def test_unknown_trip_raises(self, pool):
        """add_document raises ValueError when trip does not exist."""
        from roster.travel.tools.documents import add_document

        with pytest.raises(ValueError, match="not found"):
            await add_document(
                pool=pool,
                trip_id=str(uuid.uuid4()),
                type="receipt",
            )

    async def test_attach_insurance_without_blob(self, pool):
        """add_document allows None blob_ref for metadata-only tracking."""
        from roster.travel.tools.documents import add_document

        trip_id = await _insert_trip(pool)
        result = await add_document(
            pool=pool,
            trip_id=trip_id,
            type="insurance",
            blob_ref=None,
            metadata={"provider": "World Nomads", "policy": "WN-999"},
        )

        assert result["blob_ref"] is None
        assert result["type"] == "insurance"
        assert result["metadata"]["policy"] == "WN-999"

    async def test_attach_receipt(self, pool):
        """add_document creates a receipt document."""
        from roster.travel.tools.documents import add_document

        trip_id = await _insert_trip(pool)
        result = await add_document(
            pool=pool,
            trip_id=trip_id,
            type="receipt",
            blob_ref="file://receipts/hotel-001.png",
        )

        assert result["type"] == "receipt"

        # Verify in DB
        row = await pool.fetchrow(
            "SELECT * FROM travel.documents WHERE id = $1::uuid",
            result["document_id"],
        )
        assert row is not None
        assert str(row["trip_id"]) == trip_id
