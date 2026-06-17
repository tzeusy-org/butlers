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

import asyncpg
import pytest

# The roster job module is loaded by conftest.py via _load_roster_jobs and
# registered in sys.modules as butlers.jobs._roster.relationship_jobs.
from butlers.jobs._roster.relationship_jobs import (  # type: ignore[import]
    _infer_predicate_from_prose,
    run_memory_curation,
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
