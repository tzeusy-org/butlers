"""Tests for the memory-curation scheduled job (behavior #1: edge backfill).

The job scans relationship.facts for active rows with object_entity_id set
and a relational predicate, then proposes the corresponding structured edge
via relationship_assert_fact.  This file covers:

  - No-op when no candidate facts exist
  - Direct relational predicate backfill (partner-of, child-of, etc.)
  - Alias predicate normalisation (partner_of → partner-of)
  - Prose predicate content keyword mapping (living_arrangement → partner-of)
  - Idempotency: already-active edges are reported as unchanged
  - Owner carve-out: owner-entity subjects route to pending_approval
  - Family confidence gate: kinship at low conf → pending_approval
  - Prose predicate with no recognisable keywords → skipped
  - Error resilience: DB errors during assert are counted, not raised
  - _infer_predicate_from_prose unit tests (pure logic, no DB)
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

# The roster job module is loaded by conftest.py via _load_roster_jobs and
# registered in sys.modules as butlers.jobs._roster.relationship_jobs.
from butlers.jobs._roster.relationship_jobs import (  # type: ignore[import]
    _backfill_object_entity_ids,
    _infer_predicate_from_prose,
    run_fact_retraction_curation,
    run_memory_curation,
    run_pending_actions_curation,
)

# ---------------------------------------------------------------------------
# Skip if Docker unavailable
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# Schema creation helpers
# ---------------------------------------------------------------------------

_CREATE_ENTITIES_SQL = """
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
"""

_CREATE_ENTITY_FACTS_SQL = """
CREATE SCHEMA IF NOT EXISTS relationship;
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
"""

_CREATE_ENTITY_FACTS_UNIQUE_IDX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_ef_spo_active
    ON relationship.entity_facts (subject, predicate, object)
    WHERE validity = 'active'
"""

_CREATE_PREDICATE_REGISTRY_SQL = """
CREATE TABLE IF NOT EXISTS relationship.entity_predicate_registry (
    predicate   TEXT        NOT NULL PRIMARY KEY,
    kind        TEXT        NOT NULL,
    object_kind TEXT        NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_SEED_PREDICATES_SQL = """
INSERT INTO relationship.entity_predicate_registry
    (predicate, kind, object_kind, description)
VALUES
    ('partner-of',   'relational', 'entity', 'Partner relationship.'),
    ('parent-of',    'relational', 'entity', 'Parent-child relationship.'),
    ('child-of',     'relational', 'entity', 'Child-parent relationship.'),
    ('family-of',    'relational', 'entity', 'Family relationship.'),
    ('friend-of',    'relational', 'entity', 'Friendship.'),
    ('colleague-of', 'relational', 'entity', 'Professional colleague.'),
    ('knows',        'relational', 'entity', 'Generic acquaintance.'),
    ('works-at',     'relational', 'entity', 'Works at organisation.'),
    ('member-of',    'relational', 'entity', 'Member of group.')
ON CONFLICT (predicate) DO NOTHING
"""

_CREATE_PENDING_ACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS pending_actions (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_name    TEXT        NOT NULL,
    tool_args    JSONB       NOT NULL,
    agent_summary TEXT,
    session_id   UUID,
    status       VARCHAR     NOT NULL DEFAULT 'pending',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ,
    decided_by   TEXT,
    decided_at   TIMESTAMPTZ,
    execution_result JSONB,
    approval_rule_id UUID,
    why          TEXT,
    evidence     JSONB       NOT NULL DEFAULT '[]'::jsonb
)
"""

_CREATE_FACTS_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    subject          TEXT        NOT NULL DEFAULT '',
    predicate        TEXT        NOT NULL,
    content          TEXT        NOT NULL DEFAULT '',
    validity         TEXT        NOT NULL DEFAULT 'active',
    scope            TEXT        NOT NULL DEFAULT 'relationship',
    entity_id        UUID,
    object_entity_id UUID,
    confidence       FLOAT       NOT NULL DEFAULT 1.0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata         JSONB       DEFAULT '{}'::jsonb
)
"""

_CREATE_STATE_SQL = """
CREATE TABLE IF NOT EXISTS state (
    key        TEXT        NOT NULL PRIMARY KEY,
    value      JSONB       NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    version    INTEGER     NOT NULL DEFAULT 1
)
"""


async def _setup_schema(pool: asyncpg.Pool) -> None:
    """Create the minimal schema needed by run_memory_curation tests."""
    await pool.execute(_CREATE_ENTITIES_SQL)
    await pool.execute(_CREATE_ENTITY_FACTS_SQL)
    await pool.execute(_CREATE_ENTITY_FACTS_UNIQUE_IDX_SQL)
    await pool.execute(_CREATE_PREDICATE_REGISTRY_SQL)
    await pool.execute(_SEED_PREDICATES_SQL)
    await pool.execute(_CREATE_PENDING_ACTIONS_SQL)
    await pool.execute(_CREATE_FACTS_SQL)
    await pool.execute(_CREATE_STATE_SQL)


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Fresh isolated DB with memory-curation schema."""
    async with provisioned_postgres_pool() as p:
        await _setup_schema(p)
        yield p


# ---------------------------------------------------------------------------
# Entity helpers
# ---------------------------------------------------------------------------


async def _make_entity(
    pool: asyncpg.Pool,
    *,
    name: str = "Test Person",
    roles: list[str] | None = None,
) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, name, entity_type, roles) "
        "VALUES ($1, $1, 'person', $2) RETURNING id",
        name,
        roles or [],
    )


async def _insert_prose_fact(
    pool: asyncpg.Pool,
    *,
    predicate: str,
    content: str,
    subject_entity_id: uuid.UUID | None,
    object_entity_id: uuid.UUID,
    validity: str = "active",
) -> uuid.UUID:
    """Insert a row into the prose facts table; return the fact id."""
    return await pool.fetchval(
        """
        INSERT INTO facts (predicate, content, entity_id, object_entity_id, validity)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
        """,
        predicate,
        content,
        subject_entity_id,
        object_entity_id,
        validity,
    )


async def _count_entity_facts(
    pool: asyncpg.Pool,
    *,
    subject: uuid.UUID,
    predicate: str,
    object_entity: uuid.UUID,
) -> int:
    return await pool.fetchval(
        """
        SELECT COUNT(*) FROM relationship.entity_facts
        WHERE subject = $1
          AND predicate = $2
          AND object = $3::text
          AND object_kind = 'entity'
          AND validity = 'active'
        """,
        subject,
        predicate,
        str(object_entity),
    )


async def _count_pending_actions(pool: asyncpg.Pool) -> int:
    return await pool.fetchval("SELECT COUNT(*) FROM pending_actions")


# ---------------------------------------------------------------------------
# Pure-unit tests: _infer_predicate_from_prose (no DB required)
# ---------------------------------------------------------------------------


class TestInferPredicateFromProse:
    """Pure-logic tests for the content-keyword inference helper.

    These are synchronous unit tests — no DB, no asyncio.  The class-level
    pytestmark overrides the module-level asyncio mark so pytest does not
    warn about sync functions being marked async.
    """

    pytestmark = [
        pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
    ]

    # --- living_arrangement / relationship_status / relationship_type ---

    def test_living_arrangement_partner_keyword(self):
        result = _infer_predicate_from_prose("living_arrangement", "Cohabiting partner")
        assert result == ("partner-of", 0.9)

    def test_living_arrangement_wife_keyword(self):
        result = _infer_predicate_from_prose("living_arrangement", "Lives with wife Sarah")
        assert result == ("partner-of", 0.9)

    def test_living_arrangement_husband_keyword(self):
        result = _infer_predicate_from_prose("living_arrangement", "Husband moved in last year")
        assert result == ("partner-of", 0.9)

    def test_relationship_status_married_keyword(self):
        result = _infer_predicate_from_prose("relationship_status", "Married to Chloe Wong")
        assert result == ("partner-of", 0.9)

    def test_relationship_status_boyfriend_keyword(self):
        result = _infer_predicate_from_prose("relationship_status", "Boyfriend since 2020")
        assert result == ("partner-of", 0.9)

    def test_relationship_type_fiance_keyword(self):
        result = _infer_predicate_from_prose("relationship_type", "Fiancée of Tom Brown")
        assert result == ("partner-of", 0.9)

    def test_living_arrangement_no_match_returns_none(self):
        result = _infer_predicate_from_prose("living_arrangement", "Lives alone in a studio flat")
        assert result is None

    def test_relationship_status_no_match_returns_none(self):
        result = _infer_predicate_from_prose("relationship_status", "Single and happy")
        assert result is None

    # --- family_relationship ---

    def test_family_relationship_mother_keyword(self):
        pred, conf = _infer_predicate_from_prose("family_relationship", "Mummy is mother")
        assert pred == "child-of"
        assert conf < 0.8  # must be below family gate threshold

    def test_family_relationship_father_keyword(self):
        pred, conf = _infer_predicate_from_prose("family_relationship", "Father is James")
        assert pred == "child-of"
        assert conf < 0.8

    def test_family_relationship_son_keyword(self):
        pred, conf = _infer_predicate_from_prose("family_relationship", "Son named Oliver")
        assert pred == "parent-of"
        assert conf < 0.8

    def test_family_relationship_daughter_keyword(self):
        pred, conf = _infer_predicate_from_prose("family_relationship", "Daughter Maya")
        assert pred == "parent-of"
        assert conf < 0.8

    def test_family_relationship_sibling_keyword(self):
        pred, conf = _infer_predicate_from_prose("family_relationship", "Sibling Alice")
        assert pred == "family-of"
        assert conf < 0.8

    def test_family_relationship_sister_keyword(self):
        pred, conf = _infer_predicate_from_prose("family_relationship", "Sister Jane")
        assert pred == "family-of"
        assert conf < 0.8

    def test_family_relationship_generic_fallback(self):
        pred, conf = _infer_predicate_from_prose("family_relationship", "Distant relative")
        assert pred == "family-of"
        assert conf < 0.8

    # --- case insensitivity ---

    def test_partner_keyword_case_insensitive(self):
        result = _infer_predicate_from_prose("living_arrangement", "PARTNER since forever")
        assert result is not None
        assert result[0] == "partner-of"

    # --- unknown predicate ---

    def test_unknown_predicate_returns_none(self):
        result = _infer_predicate_from_prose("unknown_predicate", "some content")
        assert result is None


# ---------------------------------------------------------------------------
# Integration tests: run_memory_curation behaviour
# ---------------------------------------------------------------------------


class TestMemoryCurationNoOp:
    """Job is a no-op when no candidate facts exist."""

    async def test_empty_facts_table_returns_zeros(self, pool: asyncpg.Pool):
        result = await run_memory_curation(pool)
        assert result["facts_scanned"] == 0
        assert result["edges_proposed"] == 0
        assert result["edges_inserted"] == 0
        assert result["edges_pending_approval"] == 0
        assert result["errors"] == 0

    async def test_no_relational_facts_returns_zeros(self, pool: asyncpg.Pool):
        """Facts without object_entity_id set are ignored."""
        subject = await _make_entity(pool, name="Alice")
        # Prose fact without object_entity_id — not a candidate.
        await pool.execute(
            "INSERT INTO facts (predicate, content, entity_id) VALUES ($1, $2, $3)",
            "living_arrangement",
            "Lives with partner",
            subject,
        )
        result = await run_memory_curation(pool)
        assert result["facts_scanned"] == 0

    async def test_retracted_facts_are_ignored(self, pool: asyncpg.Pool):
        """Retracted (validity != 'active') facts are not processed."""
        subject = await _make_entity(pool, name="Alice")
        obj = await _make_entity(pool, name="Bob")
        await _insert_prose_fact(
            pool,
            predicate="partner-of",
            content="Partner",
            subject_entity_id=subject,
            object_entity_id=obj,
            validity="retracted",
        )
        result = await run_memory_curation(pool)
        assert result["facts_scanned"] == 0


class TestMemoryCurationDirectPredicates:
    """Direct relational predicates (already in registry format) are backfilled."""

    async def test_partner_of_direct_inserts_edge(self, pool: asyncpg.Pool):
        subject = await _make_entity(pool, name="Alice")
        obj = await _make_entity(pool, name="Bob")
        await _insert_prose_fact(
            pool,
            predicate="partner-of",
            content="Partner",
            subject_entity_id=subject,
            object_entity_id=obj,
        )

        result = await run_memory_curation(pool)

        assert result["facts_scanned"] == 1
        assert result["edges_proposed"] == 1
        assert result["edges_inserted"] == 1
        assert result["errors"] == 0
        # Verify the edge landed in entity_facts.
        count = await _count_entity_facts(
            pool, subject=subject, predicate="partner-of", object_entity=obj
        )
        assert count == 1

    async def test_friend_of_direct_inserts_edge(self, pool: asyncpg.Pool):
        subject = await _make_entity(pool, name="Alice")
        obj = await _make_entity(pool, name="Carol")
        await _insert_prose_fact(
            pool,
            predicate="friend-of",
            content="Close friend",
            subject_entity_id=subject,
            object_entity_id=obj,
        )

        result = await run_memory_curation(pool)

        assert result["edges_inserted"] == 1
        count = await _count_entity_facts(
            pool, subject=subject, predicate="friend-of", object_entity=obj
        )
        assert count == 1

    async def test_alias_predicate_partner_of_normalised(self, pool: asyncpg.Pool):
        """partner_of (underscore alias) is normalised to partner-of by the writer."""
        subject = await _make_entity(pool, name="Alice")
        obj = await _make_entity(pool, name="Dave")
        await _insert_prose_fact(
            pool,
            predicate="partner_of",  # underscore alias
            content="Partner",
            subject_entity_id=subject,
            object_entity_id=obj,
        )

        result = await run_memory_curation(pool)

        assert result["edges_inserted"] == 1
        # The writer normalises the alias; edge is stored as partner-of.
        count = await _count_entity_facts(
            pool, subject=subject, predicate="partner-of", object_entity=obj
        )
        assert count == 1

    async def test_married_to_alias_normalised_to_partner_of(self, pool: asyncpg.Pool):
        """married_to (alias) normalises to partner-of."""
        subject = await _make_entity(pool, name="Alice")
        obj = await _make_entity(pool, name="Eve")
        await _insert_prose_fact(
            pool,
            predicate="married_to",
            content="Married since 2018",
            subject_entity_id=subject,
            object_entity_id=obj,
        )

        result = await run_memory_curation(pool)

        assert result["edges_inserted"] == 1
        count = await _count_entity_facts(
            pool, subject=subject, predicate="partner-of", object_entity=obj
        )
        assert count == 1

    async def test_sibling_of_alias_normalised_to_family_of(self, pool: asyncpg.Pool):
        """sibling_of (alias) normalises to family-of."""
        subject = await _make_entity(pool, name="Alice")
        obj = await _make_entity(pool, name="Frank")
        await _insert_prose_fact(
            pool,
            predicate="sibling_of",
            content="Sibling",
            subject_entity_id=subject,
            object_entity_id=obj,
        )

        result = await run_memory_curation(pool)

        assert result["edges_inserted"] == 1
        count = await _count_entity_facts(
            pool, subject=subject, predicate="family-of", object_entity=obj
        )
        assert count == 1


class TestMemoryCurationProsePredicates:
    """Prose predicates (living_arrangement, etc.) are mapped via content keywords."""

    async def test_living_arrangement_partner_keyword_inserts_partner_of(self, pool: asyncpg.Pool):
        subject = await _make_entity(pool, name="Alice")
        obj = await _make_entity(pool, name="Bob")
        await _insert_prose_fact(
            pool,
            predicate="living_arrangement",
            content="Cohabiting partner with Bob for 3 years",
            subject_entity_id=subject,
            object_entity_id=obj,
        )

        result = await run_memory_curation(pool)

        assert result["facts_scanned"] == 1
        assert result["edges_proposed"] == 1
        assert result["edges_inserted"] == 1
        count = await _count_entity_facts(
            pool, subject=subject, predicate="partner-of", object_entity=obj
        )
        assert count == 1

    async def test_relationship_status_married_inserts_partner_of(self, pool: asyncpg.Pool):
        subject = await _make_entity(pool, name="Alice")
        obj = await _make_entity(pool, name="Bob")
        await _insert_prose_fact(
            pool,
            predicate="relationship_status",
            content="Married to Bob since 2020",
            subject_entity_id=subject,
            object_entity_id=obj,
        )

        result = await run_memory_curation(pool)

        assert result["edges_inserted"] == 1

    async def test_living_arrangement_no_keyword_skipped(self, pool: asyncpg.Pool):
        """living_arrangement without partner keyword produces no edge."""
        subject = await _make_entity(pool, name="Alice")
        obj = await _make_entity(pool, name="Roommate")
        await _insert_prose_fact(
            pool,
            predicate="living_arrangement",
            content="Shares a flat",
            subject_entity_id=subject,
            object_entity_id=obj,
        )

        result = await run_memory_curation(pool)

        assert result["facts_scanned"] == 1
        assert result["edges_proposed"] == 0
        assert result["edges_skipped_no_mapping"] == 1
        assert (
            await _count_entity_facts(
                pool, subject=subject, predicate="partner-of", object_entity=obj
            )
            == 0
        )

    async def test_family_relationship_mother_routes_to_pending_approval(self, pool: asyncpg.Pool):
        """family_relationship with parent keyword uses conf < 0.8 → pending_approval
        for non-owner entities (family confidence gate fires)."""
        subject = await _make_entity(pool, name="Alice")
        obj = await _make_entity(pool, name="Mummy")
        await _insert_prose_fact(
            pool,
            predicate="family_relationship",
            content="Mummy is Alice's mother",
            subject_entity_id=subject,
            object_entity_id=obj,
        )

        result = await run_memory_curation(pool)

        # Low conf → family gate → pending_approval (no direct insert).
        assert result["edges_proposed"] == 1
        assert result["edges_pending_approval"] == 1
        assert result["edges_inserted"] == 0
        assert await _count_pending_actions(pool) == 1


class TestMemoryCurationIdempotency:
    """Repeated runs do not create duplicate edges."""

    async def test_already_active_edge_reported_as_unchanged(self, pool: asyncpg.Pool):
        """When the edge already exists with the same provenance, the writer returns
        unchanged.  Pre-insert must use the same src/conf/verified/last_seen as the
        job (src='memory_curation', conf=1.0, verified=False, last_seen=NULL)."""
        subject = await _make_entity(pool, name="Alice")
        obj = await _make_entity(pool, name="Bob")
        # Pre-insert with identical provenance to what the job will use.
        await pool.execute(
            """
            INSERT INTO relationship.entity_facts
                (subject, predicate, object, object_kind, src, conf, verified, validity)
            VALUES ($1, 'partner-of', $2::text, 'entity', 'memory_curation', 1.0, false, 'active')
            """,
            subject,
            str(obj),
        )
        await _insert_prose_fact(
            pool,
            predicate="partner-of",
            content="Partner",
            subject_entity_id=subject,
            object_entity_id=obj,
        )

        result = await run_memory_curation(pool)

        assert result["edges_proposed"] == 1
        assert result["edges_unchanged"] == 1
        assert result["edges_inserted"] == 0
        # Still only one edge row.
        assert (
            await _count_entity_facts(
                pool, subject=subject, predicate="partner-of", object_entity=obj
            )
            == 1
        )

    async def test_second_run_is_idempotent(self, pool: asyncpg.Pool):
        """Running the job twice doesn't double-insert edges."""
        subject = await _make_entity(pool, name="Alice")
        obj = await _make_entity(pool, name="Bob")
        await _insert_prose_fact(
            pool,
            predicate="partner-of",
            content="Partner",
            subject_entity_id=subject,
            object_entity_id=obj,
        )

        first = await run_memory_curation(pool)
        second = await run_memory_curation(pool)

        assert first["edges_inserted"] == 1
        assert second["edges_unchanged"] == 1
        assert (
            await _count_entity_facts(
                pool, subject=subject, predicate="partner-of", object_entity=obj
            )
            == 1
        )


class TestMemoryCurationOwnerCarveOut:
    """Owner-entity subjects always go to pending_approval (RFC 0017 §2.3)."""

    async def test_owner_entity_subject_routes_to_pending_approval(self, pool: asyncpg.Pool):
        owner = await _make_entity(pool, name="Owner", roles=["owner"])
        partner = await _make_entity(pool, name="Chloe")
        await _insert_prose_fact(
            pool,
            predicate="partner-of",
            content="Partner",
            subject_entity_id=owner,
            object_entity_id=partner,
        )

        result = await run_memory_curation(pool)

        assert result["edges_proposed"] == 1
        assert result["edges_pending_approval"] == 1
        assert result["edges_inserted"] == 0
        # No direct entity_facts row.
        assert (
            await _count_entity_facts(
                pool, subject=owner, predicate="partner-of", object_entity=partner
            )
            == 0
        )
        # pending_actions row created.
        assert await _count_pending_actions(pool) == 1

    async def test_owner_subject_partner_of_from_prose_routes_to_pending(self, pool: asyncpg.Pool):
        """living_arrangement with partner keyword + owner subject → pending_approval."""
        owner = await _make_entity(pool, name="Owner", roles=["owner"])
        partner = await _make_entity(pool, name="Chloe")
        await _insert_prose_fact(
            pool,
            predicate="living_arrangement",
            content="Cohabiting partner Chloe",
            subject_entity_id=owner,
            object_entity_id=partner,
        )

        result = await run_memory_curation(pool)

        assert result["edges_pending_approval"] == 1
        assert result["edges_inserted"] == 0
        assert await _count_pending_actions(pool) == 1


class TestMemoryCurationSkipCases:
    """Cases that should produce no edge proposal."""

    async def test_null_subject_entity_id_skipped(self, pool: asyncpg.Pool):
        """Facts with no subject entity_id cannot produce an edge — skipped."""
        obj = await _make_entity(pool, name="Bob")
        await _insert_prose_fact(
            pool,
            predicate="partner-of",
            content="Partner",
            subject_entity_id=None,  # no subject
            object_entity_id=obj,
        )

        result = await run_memory_curation(pool)

        assert result["facts_scanned"] == 1
        assert result["edges_proposed"] == 0
        assert result["edges_skipped_no_mapping"] == 1

    async def test_unregistered_predicate_skipped(self, pool: asyncpg.Pool):
        """A predicate not in the registry raises ValueError → counted as skipped."""
        # Use a predicate that IS in our candidate set but NOT in the registry.
        # Insert a direct relational predicate that we've removed from the registry.
        await pool.execute(
            "DELETE FROM relationship.entity_predicate_registry WHERE predicate = 'knows'"
        )
        subject = await _make_entity(pool, name="Alice")
        obj = await _make_entity(pool, name="Bob")
        await _insert_prose_fact(
            pool,
            predicate="knows",
            content="General acquaintance",
            subject_entity_id=subject,
            object_entity_id=obj,
        )

        result = await run_memory_curation(pool)

        assert result["facts_scanned"] == 1
        assert result["edges_skipped_no_mapping"] == 1
        assert result["edges_inserted"] == 0


class TestMemoryCurationMultipleFacts:
    """Multiple candidate facts in a single run."""

    async def test_multiple_facts_all_processed(self, pool: asyncpg.Pool):
        alice = await _make_entity(pool, name="Alice")
        bob = await _make_entity(pool, name="Bob")
        carol = await _make_entity(pool, name="Carol")
        dave = await _make_entity(pool, name="Dave")

        await _insert_prose_fact(
            pool,
            predicate="partner-of",
            content="Partner",
            subject_entity_id=alice,
            object_entity_id=bob,
        )
        await _insert_prose_fact(
            pool,
            predicate="friend-of",
            content="Friend",
            subject_entity_id=alice,
            object_entity_id=carol,
        )
        await _insert_prose_fact(
            pool,
            predicate="colleague-of",
            content="Colleague",
            subject_entity_id=alice,
            object_entity_id=dave,
        )

        result = await run_memory_curation(pool)

        assert result["facts_scanned"] == 3
        assert result["edges_proposed"] == 3
        assert result["edges_inserted"] == 3
        assert result["errors"] == 0


# ---------------------------------------------------------------------------
# Tests: object_entity_id authoring backfill (_backfill_object_entity_ids)
# ---------------------------------------------------------------------------


async def _insert_relational_fact_no_oid(
    pool: asyncpg.Pool,
    *,
    predicate: str,
    content: str,
    subject_entity_id: uuid.UUID,
) -> uuid.UUID:
    """Insert a relational fact WITHOUT object_entity_id (old authoring style)."""
    return await pool.fetchval(
        """
        INSERT INTO facts (predicate, content, entity_id, validity)
        VALUES ($1, $2, $3, 'active')
        RETURNING id
        """,
        predicate,
        content,
        subject_entity_id,
    )


class TestObjectEntityIdBackfill:
    """Backfill helper resolves object_entity_id from content for relational facts."""

    async def test_backfill_resolves_exact_canonical_name(self, pool: asyncpg.Pool):
        """Content that exactly matches an entity canonical_name gets object_entity_id set."""
        subject = await _make_entity(pool, name="Alice")
        org = await _make_entity(pool, name="Acme Corp")

        fact_id = await _insert_relational_fact_no_oid(
            pool, predicate="works_at", content="Acme Corp", subject_entity_id=subject
        )

        result = await _backfill_object_entity_ids(pool)

        assert result["backfill_scanned"] == 1
        assert result["backfill_resolved"] == 1
        assert result["backfill_ambiguous"] == 0
        assert result["backfill_unresolved"] == 0

        row = await pool.fetchrow("SELECT object_entity_id FROM facts WHERE id = $1", fact_id)
        assert row["object_entity_id"] == org

    async def test_backfill_resolves_case_insensitive_name(self, pool: asyncpg.Pool):
        """canonical_name lookup is case-insensitive."""
        subject = await _make_entity(pool, name="Bob")
        org = await _make_entity(pool, name="Google Inc")

        fact_id = await _insert_relational_fact_no_oid(
            pool, predicate="works_at", content="google inc", subject_entity_id=subject
        )

        result = await _backfill_object_entity_ids(pool)

        assert result["backfill_resolved"] == 1
        row = await pool.fetchrow("SELECT object_entity_id FROM facts WHERE id = $1", fact_id)
        assert row["object_entity_id"] == org

    async def test_backfill_skips_ambiguous_names(self, pool: asyncpg.Pool):
        """When two entities share a canonical_name, backfill skips (ambiguous)."""
        subject = await _make_entity(pool, name="Carol")
        await _make_entity(pool, name="Shared Name")
        await _make_entity(pool, name="Shared Name")

        fact_id = await _insert_relational_fact_no_oid(
            pool, predicate="works_at", content="Shared Name", subject_entity_id=subject
        )

        result = await _backfill_object_entity_ids(pool)

        assert result["backfill_ambiguous"] == 1
        assert result["backfill_resolved"] == 0

        row = await pool.fetchrow("SELECT object_entity_id FROM facts WHERE id = $1", fact_id)
        assert row["object_entity_id"] is None

    async def test_backfill_skips_unknown_content(self, pool: asyncpg.Pool):
        """Content that matches no entity is counted as unresolved."""
        subject = await _make_entity(pool, name="Dave")

        await _insert_relational_fact_no_oid(
            pool,
            predicate="works_at",
            content="NoSuchEntityEverXYZ",
            subject_entity_id=subject,
        )

        result = await _backfill_object_entity_ids(pool)

        assert result["backfill_unresolved"] == 1
        assert result["backfill_resolved"] == 0

    async def test_backfill_skips_non_relational_predicates(self, pool: asyncpg.Pool):
        """Only _DIRECT_OR_ALIAS_PREDICATES are candidates — prose/property predicates
        with complex content are not attempted (content is not a clean entity name)."""
        subject = await _make_entity(pool, name="Eve")

        await pool.execute(
            "INSERT INTO facts (predicate, content, entity_id, validity) VALUES ($1, $2, $3, 'active')",
            "birthday",
            "March 15, 1990",
            subject,
        )

        result = await _backfill_object_entity_ids(pool)

        # birthday is not in _DIRECT_OR_ALIAS_PREDICATES, so scanned=0
        assert result["backfill_scanned"] == 0

    async def test_backfill_skips_facts_with_existing_object_entity_id(self, pool: asyncpg.Pool):
        """Facts that already have object_entity_id are not re-processed."""
        subject = await _make_entity(pool, name="Frank")
        obj = await _make_entity(pool, name="Existing Org")

        # Insert a fact WITH object_entity_id already set
        await pool.execute(
            "INSERT INTO facts (predicate, content, entity_id, object_entity_id, validity) "
            "VALUES ($1, $2, $3, $4, 'active')",
            "works_at",
            "Existing Org",
            subject,
            obj,
        )

        result = await _backfill_object_entity_ids(pool)

        assert result["backfill_scanned"] == 0  # excluded by IS NULL filter

    async def test_backfill_skips_retracted_facts(self, pool: asyncpg.Pool):
        """Retracted facts are not backfilled."""
        subject = await _make_entity(pool, name="Grace")
        await _make_entity(pool, name="Retracted Org")

        await pool.execute(
            "INSERT INTO facts (predicate, content, entity_id, validity) VALUES ($1, $2, $3, 'retracted')",
            "works_at",
            "Retracted Org",
            subject,
        )

        result = await _backfill_object_entity_ids(pool)

        assert result["backfill_scanned"] == 0

    async def test_backfill_is_idempotent(self, pool: asyncpg.Pool):
        """Running backfill twice doesn't double-update; second run finds 0 candidates."""
        subject = await _make_entity(pool, name="Alice")
        await _make_entity(pool, name="Idempotent Org")

        await _insert_relational_fact_no_oid(
            pool, predicate="works_at", content="Idempotent Org", subject_entity_id=subject
        )

        first = await _backfill_object_entity_ids(pool)
        second = await _backfill_object_entity_ids(pool)

        assert first["backfill_resolved"] == 1
        # Second run: object_entity_id is now set, so the fact is excluded by IS NULL filter
        assert second["backfill_scanned"] == 0
        assert second["backfill_resolved"] == 0


class TestMemoryCurationWithBackfill:
    """End-to-end: backfill + promotion in a single run_memory_curation call.

    Proves that relational edge-facts stored without object_entity_id (old
    authoring style) are resolved by the backfill pass and then promoted to
    entity_facts by the promotion sweep — all within one run_memory_curation
    call.  After the fix, src='memory_curation' rows appear in entity_facts
    (the job is no longer a structural no-op).
    """

    async def test_newly_authored_fact_with_object_entity_id_is_promoted(self, pool: asyncpg.Pool):
        """New authoring path: fact with object_entity_id set → promoted to entity_facts
        with src='memory_curation'.

        This is acceptance criterion #1: new LLM-authored relational edge-facts
        carrying object_entity_id are promoted by run_memory_curation.
        """
        subject = await _make_entity(pool, name="New Author Person")
        obj = await _make_entity(pool, name="New Author Org")

        # Simulate the new authoring path: prose fact WITH object_entity_id
        await _insert_prose_fact(
            pool,
            predicate="works_at",
            content="New Author Org",
            subject_entity_id=subject,
            object_entity_id=obj,
        )

        result = await run_memory_curation(pool)

        # Promotion succeeds
        assert result["edges_inserted"] == 1
        assert result["errors"] == 0

        # Verify src='memory_curation' row in entity_facts
        edge = await pool.fetchrow(
            """
            SELECT src FROM relationship.entity_facts
            WHERE subject   = $1
              AND predicate = 'works-at'
              AND object    = $2::text
              AND validity  = 'active'
            """,
            subject,
            str(obj),
        )
        assert edge is not None
        assert edge["src"] == "memory_curation"

    async def test_old_style_fact_without_object_entity_id_is_backfilled_then_promoted(
        self, pool: asyncpg.Pool
    ):
        """Old authoring path: fact without object_entity_id but content matching an
        entity → backfill resolves object_entity_id → promotion creates the edge.

        This is acceptance criterion #2: backfill resolves existing object-less
        relational facts.

        Acceptance criterion #3: src='memory_curation' rows appear after the fix.
        """
        subject = await _make_entity(pool, name="Old Author Person")
        obj = await _make_entity(pool, name="Old Author Org")

        # Simulate old authoring: relational fact WITHOUT object_entity_id
        await _insert_relational_fact_no_oid(
            pool,
            predicate="works_at",
            content="Old Author Org",
            subject_entity_id=subject,
        )

        result = await run_memory_curation(pool)

        # Backfill phase resolved the entity
        assert result["backfill_resolved"] == 1
        # Promotion phase created the edge
        assert result["edges_inserted"] == 1
        assert result["errors"] == 0

        # Verify src='memory_curation' row — job is no longer a no-op
        edge = await pool.fetchrow(
            """
            SELECT src FROM relationship.entity_facts
            WHERE subject   = $1
              AND predicate = 'works-at'
              AND object    = $2::text
              AND validity  = 'active'
            """,
            subject,
            str(obj),
        )
        assert edge is not None
        assert edge["src"] == "memory_curation"

    async def test_second_run_is_idempotent_with_backfill(self, pool: asyncpg.Pool):
        """Two consecutive runs produce one edge, not two."""
        subject = await _make_entity(pool, name="Idempotent Person")
        obj = await _make_entity(pool, name="Idempotent Org")

        await _insert_relational_fact_no_oid(
            pool, predicate="works_at", content="Idempotent Org", subject_entity_id=subject
        )

        first = await run_memory_curation(pool)
        second = await run_memory_curation(pool)

        assert first["edges_inserted"] == 1
        assert second["edges_unchanged"] == 1
        assert second["edges_inserted"] == 0
        assert second["backfill_scanned"] == 0  # already resolved on first run

        count = await _count_entity_facts(
            pool, subject=subject, predicate="works-at", object_entity=obj
        )
        assert count == 1

    async def test_return_dict_includes_backfill_keys(self, pool: asyncpg.Pool):
        """run_memory_curation result dict exposes all four backfill stat keys."""
        result = await run_memory_curation(pool)

        assert "backfill_scanned" in result
        assert "backfill_resolved" in result
        assert "backfill_ambiguous" in result
        assert "backfill_unresolved" in result


# ---------------------------------------------------------------------------
# Helpers for pending_actions curation tests
# ---------------------------------------------------------------------------


async def _setup_pending_actions_schema(pool: asyncpg.Pool) -> None:
    """Create the minimal schema needed by run_pending_actions_curation tests.

    Includes the pending_actions table, the state table (for checkpoint), and
    the insight candidate tables (used by propose_insight_candidate).
    """
    from butlers.tools.switchboard.insight.broker import create_insight_tables

    await pool.execute(_CREATE_PENDING_ACTIONS_SQL)
    await pool.execute(_CREATE_STATE_SQL)
    await create_insight_tables(pool)


async def _insert_pending_action(
    pool: asyncpg.Pool,
    *,
    tool_name: str = "channel_add",
    tool_args: Any = None,
    why: str | None = "Owner carve-out: adding email for owner",
    status: str = "pending",
    expires_at: datetime | None = None,
) -> uuid.UUID:
    """Insert a row into pending_actions and return its id."""
    if tool_args is None:
        tool_args = {"contact_id": str(uuid.uuid4()), "type": "email", "value": "owner@example.com"}
    return await pool.fetchval(
        """
        INSERT INTO pending_actions (tool_name, tool_args, why, status, expires_at)
        VALUES ($1, $2::jsonb, $3, $4, $5)
        RETURNING id
        """,
        tool_name,
        tool_args,
        why,
        status,
        expires_at,
    )


async def _count_insight_candidates(pool: asyncpg.Pool) -> int:
    return await pool.fetchval("SELECT COUNT(*) FROM insight_candidates")


async def _fetch_insight_candidates(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM insight_candidates ORDER BY created_at ASC")
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tests: run_pending_actions_curation
# ---------------------------------------------------------------------------


class TestPendingActionsCurationNoOp:
    """No-op paths for run_pending_actions_curation."""

    @pytest.fixture
    async def pa_pool(self, provisioned_postgres_pool):
        """Isolated DB with pending_actions + insight schema."""
        async with provisioned_postgres_pool() as p:
            await _setup_pending_actions_schema(p)
            yield p

    async def test_no_pending_actions_returns_zeros(self, pa_pool: asyncpg.Pool):
        """Empty table — all counters are 0."""
        result = await run_pending_actions_curation(pa_pool)

        assert result["scanned"] == 0
        assert result["surfaced"] == 0
        assert result["skipped_no_expiry"] == 0
        assert result["skipped_not_approaching"] == 0
        assert result["errors"] == 0

    async def test_no_insight_candidate_when_no_actions(self, pa_pool: asyncpg.Pool):
        """No insight candidates created when there are no pending_actions."""
        await run_pending_actions_curation(pa_pool)

        assert await _count_insight_candidates(pa_pool) == 0

    async def test_non_pending_status_ignored(self, pa_pool: asyncpg.Pool):
        """Only status='pending' rows are surfaced; approved/rejected/expired are ignored."""
        now = datetime.now(UTC)
        for status in ("approved", "rejected", "expired", "executed"):
            await _insert_pending_action(
                pa_pool,
                status=status,
                expires_at=now + timedelta(hours=1),
            )

        result = await run_pending_actions_curation(pa_pool)

        assert result["scanned"] == 0
        assert result["surfaced"] == 0
        assert await _count_insight_candidates(pa_pool) == 0


class TestPendingActionsCurationDetection:
    """Detection logic: approaching vs. not-approaching vs. no-expiry rows."""

    @pytest.fixture
    async def pa_pool(self, provisioned_postgres_pool):
        async with provisioned_postgres_pool() as p:
            await _setup_pending_actions_schema(p)
            yield p

    async def test_approaching_expiry_is_surfaced(self, pa_pool: asyncpg.Pool):
        """A pending action expiring within 24 h is surfaced as an insight candidate."""
        now = datetime.now(UTC)
        await _insert_pending_action(
            pa_pool,
            expires_at=now + timedelta(hours=12),
        )

        result = await run_pending_actions_curation(pa_pool)

        assert result["scanned"] == 1
        assert result["surfaced"] == 1
        assert result["skipped_not_approaching"] == 0
        assert result["skipped_no_expiry"] == 0
        assert result["errors"] == 0
        assert await _count_insight_candidates(pa_pool) == 1

    async def test_far_future_expiry_is_skipped(self, pa_pool: asyncpg.Pool):
        """A pending action expiring in > 24 h is NOT surfaced."""
        now = datetime.now(UTC)
        await _insert_pending_action(
            pa_pool,
            expires_at=now + timedelta(hours=48),
        )

        result = await run_pending_actions_curation(pa_pool)

        assert result["scanned"] == 1
        assert result["surfaced"] == 0
        assert result["skipped_not_approaching"] == 1
        assert await _count_insight_candidates(pa_pool) == 0

    async def test_null_expires_at_is_skipped(self, pa_pool: asyncpg.Pool):
        """A pending action with no expiry is silently skipped (no expiry = no silent loss)."""
        await _insert_pending_action(
            pa_pool,
            expires_at=None,
        )

        result = await run_pending_actions_curation(pa_pool)

        assert result["scanned"] == 1
        assert result["surfaced"] == 0
        assert result["skipped_no_expiry"] == 1
        assert await _count_insight_candidates(pa_pool) == 0

    async def test_already_expired_is_skipped(self, pa_pool: asyncpg.Pool):
        """An action whose expires_at is already past is skipped.

        The insight broker requires a future expires_at, and there is nothing
        actionable the owner can do for an already-expired pending_action.
        """
        now = datetime.now(UTC)
        await _insert_pending_action(
            pa_pool,
            expires_at=now - timedelta(minutes=5),
        )

        result = await run_pending_actions_curation(pa_pool)

        assert result["scanned"] == 1
        assert result["surfaced"] == 0
        assert result["skipped_already_expired"] == 1
        assert await _count_insight_candidates(pa_pool) == 0

    async def test_mixed_actions_only_approaching_surfaced(self, pa_pool: asyncpg.Pool):
        """Mix of approaching, far-future, null-expiry, and already-expired — only approaching surfaced."""
        now = datetime.now(UTC)
        await _insert_pending_action(pa_pool, expires_at=now + timedelta(hours=6))  # approaching
        await _insert_pending_action(pa_pool, expires_at=now + timedelta(hours=72))  # far future
        await _insert_pending_action(pa_pool, expires_at=None)  # no expiry
        await _insert_pending_action(
            pa_pool, expires_at=now - timedelta(minutes=30)
        )  # already expired

        result = await run_pending_actions_curation(pa_pool)

        assert result["scanned"] == 4
        assert result["surfaced"] == 1
        assert result["skipped_not_approaching"] == 1
        assert result["skipped_no_expiry"] == 1
        assert result["skipped_already_expired"] == 1
        assert await _count_insight_candidates(pa_pool) == 1


class TestPendingActionsCurationMessageContent:
    """Verify the insight candidate message contains the right fields."""

    @pytest.fixture
    async def pa_pool(self, provisioned_postgres_pool):
        async with provisioned_postgres_pool() as p:
            await _setup_pending_actions_schema(p)
            yield p

    async def test_message_contains_tool_name(self, pa_pool: asyncpg.Pool):
        """Insight message includes the tool_name of the pending action."""
        now = datetime.now(UTC)
        await _insert_pending_action(
            pa_pool,
            tool_name="channel_add",
            expires_at=now + timedelta(hours=8),
        )

        await run_pending_actions_curation(pa_pool)

        candidates = await _fetch_insight_candidates(pa_pool)
        assert len(candidates) == 1
        assert "channel_add" in candidates[0]["message"]

    async def test_message_contains_why(self, pa_pool: asyncpg.Pool):
        """Insight message includes the 'why' field from the pending action."""
        now = datetime.now(UTC)
        await _insert_pending_action(
            pa_pool,
            why="RFC-0017 owner carve-out: adding primary email",
            expires_at=now + timedelta(hours=4),
        )

        await run_pending_actions_curation(pa_pool)

        candidates = await _fetch_insight_candidates(pa_pool)
        assert len(candidates) == 1
        assert "RFC-0017 owner carve-out: adding primary email" in candidates[0]["message"]

    async def test_message_contains_action_id(self, pa_pool: asyncpg.Pool):
        """Insight message includes the action UUID for reference."""
        now = datetime.now(UTC)
        action_id = await _insert_pending_action(
            pa_pool,
            expires_at=now + timedelta(hours=2),
        )

        await run_pending_actions_curation(pa_pool)

        candidates = await _fetch_insight_candidates(pa_pool)
        assert len(candidates) == 1
        assert str(action_id) in candidates[0]["message"]

    async def test_dedup_key_format(self, pa_pool: asyncpg.Pool):
        """dedup_key is in the expected 3-segment format."""
        now = datetime.now(UTC)
        action_id = await _insert_pending_action(
            pa_pool,
            expires_at=now + timedelta(hours=10),
        )

        await run_pending_actions_curation(pa_pool)

        candidates = await _fetch_insight_candidates(pa_pool)
        assert len(candidates) == 1
        expected_dedup_key = f"relationship:pending-action-expiry:{action_id}"
        assert candidates[0]["dedup_key"] == expected_dedup_key

    async def test_origin_butler_is_relationship(self, pa_pool: asyncpg.Pool):
        """Insight candidate is tagged with origin_butler='relationship'."""
        now = datetime.now(UTC)
        await _insert_pending_action(pa_pool, expires_at=now + timedelta(hours=10))

        await run_pending_actions_curation(pa_pool)

        candidates = await _fetch_insight_candidates(pa_pool)
        assert candidates[0]["origin_butler"] == "relationship"

    async def test_json_string_tool_args_does_not_abort_job(self, pa_pool: asyncpg.Pool):
        """String-returned JSONB tool_args are decoded for display instead of crashing."""
        now = datetime.now(UTC)
        await _insert_pending_action(
            pa_pool,
            tool_args='{"contact_id":"contact-1","type":"email","value":"owner@example.com"}',
            expires_at=now + timedelta(hours=10),
        )

        result = await run_pending_actions_curation(pa_pool)

        assert result["errors"] == 0
        assert result["surfaced"] == 1
        candidates = await _fetch_insight_candidates(pa_pool)
        assert len(candidates) == 1
        assert '"contact_id": "contact-1"' in candidates[0]["message"]

    async def test_scalar_tool_args_does_not_abort_job(self, pa_pool: asyncpg.Pool):
        """Unexpected non-object tool_args values do not fail the scheduled job."""
        now = datetime.now(UTC)
        await _insert_pending_action(
            pa_pool,
            tool_args=["unexpected"],
            expires_at=now + timedelta(hours=10),
        )

        result = await run_pending_actions_curation(pa_pool)

        assert result["errors"] == 0
        assert result["surfaced"] == 1
        candidates = await _fetch_insight_candidates(pa_pool)
        assert len(candidates) == 1
        assert 'Args: ["unexpected"]' in candidates[0]["message"]


class TestPendingActionsCurationDedup:
    """Dedup behavior: same action not proposed twice in a single run."""

    @pytest.fixture
    async def pa_pool(self, provisioned_postgres_pool):
        async with provisioned_postgres_pool() as p:
            await _setup_pending_actions_schema(p)
            yield p

    async def test_second_run_deduped_by_broker(self, pa_pool: asyncpg.Pool):
        """Second invocation with the same pending action creates a second candidate
        row (broker dedup is cooldown-based, not insert-blocked), but both calls
        succeed without error."""
        now = datetime.now(UTC)
        await _insert_pending_action(pa_pool, expires_at=now + timedelta(hours=10))

        result1 = await run_pending_actions_curation(pa_pool)
        result2 = await run_pending_actions_curation(pa_pool)

        # Both runs should surface the action (broker may accept or filter on 2nd run
        # depending on verbosity/cooldown, but neither should return errors).
        assert result1["errors"] == 0
        assert result2["errors"] == 0
        assert result1["surfaced"] == 1
        # The second run's surfaced could be 0 (cooldown) or 1 (re-inserted).
        # Either way, no errors.


# ---------------------------------------------------------------------------
# Helpers for fact-retraction curation tests
# ---------------------------------------------------------------------------


async def _setup_fact_retraction_schema(pool: asyncpg.Pool) -> None:
    """Create the minimal schema needed by run_fact_retraction_curation tests.

    Includes: entities (public), facts, pending_actions, state, and the insight
    candidates tables (used by propose_insight_candidate).
    """
    from butlers.tools.switchboard.insight.broker import create_insight_tables

    await pool.execute(_CREATE_ENTITIES_SQL)
    await pool.execute(_CREATE_FACTS_SQL)
    await pool.execute(_CREATE_PENDING_ACTIONS_SQL)
    await pool.execute(_CREATE_STATE_SQL)
    await create_insight_tables(pool)


async def _insert_fact(
    pool: asyncpg.Pool,
    *,
    entity_id: uuid.UUID | None = None,
    predicate: str = "works_at",
    content: str = "Some company",
    validity: str = "active",
    confidence: float = 1.0,
) -> uuid.UUID:
    """Insert a row into the facts table and return its id."""
    return await pool.fetchval(
        """
        INSERT INTO facts (predicate, content, entity_id, validity, confidence)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
        """,
        predicate,
        content,
        entity_id,
        validity,
        confidence,
    )


async def _fact_validity(pool: asyncpg.Pool, fact_id: uuid.UUID) -> str | None:
    """Return the validity of a facts row (e.g. 'active', 'retracted')."""
    return await pool.fetchval("SELECT validity FROM facts WHERE id = $1", fact_id)


async def _count_pending_for_fact(pool: asyncpg.Pool, fact_id: uuid.UUID) -> int:
    """Count pending_actions rows targeting the given fact id.

    Uses a text cast to JSONB for the containment check to ensure asyncpg
    passes the value correctly regardless of codec registration.
    """
    return await pool.fetchval(
        """
        SELECT COUNT(*) FROM pending_actions
        WHERE tool_name = 'memory_forget'
          AND status = 'pending'
          AND (tool_args ->> 'memory_id') = $1
        """,
        str(fact_id),
    )


# ---------------------------------------------------------------------------
# Tests: run_fact_retraction_curation
# ---------------------------------------------------------------------------


class TestFactRetractionCurationNoOp:
    """No-op paths for run_fact_retraction_curation."""

    @pytest.fixture
    async def frc_pool(self, provisioned_postgres_pool):
        async with provisioned_postgres_pool() as p:
            await _setup_fact_retraction_schema(p)
            yield p

    async def test_empty_facts_table_returns_zeros(self, frc_pool: asyncpg.Pool):
        """Empty facts table — all counters are 0, no errors."""
        result = await run_fact_retraction_curation(frc_pool)

        assert result["contradictions_found"] == 0
        assert result["low_conf_found"] == 0
        assert result["flagged_new"] == 0
        assert result["errors"] == 0

    async def test_single_active_fact_not_flagged(self, frc_pool: asyncpg.Pool):
        """A single active, high-confidence fact is benign — nothing to flag."""
        entity = await _make_entity(frc_pool, name="Alice")
        await _insert_fact(
            frc_pool, entity_id=entity, predicate="age", content="32", confidence=1.0
        )

        result = await run_fact_retraction_curation(frc_pool)

        assert result["contradictions_found"] == 0
        assert result["low_conf_found"] == 0
        assert result["flagged_new"] == 0
        assert await _count_pending_actions(frc_pool) == 0

    async def test_retracted_facts_are_not_flagged(self, frc_pool: asyncpg.Pool):
        """Retracted facts are not scanned (validity != 'active' excluded)."""
        entity = await _make_entity(frc_pool, name="Alice")
        await _insert_fact(
            frc_pool, entity_id=entity, predicate="age", content="31", validity="retracted"
        )
        await _insert_fact(
            frc_pool, entity_id=entity, predicate="age", content="32", validity="retracted"
        )

        result = await run_fact_retraction_curation(frc_pool)

        assert result["contradictions_found"] == 0
        assert await _count_pending_actions(frc_pool) == 0


class TestFactRetractionCurationContradictions:
    """Contradiction detection: two active facts on the same entity+predicate."""

    @pytest.fixture
    async def frc_pool(self, provisioned_postgres_pool):
        async with provisioned_postgres_pool() as p:
            await _setup_fact_retraction_schema(p)
            yield p

    async def test_two_active_facts_same_predicate_both_flagged(self, frc_pool: asyncpg.Pool):
        """Two active facts on the same (entity_id, predicate) with different content
        are a contradiction — both should be flagged for owner review."""
        entity = await _make_entity(frc_pool, name="Alice")
        fact_a = await _insert_fact(
            frc_pool, entity_id=entity, predicate="workplace", content="Company A"
        )
        fact_b = await _insert_fact(
            frc_pool, entity_id=entity, predicate="workplace", content="Company B"
        )

        result = await run_fact_retraction_curation(frc_pool)

        assert result["contradictions_found"] == 2
        assert result["flagged_new"] == 2
        assert result["errors"] == 0
        # Each fact gets a pending_actions row.
        assert await _count_pending_for_fact(frc_pool, fact_a) == 1
        assert await _count_pending_for_fact(frc_pool, fact_b) == 1

    async def test_contradiction_creates_insight_candidates(self, frc_pool: asyncpg.Pool):
        """Contradicted facts produce insight candidates for owner notification."""
        entity = await _make_entity(frc_pool, name="Bob")
        await _insert_fact(frc_pool, entity_id=entity, predicate="home_city", content="London")
        await _insert_fact(frc_pool, entity_id=entity, predicate="home_city", content="Paris")

        await run_fact_retraction_curation(frc_pool)

        assert await _count_insight_candidates(frc_pool) >= 1

    async def test_same_content_two_active_facts_not_a_contradiction(self, frc_pool: asyncpg.Pool):
        """Two active rows with the SAME content are duplicate, not contradictory.
        The contradiction query requires content <> content so they won't be flagged
        by the contradiction scan.  They may be low-confidence, but with default
        confidence=1.0 they should produce zero flags."""
        entity = await _make_entity(frc_pool, name="Carol")
        await _insert_fact(frc_pool, entity_id=entity, predicate="workplace", content="Engineer")
        await _insert_fact(frc_pool, entity_id=entity, predicate="workplace", content="Engineer")

        result = await run_fact_retraction_curation(frc_pool)

        assert result["contradictions_found"] == 0
        assert result["flagged_new"] == 0

    async def test_contradiction_null_entity_id_not_flagged(self, frc_pool: asyncpg.Pool):
        """Facts without entity_id cannot be correlated — contradiction scan requires
        entity_id IS NOT NULL so they are silently excluded."""
        await _insert_fact(frc_pool, entity_id=None, predicate="workplace", content="Company A")
        await _insert_fact(frc_pool, entity_id=None, predicate="workplace", content="Company B")

        result = await run_fact_retraction_curation(frc_pool)

        assert result["contradictions_found"] == 0

    async def test_different_predicates_not_a_contradiction(self, frc_pool: asyncpg.Pool):
        """Two facts on the same entity with DIFFERENT predicates are not contradictions."""
        entity = await _make_entity(frc_pool, name="Dave")
        await _insert_fact(frc_pool, entity_id=entity, predicate="workplace", content="Acme")
        await _insert_fact(frc_pool, entity_id=entity, predicate="home_city", content="London")

        result = await run_fact_retraction_curation(frc_pool)

        assert result["contradictions_found"] == 0

    async def test_different_entities_same_predicate_not_contradiction(
        self, frc_pool: asyncpg.Pool
    ):
        """Same predicate on DIFFERENT entities is not a contradiction."""
        alice = await _make_entity(frc_pool, name="Alice")
        bob = await _make_entity(frc_pool, name="Bob")
        await _insert_fact(frc_pool, entity_id=alice, predicate="home_city", content="London")
        await _insert_fact(frc_pool, entity_id=bob, predicate="home_city", content="Paris")

        result = await run_fact_retraction_curation(frc_pool)

        assert result["contradictions_found"] == 0


class TestFactRetractionCurationLowConfidence:
    """Low-confidence fact detection (confidence < threshold)."""

    @pytest.fixture
    async def frc_pool(self, provisioned_postgres_pool):
        async with provisioned_postgres_pool() as p:
            await _setup_fact_retraction_schema(p)
            yield p

    async def test_low_confidence_fact_flagged(self, frc_pool: asyncpg.Pool):
        """A fact with confidence below the threshold is flagged."""
        entity = await _make_entity(frc_pool, name="Eve")
        low_conf_fact = await _insert_fact(
            frc_pool,
            entity_id=entity,
            predicate="has_pet",
            content="Possibly has a dog",
            confidence=0.3,
        )

        result = await run_fact_retraction_curation(frc_pool)

        assert result["low_conf_found"] == 1
        assert result["flagged_new"] == 1
        assert await _count_pending_for_fact(frc_pool, low_conf_fact) == 1

    async def test_high_confidence_fact_not_flagged(self, frc_pool: asyncpg.Pool):
        """A fact at or above the threshold (0.6) is NOT flagged by low-conf scan."""
        entity = await _make_entity(frc_pool, name="Frank")
        await _insert_fact(
            frc_pool, entity_id=entity, predicate="age", content="40", confidence=0.6
        )
        await _insert_fact(
            frc_pool, entity_id=entity, predicate="city", content="Rome", confidence=1.0
        )

        result = await run_fact_retraction_curation(frc_pool)

        assert result["low_conf_found"] == 0
        assert result["flagged_new"] == 0

    async def test_confidence_just_below_threshold_flagged(self, frc_pool: asyncpg.Pool):
        """confidence=0.599 is below 0.6 → flagged."""
        entity = await _make_entity(frc_pool, name="Grace")
        below = await _insert_fact(
            frc_pool,
            entity_id=entity,
            predicate="hobby",
            content="Maybe photography",
            confidence=0.599,
        )

        result = await run_fact_retraction_curation(frc_pool)

        assert result["low_conf_found"] == 1
        assert await _count_pending_for_fact(frc_pool, below) == 1

    async def test_multiple_low_conf_facts_all_flagged(self, frc_pool: asyncpg.Pool):
        """Multiple low-confidence facts across different entities are all flagged."""
        for i in range(3):
            entity = await _make_entity(frc_pool, name=f"Person {i}")
            await _insert_fact(
                frc_pool,
                entity_id=entity,
                predicate="hobby",
                content=f"Hobby {i}",
                confidence=0.1 * (i + 1),  # 0.1, 0.2, 0.3 — all below 0.6
            )

        result = await run_fact_retraction_curation(frc_pool)

        assert result["low_conf_found"] == 3
        assert result["flagged_new"] == 3


class TestFactRetractionCurationOwnerFacts:
    """Owner-entity facts are AUTO-RESOLVED by this trusted-internal job.

    Unlike non-owner facts (which park for approval), the owner's own facts are
    soft-retracted directly: contradiction losers are retracted while the
    highest-confidence row is kept, and low-confidence owner facts are retracted.
    No pending_actions rows are created. (RFC 0017 §2.3 parks owner writes only
    from UNtrusted sources; this curation job is a trusted internal source.)
    """

    @pytest.fixture
    async def frc_pool(self, provisioned_postgres_pool):
        async with provisioned_postgres_pool() as p:
            await _setup_fact_retraction_schema(p)
            yield p

    async def test_owner_contradiction_loser_auto_retracted_winner_kept(
        self, frc_pool: asyncpg.Pool
    ):
        """Owner contradiction: keep the highest-confidence row, retract the loser,
        no pending_actions."""
        owner = await _make_entity(frc_pool, name="Owner", roles=["owner"])
        loser = await _insert_fact(
            frc_pool, entity_id=owner, predicate="workplace", content="Company A", confidence=0.5
        )
        winner = await _insert_fact(
            frc_pool, entity_id=owner, predicate="workplace", content="Company B", confidence=0.9
        )

        result = await run_fact_retraction_curation(frc_pool)

        assert result["contradictions_found"] == 2
        assert result["owner_auto_retracted"] == 1
        assert result["flagged_new"] == 0
        # No pending_actions for either fact — auto-resolved.
        assert await _count_pending_actions(frc_pool) == 0
        # Loser retracted, winner still active.
        assert await _fact_validity(frc_pool, loser) == "retracted"
        assert await _fact_validity(frc_pool, winner) == "active"

    async def test_owner_low_conf_fact_auto_retracted(self, frc_pool: asyncpg.Pool):
        """Owner-entity low-confidence facts are auto-retracted, not parked."""
        owner = await _make_entity(frc_pool, name="Owner", roles=["owner"])
        low_conf = await _insert_fact(
            frc_pool,
            entity_id=owner,
            predicate="has_son",
            content="Possibly has a son named Oliver",
            confidence=0.2,
        )

        result = await run_fact_retraction_curation(frc_pool)

        assert result["low_conf_found"] == 1
        assert result["owner_auto_retracted"] == 1
        assert result["flagged_new"] == 0
        assert await _count_pending_for_fact(frc_pool, low_conf) == 0
        assert await _fact_validity(frc_pool, low_conf) == "retracted"


class TestFactRetractionCurationMultiValuedPredicates:
    """Multi-valued / log predicates are never treated as contradictions."""

    @pytest.fixture
    async def frc_pool(self, provisioned_postgres_pool):
        async with provisioned_postgres_pool() as p:
            await _setup_fact_retraction_schema(p)
            yield p

    async def test_log_predicate_with_differing_content_not_flagged(self, frc_pool: asyncpg.Pool):
        """Two 'activity' rows with differing content on the same entity are NOT a
        contradiction — 'activity' is multi-valued log data, not a functional fact."""
        entity = await _make_entity(frc_pool, name="Loggy")
        await _insert_fact(
            frc_pool, entity_id=entity, predicate="activity", content="Created contact Dian"
        )
        await _insert_fact(
            frc_pool, entity_id=entity, predicate="activity", content="Logged a call"
        )
        # An interaction_* predicate is likewise multi-valued.
        await _insert_fact(
            frc_pool, entity_id=entity, predicate="interaction_email", content="Email 1"
        )
        await _insert_fact(
            frc_pool, entity_id=entity, predicate="interaction_email", content="Email 2"
        )

        result = await run_fact_retraction_curation(frc_pool)

        assert result["contradictions_found"] == 0
        assert result["flagged_new"] == 0
        assert await _count_pending_actions(frc_pool) == 0


class TestFactRetractionCurationDedup:
    """Dedup behavior: the same fact is not double-proposed."""

    @pytest.fixture
    async def frc_pool(self, provisioned_postgres_pool):
        async with provisioned_postgres_pool() as p:
            await _setup_fact_retraction_schema(p)
            yield p

    async def test_second_run_skips_already_pending_facts(self, frc_pool: asyncpg.Pool):
        """Running the job twice does NOT create a second pending_actions row for
        the same fact (JSONB containment dedup check prevents it)."""
        entity = await _make_entity(frc_pool, name="Harry")
        await _insert_fact(frc_pool, entity_id=entity, predicate="home_city", content="Oslo")
        await _insert_fact(frc_pool, entity_id=entity, predicate="home_city", content="Bergen")

        first = await run_fact_retraction_curation(frc_pool)
        second = await run_fact_retraction_curation(frc_pool)

        assert first["flagged_new"] == 2
        assert second["flagged_new"] == 0
        assert second["skipped_already_pending"] == 2
        # Still exactly 2 pending_actions rows (no duplicates).
        assert await _count_pending_actions(frc_pool) == 2

    async def test_contradiction_and_low_conf_same_fact_not_double_flagged(
        self, frc_pool: asyncpg.Pool
    ):
        """A fact that is BOTH contradicted AND low-confidence should appear in only
        one pending_actions row — the contradiction scan runs first and the
        low-confidence scan skips it (de-duplication via in-memory ID set)."""
        entity = await _make_entity(frc_pool, name="Iris")
        # fact_a: contradicted AND low-confidence
        fact_a = await _insert_fact(
            frc_pool,
            entity_id=entity,
            predicate="home_city",
            content="City A",
            confidence=0.2,
        )
        # fact_b: contradicted but high-confidence — provides the conflicting row
        await _insert_fact(
            frc_pool,
            entity_id=entity,
            predicate="home_city",
            content="City B",
            confidence=0.9,
        )

        result = await run_fact_retraction_curation(frc_pool)

        # fact_a is in the contradiction group (picked up first), and should NOT
        # be double-counted by the low_conf scan.
        # fact_b is contradicted but above the low-conf threshold.
        assert result["contradictions_found"] == 2
        # fact_a gets deduped by the low_conf scan (already flagged as contradiction)
        assert result["low_conf_found"] == 0
        # Only 2 new pending_actions (one per contradiction fact, no duplicate for fact_a)
        assert result["flagged_new"] == 2
        assert await _count_pending_for_fact(frc_pool, fact_a) == 1
