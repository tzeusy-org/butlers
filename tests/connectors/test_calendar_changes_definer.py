"""calendar_changes: the journal reader the Needs-you queue's changes feed uses (core_386).

Real Postgres. Synthetic ids and text only (bu-5ii4gj.11 N3).
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

_docker = pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session"), _docker]

_T = datetime(2026, 12, 1, 15, 0, tzinfo=UTC)
_EXPECTED_COLUMNS = {
    "event_id",
    "calendar_id",
    "observed_at",
    "prior_known",
    "all_day",
    "prior_start",
    "new_start",
    "prior_end",
    "new_end",
    "prior_status",
    "new_status",
    "prior_location",
    "new_location",
    "organizer_self",
}


def _raw(*, organizer_self: bool = False, declined: bool = False, location: str = "Room 1"):
    return {
        "summary": "Secret title",
        "description": "Secret notes",
        "location": location,
        "organizer": {"email": "boss@example.test", **({"self": True} if organizer_self else {})},
        "attendees": [
            {
                "email": "me@example.test",
                "self": True,
                "responseStatus": "declined" if declined else "accepted",
            },
            {"email": "guest@example.test"},
        ],
    }


async def _revision(
    pool: Any,
    ref: str,
    etag: str,
    *,
    minutes: int,
    start_hours: int = 0,
    status: str = "confirmed",
    raw: dict | None,
    observation: str = "change",
    blocked: bool = False,
) -> None:
    await pool.execute(
        "INSERT INTO connectors.calendar_event_revisions (endpoint_identity, calendar_id, "
        "event_id, etag, status, starts_at, ends_at, observation, observed_at, policy_blocked, raw) "
        "VALUES ('ep', 'cal', $1, $2, $3, $4, $5, $6, $7, $8, $9)",
        ref,
        etag,
        status,
        _T + timedelta(hours=start_hours),
        _T + timedelta(hours=start_hours + 1),
        observation,
        datetime.now(UTC) - timedelta(minutes=minutes),
        blocked,
        raw,
    )


async def _changes(pool: Any, since_minutes: int = 600) -> list[Any]:
    return await pool.fetch(
        "SELECT * FROM public.read_calendar_event_changes($1, 50)",
        datetime.now(UTC) - timedelta(minutes=since_minutes),
    )


async def test_calendar_changes_an_organizer_move_returns_before_and_after(db) -> None:
    pool = await db("core", schema="relationship")
    ref = f"evt-{uuid.uuid4()}"
    await _revision(pool, ref, "e1", minutes=300, raw=_raw(), observation="baseline")
    await _revision(pool, ref, "e2", minutes=60, start_hours=2, raw=_raw(location="Room 2"))

    rows = [r for r in await _changes(pool) if r["event_id"] == ref]

    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == _EXPECTED_COLUMNS
    assert row["prior_known"] and row["organizer_self"] is False
    assert row["new_start"] - row["prior_start"] == timedelta(hours=2)
    assert (row["prior_location"], row["new_location"]) == ("Room 1", "Room 2")


async def test_calendar_changes_leaves_out_the_owners_own_rsvp_and_declined_events(db) -> None:
    pool = await db("core", schema="relationship")
    own, rsvp, declined = (f"evt-{uuid.uuid4()}" for _ in range(3))
    await _revision(
        pool, own, "e1", minutes=300, raw=_raw(organizer_self=True), observation="baseline"
    )
    await _revision(pool, own, "e2", minutes=60, start_hours=1, raw=_raw(organizer_self=True))
    await _revision(pool, rsvp, "e1", minutes=300, raw=_raw(), observation="baseline")
    await _revision(pool, rsvp, "e2", minutes=60, raw=_raw())  # same times, status, location
    await _revision(
        pool, declined, "e1", minutes=300, raw=_raw(declined=True), observation="baseline"
    )
    await _revision(pool, declined, "e2", minutes=60, start_hours=1, raw=_raw(declined=True))

    seen = {r["event_id"] for r in await _changes(pool)}

    assert not seen & {own, rsvp, declined}


async def test_calendar_changes_includes_a_policy_blocked_row_with_an_unknown_organizer(db) -> None:
    pool = await db("core", schema="relationship")
    ref = f"evt-{uuid.uuid4()}"
    await _revision(pool, ref, "e1", minutes=300, raw=None, blocked=True, observation="baseline")
    await _revision(pool, ref, "e2", minutes=60, start_hours=3, raw=None, blocked=True)

    rows = [r for r in await _changes(pool) if r["event_id"] == ref]

    assert len(rows) == 1
    assert rows[0]["organizer_self"] is None and rows[0]["new_location"] is None
    assert rows[0]["new_start"] - rows[0]["prior_start"] == timedelta(hours=3)


async def test_calendar_changes_is_bounded_in_age_and_in_count(db) -> None:
    pool = await db("core", schema="relationship")
    old = f"evt-{uuid.uuid4()}"
    await _revision(pool, old, "e1", minutes=60 * 24 * 20, raw=_raw(), observation="resync")

    assert old not in {r["event_id"] for r in await _changes(pool, since_minutes=60 * 24 * 30)}
    for i in range(5):
        await _revision(
            pool, f"evt-{uuid.uuid4()}", "e1", minutes=10 + i, raw=_raw(), observation="resync"
        )
    assert (
        len(
            await pool.fetch(
                "SELECT * FROM public.read_calendar_event_changes($1, 2)", _T - timedelta(days=3)
            )
        )
        <= 2
    )


async def test_calendar_changes_function_is_not_public(db) -> None:
    pool = await db("core", schema="relationship")

    assert not await pool.fetchval(
        "SELECT has_function_privilege('public', "
        "'public.read_calendar_event_changes(timestamptz, integer)', 'EXECUTE')"
    )
