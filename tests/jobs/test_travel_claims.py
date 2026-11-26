"""Travel domain time claims: the pure rule and the job end to end (bu-5ii4gj.13 S4)."""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from butlers.jobs import calendar_overlay as job
from tests.modules.test_calendar_reminder_integration import (
    _make_module,
    _ReminderProviderDouble,
)

SGT = ZoneInfo("Asia/Singapore")
TODAY = date(2030, 3, 1)
NOW = datetime(2030, 3, 1, 4, 0, tzinfo=UTC)  # 12:00 SGT
DEP = datetime(2030, 3, 10, 2, 0, tzinfo=UTC)
ARR = datetime(2030, 3, 10, 9, 0, tzinfo=UTC)


def _leg(i="l1", kind="flight", dep=DEP, arr=ARR):
    return {
        "id": i,
        "type": kind,
        "carrier": "SQ",
        "departure_city": "SIN",
        "arrival_city": "NRT",
        "departure_at": dep,
        "arrival_at": arr,
    }


def _claims(legs=(), accom=(), res=(), docs=()):
    return job.travel_claims(
        list(legs), list(accom), list(res), list(docs), today=TODAY, zone=SGT, now=NOW
    )


def _flat(by_date):
    return {c["claim_key"]: c for day in by_date.values() for c in day}


@pytest.mark.unit
def test_a_flight_blocks_its_time_and_claims_a_check_in_reminder_a_train_does_not():
    claims = _flat(_claims(legs=[_leg("f"), _leg("t", kind="train")]))

    block = claims["travel:leg:f"]
    assert block["presence"] == "block"
    assert block["span"]["start"] == DEP.isoformat() and block["span"]["end"] == ARR.isoformat()
    reminder = claims["travel:leg_checkin:f"]
    assert reminder["presence"] == "reminder"
    assert reminder["span"]["start"] == (DEP - timedelta(hours=24)).isoformat()
    assert "travel:leg_checkin:t" not in claims and claims["travel:leg:t"]["presence"] == "block"


@pytest.mark.unit
def test_no_check_in_reminder_once_its_moment_has_passed_and_a_bad_window_is_overlay_only():
    soon = _leg("f", dep=NOW + timedelta(hours=5), arr=NOW + timedelta(hours=9))
    flat = _flat(_claims(legs=[soon, _leg("x", arr=DEP)]))

    assert "travel:leg_checkin:f" not in flat  # check-in opened 19 hours ago
    assert flat["travel:leg:x"]["presence"] == "overlay"  # arrives when it departs: no block


@pytest.mark.unit
def test_a_stay_claims_an_all_day_marker_for_check_in_and_for_check_out_not_a_block():
    stay = {
        "id": "a1",
        "type": "hotel",
        "name": "Granbell",
        "check_in": datetime(2030, 3, 10, 7, tzinfo=UTC),
        "check_out": datetime(2030, 3, 14, 3, tzinfo=UTC),
    }
    flat = _flat(_claims(accom=[stay]))

    assert flat["travel:accommodation_check_in:a1"]["span"]["date"] == "2030-03-10"
    assert flat["travel:accommodation_check_out:a1"]["span"]["date"] == "2030-03-14"
    # Overlay markers, not blocks: a hotel day must not read as busy all day.
    assert {c["presence"] for c in flat.values()} == {"overlay"}


@pytest.mark.unit
def test_a_timed_reservation_blocks_by_type_and_one_without_a_time_claims_nothing():
    at = datetime(2030, 3, 11, 11, tzinfo=UTC)
    rows = [
        {"id": "r1", "type": "restaurant", "provider": "Sushi Bar", "datetime": at},
        {"id": "r2", "type": "tour", "provider": None, "datetime": at},
        {"id": "r3", "type": "tour", "provider": "X", "datetime": None},
    ]
    flat = _flat(_claims(res=rows))

    assert flat["travel:reservation:r1"]["span"]["end"] == (at + timedelta(minutes=90)).isoformat()
    assert flat["travel:reservation:r2"]["span"]["end"] == (at + timedelta(minutes=120)).isoformat()
    assert flat["travel:reservation:r2"]["label"] == "tour"
    assert "travel:reservation:r3" not in flat


@pytest.mark.unit
def test_a_visa_expiry_claims_a_reminder_30_days_ahead_or_overlay_once_that_has_passed():
    docs = [
        {"id": "d1", "type": "visa", "expiry_date": date(2030, 6, 1)},
        {"id": "d2", "type": "insurance", "expiry_date": date(2030, 3, 20)},
        {"id": "d3", "type": "receipt", "expiry_date": date(2030, 3, 20)},
    ]
    flat = _flat(_claims(docs=docs))

    assert flat["travel:document_expiry:d1"]["presence"] == "reminder"
    assert (
        flat["travel:document_expiry:d1"]["span"]["start"]
        == datetime(2030, 5, 2, 9, tzinfo=SGT).isoformat()
    )
    assert flat["travel:document_expiry:d2"]["presence"] == "overlay"
    assert "travel:document_expiry:d3" not in flat


@pytest.mark.unit
def test_a_rebooked_leg_changes_the_record_version_and_an_untouched_one_does_not():
    base = _flat(_claims(legs=[_leg()]))["travel:leg:l1"]["record_version"]
    assert _flat(_claims(legs=[_leg()]))["travel:leg:l1"]["record_version"] == base
    later = _leg(dep=DEP + timedelta(hours=3), arr=ARR + timedelta(hours=3))
    assert _flat(_claims(legs=[later]))["travel:leg:l1"]["record_version"] != base


# --- end to end -------------------------------------------------------------------------------


def e2e(fn):
    """Real Postgres: needs Docker."""
    for mark in (
        pytest.mark.integration,
        pytest.mark.asyncio(loop_scope="session"),
        pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
    ):
        fn = mark(fn)
    return fn


async def _world(db, monkeypatch):
    pool = await db("core", "travel", schema="travel")
    monkeypatch.setattr("butlers.jobs.briefing._now", lambda: NOW)
    monkeypatch.setattr(job, "_utcnow", lambda: NOW)
    await pool.execute("TRUNCATE TABLE travel.trips CASCADE")
    mod = _make_module(pool, butler_name="travel")
    mod._projection_tables_available_cache = True
    mod._provider = _ReminderProviderDouble()
    mod._resolved_calendar_id = "butlers-calendar"
    from butlers.modules.calendar import CalendarModule

    monkeypatch.setattr(CalendarModule, "for_pool", classmethod(lambda cls, p, *, butler_name: mod))
    return pool, mod


async def _flight(pool, dep=DEP, arr=ARR):
    trip = await pool.fetchval(
        "INSERT INTO travel.trips (name, destination, start_date, end_date, status) "
        "VALUES ('Tokyo', 'Tokyo', '2030-03-10', '2030-03-14', 'planned') RETURNING id"
    )
    leg = await pool.fetchval(
        "INSERT INTO travel.legs (trip_id, type, carrier, departure_city, arrival_city, "
        "departure_at, arrival_at) VALUES ($1, 'flight', 'SQ', 'SIN', 'NRT', $2, $3) RETURNING id",
        trip,
        dep,
        arr,
    )
    return trip, leg


async def _rows(pool):
    return await pool.fetch(
        "SELECT origin_ref, status, starts_at, ends_at FROM calendar_events "
        "WHERE origin_ref LIKE 'claim:travel:%' ORDER BY origin_ref"
    )


@e2e
async def test_a_flight_owns_a_block_and_a_check_in_reminder_that_move_with_it(db, monkeypatch):
    pool, mod = await _world(db, monkeypatch)
    trip, leg = await _flight(pool)

    first = await job.run_travel_calendar_overlay_contribution(pool, None)
    await mod._push_internal_events_to_provider()
    again = await job.run_travel_calendar_overlay_contribution(pool, None)

    rows = {r["origin_ref"]: r for r in await _rows(pool)}
    assert set(rows) == {f"claim:travel:leg:{leg}", f"claim:travel:leg_checkin:{leg}"}
    assert rows[f"claim:travel:leg:{leg}"]["starts_at"] == DEP
    assert rows[f"claim:travel:leg:{leg}"]["ends_at"] == ARR
    assert first["claims"]["created"] == 2 and again["claims"]["unchanged"] == 2
    assert len(mod._provider.create_calls) == 2

    # Rebooked three hours later: the same rows move, the provider gets patches, no new events.
    await pool.execute(
        "UPDATE travel.legs SET departure_at = $2, arrival_at = $3 WHERE id = $1",
        leg,
        DEP + timedelta(hours=3),
        ARR + timedelta(hours=3),
    )
    moved = await job.run_travel_calendar_overlay_contribution(pool, None)
    await mod._push_internal_events_to_provider()
    assert moved["claims"]["updated"] == 2 and len(await _rows(pool)) == 2
    assert len(mod._provider.update_calls) == 2 and len(mod._provider.create_calls) == 2

    # The trip is cancelled: both rows are retracted and the provider copies deleted.
    await pool.execute("UPDATE travel.trips SET status = 'cancelled' WHERE id = $1", trip)
    gone = await job.run_travel_calendar_overlay_contribution(pool, None)
    await mod._push_internal_events_to_provider()
    assert gone["claims"]["retracted"] == 2
    assert {r["status"] for r in await _rows(pool)} == {"cancelled"}
    assert len(mod._provider.delete_calls) == 2


@e2e
async def test_a_failed_source_read_retracts_nothing_and_keeps_the_envelopes(db, monkeypatch):
    from butlers.core.state import state_get

    pool, mod = await _world(db, monkeypatch)
    trip, leg = await _flight(pool)
    await job.run_travel_calendar_overlay_contribution(pool, None)
    before = await state_get(pool, job.overlay_key("2030-03-10"))
    await pool.execute("UPDATE travel.trips SET status = 'cancelled' WHERE id = $1", trip)

    await pool.execute("ALTER TABLE travel.documents RENAME TO documents_gone")
    try:
        result = await job.run_travel_calendar_overlay_contribution(pool, None)
    finally:
        await pool.execute("ALTER TABLE travel.documents_gone RENAME TO documents")

    assert result["complete"] is False and result["claims"]["retracted"] == 0
    assert {r["status"] for r in await _rows(pool)} == {"confirmed"}
    after = await state_get(pool, job.overlay_key("2030-03-10"))
    assert after["complete"] is False and after["claims"] == before["claims"]
    assert after["entries"] == before["entries"]


@e2e
async def test_a_flight_that_has_landed_keeps_its_rows_as_history(db, monkeypatch):
    pool, mod = await _world(db, monkeypatch)
    await _flight(pool)
    await job.run_travel_calendar_overlay_contribution(pool, None)
    await mod._push_internal_events_to_provider()

    # Two days after landing the window starts after the flight; the job no longer lists it.
    later = ARR + timedelta(days=2)
    monkeypatch.setattr("butlers.jobs.briefing._now", lambda: later)
    monkeypatch.setattr(job, "_utcnow", lambda: later)
    result = await job.run_travel_calendar_overlay_contribution(pool, None)
    await mod._push_internal_events_to_provider()

    assert result["claims"]["retracted"] == 0 and not mod._provider.delete_calls
    assert {r["status"] for r in await _rows(pool)} == {"confirmed"}


@e2e
async def test_a_reconcile_error_makes_the_travel_run_partial_and_names_the_error(db, monkeypatch):
    pool, _ = await _world(db, monkeypatch)
    await _flight(pool)
    await job.run_travel_calendar_overlay_contribution(pool, None)

    async def boom(self, *args, **kwargs):
        raise TypeError("signature drift")

    from butlers.modules.calendar import CalendarModule

    monkeypatch.setattr(CalendarModule, "reconcile_domain_claims", boom)
    result = await job.run_travel_calendar_overlay_contribution(pool, None)

    assert result["claims"] == {"error": "TypeError"} and result["complete"] is False


def test_a_long_travel_label_is_clipped_so_the_claim_stays_valid():
    from butlers.core.temporal.domain_claims import MAX_LABEL_CHARS, parse_claim

    stay = {
        "id": "a1",
        "name": "H" * 130,
        "type": "hotel",
        "check_in": datetime(2030, 3, 10, 7, tzinfo=UTC),
        "check_out": datetime(2030, 3, 14, 3, tzinfo=UTC),
    }
    claims = _flat(_claims(accom=[stay]))

    assert claims
    for raw in claims.values():
        assert len(raw["label"]) <= MAX_LABEL_CHARS
        parse_claim(raw, source_butler="travel")
