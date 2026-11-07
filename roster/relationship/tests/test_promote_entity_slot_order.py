"""POST /entities takes its identity slots once, in one order (bu-dhloi1.66)."""

from __future__ import annotations

import asyncio
import importlib
import shutil
from unittest.mock import patch

import asyncpg
import pytest

from butlers.testing.db_manager import db_manager

router = importlib.import_module("butlers.api._roster.relationship.router")
assert_fact = importlib.import_module("butlers.tools.relationship.relationship_assert_fact")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def rel_pool(db):
    pool = await db("core", "memory", "relationship", "approvals")
    await pool.execute(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Owner', 'person', ARRAY['owner'])"
    )
    return pool


def _promote(pool: asyncpg.Pool, name: str, addresses: list[str]):
    body = router.PromoteEntityRequest(
        canonical_name=name,
        entity_type="person",
        initial_facts=[
            {"predicate": "has-email", "object": address, "object_kind": "literal"}
            for address in addresses
        ],
    )
    return router.promote_entity(body, db=db_manager({"relationship": pool}))


async def test_two_promotes_sharing_addresses_in_opposite_order_do_not_deadlock(rel_pool) -> None:
    """Each promote pauses after its first fact, so per-fact slot locks would cross."""
    arrived = {"First": asyncio.Event(), "Second": asyncio.Event()}
    real_assert = assert_fact.relationship_assert_fact

    async def pausing_assert(*args, **kwargs):
        result = await real_assert(*args, **kwargs)
        name = await kwargs["conn"].fetchval(
            "SELECT canonical_name FROM public.entities WHERE id = $1", kwargs["subject"]
        )
        arrived[name].set()
        other = arrived["Second" if name == "First" else "First"]
        try:
            # With the union taken up front the other promote is parked on the first
            # slot, so this wait runs out and this promote finishes.
            await asyncio.wait_for(other.wait(), timeout=1.5)
        except TimeoutError:
            pass
        return result

    with patch.object(assert_fact, "relationship_assert_fact", pausing_assert):
        results = await asyncio.wait_for(
            asyncio.gather(
                _promote(rel_pool, "First", ["a@example.test", "b@example.test"]),
                _promote(rel_pool, "Second", ["b@example.test", "a@example.test"]),
                return_exceptions=True,
            ),
            timeout=30,
        )

    assert not any(isinstance(result, asyncpg.DeadlockDetectedError) for result in results), results
