"""sw_067: ``broker_refusals.reason`` admits ``capability_unavailable``, and only then.

Real PostgreSQL. Before the revision the CHECK refuses the new reason; after it the reason
is admitted alongside every earlier one; the downgrade refuses while a refusal with the new
reason exists (the ledger is append-only history) and otherwise restores the strict list.
"""

import asyncio

import asyncpg
import pytest

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.switchboard.goal_ledger_fixtures import pools

pytestmark = pytest.mark.integration

INSERT = (
    "INSERT INTO switchboard.broker_refusals (capability, reason) VALUES ('messenger__send', $1)"
)
EARLIER = (
    "policy_refused",
    "owner_policy_ask_on_read",
    "broker_unavailable",
)


async def _insert(url, reason):
    async with pools(url) as (owner, _):
        try:
            await owner.execute(INSERT, reason)
        except asyncpg.PostgresError:
            return False
        return True


def test_the_reason_is_admitted_from_sw_067_and_pins_the_downgrade(postgres_container):
    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_066"},  # pinned-revision: sw_067 predecessor
    )
    assert asyncio.run(_insert(url, "capability_unavailable")) is False

    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.upgrade(config, "switchboard@sw_067")  # pinned-revision: owns the reason
    for reason in (*EARLIER, "capability_unavailable"):
        assert asyncio.run(_insert(url, reason)) is True, reason
    assert asyncio.run(_insert(url, "invented_reason")) is False

    with pytest.raises(Exception, match="capability_unavailable"):
        command.downgrade(config, "switchboard@sw_066")  # pinned-revision: sw_067 fence
