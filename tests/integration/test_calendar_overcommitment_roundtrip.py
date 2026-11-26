"""Real-Postgres regression: the calendar overcommitment producer (bu-vcjgo8).

Drives ``run_calendar_overcommitment_check`` against a migrated ``public.owner_conditions``
ledger, faking only the cross-schema conflict scan and the owner timezone read. The unit tests
in ``tests/jobs/test_calendar_overcommitment.py`` mock ``reconcile_snapshot``; this file proves
the producer's actual ledger outcomes: open, resolve by omission, a partial scan that never
resolves, and concurrent scans that confirm one episode.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.api.read_models.calendar_workspace import CalendarConflictScan
from butlers.core.temporal.conflicts import ConflictEventRef, DetectedIssue
from butlers.jobs import calendar_overcommitment as job
from butlers.testing.db_manager import db_manager

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_DAY = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


def _ref(entry_id: str, start: datetime) -> ConflictEventRef:
    return ConflictEventRef(
        entry_id=entry_id,
        title="private",
        start_at=start,
        end_at=start + timedelta(hours=1),
        timezone="UTC",
        status="confirmed",
    )


def _overlap() -> DetectedIssue:
    return DetectedIssue(
        kind="overlap",
        date="2026-07-01",
        summary="private",
        severity="warning",
        events=[_ref("a", _DAY), _ref("b", _DAY + timedelta(minutes=30))],
    )


def _scan(*issues: DetectedIssue, available: bool = True) -> CalendarConflictScan:
    return CalendarConflictScan(
        issues=list(issues),
        available=available,
        entry_identity_keys={"a": "origin:A", "b": "origin:B"},
    )


@pytest.fixture
def scans(monkeypatch) -> AsyncMock:
    monkeypatch.setattr(job, "load_general_settings", AsyncMock(return_value={"timezone": "UTC"}))
    scan = AsyncMock()
    monkeypatch.setattr(job, "query_calendar_conflicts", scan)
    return scan


async def _episodes(pool: asyncpg.Pool) -> list[tuple[int, str]]:
    rows = await pool.fetch(
        "SELECT episode, state FROM public.owner_conditions "
        "WHERE source = 'calendar:overcommitment' ORDER BY episode"
    )
    return [(r["episode"], r["state"]) for r in rows]


async def test_open_then_partial_scan_keeps_then_complete_scan_resolves(
    db_pool: asyncpg.Pool, scans: AsyncMock
) -> None:
    db = db_manager({"switchboard": db_pool})

    scans.return_value = _scan(_overlap())
    assert (await job.run_calendar_overcommitment_check(db))["transitions"] == ["opened"]

    scans.return_value = _scan(available=False)
    assert (await job.run_calendar_overcommitment_check(db))["transitions"] == []
    assert await _episodes(db_pool) == [(1, "open")]

    scans.return_value = _scan()
    assert (await job.run_calendar_overcommitment_check(db))["transitions"] == ["resolved"]
    assert await _episodes(db_pool) == [(1, "resolved")]


async def test_concurrent_scans_of_one_calendar_keep_one_active_episode(
    db, scans: AsyncMock
) -> None:
    pool = await db("core", min_size=2, max_size=10)
    scans.return_value = _scan(_overlap())

    results = await asyncio.gather(
        *[
            job.run_calendar_overcommitment_check(db_manager({"switchboard": pool}))
            for _ in range(8)
        ]
    )

    transitions = sorted(t for r in results for t in r["transitions"])
    assert transitions == ["confirmed"] * 7 + ["opened"]
    assert await _episodes(pool) == [(1, "open")]


async def _seed_london_trip(pool: asyncpg.Pool, *, confirmation: str | None) -> None:
    trip = await pool.fetchval(
        "INSERT INTO travel.trips (name, destination, start_date, end_date, status) "
        "VALUES ('London', 'London', '2026-07-10', '2026-07-20', 'planned') RETURNING id"
    )
    await pool.execute(
        "INSERT INTO travel.legs (trip_id, type, arrival_airport_station, arrival_city, "
        "departure_at, arrival_at, confirmation_number) VALUES ($1, 'flight', 'LHR', 'London', "
        "$2, $3, $4)",
        trip,
        datetime(2026, 7, 10, 1, tzinfo=UTC),
        datetime(2026, 7, 10, 8, tzinfo=UTC),
        confirmation,
    )


def _nine_am_singapore_meeting() -> ConflictEventRef:
    # 09:00 in Singapore on 15 July is 02:00 in London (BST).
    start = datetime(2026, 7, 15, 1, 0, tzinfo=UTC)
    return ConflictEventRef(
        entry_id="m",
        title="private",
        start_at=start,
        end_at=start + timedelta(hours=1),
        timezone="Asia/Singapore",
        status="confirmed",
    )


async def _off_hours_rows(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT state, summary, metadata FROM public.owner_conditions "
        "WHERE source = 'calendar:overcommitment' ORDER BY episode"
    )


async def test_a_nine_oclock_meeting_during_a_confirmed_trip_is_an_off_hours_condition(
    db, monkeypatch
) -> None:
    pool = await db("core", "travel")
    monkeypatch.setattr(
        job, "load_general_settings", AsyncMock(return_value={"timezone": "Asia/Singapore"})
    )
    monkeypatch.setattr(job.flags, "OWNER_CLOCK_LIVE", True)
    scan = AsyncMock(
        return_value=CalendarConflictScan(
            issues=[],
            owner_events=[_nine_am_singapore_meeting()],
            entry_identity_keys={"m": "origin:M"},
        )
    )
    monkeypatch.setattr(job, "query_calendar_conflicts", scan)
    await _seed_london_trip(pool, confirmation="PNR123")
    manager = db_manager({"switchboard": pool, "travel": pool})
    now = datetime(2026, 7, 14, 20, 0, tzinfo=UTC)

    result = await job.run_calendar_overcommitment_check(manager, now=now)

    assert result["transitions"] == ["opened"]
    [row] = await _off_hours_rows(pool)
    assert row["state"] == "open"
    metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])
    assert metadata["kind"] == "off_hours_in_local_zone"
    assert metadata["basis"] == "confirmed_booking" and metadata["zone"] == "Europe/London"
    assert metadata["local_start"] == "02:00"
    assert "private" not in row["summary"]


async def test_a_booking_without_a_confirmation_number_raises_nothing(db, monkeypatch) -> None:
    pool = await db("core", "travel")
    monkeypatch.setattr(
        job, "load_general_settings", AsyncMock(return_value={"timezone": "Asia/Singapore"})
    )
    monkeypatch.setattr(job.flags, "OWNER_CLOCK_LIVE", True)
    monkeypatch.setattr(
        job,
        "query_calendar_conflicts",
        AsyncMock(
            return_value=CalendarConflictScan(
                issues=[], owner_events=[_nine_am_singapore_meeting()]
            )
        ),
    )
    await _seed_london_trip(pool, confirmation=None)

    result = await job.run_calendar_overcommitment_check(
        db_manager({"switchboard": pool, "travel": pool}),
        now=datetime(2026, 7, 14, 20, 0, tzinfo=UTC),
    )

    assert result["transitions"] == [] and await _off_hours_rows(pool) == []
