"""Tests for the date-driven trip lifecycle job (bu-swkpoo.2)."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import date

import pytest

from butlers.jobs._roster_loader import load_roster_jobs

TODAY = date(2030, 11, 22)

docker_available = shutil.which("docker") is not None
needs_docker = pytest.mark.skipif(not docker_available, reason="Docker not available")
session_loop = pytest.mark.asyncio(loop_scope="session")


def _jobs():
    return load_roster_jobs("travel")


@pytest.fixture
async def travel_pool(db):
    return await db("core", "travel", schemas={"travel": "travel"}, schema="travel")


async def _insert_trip(pool, *, status: str, start: date, end: date) -> str:
    trip_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO travel.trips (id, name, destination, start_date, end_date, status)
        VALUES ($1::uuid, 'Example Trip', 'Example City', $2, $3, $4)
        """,
        trip_id,
        start,
        end,
        status,
    )
    return trip_id


async def _row(pool, trip_id: str):
    return await pool.fetchrow(
        "SELECT status, metadata FROM travel.trips WHERE id = $1::uuid", trip_id
    )


def _history(row) -> list[dict]:
    meta = row["metadata"]
    meta = json.loads(meta) if isinstance(meta, str) else meta
    return meta.get("change_history", [])


# --- pure classifier (no DB) ----------------------------------------------


@pytest.mark.parametrize(
    ("status", "start", "end", "expected"),
    [
        ("planned", date(2030, 11, 20), date(2030, 11, 25), "active"),
        ("planned", TODAY, TODAY, "active"),
        ("planned", date(2030, 11, 1), date(2030, 11, 5), "completed"),
        ("active", date(2030, 11, 1), date(2030, 11, 5), "completed"),
        ("active", date(2030, 11, 20), date(2030, 11, 25), None),
        ("planned", date(2030, 12, 1), date(2030, 12, 5), None),
        ("cancelled", date(2030, 11, 1), date(2030, 11, 5), None),
        ("completed", date(2030, 11, 1), date(2030, 11, 5), None),
        ("planned", None, date(2030, 11, 5), None),
        ("planned", date(2030, 11, 1), None, None),
        ("active", None, None, None),
    ],
)
def test_lifecycle_target_status(status, start, end, expected) -> None:
    assert _jobs()._lifecycle_target_status(status, start, end, TODAY) == expected


# --- real-PG behaviour -----------------------------------------------------


@needs_docker
@session_loop
async def test_planned_in_window_becomes_active(travel_pool) -> None:
    tid = await _insert_trip(
        travel_pool, status="planned", start=date(2030, 11, 20), end=date(2030, 11, 25)
    )
    result = await _jobs().run_trip_lifecycle_advance(travel_pool, today=TODAY)
    row = await _row(travel_pool, tid)
    assert row["status"] == "active"
    assert result["activated"] == 1 and result["completed"] == 0
    hist = _history(row)
    assert hist[-1]["prior_values"] == {"status": "planned"}
    assert hist[-1]["updated_by"] == "trip_lifecycle_advance"


@needs_docker
@session_loop
async def test_planned_and_active_past_end_become_completed(travel_pool) -> None:
    planned = await _insert_trip(
        travel_pool, status="planned", start=date(2030, 10, 1), end=date(2030, 10, 5)
    )
    active = await _insert_trip(
        travel_pool, status="active", start=date(2030, 10, 1), end=date(2030, 10, 5)
    )
    result = await _jobs().run_trip_lifecycle_advance(travel_pool, today=TODAY)
    assert result["completed"] == 2
    for tid, prior in ((planned, "planned"), (active, "active")):
        row = await _row(travel_pool, tid)
        assert row["status"] == "completed"
        assert _history(row)[-1]["prior_values"] == {"status": prior}


@needs_docker
@session_loop
async def test_future_and_ongoing_active_trips_untouched(travel_pool) -> None:
    future = await _insert_trip(
        travel_pool, status="planned", start=date(2030, 12, 1), end=date(2030, 12, 5)
    )
    ongoing = await _insert_trip(
        travel_pool, status="active", start=date(2030, 11, 20), end=date(2030, 11, 25)
    )
    result = await _jobs().run_trip_lifecycle_advance(travel_pool, today=TODAY)
    assert result["activated"] == 0 and result["completed"] == 0
    assert (await _row(travel_pool, future))["status"] == "planned"
    assert (await _row(travel_pool, ongoing))["status"] == "active"


@needs_docker
@session_loop
async def test_cancelled_never_touched(travel_pool) -> None:
    past = await _insert_trip(
        travel_pool, status="cancelled", start=date(2030, 10, 1), end=date(2030, 10, 5)
    )
    current = await _insert_trip(
        travel_pool, status="cancelled", start=date(2030, 11, 20), end=date(2030, 11, 25)
    )
    await _jobs().run_trip_lifecycle_advance(travel_pool, today=TODAY)
    for tid in (past, current):
        row = await _row(travel_pool, tid)
        assert row["status"] == "cancelled"
        assert _history(row) == []


@needs_docker
@session_loop
async def test_rerun_is_idempotent(travel_pool) -> None:
    tid = await _insert_trip(
        travel_pool, status="planned", start=date(2030, 10, 1), end=date(2030, 10, 5)
    )
    await _jobs().run_trip_lifecycle_advance(travel_pool, today=TODAY)
    second = await _jobs().run_trip_lifecycle_advance(travel_pool, today=TODAY)
    assert second["activated"] == 0 and second["completed"] == 0
    assert len(_history(await _row(travel_pool, tid))) == 1


# --- fake-pool flow check (runs without Docker) ----------------------------


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.updates: list[tuple] = []

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Tx()

    async def fetch(self, sql, *args):
        return self.rows

    async def execute(self, sql, *args):
        self.updates.append(args)


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Acq:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Acq()


async def test_advance_flow_updates_only_advancing_rows() -> None:
    rows = [
        {
            "id": uuid.uuid4(),
            "status": "planned",
            "start_date": date(2030, 11, 20),
            "end_date": date(2030, 11, 25),
        },
        {
            "id": uuid.uuid4(),
            "status": "active",
            "start_date": date(2030, 10, 1),
            "end_date": date(2030, 10, 5),
        },
        {
            "id": uuid.uuid4(),
            "status": "planned",
            "start_date": date(2030, 12, 1),
            "end_date": date(2030, 12, 5),
        },
        {"id": uuid.uuid4(), "status": "planned", "start_date": None, "end_date": None},
    ]
    conn = _FakeConn(rows)
    result = await _jobs().run_trip_lifecycle_advance(_FakePool(conn), today=TODAY)
    assert result == {"trips_checked": 4, "activated": 1, "completed": 1}
    assert [u[1] for u in conn.updates] == ["active", "completed"]
    assert json.loads(conn.updates[1][2])[0]["prior_values"] == {"status": "active"}
