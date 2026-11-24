"""sw_078: the owner mode tables carry their activation read policies and still no grant.

Real PostgreSQL. ``perception_context_writer`` gets a SELECT policy on the
entries; ``butler_switchboard_rw`` gets SELECT policies on the entries and the
overlay. No role gets a write policy (owner writes run as the table owner,
outside RLS). No privilege is granted, so both tables stay dark: the runtime
role is still refused outright. The downgrade drops the policies.
"""

import asyncio

import asyncpg
import pytest

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.switchboard.goal_ledger_fixtures import pools

pytestmark = pytest.mark.integration

_TABLES = ("owner_mode_entries", "owner_mode_overlay")
_READERS = ("perception_context_writer", "butler_switchboard_rw")
_EXPECTED = {
    (
        "owner_mode_entries",
        "owner_mode_entries_projection_read",
        "{perception_context_writer}",
        "SELECT",
    ),
    ("owner_mode_entries", "owner_mode_entries_runtime_read", "{butler_switchboard_rw}", "SELECT"),
    ("owner_mode_overlay", "owner_mode_overlay_runtime_read", "{butler_switchboard_rw}", "SELECT"),
}


def _ledger(container):
    return create_migrated_test_db(
        container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_078"},  # pinned-revision: owns the mode read policies
    )


async def _policies(owner):
    rows = await owner.fetch(
        "SELECT tablename, policyname, roles::text AS roles, cmd, qual, with_check "
        "FROM pg_policies WHERE schemaname = 'switchboard' AND tablename = ANY($1::text[])",
        list(_TABLES),
    )
    return rows


async def _exercise(url):
    async with pools(url) as (owner, runtime):
        rows = await _policies(owner)
        assert {(r["tablename"], r["policyname"], r["roles"], r["cmd"]) for r in rows} == _EXPECTED
        for row in rows:
            # Each reads only as its own role, and none admits a write.
            role = row["roles"].strip("{}")
            assert row["qual"] == f"(CURRENT_USER = '{role}'::name)"
            assert row["with_check"] is None
        # Still no privilege for any reader: the policies are inert until activation.
        for table in _TABLES:
            for role in _READERS:
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                    assert not await owner.fetchval(
                        "SELECT has_table_privilege($1, $2, $3)",
                        role,
                        f"switchboard.{table}",
                        privilege,
                    ), (table, role, privilege)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await runtime.fetch(f"SELECT * FROM switchboard.{table}")


async def _dropped(url):
    async with pools(url) as (owner, _runtime):
        assert await _policies(owner) == []


def test_the_mode_tables_carry_read_policies_and_no_grant(postgres_container):
    url = _ledger(postgres_container)
    asyncio.run(_exercise(url))

    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_077")  # pinned-revision: sw_078 removal
    asyncio.run(_dropped(url))
    command.upgrade(config, "switchboard@sw_078")  # pinned-revision: sw_078 reapply
    asyncio.run(_exercise(url))
