"""Regression: a rolled-back POST /entities batch must never orphan a park (bu-g27ib).

bu-g27ib routed relationship_assert_fact()'s owner-carve-out and family-gate
parks through the approvals choke point (park_pending_action), which commits
its ``pending_actions`` row -- and fires the owner push -- on its OWN
connection acquired from *pool*, independent of any caller-supplied
``conn``/transaction (the push path needs real ``pool.acquire()`` semantics).

promote_entity() (POST /entities) asserts a BATCH of ``initial_facts`` inside
one outer transaction, passing ``conn=`` so every fact write participates in
it. Before the bu-g27ib fix, an EARLIER fact in the batch could park
(committing that row on a separate connection) and a LATER fact's
ValueError (e.g. an unregistered predicate) would then roll back the outer
transaction -- undoing the entity/fact writes but NOT the already-committed
park. Net effect: an orphaned ``pending_actions`` row (and an owner push)
referencing an entity that was never persisted.

The fix pre-validates every fact's predicate/conf/object_kind BEFORE the
transaction starts (``validate_fact_fields_or_raise``), so a later
ValueError can no longer fire after an earlier park already committed. This
test proves the exploit is closed: no orphaned pending_actions row, and no
entity row, survive a batch containing both a parking fact and an invalid
one.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

import asyncpg
import pytest
from fastapi import HTTPException

from butlers.testing.schema_standins import ENTITY_PREDICATE_REGISTRY, PENDING_ACTIONS
from roster.relationship.tests.evidence_schema import apply_evidence_schema

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


# ---------------------------------------------------------------------------
# Lazy loader for roster/relationship/api/router.py (roster/ is not an
# importable package; the module is loaded via importlib, same as production
# router_discovery.py and the sibling lazy-loader in
# tests/relationship/test_relationship_assert_fact_unit.py). Reuses
# router_discovery.py's exact module-name convention
# (f"{butler_name}_api_router") so this shares the cached module with any
# other test/app code in the same process that already loaded it.
# ---------------------------------------------------------------------------


def _load_relationship_api_router() -> ModuleType:
    module_name = "relationship_api_router"
    if module_name in sys.modules:
        return sys.modules[module_name]
    router_path = (
        Path(__file__).parents[1] / "api" / "router.py"
    )  # roster/relationship/api/router.py
    spec = importlib.util.spec_from_file_location(module_name, router_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Minimal real-Postgres schema (mirrors test_family_confidence_gate.py)
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Fresh DB with entities, entity_facts, predicate_registry, pending_actions."""
    async with provisioned_postgres_pool() as p:
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.entities (
                id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name TEXT        NOT NULL DEFAULT '',
                name           TEXT        NOT NULL DEFAULT '',
                entity_type    TEXT        NOT NULL DEFAULT 'person',
                aliases        TEXT[]      NOT NULL DEFAULT '{}',
                metadata       JSONB       DEFAULT '{}'::jsonb,
                roles          TEXT[]      NOT NULL DEFAULT '{}',
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("CREATE SCHEMA IF NOT EXISTS relationship")
        await p.execute(ENTITY_PREDICATE_REGISTRY.ddl(schema="relationship"))
        await p.execute("""
            INSERT INTO relationship.entity_predicate_registry
                (predicate, kind, object_kind, description)
            VALUES
                ('parent-of', 'relational', 'entity', 'Parent-child relationship.'),
                ('has-email', 'contact',    'literal', 'Email address.')
            ON CONFLICT (predicate) DO NOTHING
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS relationship.entity_facts (
                id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
                subject     UUID        NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
                predicate   TEXT        NOT NULL,
                object      TEXT        NOT NULL,
                object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
                src         TEXT        NOT NULL,
                conf        FLOAT       NOT NULL DEFAULT 1.0
                                CHECK (conf >= 0.0 AND conf <= 1.0),
                last_seen   TIMESTAMPTZ,
                observed_at TIMESTAMPTZ,
                metadata    JSONB,
                weight      INT,
                verified    BOOL        NOT NULL DEFAULT false,
                "primary"   BOOL,
                validity    TEXT        NOT NULL DEFAULT 'active'
                                CHECK (validity IN ('active', 'retracted', 'superseded')),
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_ef_spo_active
                ON relationship.entity_facts (subject, predicate, object)
                WHERE validity = 'active'
        """)
        await p.execute(PENDING_ACTIONS.ddl())
        # rel_034: the central writer persists evidence and a coverage receipt in
        # the same transaction as the fact, so this schema is not optional.
        await apply_evidence_schema(p)
        yield p


async def _make_owner_entity(pool: asyncpg.Pool) -> UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Owner', 'person', ARRAY['owner']) RETURNING id",
    )


class _FakeDatabaseManager:
    """Duck-types router.py's DatabaseManager enough for _pool(db) to work."""

    def __init__(self, real_pool: asyncpg.Pool) -> None:
        self._real_pool = real_pool

    def pool(self, _butler_name: str) -> asyncpg.Pool:
        return self._real_pool


async def _call_promote_entity(pool: asyncpg.Pool, body: Any):
    router_module = _load_relationship_api_router()
    return await router_module.promote_entity(body, db=_FakeDatabaseManager(pool))


def _promote_entity_request(**kwargs: Any) -> Any:
    """Build a PromoteEntityRequest via the lazily-loaded router module.

    ``initial_facts`` entries are plain dicts -- PromoteEntityRequest's
    field type coerces them into InitialFact instances, so this avoids a
    second lazy-load for the InitialFact class (router.py does not bind it
    as a module-level name, only PromoteEntityRequest).
    """
    router_module = _load_relationship_api_router()
    return router_module.PromoteEntityRequest(**kwargs)


# ---------------------------------------------------------------------------
# The exploit
# ---------------------------------------------------------------------------


async def test_parking_fact_followed_by_invalid_predicate_leaves_no_orphan(
    pool: asyncpg.Pool,
) -> None:
    """A parking fact + a later invalid-predicate fact must not orphan a park.

    Before the fix: fact[0] (low-confidence parent-of, family gate) parks --
    committing a pending_actions row on its own connection. fact[1] (an
    unregistered predicate) raises ValueError -> HTTPException(422) ->
    the outer transaction rolls back the entity creation, but NOT the
    already-committed park. This test proves that can no longer happen:
    pre-validation catches the bad predicate before the transaction (and
    therefore before any assert in the batch can park), so both the entity
    write AND any would-be park are cleanly avoided together.
    """
    await _make_owner_entity(pool)

    placeholder_child = str(uuid.uuid4())
    body = _promote_entity_request(
        canonical_name="Exploit Target",
        entity_type="person",
        initial_facts=[
            {
                "predicate": "parent-of",
                "object": placeholder_child,
                "object_kind": "entity",
                "conf": 0.5,  # below _FAMILY_GATE_CONF -> parks
            },
            {
                "predicate": "not-a-real-predicate",
                "object": "whatever",
            },
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        await _call_promote_entity(pool, body)
    assert exc_info.value.status_code == 422

    # No entity was persisted (the outer transaction rolled back).
    entity_count = await pool.fetchval(
        "SELECT COUNT(*) FROM public.entities WHERE canonical_name = 'Exploit Target'"
    )
    assert entity_count == 0, "entity row must not survive the rollback"

    # No pending_actions row survives either -- this is the actual
    # regression assertion: pre-validation must run before ANY assert in the
    # batch can park, so the family-gate fact never gets a chance to commit
    # its row on the separate connection in the first place.
    pending_count = await pool.fetchval(
        "SELECT COUNT(*) FROM pending_actions WHERE tool_name = 'relationship_assert_fact'"
    )
    assert pending_count == 0, (
        "no pending_actions row may survive when the batch that produced it rolled back "
        "(orphaned park referencing a never-persisted entity)"
    )


async def test_valid_batch_with_parking_fact_still_creates_entity_and_parks(
    pool: asyncpg.Pool,
) -> None:
    """Sanity: a batch with ONLY valid facts still creates the entity and parks.

    Guards against an overly-aggressive fix that blocks legitimate
    low-confidence-kinship-then-something-else batches.
    """
    await _make_owner_entity(pool)

    placeholder_child = str(uuid.uuid4())
    body = _promote_entity_request(
        canonical_name="Legit Target",
        entity_type="person",
        initial_facts=[
            {
                "predicate": "parent-of",
                "object": placeholder_child,
                "object_kind": "entity",
                "conf": 0.5,
            },
            {
                "predicate": "has-email",
                "object": "legit@example.com",
                "object_kind": "literal",
            },
        ],
    )

    summary = await _call_promote_entity(pool, body)
    assert summary.canonical_name == "Legit Target"

    entity_count = await pool.fetchval(
        "SELECT COUNT(*) FROM public.entities WHERE canonical_name = 'Legit Target'"
    )
    assert entity_count == 1

    pending_count = await pool.fetchval(
        "SELECT COUNT(*) FROM pending_actions WHERE tool_name = 'relationship_assert_fact'"
    )
    assert pending_count == 1, "the low-confidence kinship fact should still park"

    # has-email is not gated and not an owner write, so it commits directly.
    fact_count = await pool.fetchval(
        "SELECT COUNT(*) FROM relationship.entity_facts "
        "WHERE subject = $1 AND predicate = 'has-email' AND validity = 'active'",
        summary.id,
    )
    assert fact_count == 1
