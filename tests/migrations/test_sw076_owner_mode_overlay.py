"""sw_076: the owner mode overlay is owner-only, versioned and loosens only under step-up.

Real PostgreSQL. One row per mode records the owner's edits to its duration,
held classes and break-through senders. A write moves one version at a time
under a fresh owner action. A tightening needs nothing more; a loosening needs a
``mode_loosen`` step-up consumed in the same transaction (proven end to end in
``tests/integration/test_owner_mode_overlay.py``), so without one every
loosening refuses here. Safety and medication can never be held. The runtime
role has no privilege at all, and no row can be deleted or truncated. The
downgrade refuses while any row exists.
"""

import asyncio
from uuid import uuid4

import asyncpg
import pytest

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.migrations.test_sw072_decision_sets import _refused
from tests.switchboard.goal_ledger_fixtures import pools

pytestmark = pytest.mark.integration

_INSERT = (
    "INSERT INTO switchboard.owner_mode_overlay (mode, version, duration_s, holds, "
    "break_through, owner_action_id) VALUES ($1, $2, $3, $4::text[], $5::text[], $6)"
)
_UPDATE = (
    "UPDATE switchboard.owner_mode_overlay SET version = $2, duration_s = $3, holds = $4::text[], "
    "break_through = $5::text[], owner_action_id = $6 WHERE mode = $1"
)


def _ledger(container):
    return create_migrated_test_db(
        container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_076"},  # pinned-revision: owns the owner mode overlay
    )


async def _row(owner, mode):
    return await owner.fetchrow(
        "SELECT version, duration_s, holds, break_through FROM switchboard.owner_mode_overlay "
        "WHERE mode = $1",
        mode,
    )


async def _exercise(url):
    async with pools(url) as (owner, runtime):
        # The runtime role holds no privilege on the overlay.
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not await owner.fetchval(
                "SELECT has_table_privilege('butler_switchboard_rw', "
                "'switchboard.owner_mode_overlay', $1)",
                privilege,
            ), privilege
        assert await owner.fetchval(
            "SELECT relrowsecurity FROM pg_class WHERE oid = 'switchboard.owner_mode_overlay'::regclass"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.fetch("SELECT * FROM switchboard.owner_mode_overlay")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.execute(_INSERT, "focus", 1, None, ["proactive"], [], uuid4())

        # Tightening needs no step-up: hold more (open-ended is already the longest).
        await owner.execute(_INSERT, "focus", 1, None, ["proactive"], [], uuid4())
        await owner.execute(
            _UPDATE, "focus", 2, None, ["proactive", "reply_to_person"], [], uuid4()
        )
        assert tuple(await _row(owner, "focus")) == (2, None, ["proactive", "reply_to_person"], [])
        # Sick's default already holds proactive: re-stating it is no loosening.
        await owner.execute(_INSERT, "sick", 1, None, ["proactive"], [], uuid4())

        # Every loosening refuses without a mode_loosen step-up in this transaction.
        for args in (
            ("focus", 3, None, ["proactive"], []),  # one class fewer held
            ("focus", 3, None, ["proactive", "reply_to_person"], ["contact:a"]),  # a sender more
            ("focus", 3, 3600, ["proactive", "reply_to_person"], []),  # shorter than open-ended
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await owner.execute(_UPDATE, *args, uuid4())
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await owner.execute(_INSERT, "asleep", 1, None, [], ["contact:partner"], uuid4())
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await owner.execute(_UPDATE, "sick", 2, None, [], [], uuid4())
        assert tuple(await _row(owner, "focus")) == (2, None, ["proactive", "reply_to_person"], [])
        assert await _row(owner, "asleep") is None

        # One version at a time, under a fresh owner action, on the same mode.
        action = uuid4()
        await owner.execute(_INSERT, "hosting", 1, None, ["proactive"], [], action)
        for version, next_action in ((3, uuid4()), (1, uuid4()), (2, action)):
            with pytest.raises(asyncpg.PostgresError):
                await owner.execute(
                    _UPDATE, "hosting", version, None, ["proactive"], [], next_action
                )
        assert await _refused(
            owner,
            "UPDATE switchboard.owner_mode_overlay SET mode = 'holiday', version = 2, "
            "owner_action_id = $1 WHERE mode = 'hosting'",
            uuid4(),
        )
        with pytest.raises(asyncpg.PostgresError):
            await owner.execute(_INSERT, "focus", 2, None, ["proactive"], [], uuid4())

        # Closed values: the eight modes, unpinned classes only, no duplicates or blanks.
        for args in (
            ("in_flight", 1, None, [], []),
            ("holiday", 1, None, ["safety"], []),
            ("holiday", 1, None, ["medication"], []),
            ("holiday", 1, None, ["proactive", "proactive"], []),
            ("holiday", 1, 0, [], []),
        ):
            with pytest.raises(asyncpg.PostgresError):
                await owner.execute(_INSERT, *args, uuid4())

        # Nothing is deleted or truncated, even a statement that matches no row.
        for query in (
            "DELETE FROM switchboard.owner_mode_overlay",
            "DELETE FROM switchboard.owner_mode_overlay WHERE mode = 'emergency'",
            "TRUNCATE switchboard.owner_mode_overlay",
        ):
            assert await _refused(owner, query), query
        assert await owner.fetchval("SELECT count(*) FROM switchboard.owner_mode_overlay") == 3


def test_the_overlay_is_owner_only_versioned_and_tighten_only_without_step_up(
    postgres_container,
):
    url = _ledger(postgres_container)
    asyncio.run(_exercise(url))

    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    with pytest.raises(Exception, match="Cannot discard the owner mode overlay"):
        command.downgrade(config, "switchboard@sw_075")  # pinned-revision: sw_076 fence


def test_the_empty_overlay_round_trips(postgres_container):
    url = _ledger(postgres_container)
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_075")  # pinned-revision: sw_076 removal
    command.upgrade(config, "switchboard@sw_076")  # pinned-revision: sw_076 reapply
