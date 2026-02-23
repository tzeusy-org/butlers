"""Tests for roster/travel/tools/trips.py — list_trips, trip_summary, upcoming_travel."""

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
# Schema helpers
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
    type        TEXT NOT NULL CHECK (type IN ('boarding_pass', 'visa', 'insurance', 'receipt')),
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
# Data insertion helpers
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
    """Insert a trip and return its string ID."""
    today = date.today()
    if start_date is None:
        start_date = today + timedelta(days=10)
    if end_date is None:
        end_date = start_date + timedelta(days=7)
    row = await pool.fetchrow(
        """
        INSERT INTO travel.trips (name, destination, start_date, end_date, status)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
        """,
        name,
        destination,
        start_date,
        end_date,
        status,
    )
    return str(row["id"])


async def _insert_leg(
    pool,
    trip_id: str,
    *,
    leg_type: str = "flight",
    carrier: str = "UA",
    departure_city: str = "SFO",
    departure_airport_station: str = "SFO",
    arrival_city: str = "NRT",
    arrival_airport_station: str = "NRT",
    departure_at: datetime | None = None,
    arrival_at: datetime | None = None,
    seat: str | None = None,
    pnr: str | None = None,
) -> str:
    now = datetime.now(UTC)
    if departure_at is None:
        departure_at = now + timedelta(days=10)
    if arrival_at is None:
        arrival_at = departure_at + timedelta(hours=10)
    row = await pool.fetchrow(
        """
        INSERT INTO travel.legs
            (trip_id, type, carrier, departure_city, departure_airport_station,
             arrival_city, arrival_airport_station, departure_at, arrival_at, seat, pnr)
        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        RETURNING id
        """,
        trip_id,
        leg_type,
        carrier,
        departure_city,
        departure_airport_station,
        arrival_city,
        arrival_airport_station,
        departure_at,
        arrival_at,
        seat,
        pnr,
    )
    return str(row["id"])


async def _insert_accommodation(
    pool,
    trip_id: str,
    *,
    acc_type: str = "hotel",
    name: str = "Grand Hotel",
    check_in: datetime | None = None,
    check_out: datetime | None = None,
) -> str:
    now = datetime.now(UTC)
    if check_in is None:
        check_in = now + timedelta(days=11)
    if check_out is None:
        check_out = check_in + timedelta(days=6)
    row = await pool.fetchrow(
        """
        INSERT INTO travel.accommodations (trip_id, type, name, check_in, check_out)
        VALUES ($1::uuid, $2, $3, $4, $5)
        RETURNING id
        """,
        trip_id,
        acc_type,
        name,
        check_in,
        check_out,
    )
    return str(row["id"])


async def _insert_reservation(
    pool,
    trip_id: str,
    *,
    res_type: str = "restaurant",
    provider: str = "Nobu",
    res_datetime: datetime | None = None,
) -> str:
    if res_datetime is None:
        res_datetime = datetime.now(UTC) + timedelta(days=12)
    row = await pool.fetchrow(
        """
        INSERT INTO travel.reservations (trip_id, type, provider, datetime)
        VALUES ($1::uuid, $2, $3, $4)
        RETURNING id
        """,
        trip_id,
        res_type,
        provider,
        res_datetime,
    )
    return str(row["id"])


async def _insert_document(
    pool,
    trip_id: str,
    *,
    doc_type: str = "boarding_pass",
    blob_ref: str = "s3://bucket/bp.pdf",
) -> str:
    row = await pool.fetchrow(
        """
        INSERT INTO travel.documents (trip_id, type, blob_ref)
        VALUES ($1::uuid, $2, $3)
        RETURNING id
        """,
        trip_id,
        doc_type,
        blob_ref,
    )
    return str(row["id"])


# ---------------------------------------------------------------------------
# list_trips tests
# ---------------------------------------------------------------------------


class TestListTrips:
    async def test_empty_returns_zero_total(self, pool):
        """Empty table returns items=[], total=0."""
        from butlers.tools.travel.trips import list_trips

        result = await list_trips(pool)
        assert result["items"] == []
        assert result["total"] == 0
        assert result["limit"] == 20
        assert result["offset"] == 0

    async def test_returns_inserted_trip(self, pool):
        """Inserted trip appears in results."""
        from butlers.tools.travel.trips import list_trips

        trip_id = await _insert_trip(pool, name="Tokyo Adventure")
        result = await list_trips(pool)

        assert result["total"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["id"] == trip_id
        assert result["items"][0]["name"] == "Tokyo Adventure"

    async def test_filter_by_status(self, pool):
        """status filter restricts results to matching trips."""
        from butlers.tools.travel.trips import list_trips

        await _insert_trip(pool, name="Planned Trip", status="planned")
        await _insert_trip(pool, name="Completed Trip", status="completed")

        planned = await list_trips(pool, status="planned")
        assert planned["total"] == 1
        assert planned["items"][0]["name"] == "Planned Trip"

        completed = await list_trips(pool, status="completed")
        assert completed["total"] == 1
        assert completed["items"][0]["name"] == "Completed Trip"

    async def test_filter_by_from_date(self, pool):
        """from_date excludes trips starting before the bound."""
        from butlers.tools.travel.trips import list_trips

        today = date.today()
        await _insert_trip(pool, name="Near Trip", start_date=today + timedelta(days=5))
        await _insert_trip(pool, name="Far Trip", start_date=today + timedelta(days=30))

        result = await list_trips(pool, from_date=today + timedelta(days=20))
        assert result["total"] == 1
        assert result["items"][0]["name"] == "Far Trip"

    async def test_filter_by_to_date(self, pool):
        """to_date excludes trips starting after the bound."""
        from butlers.tools.travel.trips import list_trips

        today = date.today()
        await _insert_trip(pool, name="Near Trip", start_date=today + timedelta(days=3))
        await _insert_trip(pool, name="Far Trip", start_date=today + timedelta(days=40))

        result = await list_trips(pool, to_date=today + timedelta(days=10))
        assert result["total"] == 1
        assert result["items"][0]["name"] == "Near Trip"

    async def test_pagination_limit_offset(self, pool):
        """limit and offset paginate results correctly."""
        from butlers.tools.travel.trips import list_trips

        today = date.today()
        for i in range(5):
            await _insert_trip(pool, name=f"Trip {i}", start_date=today + timedelta(days=i + 1))

        page1 = await list_trips(pool, limit=2, offset=0)
        page2 = await list_trips(pool, limit=2, offset=2)

        assert len(page1["items"]) == 2
        assert len(page2["items"]) == 2
        assert page1["total"] == 5
        assert page2["total"] == 5
        # Pages must not overlap
        ids_p1 = {item["id"] for item in page1["items"]}
        ids_p2 = {item["id"] for item in page2["items"]}
        assert ids_p1.isdisjoint(ids_p2)

    async def test_invalid_status_raises(self, pool):
        """Invalid status raises ValueError."""
        from butlers.tools.travel.trips import list_trips

        with pytest.raises(ValueError, match="Unsupported status"):
            await list_trips(pool, status="unknown_status")

    async def test_accepts_string_dates(self, pool):
        """from_date and to_date accept ISO-8601 strings."""
        from butlers.tools.travel.trips import list_trips

        today = date.today()
        await _insert_trip(pool, start_date=today + timedelta(days=5))

        result = await list_trips(
            pool,
            from_date=(today + timedelta(days=1)).isoformat(),
            to_date=(today + timedelta(days=10)).isoformat(),
        )
        assert result["total"] == 1

    async def test_default_sort_is_start_date_desc(self, pool):
        """Results are sorted by start_date DESC by default."""
        from butlers.tools.travel.trips import list_trips

        today = date.today()
        await _insert_trip(pool, name="Earlier", start_date=today + timedelta(days=5))
        await _insert_trip(pool, name="Later", start_date=today + timedelta(days=20))

        result = await list_trips(pool)
        assert result["items"][0]["name"] == "Later"
        assert result["items"][1]["name"] == "Earlier"

    async def test_return_shape(self, pool):
        """list_trips always returns dict with expected top-level keys."""
        from butlers.tools.travel.trips import list_trips

        result = await list_trips(pool)
        assert set(result.keys()) == {"items", "total", "limit", "offset"}


# ---------------------------------------------------------------------------
# trip_summary tests
# ---------------------------------------------------------------------------


class TestTripSummary:
    async def test_returns_trip_with_all_entities(self, pool):
        """trip_summary returns trip plus all linked legs, accommodations, reservations."""
        from butlers.tools.travel.trips import trip_summary

        trip_id = await _insert_trip(pool, name="Full Trip")
        leg_id = await _insert_leg(pool, trip_id)
        acc_id = await _insert_accommodation(pool, trip_id)
        res_id = await _insert_reservation(pool, trip_id)
        doc_id = await _insert_document(pool, trip_id)

        result = await trip_summary(pool, trip_id)

        assert result["trip"]["id"] == trip_id
        assert len(result["legs"]) == 1
        assert result["legs"][0]["id"] == leg_id
        assert len(result["accommodations"]) == 1
        assert result["accommodations"][0]["id"] == acc_id
        assert len(result["reservations"]) == 1
        assert result["reservations"][0]["id"] == res_id
        assert len(result["documents"]) == 1
        assert result["documents"][0]["id"] == doc_id

    async def test_raises_on_missing_trip(self, pool):
        """trip_summary raises ValueError for non-existent trip_id."""
        from butlers.tools.travel.trips import trip_summary

        with pytest.raises(ValueError, match="Trip not found"):
            await trip_summary(pool, str(uuid.uuid4()))

    async def test_include_documents_false_omits_documents(self, pool):
        """include_documents=False returns empty documents list."""
        from butlers.tools.travel.trips import trip_summary

        trip_id = await _insert_trip(pool)
        await _insert_document(pool, trip_id)

        result = await trip_summary(pool, trip_id, include_documents=False)
        assert result["documents"] == []

    async def test_include_timeline_false_omits_timeline(self, pool):
        """include_timeline=False returns empty timeline list."""
        from butlers.tools.travel.trips import trip_summary

        trip_id = await _insert_trip(pool)
        await _insert_leg(pool, trip_id)

        result = await trip_summary(pool, trip_id, include_timeline=False)
        assert result["timeline"] == []

    async def test_timeline_includes_all_entity_types(self, pool):
        """Timeline is built from legs, accommodations, and reservations."""
        from butlers.tools.travel.trips import trip_summary

        trip_id = await _insert_trip(pool)
        await _insert_leg(pool, trip_id)
        await _insert_accommodation(pool, trip_id)
        await _insert_reservation(pool, trip_id)

        result = await trip_summary(pool, trip_id, include_timeline=True)

        entity_types = {e["entity_type"] for e in result["timeline"]}
        assert "leg" in entity_types
        assert "accommodation" in entity_types
        assert "reservation" in entity_types

    async def test_timeline_is_chronological(self, pool):
        """Timeline entries are sorted chronologically by sort_key."""
        from butlers.tools.travel.trips import trip_summary

        now = datetime.now(UTC)
        trip_id = await _insert_trip(pool)

        # Leg departs at T+5d, accommodation check_in at T+6d, reservation at T+7d
        await _insert_leg(pool, trip_id, departure_at=now + timedelta(days=5))
        await _insert_accommodation(pool, trip_id, check_in=now + timedelta(days=6))
        await _insert_reservation(pool, trip_id, res_datetime=now + timedelta(days=7))

        result = await trip_summary(pool, trip_id, include_timeline=True)

        timeline = result["timeline"]
        assert len(timeline) == 3
        assert timeline[0]["entity_type"] == "leg"
        assert timeline[1]["entity_type"] == "accommodation"
        assert timeline[2]["entity_type"] == "reservation"

    async def test_alerts_missing_boarding_pass(self, pool):
        """Alert is raised when flight leg exists but no boarding pass document."""
        from butlers.tools.travel.trips import trip_summary

        trip_id = await _insert_trip(pool)
        await _insert_leg(pool, trip_id, leg_type="flight")

        result = await trip_summary(pool, trip_id)
        alert_types = [a["type"] for a in result["alerts"]]
        assert "missing_boarding_pass" in alert_types

    async def test_no_boarding_pass_alert_when_attached(self, pool):
        """No missing_boarding_pass alert when boarding pass document is present."""
        from butlers.tools.travel.trips import trip_summary

        trip_id = await _insert_trip(pool)
        await _insert_leg(pool, trip_id, leg_type="flight")
        await _insert_document(pool, trip_id, doc_type="boarding_pass")

        result = await trip_summary(pool, trip_id)
        alert_types = [a["type"] for a in result["alerts"]]
        assert "missing_boarding_pass" not in alert_types

    async def test_unassigned_seat_alert(self, pool):
        """Alert is raised for flight leg with no seat assigned."""
        from butlers.tools.travel.trips import trip_summary

        trip_id = await _insert_trip(pool)
        await _insert_leg(pool, trip_id, leg_type="flight", seat=None)

        result = await trip_summary(pool, trip_id)
        alert_types = [a["type"] for a in result["alerts"]]
        assert "unassigned_seat" in alert_types

    async def test_no_seat_alert_when_seat_assigned(self, pool):
        """No unassigned_seat alert when a seat is assigned to the flight."""
        from butlers.tools.travel.trips import trip_summary

        trip_id = await _insert_trip(pool)
        await _insert_leg(pool, trip_id, leg_type="flight", seat="14A")

        result = await trip_summary(pool, trip_id)
        alert_types = [a["type"] for a in result["alerts"]]
        assert "unassigned_seat" not in alert_types

    async def test_return_shape(self, pool):
        """trip_summary always returns dict with expected top-level keys."""
        from butlers.tools.travel.trips import trip_summary

        trip_id = await _insert_trip(pool)
        result = await trip_summary(pool, trip_id)

        assert set(result.keys()) == {
            "trip",
            "legs",
            "accommodations",
            "reservations",
            "documents",
            "timeline",
            "alerts",
        }

    async def test_empty_trip_no_alerts(self, pool):
        """A trip with no legs has no flight-related alerts."""
        from butlers.tools.travel.trips import trip_summary

        trip_id = await _insert_trip(pool)
        result = await trip_summary(pool, trip_id)
        assert result["alerts"] == []


# ---------------------------------------------------------------------------
# upcoming_travel tests
# ---------------------------------------------------------------------------


class TestUpcomingTravel:
    async def test_empty_when_no_trips(self, pool):
        """Returns empty upcoming_trips and actions when no trips exist."""
        from butlers.tools.travel.trips import upcoming_travel

        result = await upcoming_travel(pool)
        assert result["upcoming_trips"] == []
        assert result["actions"] == []
        assert "window_start" in result
        assert "window_end" in result

    async def test_finds_trips_within_window(self, pool):
        """Returns trips with start_date within the window."""
        from butlers.tools.travel.trips import upcoming_travel

        today = date.today()
        # Trip starts in 5 days — within default 14-day window
        await _insert_trip(pool, name="Upcoming Trip", start_date=today + timedelta(days=5))

        result = await upcoming_travel(pool, within_days=14)
        assert len(result["upcoming_trips"]) == 1
        assert result["upcoming_trips"][0]["trip"]["name"] == "Upcoming Trip"

    async def test_excludes_trips_outside_window(self, pool):
        """Trips starting beyond the window are excluded."""
        from butlers.tools.travel.trips import upcoming_travel

        today = date.today()
        await _insert_trip(pool, name="Far Future Trip", start_date=today + timedelta(days=30))

        result = await upcoming_travel(pool, within_days=14)
        assert result["upcoming_trips"] == []

    async def test_excludes_completed_and_cancelled_trips(self, pool):
        """Completed and cancelled trips are not returned."""
        from butlers.tools.travel.trips import upcoming_travel

        today = date.today()
        await _insert_trip(
            pool,
            name="Completed Trip",
            start_date=today + timedelta(days=3),
            status="completed",
        )
        await _insert_trip(
            pool,
            name="Cancelled Trip",
            start_date=today + timedelta(days=5),
            status="cancelled",
        )

        result = await upcoming_travel(pool, within_days=14)
        assert result["upcoming_trips"] == []

    async def test_days_until_departure_computed(self, pool):
        """days_until_departure is correctly computed from start_date."""
        from butlers.tools.travel.trips import upcoming_travel

        today = date.today()
        await _insert_trip(pool, name="5 Days Away", start_date=today + timedelta(days=5))

        result = await upcoming_travel(pool, within_days=14)
        assert len(result["upcoming_trips"]) == 1
        assert result["upcoming_trips"][0]["days_until_departure"] == 5

    async def test_includes_pretrip_actions_for_missing_boarding_pass(self, pool):
        """include_pretrip_actions=True surfaces missing boarding pass action."""
        from butlers.tools.travel.trips import upcoming_travel

        today = date.today()
        trip_id = await _insert_trip(pool, name="Flight Trip", start_date=today + timedelta(days=3))
        await _insert_leg(pool, trip_id, leg_type="flight")

        result = await upcoming_travel(pool, within_days=14, include_pretrip_actions=True)
        action_types = [a["type"] for a in result["actions"]]
        assert "missing_boarding_pass" in action_types

    async def test_no_actions_when_include_pretrip_false(self, pool):
        """include_pretrip_actions=False returns empty actions list."""
        from butlers.tools.travel.trips import upcoming_travel

        today = date.today()
        trip_id = await _insert_trip(pool, start_date=today + timedelta(days=3))
        await _insert_leg(pool, trip_id, leg_type="flight")

        result = await upcoming_travel(pool, within_days=14, include_pretrip_actions=False)
        assert result["actions"] == []

    async def test_actions_have_urgency_rank(self, pool):
        """Actions have urgency_rank field assigned."""
        from butlers.tools.travel.trips import upcoming_travel

        today = date.today()
        trip_id = await _insert_trip(pool, start_date=today + timedelta(days=3))
        await _insert_leg(pool, trip_id, leg_type="flight")

        result = await upcoming_travel(pool, within_days=14, include_pretrip_actions=True)
        for action in result["actions"]:
            assert "urgency_rank" in action
            assert isinstance(action["urgency_rank"], int)

    async def test_high_severity_ranked_before_low(self, pool):
        """High severity actions are ranked before low severity actions."""
        from butlers.tools.travel.trips import upcoming_travel

        today = date.today()
        trip_id = await _insert_trip(pool, start_date=today + timedelta(days=3))
        # Flight with no seat (low) and no boarding pass (high)
        await _insert_leg(pool, trip_id, leg_type="flight", seat=None)

        result = await upcoming_travel(pool, within_days=14, include_pretrip_actions=True)
        actions = result["actions"]
        high_actions = [a for a in actions if a["severity"] == "high"]
        low_actions = [a for a in actions if a["severity"] == "low"]
        if high_actions and low_actions:
            assert min(a["urgency_rank"] for a in high_actions) < min(
                a["urgency_rank"] for a in low_actions
            )

    async def test_window_dates_in_result(self, pool):
        """window_start and window_end reflect today and today+within_days."""
        from butlers.tools.travel.trips import upcoming_travel

        today = date.today()
        result = await upcoming_travel(pool, within_days=7)
        assert result["window_start"] == today.isoformat()
        assert result["window_end"] == (today + timedelta(days=7)).isoformat()

    async def test_return_shape(self, pool):
        """upcoming_travel always returns dict with expected top-level keys."""
        from butlers.tools.travel.trips import upcoming_travel

        result = await upcoming_travel(pool)
        assert set(result.keys()) == {"upcoming_trips", "actions", "window_start", "window_end"}

    async def test_legs_included_in_upcoming_trips(self, pool):
        """Legs are included in each upcoming_trip entry."""
        from butlers.tools.travel.trips import upcoming_travel

        today = date.today()
        trip_id = await _insert_trip(pool, start_date=today + timedelta(days=3))
        leg_id = await _insert_leg(pool, trip_id)

        result = await upcoming_travel(pool, within_days=14)
        assert len(result["upcoming_trips"]) == 1
        trip_entry = result["upcoming_trips"][0]
        assert len(trip_entry["legs"]) == 1
        assert trip_entry["legs"][0]["id"] == leg_id

    async def test_action_includes_trip_name_and_id(self, pool):
        """Actions include trip_id and trip_name for context."""
        from butlers.tools.travel.trips import upcoming_travel

        today = date.today()
        trip_id = await _insert_trip(pool, name="My Vacation", start_date=today + timedelta(days=3))
        await _insert_leg(pool, trip_id, leg_type="flight")

        result = await upcoming_travel(pool, within_days=14, include_pretrip_actions=True)
        actions_for_trip = [a for a in result["actions"] if a["trip_id"] == trip_id]
        assert len(actions_for_trip) > 0
        assert actions_for_trip[0]["trip_name"] == "My Vacation"


# ---------------------------------------------------------------------------
# _helpers unit tests (pure Python — no DB required)
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_build_timeline_chronological_order(self):
        """_build_timeline returns entries in chronological order."""
        from butlers.tools.travel._helpers import _build_timeline

        now = datetime.now(UTC)
        legs = [
            {"id": "leg-1", "departure_at": (now + timedelta(days=1)).isoformat(), "type": "flight"}
        ]
        accommodations = [
            {"id": "acc-1", "check_in": (now + timedelta(days=2)).isoformat(), "type": "hotel"}
        ]
        reservations = [
            {
                "id": "res-1",
                "datetime": (now + timedelta(days=3)).isoformat(),
                "type": "restaurant",
            }
        ]

        timeline = _build_timeline(legs, accommodations, reservations)

        assert len(timeline) == 3
        assert timeline[0]["entity_type"] == "leg"
        assert timeline[1]["entity_type"] == "accommodation"
        assert timeline[2]["entity_type"] == "reservation"

    def test_build_timeline_empty_inputs(self):
        """_build_timeline with empty inputs returns empty list."""
        from butlers.tools.travel._helpers import _build_timeline

        assert _build_timeline([], [], []) == []

    def test_build_timeline_handles_none_timestamps(self):
        """_build_timeline places entities with None timestamps at end."""
        from butlers.tools.travel._helpers import _build_timeline

        now = datetime.now(UTC)
        legs = [
            {
                "id": "leg-1",
                "departure_at": (now + timedelta(days=1)).isoformat(),
                "type": "flight",
            }
        ]
        accommodations = [{"id": "acc-no-time", "check_in": None, "type": "hotel", "name": "Hotel"}]

        timeline = _build_timeline(legs, accommodations, [])

        # leg with timestamp comes first, accommodation with None timestamp last
        assert timeline[0]["entity_id"] == "leg-1"
        assert timeline[1]["entity_id"] == "acc-no-time"

    def test_build_timeline_entity_type_labels(self):
        """_build_timeline sets correct entity_type labels."""
        from butlers.tools.travel._helpers import _build_timeline

        now = datetime.now(UTC)
        legs = [
            {"id": "l1", "departure_at": (now + timedelta(hours=1)).isoformat(), "type": "train"}
        ]
        accommodations = [
            {"id": "a1", "check_in": (now + timedelta(hours=2)).isoformat(), "type": "airbnb"}
        ]
        reservations = [
            {"id": "r1", "datetime": (now + timedelta(hours=3)).isoformat(), "type": "car_rental"}
        ]

        timeline = _build_timeline(legs, accommodations, reservations)

        types = [e["entity_type"] for e in timeline]
        assert types == ["leg", "accommodation", "reservation"]

    def test_build_timeline_summary_content(self):
        """_build_timeline includes a non-empty summary for each entry."""
        from butlers.tools.travel._helpers import _build_timeline

        now = datetime.now(UTC)
        legs = [
            {
                "id": "l1",
                "departure_at": (now + timedelta(hours=1)).isoformat(),
                "type": "flight",
                "carrier": "UA",
                "departure_city": "SFO",
                "arrival_city": "NRT",
            }
        ]

        timeline = _build_timeline(legs, [], [])
        assert len(timeline) == 1
        assert len(timeline[0]["summary"]) > 0
        assert "SFO" in timeline[0]["summary"]
