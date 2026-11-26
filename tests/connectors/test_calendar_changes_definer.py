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

_T = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
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


def _raw(
    *,
    organizer_self: bool = False,
    declined: bool = False,
    location: str = "Room 1",
    ical: str | None = None,
    organizer_email: str = "boss@example.test",
):
    return {
        **({"iCalUID": ical} if ical else {}),
        "summary": "Secret title",
        "description": "Secret notes",
        "location": location,
        "organizer": {"email": organizer_email, **({"self": True} if organizer_self else {})},
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
    endpoint: str = "ep",
) -> None:
    await pool.execute(
        "INSERT INTO connectors.calendar_event_revisions (endpoint_identity, calendar_id, "
        "event_id, etag, status, starts_at, ends_at, observation, observed_at, policy_blocked, raw) "
        "VALUES ($10, 'cal', $1, $2, $3, $4, $5, $6, $7, $8, $9)",
        ref,
        etag,
        status,
        _T + timedelta(hours=start_hours),
        _T + timedelta(hours=start_hours + 1),
        observation,
        datetime.now(UTC) - timedelta(minutes=minutes),
        blocked,
        raw,
        endpoint,
    )


async def _changes(
    pool: Any, since_minutes: int = 600, owner_emails: list[str] | None = None, limit: int = 50
) -> list[Any]:
    return await pool.fetch(
        "SELECT * FROM public.read_calendar_event_changes($1, $2, $3::text[])",
        datetime.now(UTC) - timedelta(minutes=since_minutes),
        limit,
        owner_emails or [],
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


async def test_calendar_changes_is_bounded_in_age_and_asks_one_past_the_cap(db) -> None:
    pool = await db("core", schema="relationship")
    old = f"evt-{uuid.uuid4()}"
    await _revision(pool, old, "e1", minutes=60 * 24 * 20, raw=_raw(), observation="resync")
    await _revision(pool, old, "e2", minutes=60 * 24 * 19, start_hours=1, raw=_raw())

    assert old not in {r["event_id"] for r in await _changes(pool, since_minutes=60 * 24 * 30)}
    refs = [f"evt-{uuid.uuid4()}" for _ in range(5)]
    for i, ref in enumerate(refs):
        await _revision(pool, ref, "e1", minutes=300, raw=_raw(), observation="baseline")
        await _revision(pool, ref, "e2", minutes=10 + i, start_hours=1, raw=_raw())

    assert len(await _changes(pool, limit=2)) == 2
    assert len(await _changes(pool, limit=100000)) <= 201


async def test_calendar_changes_never_returns_a_new_invitation_or_a_past_event(db) -> None:
    pool = await db("core", schema="relationship")
    fresh, past, cancelled = (f"evt-{uuid.uuid4()}" for _ in range(3))
    await _revision(pool, fresh, "e1", minutes=60, raw=_raw())  # no earlier version
    await _revision(
        pool, past, "e1", minutes=300, start_hours=-100, raw=_raw(), observation="baseline"
    )
    await _revision(pool, past, "e2", minutes=60, start_hours=-98, raw=_raw())
    # a cancellation is judged on the start it had, which is still ahead
    await _revision(pool, cancelled, "e1", minutes=300, raw=_raw(), observation="baseline")
    await _revision(pool, cancelled, "e2", minutes=60, status="cancelled", raw=_raw())

    seen = {r["event_id"]: r for r in await _changes(pool)}

    assert fresh not in seen and past not in seen
    assert seen[cancelled]["new_status"] == "cancelled"


async def test_calendar_changes_a_resync_with_nothing_changed_yields_nothing(db) -> None:
    pool = await db("core", schema="relationship")
    ref = f"evt-{uuid.uuid4()}"
    await _revision(pool, ref, "e1", minutes=300, raw=_raw(), observation="baseline")
    for i in range(3):
        await _revision(pool, ref, f"r{i}", minutes=60 - i, raw=_raw(), observation="resync")

    assert ref not in {r["event_id"] for r in await _changes(pool)}


async def test_calendar_changes_one_meeting_on_two_endpoints_is_one_item(db) -> None:
    pool = await db("core", schema="relationship")
    ref_a, ref_b = f"evt-{uuid.uuid4()}", f"evt-{uuid.uuid4()}"
    uid = f"uid-{uuid.uuid4()}@example.test"
    for ref, endpoint, minutes in ((ref_a, "ep-a", 50), (ref_b, "ep-b", 40)):
        await _revision(
            pool,
            ref,
            "e1",
            minutes=300,
            endpoint=endpoint,
            raw=_raw(ical=uid),
            observation="baseline",
        )
        await _revision(
            pool, ref, "e2", minutes=minutes, start_hours=2, endpoint=endpoint, raw=_raw(ical=uid)
        )

    rows = [r for r in await _changes(pool) if r["event_id"] in {ref_a, ref_b}]

    assert [r["event_id"] for r in rows] == [ref_a]  # the earliest observation is kept


async def test_calendar_changes_an_organizer_that_is_one_of_the_owners_addresses_is_the_owner(
    db,
) -> None:
    pool = await db("core", schema="relationship")
    ref = f"evt-{uuid.uuid4()}"
    raw = _raw(organizer_email="Me.Work@Example.test")  # no organizer.self on this account's copy
    await _revision(pool, ref, "e1", minutes=300, raw=raw, observation="baseline")
    await _revision(pool, ref, "e2", minutes=60, start_hours=1, raw=raw)

    assert ref in {r["event_id"] for r in await _changes(pool)}
    assert ref not in {
        r["event_id"] for r in await _changes(pool, owner_emails=["me.work@example.test"])
    }


async def test_calendar_changes_function_is_not_public(db) -> None:
    pool = await db("core", schema="relationship")

    assert not await pool.fetchval(
        "SELECT has_function_privilege('public', "
        "'public.read_calendar_event_changes(timestamptz, integer, text[])', 'EXECUTE')"
    )


async def test_calendar_changes_function_is_executable_by_the_switchboard_role(db) -> None:
    pool = await db("core", schema="relationship")
    signature = "public.read_calendar_event_changes(timestamptz, integer, text[])"

    if not await pool.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'butler_switchboard_rw'"):
        pytest.skip("per-butler roles are not provisioned in this test database")
    assert await pool.fetchval(
        "SELECT has_function_privilege('butler_switchboard_rw', $1, 'EXECUTE')", signature
    )


async def test_a_readable_to_blocked_flip_is_not_a_location_change(db) -> None:
    pool = await db("core", schema="relationship")
    ref = f"evt-{uuid.uuid4()}"
    await _revision(
        pool, ref, "e1", minutes=300, raw=_raw(location="Room 1"), observation="baseline"
    )
    # the same event later arrives policy-blocked: no raw, so no location to compare
    await _revision(pool, ref, "e2", minutes=60, start_hours=1, raw=None, blocked=True)
    ref2 = f"evt-{uuid.uuid4()}"
    await _revision(pool, ref2, "e1", minutes=300, raw=None, blocked=True, observation="baseline")
    await _revision(pool, ref2, "e2", minutes=60, start_hours=1, raw=_raw(location="Room 9"))

    rows = {r["event_id"]: r for r in await _changes(pool)}

    for item in (rows[ref], rows[ref2]):
        assert item["prior_location"] is None and item["new_location"] is None
    # and a flip that changes only the location never appears
    ref3 = f"evt-{uuid.uuid4()}"
    await _revision(pool, ref3, "e1", minutes=300, raw=_raw(location="A"), observation="baseline")
    await _revision(pool, ref3, "e2", minutes=60, raw=None, blocked=True)
    assert ref3 not in {r["event_id"] for r in await _changes(pool)}
