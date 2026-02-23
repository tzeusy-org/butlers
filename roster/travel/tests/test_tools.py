"""Integration tests for all travel butler MCP tools.

Covers all 6 tool functions:
    1. record_booking   — happy path, auto-trip creation, trip matching, dedupe,
                          multiple entity types, warnings
    2. update_itinerary — time change, cancellation, seat/gate change, prior values,
                          conflict detection, status transitions
    3. list_trips       — filter by status/date, pagination, empty results, default sort
    4. trip_summary     — full trip with linked entities, timeline, alerts, toggles
    5. upcoming_travel  — within/outside window, pretrip actions, empty results
    6. add_document     — attach to trip, all types, expiry, invalid trip_id

Uses testcontainers PostgreSQL for a real DB.  Tests are isolated via the
``provisioned_postgres_pool`` fixture (fresh DB per test).
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

_docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# Schema DDL — kept inline so each test gets a clean, isolated database
# ---------------------------------------------------------------------------

_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS travel"

_CREATE_TRIPS = """
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

_CREATE_LEGS = """
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

_CREATE_ACCOMMODATIONS = """
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

_CREATE_RESERVATIONS = """
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

_CREATE_DOCUMENTS = """
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


# ---------------------------------------------------------------------------
# Pool fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with all travel schema tables."""
    async with provisioned_postgres_pool() as p:
        await p.execute(_CREATE_SCHEMA)
        await p.execute(_CREATE_TRIPS)
        await p.execute(_CREATE_LEGS)
        await p.execute(_CREATE_ACCOMMODATIONS)
        await p.execute(_CREATE_RESERVATIONS)
        await p.execute(_CREATE_DOCUMENTS)
        yield p


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _insert_trip(
    pool,
    *,
    destination: str = "Tokyo",
    days_ahead: int = 7,
    status: str = "planned",
) -> str:
    """Insert a minimal trip and return its trip_id (str)."""
    start = (_utcnow() + timedelta(days=days_ahead)).date()
    end = (_utcnow() + timedelta(days=days_ahead + 5)).date()
    row = await pool.fetchrow(
        """
        INSERT INTO travel.trips (name, destination, start_date, end_date, status)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
        """,
        f"Trip to {destination}",
        destination,
        start,
        end,
        status,
    )
    return str(row["id"])


async def _insert_leg(
    pool,
    trip_id: str,
    *,
    days_ahead: int = 7,
    carrier: str = "United Airlines",
    departure: str = "SFO",
    arrival: str = "NRT",
    seat: str | None = None,
    confirmation_number: str | None = None,
    leg_type: str = "flight",
) -> str:
    dep_at = _utcnow() + timedelta(days=days_ahead)
    arr_at = dep_at + timedelta(hours=12)
    row = await pool.fetchrow(
        """
        INSERT INTO travel.legs (
            trip_id, type, carrier,
            departure_airport_station, arrival_airport_station,
            departure_at, arrival_at,
            confirmation_number, seat, metadata
        )
        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, '{}'::jsonb)
        RETURNING id
        """,
        trip_id,
        leg_type,
        carrier,
        departure,
        arrival,
        dep_at,
        arr_at,
        confirmation_number,
        seat,
    )
    return str(row["id"])


# ---------------------------------------------------------------------------
# 1. record_booking
# ---------------------------------------------------------------------------


class TestRecordBooking:
    """Tests for record_booking — booking ingestion tool."""

    async def test_happy_path_creates_trip_and_leg(self, pool):
        """record_booking auto-creates a trip and leg from a flight payload."""
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

    async def test_auto_trip_creation_sets_destination(self, pool):
        """Auto-created trip uses arrival as destination."""
        from roster.travel.tools.bookings import record_booking

        dep_at = (_utcnow() + timedelta(days=5)).isoformat()
        arr_at = (_utcnow() + timedelta(days=5, hours=8)).isoformat()

        result = await record_booking(
            pool=pool,
            payload={
                "entity_type": "leg",
                "arrival_city": "Paris",
                "departure_at": dep_at,
                "arrival_at": arr_at,
            },
        )

        trip_row = await pool.fetchrow(
            "SELECT destination FROM travel.trips WHERE id = $1::uuid",
            result["trip_id"],
        )
        assert "Paris" in trip_row["destination"]

    async def test_trip_matching_by_date_and_destination(self, pool):
        """record_booking matches an existing planned trip by date/destination overlap."""
        from roster.travel.tools.bookings import record_booking

        trip_id = await _insert_trip(pool, destination="Tokyo", days_ahead=5)

        dep_at = (_utcnow() + timedelta(days=5)).isoformat()
        arr_at = (_utcnow() + timedelta(days=5, hours=14)).isoformat()

        result = await record_booking(
            pool=pool,
            payload={
                "entity_type": "leg",
                "arrival_city": "Tokyo",
                "departure_at": dep_at,
                "arrival_at": arr_at,
                "confirmation_number": "TK-001",
                "source_message_id": "msg-match",
            },
        )

        assert result["trip_id"] == trip_id

    async def test_deduplication_skips_existing_entity(self, pool):
        """Duplicate source_message_id + confirmation_number returns deduped=True."""
        from roster.travel.tools.bookings import record_booking

        dep_at = (_utcnow() + timedelta(days=8)).isoformat()
        arr_at = (_utcnow() + timedelta(days=8, hours=11)).isoformat()
        payload = {
            "entity_type": "leg",
            "departure_at": dep_at,
            "arrival_at": arr_at,
            "confirmation_number": "CONF-DEDUP",
            "source_message_id": "email-dedup",
        }

        first = await record_booking(pool=pool, payload=payload)
        second = await record_booking(pool=pool, payload=payload)

        assert first["created"] is True
        assert second["deduped"] is True
        assert second["entity_id"] == first["entity_id"]

    async def test_accommodation_entity_type(self, pool):
        """record_booking creates an accommodation when entity_type='accommodation'."""
        from roster.travel.tools.bookings import record_booking

        check_in = (_utcnow() + timedelta(days=3)).isoformat()
        check_out = (_utcnow() + timedelta(days=6)).isoformat()

        result = await record_booking(
            pool=pool,
            payload={
                "entity_type": "accommodation",
                "name": "Grand Hyatt Tokyo",
                "type": "hotel",
                "check_in": check_in,
                "check_out": check_out,
                "confirmation_number": "HYT-99",
                "source_message_id": "email-hotel",
            },
        )

        assert result["entity_type"] == "accommodation"
        assert result["created"] is True
        row = await pool.fetchrow(
            "SELECT * FROM travel.accommodations WHERE id = $1::uuid",
            result["entity_id"],
        )
        assert row is not None
        assert row["name"] == "Grand Hyatt Tokyo"
        assert row["type"] == "hotel"

    async def test_reservation_entity_type(self, pool):
        """record_booking creates a reservation when entity_type='reservation'."""
        from roster.travel.tools.bookings import record_booking

        event_dt = (_utcnow() + timedelta(days=4)).isoformat()

        result = await record_booking(
            pool=pool,
            payload={
                "entity_type": "reservation",
                "provider": "Avis",
                "type": "car_rental",
                "datetime": event_dt,
                "confirmation_number": "AVIS-99",
                "source_message_id": "email-car",
            },
        )

        assert result["entity_type"] == "reservation"
        assert result["created"] is True

    async def test_document_entity_type(self, pool):
        """record_booking creates a document when entity_type='document'."""
        from roster.travel.tools.bookings import record_booking

        trip_id = await _insert_trip(pool, destination="London", days_ahead=14)

        result = await record_booking(
            pool=pool,
            payload={
                "entity_type": "document",
                "doc_type": "receipt",
                "blob_ref": "s3://receipts/ba-001",
                "candidate_trip_hints": {
                    "destinations": ["London"],
                },
            },
        )

        assert result["entity_type"] == "document"
        assert result["created"] is True
        assert result["trip_id"] == trip_id

    async def test_invalid_entity_type_generates_warning_and_defaults_to_leg(self, pool):
        """Invalid entity_type falls back to 'leg' with a warning."""
        from roster.travel.tools.bookings import record_booking

        dep_at = (_utcnow() + timedelta(days=6)).isoformat()
        arr_at = (_utcnow() + timedelta(days=6, hours=9)).isoformat()

        result = await record_booking(
            pool=pool,
            payload={
                "entity_type": "spaceship",
                "departure_at": dep_at,
                "arrival_at": arr_at,
            },
        )

        assert result["entity_type"] == "leg"
        assert any("spaceship" in w for w in result["warnings"])

    async def test_missing_departure_at_returns_warning(self, pool):
        """Missing departure_at for a leg produces a warning and no entity_id."""
        from roster.travel.tools.bookings import record_booking

        result = await record_booking(
            pool=pool,
            payload={
                "entity_type": "leg",
                "arrival_at": (_utcnow() + timedelta(days=5, hours=2)).isoformat(),
                # departure_at deliberately omitted
            },
        )

        assert result["entity_id"] is None
        assert result["created"] is False
        assert len(result["warnings"]) > 0

    async def test_candidate_trip_hints_used_for_matching(self, pool):
        """Explicit candidate_trip_hints dates/destinations guide trip matching."""
        from roster.travel.tools.bookings import record_booking

        trip_id = await _insert_trip(pool, destination="Berlin", days_ahead=3)
        hint_date = (_utcnow() + timedelta(days=3)).date().isoformat()

        result = await record_booking(
            pool=pool,
            payload={
                "entity_type": "accommodation",
                "name": "Hotel Adlon",
                "type": "hotel",
                "check_in": (_utcnow() + timedelta(days=3)).isoformat(),
                "check_out": (_utcnow() + timedelta(days=6)).isoformat(),
                "candidate_trip_hints": {
                    "dates": [hint_date],
                    "destinations": ["Berlin"],
                },
            },
        )

        assert result["trip_id"] == trip_id


# ---------------------------------------------------------------------------
# 2. update_itinerary
# ---------------------------------------------------------------------------


class TestUpdateItinerary:
    """Tests for update_itinerary — itinerary patch tool."""

    async def test_time_change_on_leg(self, pool):
        """update_itinerary updates departure_at and records prior value in metadata."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool, destination="Paris", days_ahead=10)
        leg_id = await _insert_leg(pool, trip_id, days_ahead=10)

        new_dep = (_utcnow() + timedelta(days=10, hours=3)).isoformat()
        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"leg_id": leg_id, "departure_at": new_dep},
            reason="Flight delayed by airline",
        )

        assert result["trip_id"] == trip_id
        assert any(e["entity_id"] == leg_id for e in result["updated_entities"])
        assert result["conflicts"] == []

        # Verify change_history stored in leg metadata
        leg_row = await pool.fetchrow(
            "SELECT metadata FROM travel.legs WHERE id = $1::uuid", leg_id
        )
        meta = leg_row["metadata"]
        if isinstance(meta, str):
            import json

            meta = json.loads(meta)
        history = meta.get("change_history", [])
        assert len(history) == 1
        assert "departure_at" in history[0]["prior_values"]
        assert history[0]["reason"] == "Flight delayed by airline"

    async def test_cancellation_status_transition(self, pool):
        """update_itinerary transitions trip from planned to cancelled."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool, destination="Osaka", days_ahead=20)

        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"status": "cancelled"},
            reason="Passenger cancelled",
        )

        assert result["new_trip_status"] == "cancelled"
        assert result["conflicts"] == []

        row = await pool.fetchrow("SELECT status FROM travel.trips WHERE id = $1::uuid", trip_id)
        assert row["status"] == "cancelled"

    async def test_seat_change_via_leg_id_shortcut(self, pool):
        """update_itinerary updates seat using leg_id shortcut field."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool, destination="Seoul", days_ahead=14)
        leg_id = await _insert_leg(pool, trip_id, days_ahead=14, seat="22A")

        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"leg_id": leg_id, "seat": "14C"},
            reason="Seat upgrade",
        )

        assert any("seat" in e.get("fields", []) for e in result["updated_entities"])

        row = await pool.fetchrow("SELECT seat FROM travel.legs WHERE id = $1::uuid", leg_id)
        assert row["seat"] == "14C"

    async def test_prior_values_recorded_for_seat_change(self, pool):
        """Prior seat value is captured in change_history before update."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool, destination="Bangkok", days_ahead=12)
        leg_id = await _insert_leg(pool, trip_id, days_ahead=12, seat="10B")

        await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"leg_id": leg_id, "seat": "2A"},
            reason="Upgrade applied",
        )

        leg_row = await pool.fetchrow(
            "SELECT metadata FROM travel.legs WHERE id = $1::uuid", leg_id
        )
        raw_meta = leg_row["metadata"]
        if isinstance(raw_meta, str):
            import json

            raw_meta = json.loads(raw_meta)
        history = raw_meta.get("change_history", [])
        assert history[0]["prior_values"]["seat"] == "10B"

    async def test_invalid_backward_status_transition_adds_conflict(self, pool):
        """Backward status transitions are blocked and surface in conflicts."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool, destination="Sydney", days_ahead=5, status="completed")

        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"status": "planned"},
        )

        assert result["conflicts"]
        assert result["new_trip_status"] == "completed"

    async def test_unknown_trip_raises_value_error(self, pool):
        """update_itinerary raises ValueError for non-existent trip_id."""
        from roster.travel.tools.bookings import update_itinerary

        with pytest.raises(ValueError, match="not found"):
            await update_itinerary(
                pool=pool,
                trip_id=str(uuid.uuid4()),
                patch={"status": "active"},
            )

    async def test_entity_not_found_adds_conflict(self, pool):
        """Patching a leg_id that does not exist adds a conflict entry."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool, destination="Dubai", days_ahead=8)
        fake_leg_id = str(uuid.uuid4())

        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"leg_id": fake_leg_id, "seat": "5F"},
        )

        assert any(e["entity_id"] == fake_leg_id for e in result["conflicts"])

    async def test_optimistic_concurrency_conflict_on_version_token_mismatch(self, pool):
        """version_token mismatch causes a conflict and skips the update."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool, destination="Amsterdam", days_ahead=9)
        leg_id = await _insert_leg(pool, trip_id, days_ahead=9)

        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={
                "leg_id": leg_id,
                "seat": "4B",
                "version_token": "1970-01-01T00:00:00+00:00",
            },
        )

        assert any("version_token" in c.get("reason", "") for c in result["conflicts"])

    async def test_trip_field_update_destination(self, pool):
        """update_itinerary updates trip-level destination field."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool, destination="Lisbon", days_ahead=11)

        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"destination": "Porto"},
            reason="Changed city",
        )

        assert any(e["entity_type"] == "trip" for e in result["updated_entities"])
        row = await pool.fetchrow(
            "SELECT destination FROM travel.trips WHERE id = $1::uuid", trip_id
        )
        assert row["destination"] == "Porto"

    async def test_status_transition_planned_to_active(self, pool):
        """update_itinerary advances trip status from planned to active."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool, destination="Montreal", days_ahead=1, status="planned")

        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"status": "active"},
        )

        assert result["new_trip_status"] == "active"
        assert result["conflicts"] == []

    async def test_accommodation_check_out_update(self, pool):
        """update_itinerary patches accommodation check_out datetime."""
        from roster.travel.tools.bookings import update_itinerary

        trip_id = await _insert_trip(pool, destination="Vienna", days_ahead=7)
        check_in = _utcnow() + timedelta(days=7)
        check_out = check_in + timedelta(days=3)
        row = await pool.fetchrow(
            """
            INSERT INTO travel.accommodations
                (trip_id, type, name, check_in, check_out, metadata)
            VALUES ($1::uuid, 'hotel', 'Sacher Hotel', $2, $3, '{}'::jsonb)
            RETURNING id
            """,
            trip_id,
            check_in,
            check_out,
        )
        acc_id = str(row["id"])
        new_check_out = (check_in + timedelta(days=5)).isoformat()

        result = await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"accommodation_id": acc_id, "check_out": new_check_out},
            reason="Extended stay",
        )

        assert any(e["entity_id"] == acc_id for e in result["updated_entities"])


# ---------------------------------------------------------------------------
# 3. list_trips
# ---------------------------------------------------------------------------


class TestListTrips:
    """Tests for list_trips — trip query tool."""

    async def test_empty_result_when_no_trips(self, pool):
        """list_trips returns zero items and total=0 when table is empty."""
        from roster.travel.tools.trips import list_trips

        result = await list_trips(pool)

        assert result["items"] == []
        assert result["total"] == 0
        assert result["limit"] == 20
        assert result["offset"] == 0

    async def test_returns_inserted_trip(self, pool):
        """list_trips returns a single inserted trip with correct fields."""
        from roster.travel.tools.trips import list_trips

        trip_id = await _insert_trip(pool, destination="Rome", days_ahead=15)

        result = await list_trips(pool)

        assert result["total"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["id"] == trip_id
        assert result["items"][0]["destination"] == "Rome"

    async def test_filter_by_status(self, pool):
        """list_trips filters by status, excluding trips with different status."""
        from roster.travel.tools.trips import list_trips

        await _insert_trip(pool, destination="Rome", days_ahead=15, status="planned")
        await _insert_trip(pool, destination="Lyon", days_ahead=5, status="active")

        planned = await list_trips(pool, status="planned")
        active = await list_trips(pool, status="active")

        assert planned["total"] == 1
        assert planned["items"][0]["status"] == "planned"
        assert active["total"] == 1
        assert active["items"][0]["status"] == "active"

    async def test_filter_by_from_date(self, pool):
        """list_trips respects from_date lower bound on start_date."""
        from roster.travel.tools.trips import list_trips

        await _insert_trip(pool, destination="Near", days_ahead=2)
        await _insert_trip(pool, destination="Far", days_ahead=30)

        cutoff = (date.today() + timedelta(days=10)).isoformat()
        result = await list_trips(pool, from_date=cutoff)

        assert result["total"] == 1
        assert result["items"][0]["destination"] == "Far"

    async def test_filter_by_to_date(self, pool):
        """list_trips respects to_date upper bound on start_date."""
        from roster.travel.tools.trips import list_trips

        await _insert_trip(pool, destination="Near", days_ahead=2)
        await _insert_trip(pool, destination="Far", days_ahead=30)

        cutoff = (date.today() + timedelta(days=10)).isoformat()
        result = await list_trips(pool, to_date=cutoff)

        assert result["total"] == 1
        assert result["items"][0]["destination"] == "Near"

    async def test_pagination_limit_and_offset(self, pool):
        """list_trips limit/offset pagination returns correct slices."""
        from roster.travel.tools.trips import list_trips

        for i in range(5):
            await _insert_trip(pool, destination=f"City{i}", days_ahead=i + 1)

        page1 = await list_trips(pool, limit=2, offset=0)
        page2 = await list_trips(pool, limit=2, offset=2)

        assert len(page1["items"]) == 2
        assert len(page2["items"]) == 2
        assert page1["total"] == 5
        ids1 = {r["id"] for r in page1["items"]}
        ids2 = {r["id"] for r in page2["items"]}
        assert ids1.isdisjoint(ids2), "Pages must not overlap"

    async def test_default_sort_is_start_date_desc(self, pool):
        """Default sort returns trips ordered by start_date DESC."""
        from roster.travel.tools.trips import list_trips

        await _insert_trip(pool, destination="Early", days_ahead=3)
        await _insert_trip(pool, destination="Late", days_ahead=20)

        result = await list_trips(pool)

        dates = [r["start_date"] for r in result["items"]]
        assert dates == sorted(dates, reverse=True)

    async def test_invalid_status_raises_value_error(self, pool):
        """list_trips raises ValueError for unrecognised status values."""
        from roster.travel.tools.trips import list_trips

        with pytest.raises(ValueError, match="Unsupported status"):
            await list_trips(pool, status="unknown")

    async def test_string_date_inputs_accepted(self, pool):
        """list_trips accepts ISO string dates for from_date and to_date."""
        from roster.travel.tools.trips import list_trips

        await _insert_trip(pool, destination="DateTest", days_ahead=5)

        from_str = date.today().isoformat()
        to_str = (date.today() + timedelta(days=30)).isoformat()
        result = await list_trips(pool, from_date=from_str, to_date=to_str)

        assert result["total"] >= 1


# ---------------------------------------------------------------------------
# 4. trip_summary
# ---------------------------------------------------------------------------


class TestTripSummary:
    """Tests for trip_summary — full trip view tool."""

    async def test_full_trip_with_all_entity_types(self, pool):
        """trip_summary returns trip with legs, accommodations, reservations, documents."""
        from roster.travel.tools.trips import trip_summary

        trip_id = await _insert_trip(pool, destination="Barcelona", days_ahead=7)
        await _insert_leg(pool, trip_id, days_ahead=7)

        check_in = _utcnow() + timedelta(days=7)
        await pool.execute(
            """
            INSERT INTO travel.accommodations
                (trip_id, type, name, check_in, check_out, metadata)
            VALUES ($1::uuid, 'hotel', 'Hotel Arts', $2, $3, '{}'::jsonb)
            """,
            trip_id,
            check_in,
            check_in + timedelta(days=4),
        )
        await pool.execute(
            """
            INSERT INTO travel.reservations
                (trip_id, type, provider, datetime, metadata)
            VALUES ($1::uuid, 'restaurant', 'El Bulli', $2, '{}'::jsonb)
            """,
            trip_id,
            _utcnow() + timedelta(days=8),
        )
        await pool.execute(
            """
            INSERT INTO travel.documents
                (trip_id, type, blob_ref, metadata)
            VALUES ($1::uuid, 'boarding_pass', 'blob://bp-001', '{}'::jsonb)
            """,
            trip_id,
        )

        result = await trip_summary(pool, trip_id)

        assert result["trip"]["id"] == trip_id
        assert len(result["legs"]) == 1
        assert len(result["accommodations"]) == 1
        assert len(result["reservations"]) == 1
        assert len(result["documents"]) == 1
        # No missing boarding pass alert when one is attached
        assert not any(a["type"] == "missing_boarding_pass" for a in result["alerts"])

    async def test_raises_on_unknown_trip_id(self, pool):
        """trip_summary raises ValueError for a non-existent trip_id."""
        from roster.travel.tools.trips import trip_summary

        with pytest.raises(ValueError, match="Trip not found"):
            await trip_summary(pool, str(uuid.uuid4()))

    async def test_include_documents_false_omits_documents(self, pool):
        """trip_summary returns empty documents list when include_documents=False."""
        from roster.travel.tools.trips import trip_summary

        trip_id = await _insert_trip(pool, destination="Zurich", days_ahead=10)
        await pool.execute(
            """
            INSERT INTO travel.documents
                (trip_id, type, blob_ref, metadata)
            VALUES ($1::uuid, 'visa', 'blob://visa-01', '{}'::jsonb)
            """,
            trip_id,
        )

        result = await trip_summary(pool, trip_id, include_documents=False)

        assert result["documents"] == []

    async def test_include_timeline_false_omits_timeline(self, pool):
        """trip_summary returns empty timeline when include_timeline=False."""
        from roster.travel.tools.trips import trip_summary

        trip_id = await _insert_trip(pool, destination="Prague", days_ahead=8)
        await _insert_leg(pool, trip_id, days_ahead=8)

        result = await trip_summary(pool, trip_id, include_timeline=False)

        assert result["timeline"] == []

    async def test_timeline_is_chronological(self, pool):
        """trip_summary timeline entries are sorted by departure/check-in time."""
        from roster.travel.tools.trips import trip_summary

        trip_id = await _insert_trip(pool, destination="Copenhagen", days_ahead=5)
        dep_early = _utcnow() + timedelta(days=5)
        dep_late = _utcnow() + timedelta(days=7)

        await pool.execute(
            """
            INSERT INTO travel.legs
                (trip_id, type, departure_at, arrival_at, metadata)
            VALUES ($1::uuid, 'flight', $2, $3, '{}'::jsonb)
            """,
            trip_id,
            dep_late,
            dep_late + timedelta(hours=2),
        )
        await pool.execute(
            """
            INSERT INTO travel.legs
                (trip_id, type, departure_at, arrival_at, metadata)
            VALUES ($1::uuid, 'flight', $2, $3, '{}'::jsonb)
            """,
            trip_id,
            dep_early,
            dep_early + timedelta(hours=2),
        )

        result = await trip_summary(pool, trip_id)

        sort_keys = [e["sort_key"] for e in result["timeline"] if e["sort_key"]]
        assert sort_keys == sorted(sort_keys)

    async def test_alerts_missing_boarding_pass(self, pool):
        """trip_summary generates a high-severity alert when no boarding pass exists."""
        from roster.travel.tools.trips import trip_summary

        trip_id = await _insert_trip(pool, destination="Helsinki", days_ahead=4)
        await _insert_leg(pool, trip_id, days_ahead=4)

        result = await trip_summary(pool, trip_id)

        assert any(a["type"] == "missing_boarding_pass" for a in result["alerts"])
        bp_alert = next(a for a in result["alerts"] if a["type"] == "missing_boarding_pass")
        assert bp_alert["severity"] == "high"

    async def test_alerts_unassigned_seat(self, pool):
        """trip_summary generates a low-severity alert for flight with no seat."""
        from roster.travel.tools.trips import trip_summary

        trip_id = await _insert_trip(pool, destination="Reykjavik", days_ahead=6)
        await _insert_leg(pool, trip_id, days_ahead=6, seat=None)
        # Attach boarding pass to avoid that alert
        await pool.execute(
            """
            INSERT INTO travel.documents
                (trip_id, type, blob_ref, metadata)
            VALUES ($1::uuid, 'boarding_pass', 'blob://bp', '{}'::jsonb)
            """,
            trip_id,
        )

        result = await trip_summary(pool, trip_id)

        assert any(a["type"] == "unassigned_seat" for a in result["alerts"])

    async def test_empty_trip_no_alerts(self, pool):
        """trip_summary returns no alerts for a trip with no flight legs."""
        from roster.travel.tools.trips import trip_summary

        trip_id = await _insert_trip(pool, destination="Luxembourg", days_ahead=3)

        result = await trip_summary(pool, trip_id)

        # No flight legs → no boarding pass / seat alerts
        assert result["alerts"] == []

    async def test_result_shape(self, pool):
        """trip_summary result contains all expected top-level keys."""
        from roster.travel.tools.trips import trip_summary

        trip_id = await _insert_trip(pool, destination="Nairobi", days_ahead=20)
        result = await trip_summary(pool, trip_id)

        for key in (
            "trip",
            "legs",
            "accommodations",
            "reservations",
            "documents",
            "timeline",
            "alerts",
        ):
            assert key in result


# ---------------------------------------------------------------------------
# 5. upcoming_travel
# ---------------------------------------------------------------------------


class TestUpcomingTravel:
    """Tests for upcoming_travel — upcoming departures and pre-trip actions tool."""

    async def test_empty_result_when_no_upcoming_trips(self, pool):
        """upcoming_travel returns empty lists when no trips fall in window."""
        from roster.travel.tools.trips import upcoming_travel

        result = await upcoming_travel(pool, within_days=14)

        assert result["upcoming_trips"] == []
        assert result["actions"] == []

    async def test_finds_trips_within_window(self, pool):
        """upcoming_travel includes planned trips starting within within_days."""
        from roster.travel.tools.trips import upcoming_travel

        trip_id = await _insert_trip(pool, destination="Kyoto", days_ahead=5, status="planned")

        result = await upcoming_travel(pool, within_days=14)

        trip_ids = [t["trip"]["id"] for t in result["upcoming_trips"]]
        assert trip_id in trip_ids

    async def test_excludes_trips_outside_window(self, pool):
        """upcoming_travel excludes trips whose start_date is beyond within_days."""
        from roster.travel.tools.trips import upcoming_travel

        await _insert_trip(pool, destination="FarAway", days_ahead=30, status="planned")

        result = await upcoming_travel(pool, within_days=14)

        assert result["upcoming_trips"] == []

    async def test_excludes_completed_and_cancelled_trips(self, pool):
        """upcoming_travel skips completed and cancelled trips even if in window."""
        from roster.travel.tools.trips import upcoming_travel

        await _insert_trip(pool, destination="Done", days_ahead=3, status="completed")
        await _insert_trip(pool, destination="Gone", days_ahead=4, status="cancelled")

        result = await upcoming_travel(pool, within_days=14)

        assert result["upcoming_trips"] == []

    async def test_days_until_departure_computed(self, pool):
        """upcoming_travel computes days_until_departure correctly."""
        from roster.travel.tools.trips import upcoming_travel

        await _insert_trip(pool, destination="Seoul", days_ahead=3, status="planned")

        result = await upcoming_travel(pool, within_days=7)

        entry = result["upcoming_trips"][0]
        assert entry["days_until_departure"] == 3

    async def test_pretrip_actions_for_missing_boarding_pass(self, pool):
        """upcoming_travel surfaces missing_boarding_pass action for flight with no doc."""
        from roster.travel.tools.trips import upcoming_travel

        trip_id = await _insert_trip(pool, destination="Taipei", days_ahead=4, status="planned")
        await _insert_leg(pool, trip_id, days_ahead=4)

        result = await upcoming_travel(pool, within_days=14, include_pretrip_actions=True)

        assert any(a["type"] == "missing_boarding_pass" for a in result["actions"])

    async def test_no_actions_when_include_pretrip_false(self, pool):
        """upcoming_travel returns empty actions when include_pretrip_actions=False."""
        from roster.travel.tools.trips import upcoming_travel

        trip_id = await _insert_trip(pool, destination="Bogota", days_ahead=5, status="planned")
        await _insert_leg(pool, trip_id, days_ahead=5)

        result = await upcoming_travel(pool, within_days=14, include_pretrip_actions=False)

        assert result["actions"] == []

    async def test_actions_have_urgency_rank(self, pool):
        """upcoming_travel action list has consecutive urgency_rank values."""
        from roster.travel.tools.trips import upcoming_travel

        trip_id = await _insert_trip(pool, destination="Cairo", days_ahead=6, status="planned")
        await _insert_leg(pool, trip_id, days_ahead=6, seat=None)

        result = await upcoming_travel(pool, within_days=14)

        ranks = [a["urgency_rank"] for a in result["actions"]]
        assert ranks == list(range(1, len(ranks) + 1))

    async def test_high_severity_actions_ranked_before_low(self, pool):
        """upcoming_travel ranks high-severity actions before low-severity."""
        from roster.travel.tools.trips import upcoming_travel

        trip_id = await _insert_trip(pool, destination="Accra", days_ahead=7, status="planned")
        # Flight with no boarding pass (high) and no seat (low)
        await _insert_leg(pool, trip_id, days_ahead=7, seat=None)

        result = await upcoming_travel(pool, within_days=14)

        actions = result["actions"]
        # At least 2 actions: missing_boarding_pass (high) + unassigned_seat (low)
        assert len(actions) >= 2
        high_rank = next(a["urgency_rank"] for a in actions if a["type"] == "missing_boarding_pass")
        low_rank = next(a["urgency_rank"] for a in actions if a["type"] == "unassigned_seat")
        assert high_rank < low_rank

    async def test_window_dates_in_result(self, pool):
        """upcoming_travel result contains window_start and window_end."""
        from roster.travel.tools.trips import upcoming_travel

        result = await upcoming_travel(pool, within_days=7)

        assert "window_start" in result
        assert "window_end" in result
        start = date.fromisoformat(result["window_start"])
        end = date.fromisoformat(result["window_end"])
        assert (end - start).days == 7

    async def test_legs_included_per_trip(self, pool):
        """upcoming_travel embeds leg records under each trip entry."""
        from roster.travel.tools.trips import upcoming_travel

        trip_id = await _insert_trip(pool, destination="Havana", days_ahead=2, status="active")
        await _insert_leg(pool, trip_id, days_ahead=2, carrier="Cubana")

        result = await upcoming_travel(pool, within_days=7)

        entry = next(t for t in result["upcoming_trips"] if t["trip"]["id"] == trip_id)
        assert len(entry["legs"]) == 1
        assert entry["legs"][0]["carrier"] == "Cubana"


# ---------------------------------------------------------------------------
# 6. add_document
# ---------------------------------------------------------------------------


class TestAddDocument:
    """Tests for add_document — document attachment tool."""

    async def test_attach_boarding_pass(self, pool):
        """add_document attaches a boarding_pass to a trip and returns a document_id."""
        from roster.travel.tools.documents import add_document

        trip_id = await _insert_trip(pool, destination="Rome", days_ahead=5)

        result = await add_document(
            pool=pool,
            trip_id=trip_id,
            type="boarding_pass",
            blob_ref="s3://bucket/bp-001.pdf",
        )

        assert result["document_id"] is not None
        assert result["trip_id"] == trip_id
        assert result["type"] == "boarding_pass"
        assert result["blob_ref"] == "s3://bucket/bp-001.pdf"

    async def test_attach_visa_with_expiry(self, pool):
        """add_document stores a visa with expiry_date correctly."""
        from roster.travel.tools.documents import add_document

        trip_id = await _insert_trip(pool, destination="India", days_ahead=10)
        expiry = (date.today() + timedelta(days=365)).isoformat()

        result = await add_document(
            pool=pool,
            trip_id=trip_id,
            type="visa",
            blob_ref="s3://bucket/visa.pdf",
            expiry_date=expiry,
        )

        assert result["expiry_date"] is not None
        # Normalise to just the date part for comparison
        stored_expiry = result["expiry_date"]
        if len(stored_expiry) > 10:
            stored_expiry = stored_expiry[:10]
        assert stored_expiry == expiry

    async def test_attach_insurance_without_blob(self, pool):
        """add_document accepts a document with no blob_ref (metadata-only)."""
        from roster.travel.tools.documents import add_document

        trip_id = await _insert_trip(pool, destination="Brazil", days_ahead=15)

        result = await add_document(
            pool=pool,
            trip_id=trip_id,
            type="insurance",
            blob_ref=None,
        )

        assert result["document_id"] is not None
        assert result["blob_ref"] is None

    async def test_attach_receipt(self, pool):
        """add_document supports receipt type with metadata."""
        from roster.travel.tools.documents import add_document

        trip_id = await _insert_trip(pool, destination="Morocco", days_ahead=8)

        result = await add_document(
            pool=pool,
            trip_id=trip_id,
            type="receipt",
            blob_ref="s3://receipts/atm-001.jpg",
            metadata={"amount": "250 MAD", "vendor": "ATM"},
        )

        assert result["type"] == "receipt"
        assert result["metadata"].get("amount") == "250 MAD"

    async def test_all_valid_document_types(self, pool):
        """add_document accepts all four valid document types."""
        from roster.travel.tools.documents import add_document

        trip_id = await _insert_trip(pool, destination="AnyDest", days_ahead=20)

        for doc_type in ("boarding_pass", "visa", "insurance", "receipt"):
            result = await add_document(pool=pool, trip_id=trip_id, type=doc_type)
            assert result["type"] == doc_type

    async def test_invalid_type_raises_value_error(self, pool):
        """add_document raises ValueError for unsupported document types."""
        from roster.travel.tools.documents import add_document

        trip_id = await _insert_trip(pool, destination="Nowhere", days_ahead=3)

        with pytest.raises(ValueError, match="Invalid document type"):
            await add_document(pool=pool, trip_id=trip_id, type="passport")

    async def test_unknown_trip_id_raises_value_error(self, pool):
        """add_document raises ValueError when trip_id is not found."""
        from roster.travel.tools.documents import add_document

        with pytest.raises(ValueError, match="not found"):
            await add_document(
                pool=pool,
                trip_id=str(uuid.uuid4()),
                type="boarding_pass",
            )

    async def test_metadata_round_trip(self, pool):
        """add_document stores and returns arbitrary metadata dict faithfully."""
        from roster.travel.tools.documents import add_document

        trip_id = await _insert_trip(pool, destination="Capetown", days_ahead=12)
        meta = {"flight": "SA 201", "gate": "C42", "notes": "priority boarding"}

        result = await add_document(
            pool=pool,
            trip_id=trip_id,
            type="boarding_pass",
            metadata=meta,
        )

        assert result["metadata"]["flight"] == "SA 201"
        assert result["metadata"]["gate"] == "C42"
        assert result["metadata"]["notes"] == "priority boarding"

    async def test_result_shape(self, pool):
        """add_document result contains all expected keys."""
        from roster.travel.tools.documents import add_document

        trip_id = await _insert_trip(pool, destination="Malta", days_ahead=7)
        result = await add_document(pool=pool, trip_id=trip_id, type="visa")

        for key in (
            "document_id",
            "trip_id",
            "type",
            "blob_ref",
            "expiry_date",
            "created_at",
            "metadata",
        ):
            assert key in result
