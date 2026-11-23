"""sw_060: owner policy loosening under the step-up installs and round-trips.

Real PostgreSQL. sw_060 admits a ``loosen`` receipt and replaces the term
trigger; its downgrade restores sw_059's tighten-only trigger and receipt check
while no loosening exists (a loosening receipt fences it: see
``tests/integration/test_owner_stepup_consumers.py``).
"""

import asyncio

import pytest

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.switchboard.goal_ledger_fixtures import pools

pytestmark = pytest.mark.integration


async def _catalog(url):
    async with pools(url) as (owner, _):
        check = await owner.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'owner_policy_receipts_action_check'"
        )
        trigger = await owner.fetchval(
            "SELECT prosrc FROM pg_proc WHERE oid = 'switchboard.owner_policy_tighten_only'::regproc"
        )
        return "'loosen'" in check, "policy_loosen" in trigger


def test_sw060_admits_a_bound_loosening_and_round_trips_to_tighten_only(postgres_container):
    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
    )
    assert asyncio.run(_catalog(url)) == (True, True)
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_059")  # pinned-revision: sw_060 predecessor
    assert asyncio.run(_catalog(url)) == (False, False)
    command.upgrade(config, "switchboard@sw_060")  # pinned-revision: replays its upgrade
    assert asyncio.run(_catalog(url)) == (True, True)
