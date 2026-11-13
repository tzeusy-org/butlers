"""sw_077: owner mode entries are owner-only, versioned and end early only under step-up.

Real PostgreSQL. One row per mode records the owner's current entry: when it
started, when it is scheduled to end (or open-ended), the owner cause, and
when the owner exited it. A write moves one version at a time under a fresh
owner action. Entering a mode or extending an active entry needs nothing more;
exiting or ending an active entry sooner needs a ``mode_loosen`` step-up
consumed in the same transaction (proven end to end in
``tests/integration/test_owner_mode_entries.py``), so without one it refuses
here. Only owner causes are stored: no entry is inferred. The runtime role has
no privilege at all, and no row can be deleted or truncated. The downgrade
refuses while any row exists.

``now()`` is the transaction's start, so every statement in one transaction
below shares one clock with the trigger.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.perception.modes import ModeEntry, active_entry, entry_text
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.migrations.test_sw072_decision_sets import _refused
from tests.switchboard.goal_ledger_fixtures import pools

pytestmark = pytest.mark.integration

_COLUMNS = "(mode, version, since, until, cause, exited_at, owner_action_id)"


def _ledger(container):
    return create_migrated_test_db(
        container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_077"},  # pinned-revision: owns the owner mode entries
    )


def _enter(mode, version, until="NULL", cause="owner_declared"):
    """Enter ``mode`` now, at ``version``, ending at the SQL expression ``until``."""
    if version == 1:
        query = (
            f"INSERT INTO switchboard.owner_mode_entries {_COLUMNS} "
            f"VALUES ($1, 1, now(), {until}, $2, NULL, $3)"
        )
        return query, (mode, cause, uuid4())
    query = (
        f"UPDATE switchboard.owner_mode_entries SET version = $2, since = now(), "
        f"until = {until}, cause = $3, exited_at = NULL, owner_action_id = $4 WHERE mode = $1"
    )
    return query, (mode, version, cause, uuid4())


def _amend(mode, version, *, until="until", exited="NULL", since="since", cause="cause"):
    """Rewrite ``mode``'s current entry at ``version`` (SQL expressions over the old row)."""
    query = (
        f"UPDATE switchboard.owner_mode_entries SET version = $2, since = {since}, "
        f"until = {until}, cause = {cause}, exited_at = {exited}, owner_action_id = $3 "
        "WHERE mode = $1"
    )
    return query, (mode, version, uuid4())


async def _version(owner, mode):
    return await owner.fetchval(
        "SELECT version FROM switchboard.owner_mode_entries WHERE mode = $1", mode
    )


async def _refuses_privilege(owner, statement):
    query, args = statement
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await owner.execute(query, *args)


async def _refuses(owner, statement):
    query, args = statement
    with pytest.raises(asyncpg.PostgresError):
        await owner.execute(query, *args)


async def _exercise(url):
    async with pools(url) as (owner, runtime):
        # The runtime role holds no privilege on the entries.
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not await owner.fetchval(
                "SELECT has_table_privilege('butler_switchboard_rw', "
                "'switchboard.owner_mode_entries', $1)",
                privilege,
            ), privilege
        assert await owner.fetchval(
            "SELECT relrowsecurity FROM pg_class "
            "WHERE oid = 'switchboard.owner_mode_entries'::regclass"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.fetch("SELECT * FROM switchboard.owner_mode_entries")
        query, args = _enter("focus", 1)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.execute(query, *args)

        # Entering and extending need no step-up.
        await owner.execute(*_pair(_enter("focus", 1, "now() + interval '2 hours'")))
        await owner.execute(*_pair(_amend("focus", 2, until="until + interval '1 hour'")))
        await owner.execute(*_pair(_amend("focus", 3, until="NULL")))
        await owner.execute(*_pair(_amend("focus", 4, cause="'owner_confirmed'")))
        assert await _version(owner, "focus") == 4

        # Exiting, or ending an active entry sooner, refuses without a step-up.
        await _refuses_privilege(owner, _amend("focus", 5, exited="now()"))
        await _refuses_privilege(owner, _amend("focus", 5, until="now() + interval '1 hour'"))
        await owner.execute(*_pair(_enter("asleep", 1, "now() + interval '8 hours'")))
        await _refuses_privilege(owner, _amend("asleep", 2, until="until - interval '1 second'"))
        assert await _version(owner, "focus") == 4
        assert await _version(owner, "asleep") == 1

        # An active entry keeps its start; an exit stamps now and changes nothing else.
        await _refuses(owner, _amend("focus", 5, since="since - interval '1 hour'"))
        await _refuses(owner, _amend("asleep", 2, exited="now() - interval '1 second'"))
        await _refuses(owner, _amend("asleep", 2, exited="now()", until="NULL"))
        await _refuses(owner, _amend("asleep", 2, exited="now()", cause="'owner_confirmed'"))

        # A fresh entry starts now, open, and ends in the future.
        for statement in (
            _enter("hosting", 1, "now()"),
            _enter("hosting", 1, "now() - interval '1 hour'"),
            (
                f"INSERT INTO switchboard.owner_mode_entries {_COLUMNS} "
                "VALUES ('hosting', 1, now() - interval '1 second', NULL, 'owner_declared', "
                "NULL, $1)",
                (uuid4(),),
            ),
            (
                f"INSERT INTO switchboard.owner_mode_entries {_COLUMNS} "
                "VALUES ('hosting', 1, now(), NULL, 'owner_declared', now(), $1)",
                (uuid4(),),
            ),
        ):
            await _refuses(owner, statement)
        assert await _version(owner, "hosting") is None

        # Only owner causes, only the eight modes.
        for mode, cause in (("hosting", "inferred"), ("hosting", "m7_rule"), ("in_flight", None)):
            await _refuses(owner, _enter(mode, 1, cause=cause or "owner_declared"))

        # One version at a time, under a fresh owner action, on the same mode.
        action = uuid4()
        await owner.execute(
            f"INSERT INTO switchboard.owner_mode_entries {_COLUMNS} "
            "VALUES ('holiday', 1, now(), NULL, 'owner_declared', NULL, $1)",
            action,
        )
        for version in (3, 1):
            await _refuses(owner, _amend("holiday", version, until="until"))
        with pytest.raises(asyncpg.PostgresError):
            await owner.execute(
                "UPDATE switchboard.owner_mode_entries SET version = 2, owner_action_id = $1 "
                "WHERE mode = 'holiday'",
                action,
            )
        assert await _refused(
            owner,
            "UPDATE switchboard.owner_mode_entries SET mode = 'travel', version = 2, "
            "owner_action_id = $1 WHERE mode = 'holiday'",
            uuid4(),
        )
        await _refuses(owner, _enter("holiday", 1))

        # Nothing is deleted or truncated, even a statement that matches no row.
        for query in (
            "DELETE FROM switchboard.owner_mode_entries",
            "DELETE FROM switchboard.owner_mode_entries WHERE mode = 'emergency'",
            "TRUNCATE switchboard.owner_mode_entries",
        ):
            assert await _refused(owner, query), query
        assert await owner.fetchval("SELECT count(*) FROM switchboard.owner_mode_entries") == 3


async def _ended(url):
    """An entry that has ended, by its end passing or by exit, is re-entered with no step-up."""
    async with pools(url) as (owner, _runtime):
        # Its scheduled end has passed by the next transaction.
        await owner.execute(*_pair(_enter("sick", 1, "now() + interval '1 millisecond'")))
        await asyncio.sleep(0.05)
        # Ending it again "sooner" is a fresh entry, not an early end.
        await _refuses(owner, _amend("sick", 2, exited="now()"))
        await owner.execute(*_pair(_enter("sick", 2, "now() + interval '1 hour'")))
        assert await _version(owner, "sick") == 2


async def _parity(url):
    """The SQL entry text and activity predicate, checked against fixed renderings."""
    async with pools(url) as (owner, _runtime):
        text = await owner.fetchval(
            "SELECT switchboard.owner_mode_entry_text("
            "'2026-05-04 07:00:00.123456+00'::timestamptz, NULL, 'owner_declared', "
            "'2026-05-04 08:00:00+00'::timestamptz)"
        )
        assert text == "entry|1777878000123456|none|owner_declared|exited"
        text = await owner.fetchval(
            "SELECT switchboard.owner_mode_entry_text("
            "'2026-05-04 16:00:00.123456+09'::timestamptz, "
            "'2026-05-04 15:00:00.123456+00'::timestamptz, 'owner_confirmed', NULL)"
        )
        assert text == "entry|1777878000123456|1777906800123456|owner_confirmed|open"

        # Python's entry_text renders the same rows byte for byte.
        since = datetime(2026, 5, 4, 7, 0, 0, 123456, tzinfo=UTC)
        for until, cause, exited_at in (
            (None, "owner_declared", since + timedelta(hours=1)),
            (since + timedelta(hours=8), "owner_confirmed", None),
            (since + timedelta(microseconds=1), "owner_declared", None),
        ):
            expected = entry_text(ModeEntry("focus", since, until, cause), exited=bool(exited_at))
            got = await owner.fetchval(
                "SELECT switchboard.owner_mode_entry_text($1, $2, $3, $4)",
                since,
                until,
                cause,
                exited_at,
            )
            assert got == expected

        # One activity rule: an entry ending exactly now has ended, in SQL as in Python.
        at = since + timedelta(hours=2)
        for until, exited_at in (
            (None, None),
            (at + timedelta(microseconds=1), None),
            (at, None),
            (at - timedelta(microseconds=1), None),
            (None, at - timedelta(minutes=1)),
            (at + timedelta(hours=1), at - timedelta(minutes=1)),
        ):
            row = {
                "since": since,
                "until": until,
                "cause": "owner_declared",
                "exited_at": exited_at,
            }
            in_sql = await owner.fetchval(
                "SELECT switchboard.owner_mode_entry_active($1, $2, $3)", exited_at, until, at
            )
            assert in_sql is (active_entry("focus", row, at) is not None), (until, exited_at)
        assert await owner.fetchval(
            "SELECT NOT switchboard.owner_mode_entry_active(NULL, $1, $1)", at
        )


def _pair(statement):
    query, args = statement
    return (query, *args)


def test_entries_are_owner_only_versioned_and_end_early_only_under_step_up(postgres_container):
    url = _ledger(postgres_container)
    asyncio.run(_exercise(url))
    asyncio.run(_ended(url))
    asyncio.run(_parity(url))

    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    with pytest.raises(Exception, match="Cannot discard the owner mode entries"):
        command.downgrade(config, "switchboard@sw_076")  # pinned-revision: sw_077 fence


def test_the_empty_entries_round_trip(postgres_container):
    url = _ledger(postgres_container)
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_076")  # pinned-revision: sw_077 removal
    command.upgrade(config, "switchboard@sw_077")  # pinned-revision: sw_077 reapply
