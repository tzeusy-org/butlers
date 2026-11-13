"""The presence reducer and its Home adapter against the real perception SQL and roles.

The ``home.dots`` profile is unconfigured in a fresh install (activation is owner-gated), so
the fixture configures it the way an activation would, and the writer is reached with
``SET LOCAL ROLE`` exactly as the capability-health tests do.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.db import register_jsonb_codec
from butlers.jobs import home_presence_reducer as adapter
from butlers.jobs.home_presence_reducer import (
    EVIDENCE_SOURCES,
    PresenceRuntime,
    evidence_ref,
    run_presence_reducer,
)
from butlers.owner_places import OwnerPlace
from butlers.perception import PerceptionStore
from butlers.perception.presence import CONFIRM_AFTER, HEARTBEAT, PresenceState
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [pytest.mark.integration, pytest.mark.db]

OWNER = uuid.UUID("22222222-2222-4222-8222-222222222222")
HOME = uuid.UUID("33333333-3333-4333-8333-333333333333")
NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
ENTITY = "person.owner"


@pytest.fixture(scope="module")
def dsn(postgres_container):
    name = migration_db_name()
    create_migrated_test_db(postgres_container, name, chains=["core"])
    engine = create_engine(migration_bootstrap_db_url(postgres_container, name))
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO public.entities(id,canonical_name,entity_type,roles) "
                "VALUES(:id,'opaque test owner','person',ARRAY['owner'])"
            ),
            {"id": OWNER},
        )
        c.execute(
            text(
                "UPDATE perception_private.sources SET subjects=ARRAY[CAST(:id AS uuid)],"
                "max_age_seconds=3600,evidence_sources=CAST(:ev AS text[]) WHERE source='home.dots'"
            ),
            {"id": OWNER, "ev": list(EVIDENCE_SOURCES)},
        )
        for label in EVIDENCE_SOURCES:
            c.execute(
                text(
                    "INSERT INTO perception_private.source_refs(id,source,ref_type) "
                    "VALUES(:id,'home.dots','evidence')"
                ),
                {"id": evidence_ref(label)},
            )
        c.execute(
            text(
                "INSERT INTO perception_private.source_refs(id,source,ref_type) "
                "VALUES(:id,'home.dots','place')"
            ),
            {"id": HOME},
        )
    url = engine.url
    engine.dispose()
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@asynccontextmanager
async def session(dsn):
    conn = await asyncpg.connect(dsn, command_timeout=30)
    await register_jsonb_codec(conn)
    tx = conn.transaction()
    await tx.start()
    try:
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


class SavepointStore(PerceptionStore):
    """Each write in a savepoint, as a production autocommit writer connection behaves."""

    async def write(self, request):
        async with self.connection.transaction():
            return await super().write(request)


async def as_role(conn, role):
    await conn.execute("RESET ROLE")
    await conn.execute(f"SET LOCAL ROLE {role}")
    return SavepointStore(conn)


class FakePool:
    def __init__(self):
        self.rows = []

    async def fetch(self, query, *args):
        return self.rows

    def set(self, state, at):
        self.rows = [{"entity_id": ENTITY, "state": state, "last_updated": at}]


@pytest.fixture
def home(monkeypatch):
    async def healthy(pool):
        return None

    async def entities(pool):
        return frozenset({ENTITY})

    async def places(pool):
        return (OwnerPlace(HOME, "Home", "home", 0.0, 0.0, 100),)

    async def owner(pool):
        return OWNER

    monkeypatch.setattr(adapter, "_require_ha_source_healthy", healthy)
    monkeypatch.setattr(adapter, "_load_owner_presence_entity_ids", entities)
    monkeypatch.setattr(adapter, "load_active_owner_places", places)
    monkeypatch.setattr(adapter, "_owner_subject", owner)
    return FakePool()


async def tick(pool, store, runtime, state, at, now):
    pool.set(state, at)
    return await run_presence_reducer(pool, store, runtime, now=now)


async def history(conn):
    reader = await as_role(conn, "perception_owner_reader")
    rows = await reader.timeline(
        since=NOW - timedelta(days=1),
        until=NOW + timedelta(days=1),
        kind="owner.place",
        subject=OWNER,
    )
    return reader, sorted(rows, key=lambda r: (r["at"], r["confirmed_at"]))


async def test_leave_enter_and_quick_return_retraction_keep_history(dsn, home):
    async with session(dsn) as conn:
        store = await as_role(conn, "perception_dots_writer")
        runtime = PresenceRuntime()
        assert (await tick(home, store, runtime, "home", NOW, NOW))["published"] == 0
        # Establishing from unknown: an explicit reset first, never a leave or enter.
        out = await tick(home, store, runtime, "home", NOW, NOW + CONFIRM_AFTER)
        assert out == {"outcome": "ok", "published": 1}

        leave_at = NOW + timedelta(minutes=10)
        await tick(home, store, runtime, "not_home", leave_at, leave_at)
        left = await tick(home, store, runtime, "not_home", leave_at, leave_at + CONFIRM_AFTER)
        assert left["published"] == 1 and runtime.state.side == "outside"

        back = leave_at + timedelta(minutes=6)
        await tick(home, store, runtime, "home", back, back)
        entered = await tick(home, store, runtime, "home", back, back + CONFIRM_AFTER)
        assert entered["published"] == 1 and runtime.state.side == "inside"

        reader, rows = await history(conn)
        leave = next(
            r
            for r in rows
            if r["to_value"]
            and r["to_value"]["presence"] == "outside"
            and r["operation"] == "assert"
        )
        assert leave["from_value"]["presence"] == "inside"
        assert leave["retracted"] is True and leave["late"] is False  # history kept, marked
        assert any(r["operation"] == "retract" for r in rows)
        live = await reader.timeline(
            since=NOW - timedelta(days=1),
            until=NOW + timedelta(days=1),
            kind="owner.place",
            subject=OWNER,
            live=True,
        )
        assert all(r["id"] != leave["id"] for r in live)
        slot = await reader.read("owner.place", OWNER, as_of=back + CONFIRM_AFTER)
        assert slot["value"]["presence"] == "inside"
        assert set(slot["value"]) == {"presence", "place_ref", "locality_ref"}


async def test_unknown_prior_never_becomes_a_leave_even_over_a_stale_inside_slot(dsn, home):
    async with session(dsn) as conn:
        store = await as_role(conn, "perception_dots_writer")
        first = PresenceRuntime()
        await tick(home, store, first, "home", NOW, NOW)
        await tick(home, store, first, "home", NOW, NOW + CONFIRM_AFTER)
        # A restarted process remembers nothing while the slot still holds "inside".
        later = NOW + timedelta(hours=3)
        restarted = PresenceRuntime()
        await tick(home, store, restarted, "not_home", later, later)
        out = await tick(home, store, restarted, "not_home", later, later + CONFIRM_AFTER)
        assert out["published"] == 1
        _, rows = await history(conn)
        outside = [
            r
            for r in rows
            if r["operation"] == "assert" and (r["to_value"] or {}).get("presence") == "outside"
        ]
        assert len(outside) == 1 and outside[0]["from_state"] == "unknown"
        assert outside[0]["from_value"] is None


async def test_duplicate_and_concurrent_delivery_make_one_slot_version(dsn, home):
    async with session(dsn) as conn:
        store = await as_role(conn, "perception_dots_writer")
        a, b = PresenceRuntime(), PresenceRuntime()
        await tick(home, store, a, "home", NOW, NOW)
        await tick(home, store, b, "home", NOW, NOW)
        await tick(home, store, a, "home", NOW, NOW + CONFIRM_AFTER)
        # The second runner confirms the same transition a moment later: same event, so the
        # writer returns the original receipt (or refuses a changed replay) and writes nothing.
        out = await tick(home, store, b, "home", NOW, NOW + CONFIRM_AFTER + timedelta(seconds=7))
        assert out["outcome"] == "ok"
        # A second delivery of the very same decision is idempotent.
        again = PresenceRuntime()
        await tick(home, store, again, "home", NOW, NOW)
        await tick(home, store, again, "home", NOW, NOW + CONFIRM_AFTER)
        reader, rows = await history(conn)
        establish = [r for r in rows if (r["to_value"] or {}).get("presence") == "inside"]
        assert len(establish) == 1
        slot = await reader.read("owner.place", OWNER, as_of=NOW + CONFIRM_AFTER)
        assert slot["generation"] == 2  # one reset, one establish: nothing duplicated


async def test_a_delayed_confirmation_is_marked_late(dsn, home):
    async with session(dsn) as conn:
        store = await as_role(conn, "perception_dots_writer")
        runtime = PresenceRuntime()
        await tick(home, store, runtime, "home", NOW, NOW)
        await tick(home, store, runtime, "home", NOW, NOW + CONFIRM_AFTER)
        leave_at = NOW + timedelta(minutes=10)
        # The observation only reaches the reducer 16 minutes after it happened.
        await tick(home, store, runtime, "not_home", leave_at, leave_at + timedelta(minutes=16))
        _, rows = await history(conn)
        leave = next(r for r in rows if (r["to_value"] or {}).get("presence") == "outside")
        assert leave["late"] is True
        reader, _ = await history(conn)
        live = await reader.timeline(
            since=NOW - timedelta(days=1),
            until=NOW + timedelta(days=1),
            kind="owner.place",
            subject=OWNER,
            live=True,
        )
        assert all(r["id"] != leave["id"] for r in live)


async def test_input_trouble_writes_nothing_and_forgets_the_carried_side(dsn, home, monkeypatch):
    async with session(dsn) as conn:
        store = await as_role(conn, "perception_dots_writer")
        runtime = PresenceRuntime()
        await tick(home, store, runtime, "home", NOW, NOW)
        await tick(home, store, runtime, "home", NOW, NOW + CONFIRM_AFTER)
        assert runtime.state.side == "inside"
        _, before = await history(conn)
        store = await as_role(conn, "perception_dots_writer")

        async def blind(pool):
            raise adapter.HASourceUnmeasurableError("blind", last_success_at=None)

        monkeypatch.setattr(adapter, "_require_ha_source_healthy", blind)
        later = NOW + timedelta(minutes=20)
        out = await tick(home, store, runtime, "home", later, later)
        assert out == {"outcome": "unmeasurable"}
        assert runtime.state.side is None  # forgotten, not carried across the gap

        async def healthy(pool):
            return None

        monkeypatch.setattr(adapter, "_require_ha_source_healthy", healthy)
        out_at = later + timedelta(minutes=2)
        await tick(home, store, runtime, "not_home", out_at, out_at)
        await tick(home, store, runtime, "not_home", out_at, out_at + CONFIRM_AFTER)
        _, rows = await history(conn)
        assert len(rows) > len(before)
        outside = [
            r
            for r in rows
            if r["operation"] == "assert" and (r["to_value"] or {}).get("presence") == "outside"
        ]
        # Establish over an unknown prior, never a leave from the stale inside.
        assert len(outside) == 1 and outside[0]["from_state"] == "unknown"

        async def none(pool):
            return None

        monkeypatch.setattr(adapter, "_load_owner_presence_entity_ids", none)
        runtime.state = PresenceState(side="inside", place_ref=HOME, last_agree=NOW)
        assert await tick(home, store, runtime, "home", NOW, NOW) == {"outcome": "unconfigured"}
        assert runtime.state.side is None


async def test_the_fifteen_minute_late_boundary_is_exclusive_at_this_seam(dsn, home):
    async with session(dsn) as conn:
        store = await as_role(conn, "perception_dots_writer")
        runtime = PresenceRuntime()
        await tick(home, store, runtime, "home", NOW, NOW)
        await tick(home, store, runtime, "home", NOW, NOW + CONFIRM_AFTER)
        leave_at = NOW + timedelta(minutes=10)
        # Delivered exactly 15:00 after it happened: not late. One second more: late.
        await tick(home, store, runtime, "not_home", leave_at, leave_at + timedelta(minutes=15))
        _, rows = await history(conn)
        leave = next(r for r in rows if (r["to_value"] or {}).get("presence") == "outside")
        assert leave["late"] is False
        store = await as_role(conn, "perception_dots_writer")
        other = PresenceRuntime()
        await tick(home, store, other, "home", NOW, NOW)
        await tick(home, store, other, "home", NOW, NOW + CONFIRM_AFTER)
        second_leave = NOW + timedelta(hours=1)
        await tick(
            home,
            store,
            other,
            "not_home",
            second_leave,
            second_leave + timedelta(minutes=15, seconds=1),
        )
        _, rows = await history(conn)
        outside = sorted(
            (r for r in rows if (r["to_value"] or {}).get("presence") == "outside"),
            key=lambda r: r["at"],
        )
        assert [r["late"] for r in outside] == [False, True]


async def test_a_refused_publish_does_not_advance_state(dsn, home):
    async with session(dsn) as conn:
        denied = await as_role(conn, "perception_owner_reader")  # a reader may not write
        runtime = PresenceRuntime()
        await tick(home, denied, runtime, "home", NOW, NOW)
        out = await tick(home, denied, runtime, "home", NOW, NOW + CONFIRM_AFTER)
        assert out["outcome"] == "refused" and runtime.state.side is None


async def test_heartbeat_keeps_a_held_state_fresh(dsn, home):
    async with session(dsn) as conn:
        store = await as_role(conn, "perception_dots_writer")
        runtime = PresenceRuntime()
        await tick(home, store, runtime, "home", NOW, NOW)
        await tick(home, store, runtime, "home", NOW, NOW + CONFIRM_AFTER)
        beat = NOW + CONFIRM_AFTER + HEARTBEAT
        out = await tick(home, store, runtime, "home", beat - timedelta(seconds=5), beat)
        assert out["published"] == 1
        reader, _ = await history(conn)
        slot = await reader.read("owner.place", OWNER, as_of=beat)
        assert slot["value"]["presence"] == "inside" and slot["generation"] == 3
