"""Tests for Travel butler scheduled job handlers."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from butlers.testing.clock import FakeClock

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

# Single time source for the whole module. Fixtures build rows via `_today()` /
# `_utcnow()`, and the autouse `_freeze_travel_clock` fixture pins the job
# module's own `datetime.now()` to the same anchors. The jobs compute day-deltas
# (`days_until_departure`, `days_until_expiry`) from *their* clock at run time;
# if the fixture-build read and the job's read straddle a UTC-midnight boundary
# they differ by a day and date-anchored assertions flake (e.g.
# ``assert days_until_departure == 5`` failing 4 or 6, or an
# ``expiry_date=_today()`` doc landing at -1 days and dropping out of the scan).
# Sharing one frozen UTC clock eliminates the off-by-one regardless of wall time
# or the process-local timezone. In particular, do not pair ``date.today()``
# (local date) with ``datetime.now(UTC)``: those dates differ for part of every
# day outside UTC.
_NOW = datetime.now(UTC)


def _today() -> date:
    return _NOW.date()


def _utcnow() -> datetime:
    return _NOW


class _RealInstanceCheck(type):
    """Metaclass making ``isinstance(x, Frozen)`` match the real base class.

    The job isinstance-checks raw ``date``/``datetime`` values read straight from
    the DB against the module-level ``date``/``datetime`` names we monkeypatch
    below. A plain subclass would make those checks return False for real
    (non-subclass) DB values and silently drop rows, so route the instance check
    back to the true base (the next class in the MRO).
    """

    def __instancecheck__(cls, obj: object) -> bool:
        return isinstance(obj, cls.__mro__[1])


class _FrozenDate(date, metaclass=_RealInstanceCheck):
    @classmethod
    def today(cls) -> date:
        return _NOW.date()


class _FrozenDateTime(datetime, metaclass=_RealInstanceCheck):
    @classmethod
    def now(cls, tz=None) -> datetime:
        return _NOW


@pytest.mark.parametrize(
    "frozen_utc",
    [
        datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        datetime(2026, 1, 1, 23, 55, tzinfo=UTC),
    ],
)
def test_frozen_fixture_date_uses_utc_instant_at_day_boundaries(
    monkeypatch, frozen_utc: datetime
) -> None:
    """Fixture dates stay anchored to UTC on both sides of UTC midnight."""
    monkeypatch.setitem(globals(), "_NOW", frozen_utc)

    assert _today() == frozen_utc.date()
    assert _FrozenDate.today() == frozen_utc.date()


@pytest.fixture(autouse=True)
def _freeze_travel_clock(monkeypatch):
    """Pin the travel job module's clock to the module-import anchors."""
    import sys

    # The roster job module is registered in sys.modules under this dotted name
    # by the _roster_loader (conftest preloads it); the intermediate
    # ``butlers.jobs._roster`` is not a real package, so ``from ... import`` on it
    # fails — reach the module object via sys.modules, loading it if needed.
    _key = "butlers.jobs._roster.travel_jobs"
    if _key not in sys.modules:
        from butlers.jobs._roster_loader import load_roster_jobs

        load_roster_jobs("travel")
    _jobs_mod = sys.modules[_key]

    monkeypatch.setattr(_jobs_mod, "date", _FrozenDate)
    monkeypatch.setattr(_jobs_mod, "datetime", _FrozenDateTime)


# ---------------------------------------------------------------------------
# Database: the real core + travel chains (core owns the insight tables)
# ---------------------------------------------------------------------------

# The db factory's pools live on the session loop, so DB tests run there too.
session_loop = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def travel_pool(db):
    """A private clone migrated with the core and travel chains."""
    return await db("core", "travel", schemas={"travel": "travel"}, schema="travel")


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


@session_loop
async def test_upcoming_travel_check_no_trips(travel_pool):
    """No-op: returns zeros when no upcoming trips exist."""
    from butlers.jobs._roster.travel_jobs import run_upcoming_travel_check

    result = await run_upcoming_travel_check(travel_pool)

    assert result["trips_found"] == 0
    assert result["actions_found"] == 0
    assert result["pretrip_actions"] == []


@session_loop
async def test_upcoming_travel_check_no_trips_in_window(travel_pool):
    """Trips starting beyond 7 days are excluded."""
    from butlers.jobs._roster.travel_jobs import run_upcoming_travel_check

    # Trip starting in 10 days — outside the 7-day window
    await _insert_trip(travel_pool, start_date=_today() + timedelta(days=10))

    result = await run_upcoming_travel_check(travel_pool)

    assert result["trips_found"] == 0
    assert result["actions_found"] == 0


@session_loop
async def test_upcoming_travel_check_excludes_completed(travel_pool):
    """Completed or cancelled trips are excluded."""
    from butlers.jobs._roster.travel_jobs import run_upcoming_travel_check

    await _insert_trip(travel_pool, start_date=_today() + timedelta(days=2), status="completed")
    await _insert_trip(travel_pool, start_date=_today() + timedelta(days=2), status="cancelled")

    result = await run_upcoming_travel_check(travel_pool)

    assert result["trips_found"] == 0


@session_loop
async def test_upcoming_travel_check_trip_found(travel_pool):
    """Planned trip within 7 days is detected."""
    from butlers.jobs._roster.travel_jobs import run_upcoming_travel_check

    await _insert_trip(travel_pool, name="Tokyo Trip", start_date=_today() + timedelta(days=4))

    result = await run_upcoming_travel_check(travel_pool)

    assert result["trips_found"] == 1


@session_loop
async def test_upcoming_travel_check_missing_boarding_pass(travel_pool):
    """Missing boarding pass is surfaced for flight trips."""
    from butlers.jobs._roster.travel_jobs import run_upcoming_travel_check

    trip_id = await _insert_trip(
        travel_pool, name="Paris Trip", start_date=_today() + timedelta(days=2)
    )
    # Add a flight leg, but no boarding pass document
    await _insert_leg(travel_pool, trip_id=trip_id, leg_type="flight")

    result = await run_upcoming_travel_check(travel_pool)

    assert result["trips_found"] == 1
    assert result["actions_found"] >= 1
    action_types = [a["type"] for a in result["pretrip_actions"]]
    assert "missing_boarding_pass" in action_types


@session_loop
async def test_upcoming_travel_check_no_missing_boarding_pass_when_attached(
    travel_pool,
):
    """Boarding pass action is not raised when document is already attached."""
    from butlers.jobs._roster.travel_jobs import run_upcoming_travel_check

    trip_id = await _insert_trip(travel_pool, start_date=_today() + timedelta(days=2))
    await _insert_leg(travel_pool, trip_id=trip_id, leg_type="flight")
    # Attach a boarding pass — action should NOT be surfaced
    await _insert_document(travel_pool, trip_id=trip_id, doc_type="boarding_pass")

    result = await run_upcoming_travel_check(travel_pool)

    action_types = [a["type"] for a in result["pretrip_actions"]]
    assert "missing_boarding_pass" not in action_types


@session_loop
async def test_upcoming_travel_check_unassigned_seat(travel_pool):
    """Unassigned seat action is surfaced when flight leg has no seat."""
    from butlers.jobs._roster.travel_jobs import run_upcoming_travel_check

    trip_id = await _insert_trip(travel_pool, start_date=_today() + timedelta(days=3))
    # Flight leg with no seat
    await _insert_leg(travel_pool, trip_id=trip_id, leg_type="flight", seat=None)

    result = await run_upcoming_travel_check(travel_pool)

    action_types = [a["type"] for a in result["pretrip_actions"]]
    assert "unassigned_seat" in action_types


@session_loop
async def test_upcoming_travel_check_no_unassigned_seat_when_assigned(travel_pool):
    """Unassigned seat action is not raised when a seat is assigned."""
    from butlers.jobs._roster.travel_jobs import run_upcoming_travel_check

    trip_id = await _insert_trip(travel_pool, start_date=_today() + timedelta(days=3))
    # Flight leg WITH seat assigned
    await _insert_leg(travel_pool, trip_id=trip_id, leg_type="flight", seat="14A")
    await _insert_document(travel_pool, trip_id=trip_id, doc_type="boarding_pass")

    result = await run_upcoming_travel_check(travel_pool)

    action_types = [a["type"] for a in result["pretrip_actions"]]
    assert "unassigned_seat" not in action_types


@session_loop
async def test_upcoming_travel_check_check_in_pending(travel_pool):
    """Check-in pending action is surfaced for flights departing within 24h."""
    from butlers.jobs._roster.travel_jobs import run_upcoming_travel_check

    # Trip departing today (start_date = today)
    trip_id = await _insert_trip(travel_pool, start_date=_today())
    # Flight departing in 12 hours — within 24h check-in window
    departure_soon = _utcnow() + timedelta(hours=12)
    arrival_soon = departure_soon + timedelta(hours=8)
    await _insert_leg(
        travel_pool,
        trip_id=trip_id,
        leg_type="flight",
        departure_at=departure_soon,
        arrival_at=arrival_soon,
    )

    result = await run_upcoming_travel_check(travel_pool)

    action_types = [a["type"] for a in result["pretrip_actions"]]
    assert "check_in_pending" in action_types


@session_loop
async def test_upcoming_travel_check_no_check_in_for_future_flight(travel_pool):
    """Check-in pending action is NOT raised for flights departing beyond 24h."""
    from butlers.jobs._roster.travel_jobs import run_upcoming_travel_check

    trip_id = await _insert_trip(travel_pool, start_date=_today() + timedelta(days=3))
    # Flight departing in 3 days — outside 24h check-in window
    departure_future = _utcnow() + timedelta(days=3)
    arrival_future = departure_future + timedelta(hours=8)
    await _insert_leg(
        travel_pool,
        trip_id=trip_id,
        leg_type="flight",
        departure_at=departure_future,
        arrival_at=arrival_future,
        seat="12B",
    )
    await _insert_document(travel_pool, trip_id=trip_id, doc_type="boarding_pass")

    result = await run_upcoming_travel_check(travel_pool)

    action_types = [a["type"] for a in result["pretrip_actions"]]
    assert "check_in_pending" not in action_types


@session_loop
async def test_upcoming_travel_check_non_flight_no_boarding_pass_check(travel_pool):
    """Trips with only non-flight legs do not generate a boarding pass action."""
    from butlers.jobs._roster.travel_jobs import run_upcoming_travel_check

    trip_id = await _insert_trip(travel_pool, start_date=_today() + timedelta(days=2))
    # Train leg — no boarding pass required
    await _insert_leg(travel_pool, trip_id=trip_id, leg_type="train")

    result = await run_upcoming_travel_check(travel_pool)

    action_types = [a["type"] for a in result["pretrip_actions"]]
    assert "missing_boarding_pass" not in action_types


@session_loop
async def test_upcoming_travel_check_multiple_trips(travel_pool):
    """Multiple upcoming trips are all scanned."""
    from butlers.jobs._roster.travel_jobs import run_upcoming_travel_check

    for i in range(3):
        trip_id = await _insert_trip(
            travel_pool,
            name=f"Trip {i}",
            start_date=_today() + timedelta(days=i + 1),
        )
        await _insert_leg(travel_pool, trip_id=trip_id, leg_type="flight")

    result = await run_upcoming_travel_check(travel_pool)

    assert result["trips_found"] == 3


@session_loop
async def test_upcoming_travel_check_action_includes_trip_context(travel_pool):
    """Pretrip actions include trip_id, trip_name, and days_until_departure."""
    from butlers.jobs._roster.travel_jobs import run_upcoming_travel_check

    trip_id = await _insert_trip(
        travel_pool, name="Barcelona Trip", start_date=_today() + timedelta(days=5)
    )
    await _insert_leg(travel_pool, trip_id=trip_id, leg_type="flight")

    result = await run_upcoming_travel_check(travel_pool)

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


@session_loop
async def test_trip_document_expiry_no_documents(travel_pool):
    """No-op: returns zeros when no documents exist."""
    from butlers.jobs._roster.travel_jobs import run_trip_document_expiry

    result = await run_trip_document_expiry(travel_pool)

    assert result["documents_scanned"] == 0
    assert result["urgent"] == 0
    assert result["warning"] == 0
    assert result["informational"] == 0
    assert result["expiring_documents"] == []


@session_loop
async def test_trip_document_expiry_no_expiring_within_90_days(travel_pool):
    """Documents expiring beyond 90 days are excluded."""
    from butlers.jobs._roster.travel_jobs import run_trip_document_expiry

    trip_id = await _insert_trip(travel_pool)
    await _insert_document(
        travel_pool,
        trip_id=trip_id,
        doc_type="visa",
        expiry_date=_today() + timedelta(days=120),
    )

    result = await run_trip_document_expiry(travel_pool)

    assert result["documents_scanned"] == 0


@session_loop
async def test_trip_document_expiry_urgent_30_days(travel_pool):
    """Document expiring within 30 days is classified as urgent."""
    from butlers.jobs._roster.travel_jobs import run_trip_document_expiry

    trip_id = await _insert_trip(travel_pool)
    await _insert_document(
        travel_pool,
        trip_id=trip_id,
        doc_type="visa",
        expiry_date=_today() + timedelta(days=20),
    )

    result = await run_trip_document_expiry(travel_pool)

    assert result["documents_scanned"] == 1
    assert result["urgent"] == 1
    assert result["warning"] == 0
    assert result["informational"] == 0
    assert result["expiring_documents"][0]["urgency"] == "urgent"


@session_loop
async def test_trip_document_expiry_warning_60_days(travel_pool):
    """Document expiring within 31-60 days is classified as warning."""
    from butlers.jobs._roster.travel_jobs import run_trip_document_expiry

    trip_id = await _insert_trip(travel_pool)
    await _insert_document(
        travel_pool,
        trip_id=trip_id,
        doc_type="insurance",
        expiry_date=_today() + timedelta(days=45),
    )

    result = await run_trip_document_expiry(travel_pool)

    assert result["documents_scanned"] == 1
    assert result["urgent"] == 0
    assert result["warning"] == 1
    assert result["informational"] == 0
    assert result["expiring_documents"][0]["urgency"] == "warning"


@session_loop
async def test_trip_document_expiry_informational_90_days(travel_pool):
    """Document expiring within 61-90 days is classified as informational."""
    from butlers.jobs._roster.travel_jobs import run_trip_document_expiry

    trip_id = await _insert_trip(travel_pool)
    await _insert_document(
        travel_pool,
        trip_id=trip_id,
        doc_type="visa",
        expiry_date=_today() + timedelta(days=75),
    )

    result = await run_trip_document_expiry(travel_pool)

    assert result["documents_scanned"] == 1
    assert result["urgent"] == 0
    assert result["warning"] == 0
    assert result["informational"] == 1
    assert result["expiring_documents"][0]["urgency"] == "informational"


@session_loop
async def test_trip_document_expiry_graduated_urgency(travel_pool):
    """Multiple documents at different expiry windows are classified correctly."""
    from butlers.jobs._roster.travel_jobs import run_trip_document_expiry

    trip_id = await _insert_trip(travel_pool)
    # Urgent: expires in 15 days
    await _insert_document(
        travel_pool, trip_id=trip_id, doc_type="visa", expiry_date=_today() + timedelta(days=15)
    )
    # Warning: expires in 50 days
    await _insert_document(
        travel_pool,
        trip_id=trip_id,
        doc_type="insurance",
        expiry_date=_today() + timedelta(days=50),
    )
    # Informational: expires in 80 days
    await _insert_document(
        travel_pool, trip_id=trip_id, doc_type="visa", expiry_date=_today() + timedelta(days=80)
    )

    result = await run_trip_document_expiry(travel_pool)

    assert result["documents_scanned"] == 3
    assert result["urgent"] == 1
    assert result["warning"] == 1
    assert result["informational"] == 1


@session_loop
async def test_trip_document_expiry_excludes_receipt_type(travel_pool):
    """Receipt documents are excluded from expiry scans."""
    from butlers.jobs._roster.travel_jobs import run_trip_document_expiry

    trip_id = await _insert_trip(travel_pool)
    # Receipt with expiry date — should be excluded
    await _insert_document(
        travel_pool,
        trip_id=trip_id,
        doc_type="receipt",
        expiry_date=_today() + timedelta(days=10),
    )

    result = await run_trip_document_expiry(travel_pool)

    assert result["documents_scanned"] == 0


@session_loop
async def test_trip_document_expiry_excludes_null_expiry(travel_pool):
    """Documents without an expiry_date are excluded."""
    from butlers.jobs._roster.travel_jobs import run_trip_document_expiry

    trip_id = await _insert_trip(travel_pool)
    # Visa with no expiry date
    await _insert_document(travel_pool, trip_id=trip_id, doc_type="visa", expiry_date=None)

    result = await run_trip_document_expiry(travel_pool)

    assert result["documents_scanned"] == 0


@session_loop
async def test_trip_document_expiry_document_has_linked_trip_context(travel_pool):
    """Expiring document result includes trip_id, trip_name, and document_type."""
    from butlers.jobs._roster.travel_jobs import run_trip_document_expiry

    trip_id = await _insert_trip(travel_pool, name="Visa Trip")
    await _insert_document(
        travel_pool,
        trip_id=trip_id,
        doc_type="visa",
        expiry_date=_today() + timedelta(days=25),
    )

    result = await run_trip_document_expiry(travel_pool)

    assert result["documents_scanned"] == 1
    doc = result["expiring_documents"][0]
    assert doc["trip_id"] == trip_id
    assert doc["trip_name"] == "Visa Trip"
    assert doc["document_type"] == "visa"
    assert "expiry_date" in doc
    assert "days_until_expiry" in doc
    assert "message" in doc


@session_loop
async def test_trip_document_expiry_sorted_by_expiry_date(travel_pool):
    """Documents are returned sorted by expiry_date ascending."""
    from butlers.jobs._roster.travel_jobs import run_trip_document_expiry

    trip_id = await _insert_trip(travel_pool)
    # Insert in reverse order to ensure sorting works
    await _insert_document(
        travel_pool, trip_id=trip_id, doc_type="visa", expiry_date=_today() + timedelta(days=80)
    )
    await _insert_document(
        travel_pool,
        trip_id=trip_id,
        doc_type="insurance",
        expiry_date=_today() + timedelta(days=20),
    )
    await _insert_document(
        travel_pool, trip_id=trip_id, doc_type="visa", expiry_date=_today() + timedelta(days=50)
    )

    result = await run_trip_document_expiry(travel_pool)

    days_list = [d["days_until_expiry"] for d in result["expiring_documents"]]
    assert days_list == sorted(days_list)


@session_loop
async def test_trip_document_expiry_expiring_today_is_urgent(travel_pool):
    """Document expiring today is classified as urgent."""
    from butlers.jobs._roster.travel_jobs import run_trip_document_expiry

    trip_id = await _insert_trip(travel_pool)
    await _insert_document(
        travel_pool,
        trip_id=trip_id,
        doc_type="insurance",
        expiry_date=_today(),
    )

    result = await run_trip_document_expiry(travel_pool)

    assert result["documents_scanned"] == 1
    assert result["urgent"] == 1
    assert result["expiring_documents"][0]["urgency"] == "urgent"


@session_loop
async def test_trip_document_expiry_insurance_included(travel_pool):
    """Insurance documents are included in expiry scans."""
    from butlers.jobs._roster.travel_jobs import run_trip_document_expiry

    trip_id = await _insert_trip(travel_pool)
    await _insert_document(
        travel_pool,
        trip_id=trip_id,
        doc_type="insurance",
        expiry_date=_today() + timedelta(days=15),
    )

    result = await run_trip_document_expiry(travel_pool)

    assert result["documents_scanned"] == 1


@session_loop
async def test_trip_document_expiry_boarding_pass_included(travel_pool):
    """Boarding pass documents are included in expiry scans."""
    from butlers.jobs._roster.travel_jobs import run_trip_document_expiry

    trip_id = await _insert_trip(travel_pool)
    await _insert_document(
        travel_pool,
        trip_id=trip_id,
        doc_type="boarding_pass",
        expiry_date=_today() + timedelta(days=5),
    )

    result = await run_trip_document_expiry(travel_pool)

    assert result["documents_scanned"] == 1


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — no data / no-op paths
# ---------------------------------------------------------------------------


@session_loop
async def test_insight_scan_no_trips_no_documents(travel_pool):
    """No-op: returns zeros when no trips or documents exist."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    result = await run_insight_scan(travel_pool)

    assert result["candidates_proposed"] == 0
    assert result["candidates_accepted"] == 0
    assert result["candidates_filtered"] == 0
    assert result["candidates_errored"] == 0
    assert result["early_exit"] is False


@session_loop
async def test_insight_scan_excludes_completed_trips(travel_pool):
    """Completed and cancelled trips do not generate pre-trip insights."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    await _insert_trip(travel_pool, start_date=_today() + timedelta(days=3), status="completed")
    await _insert_trip(travel_pool, start_date=_today() + timedelta(days=3), status="cancelled")

    result = await run_insight_scan(travel_pool)

    assert result["candidates_proposed"] == 0


@session_loop
async def test_insight_scan_excludes_trips_beyond_window(travel_pool):
    """Trips departing beyond 7 days are excluded from pre-trip insights."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    await _insert_trip(travel_pool, start_date=_today() + timedelta(days=10))

    result = await run_insight_scan(travel_pool)

    assert result["candidates_proposed"] == 0


@session_loop
async def test_insight_scan_excludes_past_trips(travel_pool):
    """Trips with departure dates in the past are excluded."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    await _insert_trip(travel_pool, start_date=_today() - timedelta(days=1))

    result = await run_insight_scan(travel_pool)

    assert result["candidates_proposed"] == 0


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — pre-trip preparation priorities
# ---------------------------------------------------------------------------


@session_loop
async def test_insight_scan_pretrip_priority_critical_1_day(travel_pool):
    """Trip departing in 1 day gets priority 92 (time-critical)."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    await _insert_trip(
        travel_pool, name="Tokyo", destination="Tokyo", start_date=_today() + timedelta(days=1)
    )

    result = await run_insight_scan(travel_pool)

    assert result["candidates_proposed"] >= 1
    assert result["candidates_accepted"] >= 1

    rows = await travel_pool.fetch(
        "SELECT priority, dedup_key, message FROM insight_candidates WHERE origin_butler = 'travel'"
    )
    pretrip_rows = [r for r in rows if "pre-trip" in r["dedup_key"]]
    assert len(pretrip_rows) == 1
    assert pretrip_rows[0]["priority"] == 92
    assert "Tokyo" in pretrip_rows[0]["message"]


@session_loop
async def test_insight_scan_pretrip_priority_urgent_3_days(travel_pool):
    """Trip departing in 3 days gets priority 78."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    await _insert_trip(
        travel_pool, name="Paris", destination="Paris", start_date=_today() + timedelta(days=3)
    )

    result = await run_insight_scan(travel_pool)

    assert result["candidates_accepted"] >= 1
    rows = await travel_pool.fetch(
        "SELECT priority FROM insight_candidates"
        " WHERE origin_butler = 'travel' AND category = 'pre-trip'"
    )
    assert rows[0]["priority"] == 78


@session_loop
async def test_insight_scan_pretrip_priority_info_7_days(travel_pool):
    """Trip departing in 5-7 days gets priority 65."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    await _insert_trip(
        travel_pool, name="London", destination="London", start_date=_today() + timedelta(days=6)
    )

    result = await run_insight_scan(travel_pool)

    assert result["candidates_accepted"] >= 1
    rows = await travel_pool.fetch(
        "SELECT priority FROM insight_candidates"
        " WHERE origin_butler = 'travel' AND category = 'pre-trip'"
    )
    assert rows[0]["priority"] == 65


@session_loop
async def test_insight_scan_pretrip_dedup_key_format(travel_pool):
    """Pre-trip dedup_key follows travel:pre-trip:{trip-id}:{departure-date} format."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    departure = _today() + timedelta(days=4)
    trip_id = await _insert_trip(travel_pool, start_date=departure)

    await run_insight_scan(travel_pool)

    rows = await travel_pool.fetch(
        "SELECT dedup_key FROM insight_candidates WHERE category = 'pre-trip'"
    )
    assert len(rows) == 1
    dedup_key = rows[0]["dedup_key"]
    assert dedup_key.startswith("travel:pre-trip:")
    assert trip_id in dedup_key
    assert departure.isoformat() in dedup_key


@session_loop
async def test_insight_scan_pretrip_message_references_destination(travel_pool):
    """Pre-trip message references the destination and pre-trip checklist."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    await _insert_trip(
        travel_pool,
        name="Bali Trip",
        destination="Bali",
        start_date=_today() + timedelta(days=5),
    )

    await run_insight_scan(travel_pool)

    rows = await travel_pool.fetch(
        "SELECT message FROM insight_candidates WHERE category = 'pre-trip'"
    )
    assert len(rows) == 1
    message = rows[0]["message"]
    assert "Bali" in message
    assert "checklist" in message.lower()


@session_loop
async def test_insight_scan_pretrip_expires_at_is_departure_date(travel_pool):
    """Pre-trip candidate expires_at is set to the departure date."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    departure = _today() + timedelta(days=4)
    await _insert_trip(travel_pool, start_date=departure)

    await run_insight_scan(travel_pool)

    rows = await travel_pool.fetch(
        "SELECT expires_at FROM insight_candidates WHERE category = 'pre-trip'"
    )
    assert len(rows) == 1
    expires_at = rows[0]["expires_at"]
    assert expires_at.date() == departure


@session_loop
async def test_insight_scan_pretrip_departing_today_has_future_expiry(
    travel_pool,
):
    """A same-day departure remains eligible after UTC midnight."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    await _insert_trip(
        travel_pool,
        start_date=_today(),
        end_date=_today() + timedelta(days=3),
    )

    result = await run_insight_scan(travel_pool)

    expires_at = await travel_pool.fetchval(
        "SELECT expires_at FROM insight_candidates WHERE category = 'pre-trip'"
    )
    assert result["candidates_errored"] == 0
    assert expires_at.date() == _today()
    assert expires_at > _utcnow()


@session_loop
async def test_insight_scan_pretrip_survives_wall_clock_crossing_utc_midnight(
    travel_pool,
    monkeypatch,
):
    """A same-day departure stays acceptable once the wall clock passes UTC midnight.

    Reproduces the CI failure on PR #3814: the scan ran at 23:58:35Z and the broker
    validated the candidate at 00:16:40Z the next day. The scan stamps ``expires_at``
    as the final instant of the departure *date*, so a broker that re-reads its own
    wall clock sees an instant already in the past and rejects the candidate.

    Both clocks are pinned to explicit instants straddling the boundary, so the
    crossing is reproduced at any wall-clock hour rather than only near midnight.
    """
    from butlers.jobs._roster.travel_jobs import run_insight_scan
    from butlers.tools.switchboard.insight import broker as insight_broker

    scan_instant = datetime(2026, 8, 23, 23, 58, 35, tzinfo=UTC)
    broker_instant = datetime(2026, 8, 24, 0, 16, 40, tzinfo=UTC)
    broker_clock = FakeClock(broker_instant)

    class _BrokerClock(datetime, metaclass=_RealInstanceCheck):
        """The broker's own clock, already on the far side of UTC midnight."""

        @classmethod
        def now(cls, tz=None) -> datetime:
            return broker_clock.now()

    # The autouse fixture already points the job module's date/datetime at the
    # frozen classes; both read ``_NOW`` at call time, so rebinding it here moves
    # the scan's clock without disturbing the broker's.
    monkeypatch.setitem(globals(), "_NOW", scan_instant)
    monkeypatch.setattr(insight_broker, "datetime", _BrokerClock)

    await _insert_trip(
        travel_pool,
        start_date=scan_instant.date(),
        end_date=scan_instant.date() + timedelta(days=3),
    )

    result = await run_insight_scan(travel_pool)

    assert result["candidates_errored"] == 0
    assert result["candidates_accepted"] == 1
    expires_at = await travel_pool.fetchval(
        "SELECT expires_at FROM insight_candidates WHERE category = 'pre-trip'"
    )
    assert expires_at == datetime(2026, 8, 23, 23, 59, 59, 999999, tzinfo=UTC)


@session_loop
async def test_insight_scan_trip_candidates_include_correlation_metadata(
    travel_pool,
    monkeypatch,
):
    """Trip-linked insight candidates carry only their stored trip/date facts."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan
    from butlers.tools.travel import health as travel_health

    monkeypatch.setattr(
        travel_health,
        "request_health_medication_snapshot",
        AsyncMock(return_value=_active_medication_snapshot()),
    )

    departure = _today() + timedelta(days=5)
    trip_id = await _insert_trip(
        travel_pool,
        start_date=departure,
        end_date=departure + timedelta(days=4),
    )
    expiry = _today() + timedelta(days=20)
    await _insert_document(travel_pool, trip_id=trip_id, doc_type="visa", expiry_date=expiry)

    await run_insight_scan(travel_pool)

    rows = await travel_pool.fetch(
        "SELECT category, metadata FROM insight_candidates WHERE category = ANY($1::text[])",
        ["pre-trip", "document-expiry", "medication-prep"],
    )
    metadata_by_category = {row["category"]: row["metadata"] for row in rows}

    assert metadata_by_category["pre-trip"] == {
        "entity_id": trip_id,
        "event_date": departure.isoformat(),
    }
    assert metadata_by_category["document-expiry"] == {
        "entity_id": trip_id,
        "event_date": expiry.isoformat(),
    }
    assert metadata_by_category["medication-prep"] == {
        "entity_id": trip_id,
        "event_date": departure.isoformat(),
    }


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — document expiry
# ---------------------------------------------------------------------------


@session_loop
async def test_insight_scan_document_expiry_30_days_priority_85(travel_pool):
    """Visa expiring within 30 days generates priority 85 insight."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    trip_id = await _insert_trip(travel_pool)
    expiry = _today() + timedelta(days=20)
    await _insert_document(travel_pool, trip_id=trip_id, doc_type="visa", expiry_date=expiry)

    result = await run_insight_scan(travel_pool)

    assert result["candidates_accepted"] >= 1
    rows = await travel_pool.fetch(
        "SELECT priority, cooldown_days FROM insight_candidates WHERE category = 'document-expiry'"
    )
    assert len(rows) == 1
    assert rows[0]["priority"] == 85
    assert rows[0]["cooldown_days"] == 3


@session_loop
async def test_insight_scan_document_expiry_60_days_priority_65(travel_pool):
    """Document expiring within 31-60 days generates priority 65 insight."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    trip_id = await _insert_trip(travel_pool)
    expiry = _today() + timedelta(days=45)
    await _insert_document(travel_pool, trip_id=trip_id, doc_type="insurance", expiry_date=expiry)

    result = await run_insight_scan(travel_pool)

    assert result["candidates_accepted"] >= 1
    rows = await travel_pool.fetch(
        "SELECT priority, cooldown_days FROM insight_candidates WHERE category = 'document-expiry'"
    )
    assert len(rows) == 1
    assert rows[0]["priority"] == 65
    assert rows[0]["cooldown_days"] == 7


@session_loop
async def test_insight_scan_document_expiry_90_days_priority_45(travel_pool):
    """Document expiring within 61-90 days generates priority 45 insight."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    trip_id = await _insert_trip(travel_pool)
    expiry = _today() + timedelta(days=75)
    await _insert_document(travel_pool, trip_id=trip_id, doc_type="visa", expiry_date=expiry)

    result = await run_insight_scan(travel_pool)

    assert result["candidates_accepted"] >= 1
    rows = await travel_pool.fetch(
        "SELECT priority, cooldown_days FROM insight_candidates WHERE category = 'document-expiry'"
    )
    assert len(rows) == 1
    assert rows[0]["priority"] == 45
    assert rows[0]["cooldown_days"] == 14


@session_loop
async def test_insight_scan_document_expiry_dedup_key_format(travel_pool):
    """Document expiry dedup_key follows travel:document-expiry:{type}:{expiry-date} format."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    trip_id = await _insert_trip(travel_pool)
    expiry = _today() + timedelta(days=25)
    await _insert_document(travel_pool, trip_id=trip_id, doc_type="visa", expiry_date=expiry)

    await run_insight_scan(travel_pool)

    rows = await travel_pool.fetch(
        "SELECT dedup_key FROM insight_candidates WHERE category = 'document-expiry'"
    )
    assert len(rows) == 1
    dedup_key = rows[0]["dedup_key"]
    assert dedup_key == f"travel:document-expiry:visa:{expiry.isoformat()}"


@session_loop
async def test_insight_scan_document_excludes_receipt_type(travel_pool):
    """Receipt documents are excluded from document expiry insight scan."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    # Use trip outside pre-trip window so only document type matters
    trip_id = await _insert_trip(travel_pool, start_date=_today() + timedelta(days=30))
    await _insert_document(
        travel_pool,
        trip_id=trip_id,
        doc_type="receipt",
        expiry_date=_today() + timedelta(days=10),
    )

    result = await run_insight_scan(travel_pool)

    doc_rows = await travel_pool.fetch(
        "SELECT id FROM insight_candidates WHERE category = 'document-expiry'"
    )
    assert len(doc_rows) == 0
    assert result["candidates_proposed"] == 0


@session_loop
async def test_insight_scan_document_excludes_beyond_90_days(travel_pool):
    """Documents expiring beyond 90 days are excluded from insight scan."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    # Use trip outside pre-trip window so only document expiry matters
    trip_id = await _insert_trip(travel_pool, start_date=_today() + timedelta(days=30))
    await _insert_document(
        travel_pool,
        trip_id=trip_id,
        doc_type="visa",
        expiry_date=_today() + timedelta(days=120),
    )

    result = await run_insight_scan(travel_pool)

    doc_rows = await travel_pool.fetch(
        "SELECT id FROM insight_candidates WHERE category = 'document-expiry'"
    )
    assert len(doc_rows) == 0
    assert result["candidates_proposed"] == 0


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — verbosity=off early exit
# ---------------------------------------------------------------------------


@session_loop
async def test_insight_scan_verbosity_off_exits_early(travel_pool):
    """When verbosity=off, first candidate returns filtered and job exits early."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    await travel_pool.execute(
        "INSERT INTO insight_settings (id, verbosity) VALUES (1, 'off') "
        "ON CONFLICT (id) DO UPDATE SET verbosity = 'off'"
    )

    await _insert_trip(travel_pool, name="Trip A", start_date=_today() + timedelta(days=2))
    await _insert_trip(travel_pool, name="Trip B", start_date=_today() + timedelta(days=4))

    result = await run_insight_scan(travel_pool)

    assert result["early_exit"] is True
    assert result["candidates_proposed"] >= 1
    assert result["candidates_accepted"] == 0
    rows = await travel_pool.fetch("SELECT id FROM insight_candidates WHERE status = 'pending'")
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — multiple candidates in one run
# ---------------------------------------------------------------------------


@session_loop
async def test_insight_scan_multiple_trips_generate_multiple_candidates(
    travel_pool,
):
    """Multiple trips within window each generate their own pre-trip candidate."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    await _insert_trip(travel_pool, name="Trip A", start_date=_today() + timedelta(days=2))
    await _insert_trip(travel_pool, name="Trip B", start_date=_today() + timedelta(days=5))

    result = await run_insight_scan(travel_pool)

    assert result["candidates_proposed"] == 2
    assert result["candidates_accepted"] == 2
    rows = await travel_pool.fetch("SELECT id FROM insight_candidates WHERE category = 'pre-trip'")
    assert len(rows) == 2


@session_loop
async def test_insight_scan_combined_pretrip_and_document_candidates(
    travel_pool,
):
    """Pre-trip and document expiry candidates coexist in one scan run."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    trip_id = await _insert_trip(travel_pool, start_date=_today() + timedelta(days=4))
    await _insert_document(
        travel_pool,
        trip_id=trip_id,
        doc_type="visa",
        expiry_date=_today() + timedelta(days=25),
    )

    result = await run_insight_scan(travel_pool)

    assert result["candidates_proposed"] == 2
    assert result["candidates_accepted"] == 2

    pretrip = await travel_pool.fetch(
        "SELECT id FROM insight_candidates WHERE category = 'pre-trip'"
    )
    doc_expiry = await travel_pool.fetch(
        "SELECT id FROM insight_candidates WHERE category = 'document-expiry'"
    )
    assert len(pretrip) == 1
    assert len(doc_expiry) == 1


@session_loop
async def test_insight_scan_result_keys_present(travel_pool):
    """Result dict always contains all expected keys."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan

    result = await run_insight_scan(travel_pool)

    assert "candidates_proposed" in result
    assert "candidates_accepted" in result
    assert "candidates_filtered" in result
    assert "candidates_errored" in result
    assert "early_exit" in result


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — medication preparation via Health MCP
# ---------------------------------------------------------------------------


def _active_medication_snapshot() -> dict:
    return {
        "schema_version": "health.medication-travel.v1",
        "status": "ok",
        "medications": [
            {
                "name": "Private medication name",
                "dosage": "Private dosage",
                "frequency": "Private frequency",
                "schedule": ["Private schedule"],
            }
        ],
        "error": None,
    }


@session_loop
async def test_insight_scan_medication_prep_uses_health_consumer_once_for_eligible_trips(
    travel_pool,
    monkeypatch,
):
    """Eligible trips share one privacy-minimized Health snapshot request per scan."""
    from butlers.core.tool_call_capture import (
        reset_current_switchboard_client,
        set_current_switchboard_client,
    )
    from butlers.jobs._roster.travel_jobs import run_insight_scan
    from butlers.tools.travel import health as travel_health

    client = object()
    snapshot_reader = AsyncMock(return_value=_active_medication_snapshot())
    monkeypatch.setattr(travel_health, "request_health_medication_snapshot", snapshot_reader)

    near_trip_id = await _insert_trip(
        travel_pool,
        name="Near trip",
        destination="Tokyo",
        start_date=_today() + timedelta(days=5),
        end_date=_today() + timedelta(days=12),
    )
    later_trip_id = await _insert_trip(
        travel_pool,
        name="Later trip",
        destination="Lisbon",
        start_date=_today() + timedelta(days=10),
        end_date=_today() + timedelta(days=15),
    )

    token = set_current_switchboard_client(client)
    try:
        result = await run_insight_scan(travel_pool)
    finally:
        reset_current_switchboard_client(token)

    snapshot_reader.assert_awaited_once_with(travel_pool, client)
    rows = await travel_pool.fetch(
        """
        SELECT priority, dedup_key, message, expires_at
        FROM insight_candidates
        WHERE category = 'medication-prep'
        ORDER BY priority DESC
        """
    )

    assert result["candidates_accepted"] == 3  # one pre-trip plus two medication reminders
    assert [row["priority"] for row in rows] == [75, 55]
    assert {row["dedup_key"] for row in rows} == {
        f"travel:medication-prep:{near_trip_id}",
        f"travel:medication-prep:{later_trip_id}",
    }
    assert {row["expires_at"].date() for row in rows} == {
        _today() + timedelta(days=5),
        _today() + timedelta(days=10),
    }
    assert all("medication supply" in row["message"].lower() for row in rows)
    assert all("Private" not in row["message"] for row in rows)


@session_loop
@pytest.mark.parametrize(
    ("departure_days", "expected_priority"),
    [(0, 75), (7, 75), (8, 55), (14, 55), (15, None)],
)
async def test_insight_scan_medication_prep_honors_departure_window_boundaries(
    travel_pool,
    monkeypatch,
    departure_days: int,
    expected_priority: int | None,
):
    """Today through day 14 are eligible, with the day 7 priority boundary inclusive."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan
    from butlers.tools.travel import health as travel_health

    snapshot_reader = AsyncMock(return_value=_active_medication_snapshot())
    monkeypatch.setattr(travel_health, "request_health_medication_snapshot", snapshot_reader)

    start_date = _today() + timedelta(days=departure_days)
    await _insert_trip(
        travel_pool,
        start_date=start_date,
        end_date=start_date + timedelta(days=4),
    )

    await run_insight_scan(travel_pool)

    row = await travel_pool.fetchrow(
        "SELECT priority, expires_at FROM insight_candidates WHERE category = 'medication-prep'"
    )
    if expected_priority is None:
        assert row is None
        snapshot_reader.assert_not_awaited()
    else:
        assert row["priority"] == expected_priority
        assert row["expires_at"].date() == start_date
        snapshot_reader.assert_awaited_once()


@session_loop
@pytest.mark.parametrize(
    ("duration_days", "expected_candidates"),
    [(3, 0), (4, 1)],
)
async def test_insight_scan_medication_prep_requires_trip_longer_than_three_days(
    travel_pool,
    monkeypatch,
    duration_days: int,
    expected_candidates: int,
):
    from butlers.jobs._roster.travel_jobs import run_insight_scan
    from butlers.tools.travel import health as travel_health

    snapshot_reader = AsyncMock(return_value=_active_medication_snapshot())
    monkeypatch.setattr(travel_health, "request_health_medication_snapshot", snapshot_reader)

    start_date = _today() + timedelta(days=10)
    await _insert_trip(
        travel_pool,
        start_date=start_date,
        end_date=start_date + timedelta(days=duration_days),
    )

    await run_insight_scan(travel_pool)

    rows = await travel_pool.fetch(
        "SELECT id FROM insight_candidates WHERE category = 'medication-prep'"
    )
    assert len(rows) == expected_candidates
    if expected_candidates:
        snapshot_reader.assert_awaited_once()
    else:
        snapshot_reader.assert_not_awaited()


@session_loop
@pytest.mark.parametrize(
    "snapshot",
    [
        {
            "schema_version": "health.medication-travel.v1",
            "status": "ok",
            "medications": [],
            "error": None,
        },
        {
            "schema_version": "health.medication-travel.v1",
            "status": "error",
            "medications": [],
            "error": {
                "code": "health_unavailable",
                "message": "Health medication data is temporarily unavailable.",
                "retryable": True,
            },
        },
    ],
)
async def test_insight_scan_medication_prep_skips_empty_or_failed_health_snapshot(
    travel_pool,
    monkeypatch,
    caplog,
    snapshot: dict,
):
    """Empty Health data and typed failures are both no-candidate outcomes, not fabricated data."""
    from butlers.jobs._roster.travel_jobs import run_insight_scan
    from butlers.tools.travel import health as travel_health

    snapshot_reader = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(travel_health, "request_health_medication_snapshot", snapshot_reader)

    start_date = _today() + timedelta(days=10)
    await _insert_trip(travel_pool, start_date=start_date, end_date=start_date + timedelta(days=7))

    result = await run_insight_scan(travel_pool)

    snapshot_reader.assert_awaited_once()
    rows = await travel_pool.fetch(
        "SELECT id FROM insight_candidates WHERE category = 'medication-prep'"
    )
    assert rows == []
    assert result["candidates_errored"] == 0
    if snapshot["status"] == "error":
        assert "Health snapshot failed (code=health_unavailable, retryable=True)" in caplog.text


@session_loop
async def test_insight_scan_medication_prep_honors_broker_verbosity_filter(
    travel_pool,
    monkeypatch,
):
    from butlers.jobs._roster.travel_jobs import run_insight_scan
    from butlers.tools.travel import health as travel_health

    snapshot_reader = AsyncMock(return_value=_active_medication_snapshot())
    monkeypatch.setattr(travel_health, "request_health_medication_snapshot", snapshot_reader)

    await travel_pool.execute(
        "INSERT INTO insight_settings (id, verbosity) VALUES (1, 'off') "
        "ON CONFLICT (id) DO UPDATE SET verbosity = 'off'"
    )
    start_date = _today() + timedelta(days=10)
    await _insert_trip(travel_pool, start_date=start_date, end_date=start_date + timedelta(days=7))

    result = await run_insight_scan(travel_pool)

    snapshot_reader.assert_awaited_once()
    assert result == {
        "candidates_proposed": 1,
        "candidates_accepted": 0,
        "candidates_filtered": 1,
        "candidates_errored": 0,
        "early_exit": True,
    }
