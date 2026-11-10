"""sw_066: an effect's delivery binding is set once, while the effect is still in flight.

Real PostgreSQL. Before the revision no binding can be added to an inserted effect; after it
``delivery_ref``, ``recipient_channel`` and a prompt's flag and expiry may be set from NULL while
the effect is ``dispatched``, never changed afterwards, never added once it has committed, and
lineage stays immutable. The downgrade restores the old guard.
"""

import asyncio
from uuid import uuid4

import asyncpg
import pytest

from alembic import command
from butlers.core.goal_admission import Evidence
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.switchboard.goal_admission_fixtures import limits
from tests.switchboard.goal_ledger_fixtures import admit, configure, effect, pools

pytestmark = pytest.mark.integration

COMMIT_WITH_BINDING = (
    "UPDATE switchboard.effects SET status = 'committed', delivery_ref = 'tg:1', "
    "prompt_accepts_input = true, prompt_expires_at = now() + interval '1 hour' WHERE id = $1"
)
BIND_WITHOUT_COMMIT = (
    "UPDATE switchboard.effects SET delivery_ref = 'tg:1', prompt_accepts_input = true, "
    "prompt_expires_at = now() + interval '1 hour' WHERE id = $1"
)


def _ledger(container, revision):
    return create_migrated_test_db(
        container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": revision},
    )


async def _effect(url, status):
    """An in-flight owner delivery: recipient and channel recorded as reserve() does."""
    async with pools(url) as (owner, runtime):
        await configure(owner, limits())
        goal = await admit(runtime, Evidence("message", "owner", owner=True))
        _, effect_id = await effect(
            owner, goal.goal_id, status=status, entity=uuid4(), channel="telegram"
        )
        return effect_id


async def _try(url, query, *args):
    async with pools(url) as (owner, _):
        try:
            await owner.execute(query, *args)
        except asyncpg.PostgresError:
            return False
        return True


def test_the_binding_is_set_once_and_only_by_the_commit(postgres_container):
    url = _ledger(postgres_container, "sw_065")  # pinned-revision: sw_066 predecessor
    before = asyncio.run(_effect(url, "dispatched"))
    assert asyncio.run(_try(url, COMMIT_WITH_BINDING, before)) is False

    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.upgrade(config, "switchboard@sw_066")  # pinned-revision: owns the guard
    inflight = asyncio.run(_effect(url, "dispatched"))
    assert asyncio.run(_try(url, COMMIT_WITH_BINDING, inflight)) is True
    for rewrite in (  # a second write, or any change of what was recorded
        "UPDATE switchboard.effects SET delivery_ref = 'tg:2' WHERE id = $1",
        "UPDATE switchboard.effects SET prompt_accepts_input = false WHERE id = $1",
        "UPDATE switchboard.effects SET prompt_expires_at = now() WHERE id = $1",
        "UPDATE switchboard.effects SET recipient_channel = 'email' WHERE id = $1",
        "UPDATE switchboard.effects SET recipient_entity = gen_random_uuid() WHERE id = $1",
        "UPDATE switchboard.effects SET args_digest = 'other' WHERE id = $1",
    ):
        assert asyncio.run(_try(url, rewrite, inflight)) is False, rewrite

    # A write outside the commit transition is refused: not on its own, not on dispatch.
    requested = asyncio.run(_effect(url, "requested"))
    assert asyncio.run(_try(url, BIND_WITHOUT_COMMIT, requested)) is False
    dispatch = (
        "UPDATE switchboard.effects SET status = 'dispatched', delivery_ref = 'tg:3' WHERE id = $1"
    )
    assert asyncio.run(_try(url, dispatch, requested)) is False
    # An effect that already committed without a binding never gains one.
    done = asyncio.run(_effect(url, "committed"))
    assert asyncio.run(_try(url, BIND_WITHOUT_COMMIT, done)) is False
    # The prompt binding check still holds: a prompt needs a delivery reference.
    bare = asyncio.run(_effect(url, "dispatched"))
    no_ref = (
        "UPDATE switchboard.effects SET status = 'committed', prompt_accepts_input = true, "
        "prompt_expires_at = now() + interval '1 hour' WHERE id = $1"
    )
    assert asyncio.run(_try(url, no_ref, bare)) is False

    command.downgrade(config, "switchboard@sw_065")  # pinned-revision: sw_066 fence
    after = asyncio.run(_effect(url, "dispatched"))
    assert asyncio.run(_try(url, COMMIT_WITH_BINDING, after)) is False
