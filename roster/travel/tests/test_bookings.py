"""Integration tests for roster/travel/tools/bookings.py and documents.py.

Uses provisioned_postgres_pool fixture for a real PostgreSQL schema, following
the same pattern as roster/travel/tests/test_trips.py and
roster/finance/tests/test_tools.py.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

_docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]


def _load_travel_router():
    module_name = "travel_api_router_booking_integration"
    if module_name in sys.modules:
        return sys.modules[module_name]
    router_path = Path(__file__).parents[1] / "api" / "router.py"
    spec = importlib.util.spec_from_file_location(module_name, router_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


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

# bu-2jtfw.8: PNR-keyed booking identity, traveller party, and connections.
CREATE_PUBLIC_ENTITIES_SQL = """
CREATE TABLE IF NOT EXISTS public.entities (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name  TEXT NOT NULL DEFAULT '',
    entity_type     TEXT NOT NULL DEFAULT 'other',
    aliases         TEXT[] NOT NULL DEFAULT '{}',
    metadata        JSONB DEFAULT '{}'::jsonb,
    roles           TEXT[] NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_BOOKING_RECORDS_SQL = """
CREATE TABLE IF NOT EXISTS travel.booking_records (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id            UUID REFERENCES travel.trips(id) ON DELETE CASCADE,
    record_locator     TEXT,
    source_message_id  TEXT,
    provider           TEXT NOT NULL DEFAULT '',
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_BOOKING_RECORDS_UNIQUE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_booking_records_provider_locator
    ON travel.booking_records (provider, record_locator) WHERE record_locator IS NOT NULL
"""

ALTER_LEGS_ADD_SEGMENT_IDENTITY_SQL = """
ALTER TABLE travel.legs
    ADD COLUMN IF NOT EXISTS segment_index INT,
    ADD COLUMN IF NOT EXISTS booking_record_id UUID
        REFERENCES travel.booking_records(id) ON DELETE SET NULL
"""

CREATE_LEGS_SEGMENT_UNIQUE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_legs_booking_record_segment
    ON travel.legs (booking_record_id, segment_index)
    WHERE booking_record_id IS NOT NULL AND segment_index IS NOT NULL
"""

CREATE_TRAVELLERS_SQL = """
CREATE TABLE IF NOT EXISTS travel.travellers (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id       UUID NOT NULL REFERENCES travel.trips(id) ON DELETE CASCADE,
    entity_id     UUID REFERENCES public.entities(id),
    traveller_key TEXT NOT NULL,
    display_name  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (trip_id, traveller_key)
)
"""

CREATE_LEG_PASSENGERS_SQL = """
CREATE TABLE IF NOT EXISTS travel.leg_passengers (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    leg_id         UUID NOT NULL REFERENCES travel.legs(id) ON DELETE CASCADE,
    traveller_id   UUID NOT NULL REFERENCES travel.travellers(id) ON DELETE CASCADE,
    seat           TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (leg_id, traveller_id)
)
"""

CREATE_AIRPORT_MINIMUM_CONNECT_SQL = """
CREATE TABLE IF NOT EXISTS travel.airport_minimum_connect (
    airport_code               TEXT PRIMARY KEY,
    minimum_connect_minutes    INT NOT NULL,
    interline_buffer_minutes   INT NOT NULL DEFAULT 30,
    source                     TEXT,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_CONNECTIONS_SQL = """
CREATE TABLE IF NOT EXISTS travel.connections (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id            UUID NOT NULL REFERENCES travel.trips(id) ON DELETE CASCADE,
    inbound_leg_id     UUID NOT NULL REFERENCES travel.legs(id) ON DELETE CASCADE,
    outbound_leg_id    UUID NOT NULL REFERENCES travel.legs(id) ON DELETE CASCADE,
    verdict            TEXT NOT NULL CHECK (verdict IN ('holds', 'tight', 'broken', 'unknown')),
    available_minutes  INT,
    evidence           JSONB NOT NULL DEFAULT '{}'::jsonb,
    computed_at        TIMESTAMPTZ NOT NULL,
    verdict_changed_at TIMESTAMPTZ NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (inbound_leg_id, outbound_leg_id)
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
        await p.execute(CREATE_PUBLIC_ENTITIES_SQL)
        await p.execute(CREATE_BOOKING_RECORDS_SQL)
        await p.execute(CREATE_BOOKING_RECORDS_UNIQUE_INDEX_SQL)
        await p.execute(ALTER_LEGS_ADD_SEGMENT_IDENTITY_SQL)
        await p.execute(CREATE_LEGS_SEGMENT_UNIQUE_INDEX_SQL)
        await p.execute(CREATE_TRAVELLERS_SQL)
        await p.execute(CREATE_LEG_PASSENGERS_SQL)
        await p.execute(CREATE_AIRPORT_MINIMUM_CONNECT_SQL)
        await p.execute(CREATE_CONNECTIONS_SQL)
        yield p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _insert_trip(pool, destination: str = "Tokyo", days_ahead: int = 7) -> str:
    """Insert a minimal trip and return its trip_id."""
    start = date.today() + timedelta(days=days_ahead)
    end = date.today() + timedelta(days=days_ahead + 5)
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
        from butlers.tools.travel.bookings import record_booking

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
        from butlers.tools.travel.bookings import record_booking

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
        from butlers.tools.travel.bookings import record_booking

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
        from butlers.tools.travel.bookings import record_booking

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

    async def test_invalid_leg_input_is_rejected_before_any_persistence(self, pool):
        """Every malformed DB-bound value fails before booking identity is written."""
        from butlers.tools.travel.bookings import record_booking

        valid = {
            "entity_type": "leg",
            "record_locator": "MALFORMED",
            "provider": "Test Air",
            "departure_at": "2026-10-16T08:00:00+00:00",
            "arrival_at": "2026-10-16T14:00:00+00:00",
        }
        malformed_payloads = [
            {key: value for key, value in valid.items() if key != "departure_at"},
            {**valid, "segment_index": "first"},
            {**valid, "passengers": [{"name": "Alice", "seat": 14}]},
            {**valid, "arrival_at": "2026-10-16T07:00:00+00:00"},
            {**valid, "metadata": {"not_json": object()}},
        ]

        for payload in malformed_payloads:
            result = await record_booking(pool=pool, payload=payload)

            assert result["entity_id"] is None
            assert result["created"] is False
            assert result["trip_created"] is False
            assert result["trip_event_payload"] is None
            assert result["warnings"]
            assert await pool.fetchval("SELECT count(*) FROM travel.trips") == 0
            assert await pool.fetchval("SELECT count(*) FROM travel.booking_records") == 0
            assert await pool.fetchval("SELECT count(*) FROM travel.legs") == 0

        # These values pass Python shape validation but PostgreSQL rejects
        # them while binding the leg. The surrounding transaction must also
        # remove the booking identity and trip created earlier in the call.
        db_rejected_payloads = [
            {**valid, "carrier": "Invalid\x00Carrier"},
            {**valid, "metadata": {"invalid_number": float("nan")}},
        ]
        for payload in db_rejected_payloads:
            with pytest.raises(asyncpg.PostgresError):
                await record_booking(pool=pool, payload=payload)
            assert await pool.fetchval("SELECT count(*) FROM travel.trips") == 0
            assert await pool.fetchval("SELECT count(*) FROM travel.booking_records") == 0
            assert await pool.fetchval("SELECT count(*) FROM travel.legs") == 0


# ---------------------------------------------------------------------------
# record_booking — PNR-keyed identity, traveller party, connections (bu-2jtfw.8)
# ---------------------------------------------------------------------------


class TestRecordBookingIdentity:
    """The real DD94XR fixture: one PNR, two passengers, two segments.

    Historically this split into two separate one-day trips (one per
    destination substring) and the return leg deduped away against the
    outbound because both shared one confirmation_number. This class proves
    the PNR-keyed identity contract instead.
    """

    _RECORD_LOCATOR = "DD94XR"
    _SHARED_CONFIRMATION = "SHARED-CONF-001"
    _OUTBOUND_DEP = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
    _OUTBOUND_ARR = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)
    _RETURN_DEP = datetime(2026, 10, 25, 14, 0, tzinfo=UTC)
    _RETURN_ARR = datetime(2026, 10, 25, 20, 0, tzinfo=UTC)

    def _segment_payload(self, *, segment_index: int, passenger_name: str) -> dict:
        if segment_index == 0:
            dep, arr, dep_station, arr_station, flight_number = (
                self._OUTBOUND_DEP,
                self._OUTBOUND_ARR,
                "SIN",
                "PEK",
                "DD94XR",
            )
        else:
            dep, arr, dep_station, arr_station, flight_number = (
                self._RETURN_DEP,
                self._RETURN_ARR,
                "PEK",
                "SIN",
                "DD94YR",
            )
        return {
            "entity_type": "leg",
            "record_locator": self._RECORD_LOCATOR,
            "segment_index": segment_index,
            "confirmation_number": self._SHARED_CONFIRMATION,
            "provider": "Test Air",
            "departure_airport_station": dep_station,
            "arrival_airport_station": arr_station,
            "departure_at": dep.isoformat(),
            "arrival_at": arr.isoformat(),
            "metadata": {"flight_number": flight_number},
            "passengers": [{"name": passenger_name}],
            "source_message_id": f"dd94xr-seg{segment_index}-{passenger_name}",
        }

    def _fixture_payloads(self) -> list[dict]:
        return [
            self._segment_payload(segment_index=0, passenger_name="Alice Traveller"),
            self._segment_payload(segment_index=0, passenger_name="Bob Traveller"),
            self._segment_payload(segment_index=1, passenger_name="Alice Traveller"),
            self._segment_payload(segment_index=1, passenger_name="Bob Traveller"),
        ]

    async def test_dd94xr_fixture_yields_one_trip_two_legs_four_leg_passengers(self, pool):
        """Ingesting the four DD94XR payloads (any order) converges on one trip."""
        from butlers.tools.travel.bookings import record_booking

        results = [
            await record_booking(pool=pool, payload=payload)
            for payload in reversed(self._fixture_payloads())
        ]

        trip_ids = {r["trip_id"] for r in results}
        assert len(trip_ids) == 1, f"expected one converged trip, got {trip_ids}"
        trip_id = trip_ids.pop()

        trip_row = await pool.fetchrow(
            "SELECT start_date, end_date FROM travel.trips WHERE id = $1::uuid", trip_id
        )
        assert trip_row["start_date"].isoformat() == "2026-10-16"
        assert trip_row["end_date"].isoformat() == "2026-10-25"

        leg_count = await pool.fetchval(
            "SELECT count(*) FROM travel.legs WHERE trip_id = $1::uuid", trip_id
        )
        assert leg_count == 2

        leg_passenger_count = await pool.fetchval(
            """
            SELECT count(*) FROM travel.leg_passengers lp
            JOIN travel.legs l ON l.id = lp.leg_id
            WHERE l.trip_id = $1::uuid
            """,
            trip_id,
        )
        assert leg_passenger_count == 4
        traveller_count = await pool.fetchval(
            "SELECT count(*) FROM travel.travellers WHERE trip_id = $1::uuid",
            trip_id,
        )
        assert traveller_count == 2
        assert await pool.fetchval("SELECT count(*) FROM public.entities") == 0

        legs = await pool.fetch(
            "SELECT confirmation_number, pnr FROM travel.legs "
            "WHERE trip_id = $1::uuid ORDER BY segment_index",
            trip_id,
        )
        assert [row["confirmation_number"] for row in legs] == [
            self._SHARED_CONFIRMATION,
            self._SHARED_CONFIRMATION,
        ]
        assert [row["pnr"] for row in legs] == [self._RECORD_LOCATOR, self._RECORD_LOCATOR]
        trip_metadata = await pool.fetchval(
            "SELECT metadata FROM travel.trips WHERE id = $1::uuid", trip_id
        )
        assert trip_metadata["identity_confidence"] == "strong"

        # Both segments converged one leg each, not one leg per passenger.
        assert results[0]["entity_id"] == results[1]["entity_id"]
        assert results[2]["entity_id"] == results[3]["entity_id"]
        assert results[0]["entity_id"] != results[2]["entity_id"]

    async def test_dd94xr_live_api_seams_return_party_and_legs(self, pool):
        """The real DB state survives both dashboard routes, not only direct SQL assertions."""
        from butlers.tools.travel.bookings import record_booking

        results = [
            await record_booking(pool=pool, payload=payload) for payload in self._fixture_payloads()
        ]
        trip_id = results[0]["trip_id"]
        router_module = _load_travel_router()
        app = FastAPI()
        app.include_router(router_module.router)
        app.dependency_overrides[router_module._get_db_manager] = lambda: SimpleNamespace(
            pool=lambda _name: pool
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            summary_response, legs_response = await asyncio.gather(
                client.get(f"/api/travel/trips/{trip_id}"),
                client.get(f"/api/travel/trips/{trip_id}/legs"),
            )

        assert summary_response.status_code == 200
        summary = summary_response.json()
        assert len(summary["party"]) == 2
        assert summary["connections"] == []
        assert summary["connection_reason"] == "no_connection_on_journey"
        assert legs_response.status_code == 200
        assert len(legs_response.json()) == 2

        # Migration-era trips predate the durable derivation marker. Even when
        # their leg topology plainly contains a connection, an empty derived
        # table must remain unavailable rather than claim there is no connection.
        migration_trip_id = await pool.fetchval(
            "INSERT INTO travel.trips (name, destination, start_date, end_date, status) "
            "VALUES ('Migration-era connection', 'Tokyo', '2026-11-01', '2026-11-01', "
            "'planned') RETURNING id"
        )
        await pool.execute(
            "INSERT INTO travel.legs (trip_id, type, departure_airport_station, "
            "arrival_airport_station, departure_at, arrival_at) VALUES "
            "($1, 'flight', 'SIN', 'HKG', '2026-11-01T08:00:00+00:00', "
            "'2026-11-01T10:00:00+00:00'), "
            "($1, 'flight', 'HKG', 'NRT', '2026-11-01T11:00:00+00:00', "
            "'2026-11-01T15:00:00+00:00')",
            migration_trip_id,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            migration_response = await client.get(f"/api/travel/trips/{migration_trip_id}")
        assert migration_response.status_code == 200
        migration_summary = migration_response.json()
        assert migration_summary["connections"] == []
        assert migration_summary["connection_reason"] is None

    async def test_reingesting_same_segment_is_idempotent(self, pool):
        """Same-segment convergence is idempotent and never erases populated fields."""
        from butlers.tools.travel.bookings import record_booking

        payload = self._segment_payload(segment_index=0, passenger_name="Alice Traveller")
        payload.update(
            carrier="Operating Air",
            departure_city="Singapore",
            arrival_city="Beijing",
            seat="18A",
            metadata={
                "flight_number": "DD94XR",
                "gate": "A12",
                "flight_status": {"status": "active", "terminal": "1"},
            },
        )
        first = await record_booking(pool=pool, payload=payload)
        sparse = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "carrier",
                "departure_airport_station",
                "departure_city",
                "arrival_airport_station",
                "arrival_city",
                "metadata",
            }
        }
        sparse.update(
            departure_at="2030-01-01T08:00:00+00:00",
            arrival_at="2030-01-01T14:00:00+00:00",
            carrier="  ",
            departure_city="",
            arrival_city=" ",
            seat="",
            metadata={
                "flight_number": None,
                "gate": "",
                "flight_status": {"status": None, "terminal": " "},
            },
        )
        second = await record_booking(pool=pool, payload=sparse)

        assert first["created"] is True
        assert second["created"] is False
        assert second["entity_id"] == first["entity_id"]
        assert second["trip_id"] == first["trip_id"]

        leg_count = await pool.fetchval(
            "SELECT count(*) FROM travel.legs WHERE trip_id = $1::uuid", first["trip_id"]
        )
        assert leg_count == 1

        preserved = await pool.fetchrow(
            "SELECT carrier, departure_airport_station, departure_city, "
            "arrival_airport_station, arrival_city, pnr, seat, metadata, departure_at, arrival_at "
            "FROM travel.legs WHERE id = $1::uuid",
            first["entity_id"],
        )
        assert tuple(preserved.values()) == (
            "Operating Air",
            "SIN",
            "Singapore",
            "PEK",
            "Beijing",
            self._RECORD_LOCATOR,
            "18A",
            {
                "flight_number": "DD94XR",
                "gate": "A12",
                "flight_status": {"status": "active", "terminal": "1"},
                "source_message_id": "dd94xr-seg0-Alice Traveller",
            },
            self._OUTBOUND_DEP,
            self._OUTBOUND_ARR,
        )
        trip_dates = await pool.fetchrow(
            "SELECT start_date, end_date FROM travel.trips WHERE id = $1::uuid", first["trip_id"]
        )
        assert (trip_dates["start_date"], trip_dates["end_date"]) == (
            self._OUTBOUND_DEP.date(),
            self._OUTBOUND_ARR.date(),
        )

        reverse_sparse = {**sparse, "record_locator": "SPARSE2", "source_message_id": "sparse2"}
        reverse_full = {**payload, "record_locator": "SPARSE2", "source_message_id": "full2"}
        sparse_first = await record_booking(pool=pool, payload=reverse_sparse)
        await record_booking(pool=pool, payload=reverse_full)
        enriched = await pool.fetchrow(
            "SELECT carrier, departure_airport_station, departure_city, "
            "arrival_airport_station, arrival_city, seat "
            "FROM travel.legs WHERE id = $1::uuid",
            sparse_first["entity_id"],
        )
        assert tuple(enriched.values()) == (
            "Operating Air",
            "SIN",
            "Singapore",
            "PEK",
            "Beijing",
            "18A",
        )

    async def test_concurrent_ingestion_converges_on_one_trip(self, pool):
        """Two sessions racing the same PNR converge on one trip, one leg per segment."""
        from butlers.tools.travel.bookings import record_booking

        results = await asyncio.gather(
            *[record_booking(pool=pool, payload=payload) for payload in self._fixture_payloads()]
        )

        trip_ids = {r["trip_id"] for r in results}
        assert len(trip_ids) == 1, f"expected one converged trip under concurrency, got {trip_ids}"
        trip_id = trip_ids.pop()

        leg_count = await pool.fetchval(
            "SELECT count(*) FROM travel.legs WHERE trip_id = $1::uuid", trip_id
        )
        assert leg_count == 2

        leg_passenger_count = await pool.fetchval(
            """
            SELECT count(*) FROM travel.leg_passengers lp
            JOIN travel.legs l ON l.id = lp.leg_id
            WHERE l.trip_id = $1::uuid
            """,
            trip_id,
        )
        assert leg_passenger_count == 4

    async def test_no_record_locator_stamps_weak_identity_confidence(self, pool):
        """Without a record_locator, the fuzzy-matched trip is stamped 'weak'."""
        from butlers.tools.travel.bookings import record_booking

        dep_at = (_utcnow() + timedelta(days=20)).isoformat()
        arr_at = (_utcnow() + timedelta(days=20, hours=5)).isoformat()

        result = await record_booking(
            pool=pool,
            payload={
                "entity_type": "leg",
                "arrival": "Berlin",
                "departure_at": dep_at,
                "arrival_at": arr_at,
            },
        )

        metadata = await pool.fetchval(
            "SELECT metadata FROM travel.trips WHERE id = $1::uuid", result["trip_id"]
        )
        assert metadata["identity_confidence"] == "weak"

    async def test_party_reuses_an_existing_canonical_person_entity(self, pool):
        """Travel links to the shared identity spine instead of minting a duplicate person."""
        from butlers.tools.travel.bookings import record_booking

        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type, metadata) "
            "VALUES ('Alice Traveller', 'person', '{}'::jsonb) RETURNING id"
        )
        result = await record_booking(
            pool=pool,
            payload=self._segment_payload(segment_index=0, passenger_name="Alice Traveller"),
        )
        explicit_payload = self._segment_payload(segment_index=0, passenger_name="Alice Traveller")
        explicit_payload["passengers"] = [{"entity_id": str(entity_id), "name": "Alice Traveller"}]
        await record_booking(pool=pool, payload=explicit_payload)

        linked_entity_id = await pool.fetchval(
            "SELECT entity_id FROM travel.travellers WHERE trip_id = $1::uuid",
            result["trip_id"],
        )
        assert linked_entity_id == entity_id
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.entities WHERE canonical_name = 'Alice Traveller'"
            )
            == 1
        )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM travel.travellers WHERE trip_id = $1::uuid",
                result["trip_id"],
            )
            == 1
        )

    async def test_name_keyed_traveller_is_promoted_when_person_becomes_resolvable(self, pool):
        """A later identity resolution preserves one local party member and leg link."""
        from butlers.tools.travel.bookings import record_booking

        payload = self._segment_payload(segment_index=0, passenger_name="Alice Traveller")
        first = await record_booking(pool=pool, payload=payload)
        original_traveller_id = await pool.fetchval(
            "SELECT id FROM travel.travellers WHERE trip_id = $1::uuid", first["trip_id"]
        )
        entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type, metadata) "
            "VALUES ('Alice Traveller', 'person', '{}'::jsonb) RETURNING id"
        )

        await record_booking(pool=pool, payload=payload)

        traveller = await pool.fetchrow(
            "SELECT id, entity_id, traveller_key FROM travel.travellers WHERE trip_id = $1::uuid",
            first["trip_id"],
        )
        assert traveller["id"] == original_traveller_id
        assert traveller["entity_id"] == entity_id
        assert traveller["traveller_key"] == f"entity:{entity_id}"
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM travel.leg_passengers WHERE leg_id = $1::uuid",
                first["entity_id"],
            )
            == 1
        )

        intermediate_entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type, metadata) "
            "VALUES ('Alice intermediate', 'person', $1::jsonb) RETURNING id",
            {"merged_into": str(entity_id)},
        )
        source_entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type, metadata) "
            "VALUES ('Alice duplicate', 'person', $1::jsonb) RETURNING id",
            {"merged_into": str(intermediate_entity_id)},
        )
        source_traveller_id = await pool.fetchval(
            "INSERT INTO travel.travellers (trip_id, entity_id, traveller_key, display_name) "
            "VALUES ($1::uuid, $2::uuid, $3, 'Alice duplicate') RETURNING id",
            first["trip_id"],
            source_entity_id,
            f"entity:{source_entity_id}",
        )
        await pool.execute(
            "INSERT INTO travel.leg_passengers (leg_id, traveller_id, seat) "
            "VALUES ($1::uuid, $2::uuid, '14A')",
            first["entity_id"],
            source_traveller_id,
        )

        # Ordinary name-only re-ingest still uses the merged source's old
        # canonical name. Resolution must follow its lineage to the survivor.
        payload["passengers"] = [{"name": "Alice duplicate"}]
        await record_booking(pool=pool, payload=payload)

        party = await pool.fetch(
            "SELECT entity_id FROM travel.travellers WHERE trip_id = $1::uuid",
            first["trip_id"],
        )
        assert [row["entity_id"] for row in party] == [entity_id]
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM travel.leg_passengers WHERE leg_id = $1::uuid",
                first["entity_id"],
            )
            == 1
        )

    async def test_supplied_entity_id_must_be_a_live_canonical_person(self, pool):
        from butlers.tools.travel.bookings import record_booking

        invalid_states = [
            ("place", {}),
            ("person", {"merged_into": str(uuid.uuid4())}),
            ("person", {"deleted_at": "2026-09-10T00:00:00Z"}),
        ]
        for index, (entity_type, metadata) in enumerate(invalid_states):
            entity_id = await pool.fetchval(
                "INSERT INTO public.entities (canonical_name, entity_type, metadata) "
                "VALUES ('Invalid Traveller', $1, $2::jsonb) RETURNING id",
                entity_type,
                metadata,
            )
            payload = self._segment_payload(segment_index=index, passenger_name="Invalid Traveller")
            payload["record_locator"] = f"INVALID{index}"
            payload["passengers"] = [{"entity_id": str(entity_id), "name": "Invalid Traveller"}]

            result = await record_booking(pool=pool, payload=payload)

            assert result["entity_id"] is None
            assert result["trip_created"] is False
        assert result["warnings"] == [
            "record_booking: passenger entity_id must identify a live person"
        ]
        assert await pool.fetchval("SELECT count(*) FROM travel.trips") == 0
        assert await pool.fetchval("SELECT count(*) FROM travel.booking_records") == 0

    async def test_record_locator_is_scoped_by_provider(self, pool):
        """Provider reuse of the same locator must not merge unrelated bookings."""
        from butlers.tools.travel.bookings import record_booking

        first_payload = self._segment_payload(segment_index=0, passenger_name="Alice Traveller")
        second_payload = {**first_payload, "provider": "Other Air"}
        first = await record_booking(pool=pool, payload=first_payload)
        second = await record_booking(pool=pool, payload=second_payload)

        assert first["trip_id"] != second["trip_id"]
        assert await pool.fetchval("SELECT count(*) FROM travel.booking_records") == 2

        legacy_trip_ids = []
        for day in (1, 10):
            legacy_trip_id = await pool.fetchval(
                "INSERT INTO travel.trips (name, destination, start_date, end_date, status) "
                "VALUES ($1, 'Legacy', $2, $2, 'planned') RETURNING id",
                f"Legacy fragment {day}",
                date(2026, 11, day),
            )
            legacy_trip_ids.append(legacy_trip_id)
            await pool.execute(
                "INSERT INTO travel.legs (trip_id, type, carrier, pnr, departure_at, arrival_at) "
                "VALUES ($1, 'flight', 'Legacy Air', 'LEGACY1', $2, $3)",
                legacy_trip_id,
                datetime(2026, 11, day, 8, tzinfo=UTC),
                datetime(2026, 11, day, 12, tzinfo=UTC),
            )

        legacy_payload = self._segment_payload(segment_index=1, passenger_name="Alice Traveller")
        legacy_payload.update(
            provider="Expedia",
            carrier="Legacy Air",
            record_locator="LEGACY1",
        )
        trip_count_before = await pool.fetchval("SELECT count(*) FROM travel.trips")
        reconciled = await record_booking(pool=pool, payload=legacy_payload)

        assert await pool.fetchval("SELECT count(*) FROM travel.trips") == trip_count_before
        assert uuid.UUID(reconciled["trip_id"]) in legacy_trip_ids

    async def test_blank_provider_uses_weak_matching_not_pnr_identity(self, pool):
        """A locator without provider context is not a strong global identity."""
        from butlers.tools.travel.bookings import record_booking

        first_payload = self._segment_payload(segment_index=0, passenger_name="Alice Traveller")
        first_payload["provider"] = "   "
        second_payload = self._segment_payload(segment_index=1, passenger_name="Alice Traveller")
        second_payload["provider"] = None

        first = await record_booking(pool=pool, payload=first_payload)
        second = await record_booking(pool=pool, payload=second_payload)

        assert first["trip_id"] != second["trip_id"]
        assert await pool.fetchval("SELECT count(*) FROM travel.booking_records") == 0
        confidences = await pool.fetch(
            "SELECT metadata ->> 'identity_confidence' AS confidence FROM travel.trips"
        )
        assert {row["confidence"] for row in confidences} == {"weak"}


# ---------------------------------------------------------------------------
# record_booking — accommodation entity
# ---------------------------------------------------------------------------


class TestRecordBookingAccommodation:
    """Tests for record_booking with entity_type='accommodation'."""

    async def test_creates_accommodation(self, pool):
        """record_booking creates an accommodation linked to the trip."""
        from butlers.tools.travel.bookings import record_booking

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
        from butlers.tools.travel.bookings import record_booking

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
        from butlers.tools.travel.bookings import record_booking

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
# metadata JSONB encoding (bu-2jtfw.1)
#
# `register_jsonb_codec` (src/butlers/db.py) already serializes a bound Python
# value to JSONB on the wire. Pre-serializing it with `json.dumps()` before
# binding double-encodes it: the column ends up holding a JSONB *string*
# (jsonb_typeof = 'string') instead of the real object, which is exactly why
# `jsonb_typeof(...)` -- not the codec's `isinstance(..., str)` decode
# fallback -- is what proves the fix: the fallback silently tolerates the bug.
# ---------------------------------------------------------------------------


class TestMetadataStoredAsRealJsonbObject:
    """record_booking must never double-encode any entity's metadata column."""

    async def test_leg_metadata_is_a_real_object_not_a_json_string(self, pool):
        """The DD94XR fixture: flight_status.py's selection predicate depends
        on `travel.legs.metadata` being a real jsonb object (`? 'flight_number'`
        is never true against a jsonb string scalar)."""
        from butlers.tools.travel.bookings import record_booking

        dep_at = (_utcnow() + timedelta(days=2)).isoformat()
        arr_at = (_utcnow() + timedelta(days=2, hours=11)).isoformat()

        result = await record_booking(
            pool=pool,
            payload={
                "entity_type": "leg",
                "carrier": "Delta",
                "departure": "JFK",
                "arrival": "LHR",
                "departure_at": dep_at,
                "arrival_at": arr_at,
                "confirmation_number": "DD94XR-CONF",
                "source_message_id": "dd94xr-email-001",
                "metadata": {"flight_number": "DD94XR"},
            },
        )

        typeof = await pool.fetchval(
            "SELECT jsonb_typeof(metadata) FROM travel.legs WHERE id = $1::uuid",
            result["entity_id"],
        )
        assert typeof == "object"

        row = await pool.fetchrow(
            "SELECT metadata FROM travel.legs WHERE id = $1::uuid", result["entity_id"]
        )
        # A real jsonb object decodes straight to a dict via the codec; a
        # double-encoded string would decode to `str`, and this subscript
        # would raise TypeError.
        assert row["metadata"]["flight_number"] == "DD94XR"
        assert row["metadata"]["source_message_id"] == "dd94xr-email-001"

    async def test_accommodation_reservation_document_metadata_are_real_objects(self, pool):
        """The same double-encoding bug in bookings.py hit every entity type,
        not just legs -- accommodation, reservation, and document writers all
        went through the identical `json.dumps()` call-site pattern."""
        from butlers.tools.travel.bookings import record_booking

        accom = await record_booking(
            pool=pool,
            payload={
                "entity_type": "accommodation",
                "check_in": (_utcnow() + timedelta(days=3)).isoformat(),
                "check_out": (_utcnow() + timedelta(days=5)).isoformat(),
                "source_message_id": "accom-jsonb-001",
                "metadata": {"room": "1204"},
            },
        )
        reservation = await record_booking(
            pool=pool,
            payload={
                "entity_type": "reservation",
                "datetime": (_utcnow() + timedelta(days=4)).isoformat(),
                "source_message_id": "res-jsonb-001",
                "metadata": {"table": "12"},
            },
        )
        document = await record_booking(
            pool=pool,
            payload={
                "entity_type": "document",
                "source_message_id": "doc-jsonb-001",
                "metadata": {"page_count": 2},
            },
        )

        checks = (
            ("travel.accommodations", accom["entity_id"], "room", "1204"),
            ("travel.reservations", reservation["entity_id"], "table", "12"),
            ("travel.documents", document["entity_id"], "page_count", 2),
        )
        for table, entity_id, key, expected in checks:
            typeof = await pool.fetchval(
                f"SELECT jsonb_typeof(metadata) FROM {table} WHERE id = $1::uuid", entity_id
            )
            assert typeof == "object", f"{table} metadata was not a real jsonb object"
            row = await pool.fetchrow(
                f"SELECT metadata FROM {table} WHERE id = $1::uuid", entity_id
            )
            assert row["metadata"][key] == expected


# ---------------------------------------------------------------------------
# update_itinerary — trip-level mutations
# ---------------------------------------------------------------------------


class TestUpdateItineraryTripLevel:
    """Tests for update_itinerary applied at the trip level."""

    async def test_update_trip_status_planned_to_active(self, pool):
        """update_itinerary advances status from planned to active."""
        from butlers.tools.travel.bookings import update_itinerary

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
        from butlers.tools.travel.bookings import update_itinerary

        trip_id = await _insert_trip(pool)
        await update_itinerary(
            pool=pool,
            trip_id=trip_id,
            patch={"status": "active"},
            reason="trip commenced",
        )

        typeof = await pool.fetchval(
            "SELECT jsonb_typeof(metadata) FROM travel.trips WHERE id = $1::uuid", trip_id
        )
        assert typeof == "object"

        row = await pool.fetchrow("SELECT metadata FROM travel.trips WHERE id = $1::uuid", trip_id)
        # A real jsonb object decodes straight to a dict; a double-encoded
        # string would decode to `str`, and `.get(...)` below would raise.
        meta = row["metadata"]
        history = meta.get("change_history", [])
        assert len(history) == 1
        entry = history[0]
        assert entry["prior_values"]["status"] == "planned"
        assert entry["updated_by"] == "update_itinerary"
        assert entry["reason"] == "trip commenced"

    async def test_invalid_backward_status_transition_adds_conflict(self, pool):
        """update_itinerary rejects backward status transitions and adds conflict."""
        from butlers.tools.travel.bookings import update_itinerary

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
        from butlers.tools.travel.bookings import update_itinerary

        with pytest.raises(ValueError, match="not found"):
            await update_itinerary(
                pool=pool,
                trip_id=str(uuid.uuid4()),
                patch={"status": "active"},
            )

    async def test_update_trip_destination_field(self, pool):
        """update_itinerary updates a trip destination string field."""
        from butlers.tools.travel.bookings import update_itinerary

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
        from butlers.tools.travel.bookings import update_itinerary

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
        from butlers.tools.travel.bookings import update_itinerary

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

        typeof = await pool.fetchval(
            "SELECT jsonb_typeof(metadata) FROM travel.legs WHERE id = $1::uuid", leg_id
        )
        assert typeof == "object"

        leg_row = await pool.fetchrow(
            "SELECT metadata FROM travel.legs WHERE id = $1::uuid", leg_id
        )
        # A real jsonb object decodes straight to a dict; a double-encoded
        # string would decode to `str`, and `.get(...)` below would raise.
        meta = leg_row["metadata"]
        history = meta.get("change_history", [])
        assert len(history) == 1
        assert history[0]["prior_values"]["departure_at"] == orig_dep
        assert history[0]["reason"] == "gate change"

    async def test_entity_not_found_adds_conflict(self, pool):
        """update_itinerary adds conflict when entity_id does not exist in trip."""
        from butlers.tools.travel.bookings import update_itinerary

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
        from butlers.tools.travel.bookings import update_itinerary

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
        from butlers.tools.travel.bookings import update_itinerary

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
        from butlers.tools.travel.documents import add_document

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
        from butlers.tools.travel.documents import add_document

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
        from butlers.tools.travel.documents import add_document

        trip_id = await _insert_trip(pool)
        with pytest.raises(ValueError, match="Invalid document type"):
            await add_document(
                pool=pool,
                trip_id=trip_id,
                type="passport",  # not in allowed types
            )

    async def test_unknown_trip_raises(self, pool):
        """add_document raises ValueError when trip does not exist."""
        from butlers.tools.travel.documents import add_document

        with pytest.raises(ValueError, match="not found"):
            await add_document(
                pool=pool,
                trip_id=str(uuid.uuid4()),
                type="receipt",
            )

    async def test_attach_insurance_without_blob(self, pool):
        """add_document allows None blob_ref for metadata-only tracking."""
        from butlers.tools.travel.documents import add_document

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
        from butlers.tools.travel.documents import add_document

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
