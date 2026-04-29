"""Tests for entity_id anchoring in loan tools (bu-x7fdu.2).

Verifies that store_fact() is called with the contact's resolved entity_id
(not None) for loan_create and loan_settle, and that loan_list returns
correct data including multi-currency and settled-but-active facts.
"""

from __future__ import annotations

import shutil
import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with relationship and facts tables."""
    async with provisioned_postgres_pool() as p:
        # public.entities: required by store_fact entity_id FK and entity_create
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.entities (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                canonical_name VARCHAR NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                entity_type VARCHAR NOT NULL DEFAULT 'other',
                aliases TEXT[] NOT NULL DEFAULT '{}',
                metadata JSONB DEFAULT '{}'::jsonb,
                roles TEXT[] NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # contacts: full relationship schema (unqualified = public in test context).
        # Includes entity_id FK so resolve_contact_entity_id() works, and all name
        # fields so contact_create() can set first_name/last_name.
        await p.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                first_name TEXT,
                last_name TEXT,
                nickname TEXT,
                company TEXT,
                job_title TEXT,
                gender TEXT,
                pronouns TEXT,
                avatar_url TEXT,
                entity_id UUID REFERENCES public.entities(id),
                stay_in_touch_days INT,
                listed BOOLEAN NOT NULL DEFAULT true,
                metadata JSONB NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts (first_name, last_name)
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS predicate_registry (
                name TEXT PRIMARY KEY,
                expected_subject_type TEXT,
                expected_object_type TEXT,
                is_edge BOOLEAN NOT NULL DEFAULT false,
                is_temporal BOOLEAN NOT NULL DEFAULT false,
                description TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                status TEXT NOT NULL DEFAULT 'active',
                superseded_by TEXT,
                deprecated_at TIMESTAMPTZ,
                inverse_of TEXT,
                is_symmetric BOOLEAN NOT NULL DEFAULT false,
                aliases TEXT[] NOT NULL DEFAULT '{}',
                usage_count INTEGER NOT NULL DEFAULT 0,
                last_used_at TIMESTAMPTZ
            )
        """)
        await p.execute("""
            INSERT INTO predicate_registry (name, is_temporal) VALUES
                ('loan', false),
                ('activity', true)
            ON CONFLICT (name) DO NOTHING
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding TEXT,
                search_vector TSVECTOR,
                importance FLOAT NOT NULL DEFAULT 5.0,
                confidence FLOAT NOT NULL DEFAULT 1.0,
                decay_rate FLOAT NOT NULL DEFAULT 0.008,
                permanence TEXT NOT NULL DEFAULT 'standard',
                source_butler TEXT,
                source_episode_id UUID,
                supersedes_id UUID REFERENCES facts(id) ON DELETE SET NULL,
                validity TEXT NOT NULL DEFAULT 'active',
                scope TEXT NOT NULL DEFAULT 'global',
                reference_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_confirmed_at TIMESTAMPTZ,
                tags JSONB DEFAULT '[]'::jsonb,
                metadata JSONB DEFAULT '{}'::jsonb,
                entity_id UUID REFERENCES public.entities(id),
                object_entity_id UUID REFERENCES public.entities(id),
                valid_at TIMESTAMPTZ DEFAULT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'owner',
                request_id TEXT,
                idempotency_key TEXT,
                observed_at TIMESTAMPTZ DEFAULT now(),
                invalid_at TIMESTAMPTZ,
                retention_class TEXT NOT NULL DEFAULT 'operational',
                sensitivity TEXT NOT NULL DEFAULT 'normal'
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate
                ON facts (subject, predicate)
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS memory_links (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_type TEXT NOT NULL,
                source_id UUID NOT NULL,
                target_type TEXT NOT NULL,
                target_id UUID NOT NULL,
                relation TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (source_type, source_id, target_type, target_id)
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS activity_feed (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                action TEXT NOT NULL,
                summary TEXT NOT NULL,
                entity_type TEXT,
                entity_id UUID,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        yield p


@pytest.fixture(autouse=True, scope="session")
def patch_embedding_engine_loans():
    """Session-scoped fixture patching get_embedding_engine for loan tests."""
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384

    with patch("butlers.modules.memory.tools.get_embedding_engine", return_value=engine):
        for mod_name in (
            "butlers.tools.relationship.loans",
            "butlers.tools.relationship.feed",
        ):
            mod = sys.modules.get(mod_name)
            if mod is not None and hasattr(mod, "_embedding_engine"):
                mod._embedding_engine = None
        yield engine


async def _make_contact_with_entity(pool, name: str) -> dict:
    """Create a contact row with a linked entity, returning the contact dict."""
    from butlers.tools.relationship import contact_create

    return await contact_create(pool, name)


# ------------------------------------------------------------------
# loan_create entity_id anchoring
# ------------------------------------------------------------------


async def test_loan_create_stores_entity_id(pool):
    """loan_create passes the contact's entity_id to store_fact (not None)."""
    lender = await _make_contact_with_entity(pool, "Entity-Lender")
    borrower = await _make_contact_with_entity(pool, "Entity-Borrower")

    from butlers.tools.relationship import loan_create

    loan = await loan_create(
        pool,
        lender_contact_id=lender["id"],
        borrower_contact_id=borrower["id"],
        description="Entity anchor test",
        amount_cents=3000,
    )

    # Verify entity_id is stored on the facts row (not NULL)
    row = await pool.fetchrow("SELECT entity_id FROM facts WHERE id = $1", loan["id"])
    assert row is not None
    assert row["entity_id"] is not None, "entity_id must not be NULL on loan fact"

    # Verify entity_id matches lender's entity (actor_contact = lender_contact_id)
    lender_entity = await pool.fetchval(
        "SELECT entity_id FROM contacts WHERE id = $1", lender["id"]
    )
    assert row["entity_id"] == lender_entity


async def test_loan_create_contact_id_resolves_entity(pool):
    """loan_create with contact_id sets entity_id from that contact."""
    contact = await _make_contact_with_entity(pool, "Loan-Contact-Direct")

    from butlers.tools.relationship import loan_create

    loan = await loan_create(
        pool,
        contact_id=contact["id"],
        direction="lent",
        amount_cents=1500,
        description="Direct contact loan",
    )

    row = await pool.fetchrow("SELECT entity_id FROM facts WHERE id = $1", loan["id"])
    assert row is not None
    assert row["entity_id"] is not None

    contact_entity = await pool.fetchval(
        "SELECT entity_id FROM contacts WHERE id = $1", contact["id"]
    )
    assert row["entity_id"] == contact_entity


# ------------------------------------------------------------------
# loan_settle preserves entity_id on supersession
# ------------------------------------------------------------------


async def test_loan_settle_preserves_entity_id(pool):
    """loan_settle passes the existing entity_id to the superseding fact."""
    lender = await _make_contact_with_entity(pool, "Settle-Lender")
    borrower = await _make_contact_with_entity(pool, "Settle-Borrower")

    from butlers.tools.relationship import loan_create, loan_settle

    loan = await loan_create(
        pool,
        lender_contact_id=lender["id"],
        borrower_contact_id=borrower["id"],
        description="Settle preserve entity test",
        amount_cents=8000,
    )

    # Get entity_id from the original fact
    original_row = await pool.fetchrow("SELECT entity_id FROM facts WHERE id = $1", loan["id"])
    original_entity_id = original_row["entity_id"]
    assert original_entity_id is not None

    settled = await loan_settle(pool, loan["id"])

    # The settled fact must carry the same entity_id
    settled_row = await pool.fetchrow("SELECT entity_id FROM facts WHERE id = $1", settled["id"])
    assert settled_row is not None
    assert settled_row["entity_id"] == original_entity_id, (
        "Settled fact entity_id must match the original loan's entity_id"
    )


async def test_loan_settle_settled_flag_set(pool):
    """loan_settle marks the settled flag and preserves active validity."""
    lender = await _make_contact_with_entity(pool, "Settle-Flag-Lender")
    borrower = await _make_contact_with_entity(pool, "Settle-Flag-Borrower")

    from butlers.tools.relationship import loan_create, loan_settle

    loan = await loan_create(
        pool,
        lender_contact_id=lender["id"],
        borrower_contact_id=borrower["id"],
        description="Flag test",
        amount_cents=2000,
    )
    settled = await loan_settle(pool, loan["id"])

    assert settled["settled"] is True
    assert settled["settled_at"] is not None

    # The new fact row should have validity = 'active' (supersession deactivates old)
    row = await pool.fetchrow("SELECT validity, metadata FROM facts WHERE id = $1", settled["id"])
    assert row is not None
    assert row["validity"] == "active"


# ------------------------------------------------------------------
# Multi-currency loan read accuracy
# ------------------------------------------------------------------


async def test_loan_create_multi_currency(pool):
    """loan_create stores and returns non-USD currency accurately."""
    lender = await _make_contact_with_entity(pool, "Multicurrency-Lender")
    borrower = await _make_contact_with_entity(pool, "Multicurrency-Borrower")

    from butlers.tools.relationship import loan_create, loan_list

    loan = await loan_create(
        pool,
        lender_contact_id=lender["id"],
        borrower_contact_id=borrower["id"],
        description="EUR loan",
        amount_cents=5000,
        currency="EUR",
    )

    assert loan["currency"] == "EUR"
    assert loan["amount_cents"] == 5000
    assert loan["amount"] == Decimal("50.00")

    # Verify round-trip via loan_list
    loans = await loan_list(pool, lender["id"])
    eur_loans = [loan for loan in loans if loan.get("currency") == "EUR"]
    assert len(eur_loans) == 1
    assert eur_loans[0]["amount_cents"] == 5000


# ------------------------------------------------------------------
# loan_list entity-keyed query returns results
# ------------------------------------------------------------------


async def test_loan_list_entity_keyed_query(pool):
    """loan_list returns loans after entity_id fix.

    With entity_id anchoring, loans for the same contact entity in the same
    scope use supersession key (entity_id, scope, predicate).  Two loans from
    the same lender entity result in only the latest being active.  Loans from
    distinct lenders are independent and each returns one active result.
    """
    lender_a = await _make_contact_with_entity(pool, "ListQuery-Lender-A")
    lender_b = await _make_contact_with_entity(pool, "ListQuery-Lender-B")
    borrower = await _make_contact_with_entity(pool, "ListQuery-Borrower")

    from butlers.tools.relationship import loan_create, loan_list

    await loan_create(
        pool,
        lender_contact_id=lender_a["id"],
        borrower_contact_id=borrower["id"],
        description="First loan",
        amount_cents=1000,
    )
    await loan_create(
        pool,
        lender_contact_id=lender_b["id"],
        borrower_contact_id=borrower["id"],
        description="Second loan",
        amount_cents=2000,
    )

    loans_a = await loan_list(pool, lender_a["id"])
    loans_b = await loan_list(pool, lender_b["id"])
    assert len(loans_a) == 1
    assert loans_a[0]["description"] == "First loan"
    assert len(loans_b) == 1
    assert loans_b[0]["description"] == "Second loan"
