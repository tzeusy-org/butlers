"""DB-level regression tests for dismissed-pair queue suppression (bu-ddhw6).

These tests execute the real ``_dismissed_pair_suppression_sql`` against a
migrated PostgreSQL database (via testcontainers/Docker) rather than asserting
on a source grep. They cover the two spec scenarios that were previously
untested at the DB level:

- ``relationship-merge-review`` — "the queue MUST stop listing that pair" once
  a pair is dismissed.
- ``relationship-entity-lifecycle`` — "Dismissed pair re-raises only on new
  evidence": a dismissed pair re-appears once a ``{predicate, shared_value}``
  not present in the dismissal snapshot arises.

Both column orderings of the dismissed pair (entity as ``entity_a`` vs
``entity_b``) are exercised.

EDGE BUG (bu-ddhw6): suppression used to be keyed on entity x evidence rather
than on the peer pair. If X shared value V with both Y and Z and only X-Y was
dismissed, X's entire dup row for V was suppressed — hiding the still-live X-Z
candidate from X's side of the queue. The ``test_xz_survives_xy_dismissal_same_value``
case is the synthetic-red proof for that fix: it fails against the old SQL and
passes against the pair-keyed SQL.

The suppression clause is exercised through both of its production call sites:
  1. ``_classify_entity_state`` (single-entity classification helper).
  2. the queue duplicate-candidate bucket SQL (``dup_detected_sql`` fragment).
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
    await p.execute("TRUNCATE TABLE relationship.merge_reviews CASCADE")
    await p.execute("TRUNCATE TABLE relationship.entity_facts CASCADE")
    await p.execute("TRUNCATE TABLE public.entities CASCADE")
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _make_entity(pool: asyncpg.Pool, name: str) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) "
        "VALUES ($1, 'person') RETURNING id",
        name,
    )


async def _add_fact(
    pool: asyncpg.Pool,
    *,
    subject: uuid.UUID,
    predicate: str,
    object_value: str,
) -> None:
    """Insert one active literal entity-fact with a fresh last_seen."""
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, validity, last_seen)
        VALUES ($1, $2, $3, 'literal', 'test', 'active', $4)
        """,
        subject,
        predicate,
        object_value,
        datetime.now(UTC),
    )


async def _dismiss_pair(
    pool: asyncpg.Pool,
    *,
    entity_a: uuid.UUID,
    entity_b: uuid.UUID,
    shared_facts: list[dict[str, str]],
) -> uuid.UUID:
    """Insert a ``merge_reviews`` row with outcome='dismissed'.

    Mirrors what ``dismiss_pair`` writes server-side: the ``shared_facts`` JSON
    snapshot is the suppression key. ``shared_facts`` entries are
    ``{"predicate": ..., "object": ...}`` dicts (per ``merge_review`` derivation).
    """
    return await pool.fetchval(
        """
        INSERT INTO relationship.merge_reviews
            (entity_a, entity_b, shared_facts, divergent_facts, outcome, reviewed_at)
        VALUES ($1, $2, $3, $4, 'dismissed', $5)
        RETURNING id
        """,
        entity_a,
        entity_b,
        # The registered JSONB codec encodes Python objects directly; passing a
        # pre-serialised string would store a JSON scalar (string), which
        # jsonb_array_elements() cannot iterate ("cannot extract elements from a
        # scalar").
        shared_facts,
        [],
        datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Queue dup-bucket fragment runner (call site #2: dup_detected_sql)
# ---------------------------------------------------------------------------


async def _queue_dup_entity_ids(pool: asyncpg.Pool, router_mod) -> set[uuid.UUID]:
    """Run the queue duplicate-candidate bucket SQL and return surfaced entity ids.

    This reproduces ``dup_detected_sql`` from ``get_entities_queue`` verbatim
    (the second production call site of ``_dismissed_pair_suppression_sql``), so
    the test asserts on the same SQL the endpoint runs.
    """
    dup_predicates_literal = ", ".join(f"'{p}'" for p in router_mod._DUP_DETECTION_PREDICATES)
    suppression = router_mod._dismissed_pair_suppression_sql("e.id", "grp.predicate", "grp.object")
    active = router_mod._active_entity_condition("e")
    rows = await pool.fetch(
        f"""
        SELECT DISTINCT e.id AS entity_id
        FROM public.entities e
        CROSS JOIN (
            SELECT predicate, object
            FROM relationship.entity_facts
            WHERE predicate IN ({dup_predicates_literal})
              AND validity = 'active'
            GROUP BY predicate, object
            HAVING count(DISTINCT subject) > 1
        ) AS grp
        JOIN relationship.entity_facts f_link
            ON f_link.subject = e.id
           AND f_link.predicate = grp.predicate
           AND f_link.object = grp.object
           AND f_link.validity = 'active'
        WHERE (e.metadata->>'unidentified') IS DISTINCT FROM 'true'
          AND {active}
          AND {suppression}
        """
    )
    return {r["entity_id"] for r in rows}


async def _classify(pool: asyncpg.Pool, router_mod, entity_id: uuid.UUID) -> str:
    state, _ = await router_mod._classify_entity_state(pool, entity_id)
    return state


# ---------------------------------------------------------------------------
# Scenario 1: dismissing a pair stops the queue from listing it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ordering", ["entity_a_first", "entity_b_first"])
async def test_dismissed_pair_drops_from_queue(pool, router_mod, ordering):
    """A dismissed A-B pair stops appearing in the queue (both column orderings).

    Covers ``relationship-merge-review`` "the queue MUST stop listing that pair"
    and asserts ordering-independence: who is stored as ``entity_a`` vs
    ``entity_b`` in the dismissal row must not matter.
    """
    a = await _make_entity(pool, "Alice")
    b = await _make_entity(pool, "Bob")
    await _add_fact(pool, subject=a, predicate="has-email", object_value="ab@example.com")
    await _add_fact(pool, subject=b, predicate="has-email", object_value="ab@example.com")

    # Before dismissal: both are duplicate-candidates.
    assert await _classify(pool, router_mod, a) == "duplicate-candidate"
    assert await _classify(pool, router_mod, b) == "duplicate-candidate"
    assert await _queue_dup_entity_ids(pool, router_mod) == {a, b}

    shared = [{"predicate": "has-email", "object": "ab@example.com"}]
    if ordering == "entity_a_first":
        await _dismiss_pair(pool, entity_a=a, entity_b=b, shared_facts=shared)
    else:
        await _dismiss_pair(pool, entity_a=b, entity_b=a, shared_facts=shared)

    # After dismissal: the pair is fully suppressed regardless of column ordering.
    assert await _classify(pool, router_mod, a) != "duplicate-candidate"
    assert await _classify(pool, router_mod, b) != "duplicate-candidate"
    assert await _queue_dup_entity_ids(pool, router_mod) == set()


# ---------------------------------------------------------------------------
# Scenario 2: re-raise only on NEW evidence
# ---------------------------------------------------------------------------


async def test_dismissed_pair_re_raises_on_new_evidence(pool, router_mod):
    """A dismissed pair re-appears when a shared value NOT in the snapshot arises.

    Covers ``relationship-entity-lifecycle`` "Dismissed pair re-raises only on
    new evidence": the dismissal snapshot covered the email but not a later
    shared phone, so the new phone evidence re-raises the pair.
    """
    a = await _make_entity(pool, "Alice")
    b = await _make_entity(pool, "Bob")
    await _add_fact(pool, subject=a, predicate="has-email", object_value="ab@example.com")
    await _add_fact(pool, subject=b, predicate="has-email", object_value="ab@example.com")

    # Dismiss the email-shared pair; snapshot covers ONLY the email.
    await _dismiss_pair(
        pool,
        entity_a=a,
        entity_b=b,
        shared_facts=[{"predicate": "has-email", "object": "ab@example.com"}],
    )
    assert await _queue_dup_entity_ids(pool, router_mod) == set()
    assert await _classify(pool, router_mod, a) != "duplicate-candidate"

    # New shared evidence appears: both now also share a phone NOT in the snapshot.
    await _add_fact(pool, subject=a, predicate="has-phone", object_value="+15550001234")
    await _add_fact(pool, subject=b, predicate="has-phone", object_value="+15550001234")

    # The pair re-raises on the new (uncovered) phone evidence.
    assert await _queue_dup_entity_ids(pool, router_mod) == {a, b}
    assert await _classify(pool, router_mod, a) == "duplicate-candidate"
    assert await _classify(pool, router_mod, b) == "duplicate-candidate"


# ---------------------------------------------------------------------------
# Scenario 3: EDGE BUG — dismissing X-Y must not hide X-Z for the same value
# ---------------------------------------------------------------------------


async def test_xz_survives_xy_dismissal_same_value(pool, router_mod):
    """Synthetic-red for the edge bug: X-Y dismissal must not suppress X-Z.

    X shares email V with BOTH Y and Z. Only X-Y is dismissed. The old SQL keyed
    suppression on entity x evidence, so X's single dup row for V was suppressed
    entirely — wrongly hiding the still-live X-Z candidate from X's side. The
    pair-keyed fix keeps X in the queue because the X-Z pair is undismissed.

    All three entities remain duplicate-candidates here: X via undismissed X-Z,
    Z via undismissed X-Z/Y-Z, and Y via undismissed Y-Z. The load-bearing
    assertion is that X and Z survive — pre-fix X disappears, post-fix it stays.
    (The "all peers dismissed → suppress" path is covered separately by
    ``test_all_peers_dismissed_suppresses_row``.)
    """
    x = await _make_entity(pool, "Xavier")
    y = await _make_entity(pool, "Yolanda")
    z = await _make_entity(pool, "Zane")
    shared_value = "shared@example.com"
    for ent in (x, y, z):
        await _add_fact(pool, subject=ent, predicate="has-email", object_value=shared_value)

    # Dismiss ONLY X-Y for that shared value.
    await _dismiss_pair(
        pool,
        entity_a=x,
        entity_b=y,
        shared_facts=[{"predicate": "has-email", "object": shared_value}],
    )

    surfaced = await _queue_dup_entity_ids(pool, router_mod)

    # X stays: it still has an undismissed peer (Z) on the same value.
    assert x in surfaced, "X-Z is undismissed; X must remain a duplicate-candidate"
    # Z stays: its peers X and Y are both undismissed against Z.
    assert z in surfaced, "Z has undismissed peers; Z must remain a duplicate-candidate"
    # Classification agrees on the per-entity helper call site too.
    assert await _classify(pool, router_mod, x) == "duplicate-candidate"
    assert await _classify(pool, router_mod, z) == "duplicate-candidate"

    # Y also stays: it still shares V with Z (Y-Z is undismissed). Only the
    # specific X-Y pair was dismissed, which must not suppress any other pair.
    assert y in surfaced, "Y-Z is undismissed; Y must remain a duplicate-candidate"
    assert await _classify(pool, router_mod, y) == "duplicate-candidate"


async def test_all_peers_dismissed_suppresses_row(pool, router_mod):
    """When every sharing peer is dismissed, the row is suppressed (control case).

    Complements the edge-bug test: X shares V with Y and Z, and BOTH X-Y and X-Z
    are dismissed (snapshots cover V). X must then drop out entirely — confirming
    the pair-keyed clause still suppresses once no undismissed peer remains.
    """
    x = await _make_entity(pool, "Xavier")
    y = await _make_entity(pool, "Yolanda")
    z = await _make_entity(pool, "Zane")
    shared_value = "shared@example.com"
    for ent in (x, y, z):
        await _add_fact(pool, subject=ent, predicate="has-email", object_value=shared_value)

    snap = [{"predicate": "has-email", "object": shared_value}]
    await _dismiss_pair(pool, entity_a=x, entity_b=y, shared_facts=snap)
    await _dismiss_pair(pool, entity_a=z, entity_b=x, shared_facts=snap)  # reversed ordering

    surfaced = await _queue_dup_entity_ids(pool, router_mod)
    assert x not in surfaced, "All of X's peers on V are dismissed; X must be suppressed"
    assert await _classify(pool, router_mod, x) != "duplicate-candidate"
    # Y and Z still share V with each other (undismissed Y-Z), so they remain.
    assert {y, z} <= surfaced
