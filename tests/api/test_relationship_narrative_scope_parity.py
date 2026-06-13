"""Parity regression (bu-3jrq3): narrative-store scope filter MUST be uniform.

The four entity-anchored narrative-store read surfaces — dashboard facts drill,
delta banner, compare blocks, and the ``relationship_lookup`` MCP tool — used to
disagree on scope filtering:

- drill / delta / compare hardcoded ``scope = 'relationship'``;
- the lookup tool applied NO scope filter at all.

Meanwhile ``memory_store_fact`` defaults ``scope = 'global'`` and relationship
runtime guidance routes edge-facts through it, so narrative facts routinely land
with ``scope = 'global'`` — visible via the lookup tool but invisible in the
dashboard drill, delta banner, and compare blocks.

Canonical rule (now centralized in ``staleness.narrative_scope_sql``):
``scope IN ('relationship', 'global')``. A fact stored at default
(``global``) scope against an entity MUST be visible in ALL four surfaces; a
fact under a foreign butler scope (e.g. ``'health'``) MUST be visible in NONE.

This test seeds the same narrative fact set once and asserts every surface
returns an identical predicate set.
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
    await p.execute("TRUNCATE TABLE public.facts CASCADE")
    await p.execute("TRUNCATE TABLE public.entities CASCADE")
    yield p
    await p.close()


async def _make_entity(pool: asyncpg.Pool, name: str) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) "
        "VALUES ($1, 'person') RETURNING id",
        name,
    )


async def _seed_narrative_fact(
    pool: asyncpg.Pool,
    *,
    entity_id: uuid.UUID,
    predicate: str,
    content: str,
    scope: str,
) -> None:
    await pool.execute(
        """
        INSERT INTO public.facts
            (entity_id, subject, predicate, content, scope, validity,
             source_butler, confidence, observed_at, created_at)
        VALUES ($1, $2, $3, $4, $5, 'active', 'relationship', 0.9, $6, $6)
        """,
        entity_id,
        content,  # subject is a human-readable label; any non-null value works
        predicate,
        content,
        scope,
        datetime.now(UTC),
    )


# Predicate → scope. The canonical rule includes relationship + global and
# excludes any foreign butler scope.
_VISIBLE = {"narr_rel": "relationship", "narr_global": "global"}
_HIDDEN = {"narr_health": "health"}
_EXPECTED_VISIBLE = set(_VISIBLE)


async def _seed_all(pool: asyncpg.Pool, entity_id: uuid.UUID) -> None:
    for predicate, scope in {**_VISIBLE, **_HIDDEN}.items():
        await _seed_narrative_fact(
            pool,
            entity_id=entity_id,
            predicate=predicate,
            content=f"value-{predicate}",
            scope=scope,
        )


async def test_narrative_scope_parity_across_all_four_surfaces(pool):
    """drill / delta / compare / lookup MUST return the identical narrative set."""
    from butlers.tools.relationship.relationship_lookup import _fetch_narrative_facts

    router = _relationship_router_module()
    _fetch_narrative_drill_facts = router._fetch_narrative_drill_facts
    _fetch_narrative_delta_facts = router._fetch_narrative_delta_facts
    _fetch_narrative_facts_for_compare = router._fetch_narrative_facts_for_compare

    entity_id = await _make_entity(pool, "Parity Subject")
    await _seed_all(pool, entity_id)

    # An epoch marked_at so the delta surface returns everything changed "since".
    marked_at = datetime(2000, 1, 1, tzinfo=UTC)

    drill_rows = await _fetch_narrative_drill_facts(
        pool, entity_id, validity="active", predicate=None, limit=200
    )
    delta_rows = await _fetch_narrative_delta_facts(pool, entity_id, marked_at)
    compare_rows = await _fetch_narrative_facts_for_compare(pool, entity_id)
    lookup_rows = await _fetch_narrative_facts(pool, entity_id)

    drill_preds = {r["predicate"] for r in drill_rows}
    delta_preds = {r["predicate"] for r in delta_rows}
    compare_preds = {r["predicate"] for r in compare_rows}
    lookup_preds = {r["predicate"] for r in lookup_rows}

    # Parity: every surface returns the SAME set.
    assert drill_preds == delta_preds == compare_preds == lookup_preds, (
        "narrative read surfaces disagree on scope: "
        f"drill={drill_preds} delta={delta_preds} "
        f"compare={compare_preds} lookup={lookup_preds}"
    )

    # And that set is exactly the canonical visible scopes (relationship+global),
    # never the foreign 'health' scope.
    assert drill_preds == _EXPECTED_VISIBLE
    assert "narr_health" not in drill_preds


async def test_default_global_fact_is_visible_in_all_surfaces(pool):
    """Acceptance: a default-scope (global) fact appears in ALL four surfaces."""
    from butlers.tools.relationship.relationship_lookup import _fetch_narrative_facts

    router = _relationship_router_module()
    _fetch_narrative_drill_facts = router._fetch_narrative_drill_facts
    _fetch_narrative_delta_facts = router._fetch_narrative_delta_facts
    _fetch_narrative_facts_for_compare = router._fetch_narrative_facts_for_compare

    entity_id = await _make_entity(pool, "Default Scope Subject")
    await _seed_narrative_fact(
        pool,
        entity_id=entity_id,
        predicate="works_at",
        content="Acme Corp",
        scope="global",  # memory_store_fact default
    )

    marked_at = datetime(2000, 1, 1, tzinfo=UTC)
    drill = await _fetch_narrative_drill_facts(
        pool, entity_id, validity="active", predicate=None, limit=200
    )
    delta = await _fetch_narrative_delta_facts(pool, entity_id, marked_at)
    compare = await _fetch_narrative_facts_for_compare(pool, entity_id)
    lookup = await _fetch_narrative_facts(pool, entity_id)

    for rows, surface in (
        (drill, "drill"),
        (delta, "delta"),
        (compare, "compare"),
        (lookup, "lookup"),
    ):
        preds = {r["predicate"] for r in rows}
        assert "works_at" in preds, f"default-global fact invisible in {surface} surface"
