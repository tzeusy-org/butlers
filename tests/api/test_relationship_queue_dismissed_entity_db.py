"""DB-level regression tests for per-entity queue dismissal.

The Dismiss button (``POST /entities/queue/dismiss``) writes an active
``queue.dismissed`` state-marker triple. Before this fix the queue READ path
(``get_entities_queue``) and the single-entity classifier
(``_classify_entity_state``) never honoured that triple, so the dismissed
entity was re-classified into the same bucket on the post-dismiss refetch and
reappeared — the button looked like a no-op.

These tests run the real endpoint SQL against a migrated PostgreSQL database
(via testcontainers/Docker) and assert that a dismissed entity:

  1. leaves ``GET /entities/queue`` (every bucket: unidentified / stale /
     duplicate-candidate), and
  2. classifies as ``healthy`` via ``_classify_entity_state``,

while a still-undismissed peer in the same dup group remains surfaced.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


def _relationship_router_module():
    """Load the dynamically-discovered relationship router module."""
    from butlers.api.router_discovery import discover_butler_routers

    for name, module in discover_butler_routers():
        if name == "relationship":
            return module
    raise AssertionError("relationship router module not discovered")


@pytest.fixture(scope="module")
def router_mod():
    return _relationship_router_module()


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision core + memory + relationship chains (flat public topology)."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "relationship"],
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE relationship.entity_facts CASCADE")
    await p.execute("TRUNCATE TABLE public.entities CASCADE")
    yield p
    await p.close()


class _FakeDB:
    """Minimal DatabaseManager stand-in exposing ``pool(name)`` for the router."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    def pool(self, _name: str):
        return self._pool


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _make_entity(
    pool: asyncpg.Pool, name: str, *, unidentified: bool = False, roles: list[str] | None = None
) -> uuid.UUID:
    metadata = {"unidentified": "true"} if unidentified else {}
    return await pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, metadata, roles)
        VALUES ($1, 'person', $2, $3)
        RETURNING id
        """,
        name,
        metadata,
        roles or [],
    )


async def _add_fact(
    pool: asyncpg.Pool,
    *,
    subject: uuid.UUID,
    predicate: str,
    object_value: str,
    last_seen: datetime | None = None,
) -> None:
    """Insert one active literal entity-fact."""
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, validity, last_seen)
        VALUES ($1, $2, $3, 'literal', 'test', 'active', $4)
        """,
        subject,
        predicate,
        object_value,
        last_seen or datetime.now(UTC),
    )


async def _dismiss(pool: asyncpg.Pool, router_mod, entity_id: uuid.UUID) -> None:
    """Write the ``queue.dismissed`` triple exactly as the dismiss endpoint does."""
    await _add_fact(
        pool,
        subject=entity_id,
        predicate=router_mod._QUEUE_DISMISSED_PREDICATE,
        object_value=router_mod._QUEUE_DISMISSED_OBJECT,
    )


async def _queue_entity_ids(pool: asyncpg.Pool, router_mod) -> set[uuid.UUID]:
    """Run the real ``get_entities_queue`` endpoint and return surfaced entity ids."""
    resp = await router_mod.get_entities_queue(limit=200, offset=0, db=_FakeDB(pool))
    return {item.entity_id for item in resp.items}


async def _classify(pool: asyncpg.Pool, router_mod, entity_id: uuid.UUID) -> str:
    state, _ = await router_mod._classify_entity_state(pool, entity_id)
    return state


# Every test needs a registered owner entity to pass the Amendment 12b gate.
@pytest.fixture
async def owner(pool) -> uuid.UUID:
    return await _make_entity(pool, "Owner", roles=["owner"])


# ---------------------------------------------------------------------------
# Scenario 1: dismissing an unidentified entity removes it from the queue
# ---------------------------------------------------------------------------


async def test_dismissed_unidentified_drops_from_queue(pool, router_mod, owner):
    e = await _make_entity(pool, "Mystery", unidentified=True)

    assert e in await _queue_entity_ids(pool, router_mod)
    assert await _classify(pool, router_mod, e) == "unidentified"

    await _dismiss(pool, router_mod, e)

    assert e not in await _queue_entity_ids(pool, router_mod)
    assert await _classify(pool, router_mod, e) == "healthy"


# ---------------------------------------------------------------------------
# Scenario 2: dismissing a stale entity removes it from the queue
# ---------------------------------------------------------------------------


async def test_dismissed_stale_drops_from_queue(pool, router_mod, owner):
    e = await _make_entity(pool, "Faded")
    # An entity with no fresh fact is stale (no active fact within 365 days).

    assert e in await _queue_entity_ids(pool, router_mod)
    assert await _classify(pool, router_mod, e) == "stale"

    await _dismiss(pool, router_mod, e)

    assert e not in await _queue_entity_ids(pool, router_mod)
    assert await _classify(pool, router_mod, e) == "healthy"


# ---------------------------------------------------------------------------
# Scenario 3: dismissing one dup-candidate drops it but not its peer
# ---------------------------------------------------------------------------


async def test_dismissed_dup_candidate_drops_only_itself(pool, router_mod, owner):
    a = await _make_entity(pool, "Alice")
    b = await _make_entity(pool, "Bob")
    await _add_fact(pool, subject=a, predicate="has-email", object_value="ab@example.com")
    await _add_fact(pool, subject=b, predicate="has-email", object_value="ab@example.com")

    assert await _queue_entity_ids(pool, router_mod) >= {a, b}
    assert await _classify(pool, router_mod, a) == "duplicate-candidate"

    await _dismiss(pool, router_mod, a)

    surfaced = await _queue_entity_ids(pool, router_mod)
    assert a not in surfaced, "dismissed entity must leave the queue"
    assert b in surfaced, "peer is undismissed and still shares the value — must remain"
    assert await _classify(pool, router_mod, a) == "healthy"
    assert await _classify(pool, router_mod, b) == "duplicate-candidate"
