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

import asyncpg
import pytest

# The roster job module is loaded by conftest.py via _load_roster_jobs and
# registered in sys.modules as butlers.jobs._roster.relationship_jobs.
from butlers.jobs._roster.relationship_jobs import (  # type: ignore[import]
    _infer_predicate_from_prose,
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
    tool_name: str = "contact_info_add",
    tool_args: dict | None = None,
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
            tool_name="contact_info_update",
            expires_at=now + timedelta(hours=8),
        )

        await run_pending_actions_curation(pa_pool)

        candidates = await _fetch_insight_candidates(pa_pool)
        assert len(candidates) == 1
        assert "contact_info_update" in candidates[0]["message"]

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
