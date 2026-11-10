"""sw_064: the legacy goal states convert losslessly and leave the status CHECK.

Real PostgreSQL. A ledger at sw_063 holds one goal in each legacy state. ``settling`` becomes
``waiting`` on a settle envelope, ``awaiting_approval`` becomes ``waiting`` on an approval envelope
bound to its parked effect, and ``recovering`` becomes ``admitted``; each keeps its original status
in ``legacy_status``. An ``awaiting_approval`` goal with nothing parked to bind is refused rather
than guessed, and the downgrade restores the legacy states.
"""

import asyncio
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.core.goal_admission import Evidence
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.switchboard.goal_admission_fixtures import limits
from tests.switchboard.goal_ledger_fixtures import admit, configure, effect, pools

pytestmark = pytest.mark.integration

OWNER = Evidence("message", "owner", owner=True)


def _ledger(container, revision=None):
    return create_migrated_test_db(
        container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": revision} if revision else None,
    )


async def _goal_in(owner, runtime, status, *, effect_status="committed"):
    goal = await admit(runtime, Evidence("message", f"owner-{uuid4()}", owner=True))
    _, effect_id = await effect(owner, goal.goal_id, status=effect_status)
    await owner.execute(
        "UPDATE switchboard.goals SET status = $2 WHERE id = $1", goal.goal_id, status
    )
    return goal.goal_id, effect_id


async def _populate(url, *, parked_approval=True):
    async with pools(url) as (owner, runtime):
        await configure(owner, limits())
        ids = {}
        ids["settling"], _ = await _goal_in(owner, runtime, "settling")
        ids["recovering"], _ = await _goal_in(owner, runtime, "recovering")
        ids["approval"], ids["parked"] = await _goal_in(
            owner,
            runtime,
            "awaiting_approval",
            effect_status="parked" if parked_approval else "committed",
        )
        return ids


async def _read(url, ids):
    async with pools(url) as (owner, _):
        out = {}
        for name in ("settling", "recovering", "approval"):
            out[name] = {
                "goal": dict(
                    await owner.fetchrow("SELECT * FROM switchboard.goals WHERE id = $1", ids[name])
                ),
                "envelopes": [
                    dict(r)
                    for r in await owner.fetch(
                        "SELECT * FROM switchboard.wait_envelopes WHERE goal_id = $1", ids[name]
                    )
                ],
                "generations": await owner.fetchval(
                    "SELECT count(*) FROM switchboard.wait_generations WHERE goal_id = $1",
                    ids[name],
                ),
            }
        return out


def test_sw064_converts_each_legacy_state_and_the_downgrade_restores_them(postgres_container):
    url = _ledger(postgres_container, "sw_063")  # pinned-revision: sw_064 predecessor
    ids = asyncio.run(_populate(url))
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.upgrade(config, "switchboard@sw_064")  # pinned-revision: owns the remap
    rows = asyncio.run(_read(url, ids))

    settling = rows["settling"]
    assert (settling["goal"]["status"], settling["goal"]["legacy_status"]) == (
        "waiting",
        "settling",
    )
    (envelope,) = settling["envelopes"]
    assert (envelope["kind"], envelope["state"], envelope["max_wakes"]) == ("settle", "open", 1)
    assert envelope["approval_effect_id"] is None
    assert settling["generations"] == 1
    assert settling["goal"]["plan_version"] == envelope["plan_version"] == 1

    approval = rows["approval"]
    assert (approval["goal"]["status"], approval["goal"]["legacy_status"]) == (
        "waiting",
        "awaiting_approval",
    )
    (bound,) = approval["envelopes"]
    assert (bound["kind"], bound["approval_effect_id"]) == ("approval", ids["parked"])
    assert bound["approval_digest"] is not None

    recovering = rows["recovering"]
    assert (recovering["goal"]["status"], recovering["goal"]["legacy_status"]) == (
        "admitted",
        "recovering",
    )
    assert recovering["envelopes"] == []

    async def legacy_state_is_refused():
        async with pools(url) as (owner, _):
            with pytest.raises(asyncpg.CheckViolationError):
                await owner.execute(
                    "UPDATE switchboard.goals SET status = 'settling' WHERE id = $1",
                    ids["settling"],
                )

    asyncio.run(legacy_state_is_refused())

    command.downgrade(config, "switchboard@sw_063")  # pinned-revision: sw_064 fence
    restored = asyncio.run(_read(url, ids))
    assert restored["settling"]["goal"]["status"] == "settling"
    assert restored["approval"]["goal"]["status"] == "awaiting_approval"
    assert restored["recovering"]["goal"]["status"] == "recovering"
    assert {e["state"] for e in restored["settling"]["envelopes"]} == {"cancelled"}


def test_sw064_refuses_an_approval_wait_with_nothing_parked_to_bind(postgres_container):
    url = _ledger(postgres_container, "sw_063")  # pinned-revision: sw_064 predecessor
    asyncio.run(_populate(url, parked_approval=False))
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    with pytest.raises(DBAPIError, match="no parked effect"):
        command.upgrade(config, "switchboard@sw_064")  # pinned-revision: owns the remap
