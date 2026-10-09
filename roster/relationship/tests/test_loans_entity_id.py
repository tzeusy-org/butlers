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

from butlers.testing.migrated_templates import MigrationStage
from butlers.testing.migration import migrated_pool

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def pool(postgres_container):
    """Real chains and all sibling constraints; only case data is seeded below."""
    async with migrated_pool(
        postgres_container,
        stages=(
            MigrationStage("core"),
            MigrationStage("memory"),
            MigrationStage("relationship", schema="relationship"),
            MigrationStage("approvals"),
        ),
        pool_schema="relationship",
    ) as p:
        await p.execute(
            "UPDATE approval_delivery_rollout SET admission_enabled=true WHERE singleton"
        )
        await p.execute("""
                    INSERT INTO predicate_registry (name, is_temporal) VALUES
                        ('loan', false),
                        ('activity', true)
                    ON CONFLICT (name) DO NOTHING
                """)
        yield p


@pytest.fixture(autouse=True, scope="session")
def patch_embedding_engine_loans():
    """Session-scoped fixture patching get_embedding_engine for loan tests."""
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    engine.model_name = "test-model"

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
        currency="USD",
    )

    # Verify entity_id is stored on the facts row (not NULL)
    row = await pool.fetchrow("SELECT entity_id FROM facts WHERE id = $1", loan["id"])
    assert row is not None
    assert row["entity_id"] is not None, "entity_id must not be NULL on loan fact"

    # Verify entity_id matches lender's entity (actor_contact = lender_contact_id).
    # Cutover (bu-irphu): the contact's entity is the linked entity returned by
    # contact_create (public.contacts is no longer written).
    lender_entity = lender["entity_id"]
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
        currency="USD",
    )

    row = await pool.fetchrow("SELECT entity_id FROM facts WHERE id = $1", loan["id"])
    assert row is not None
    assert row["entity_id"] is not None

    contact_entity = contact["entity_id"]
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
        currency="USD",
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
        currency="USD",
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


async def test_loan_list_multiple_loans_same_lender(pool):
    """loan_list returns all active loans for a lender, including multiple loans.

    With temporal fact storage, each loan coexists independently — two loans
    from the same lender entity are both active and both returned by loan_list.
    """
    lender = await _make_contact_with_entity(pool, "ListQuery-Lender")
    borrower = await _make_contact_with_entity(pool, "ListQuery-Borrower")

    from butlers.tools.relationship import loan_create, loan_list

    await loan_create(
        pool,
        lender_contact_id=lender["id"],
        borrower_contact_id=borrower["id"],
        description="First loan",
        amount_cents=1000,
        currency="USD",
    )
    await loan_create(
        pool,
        lender_contact_id=lender["id"],
        borrower_contact_id=borrower["id"],
        description="Second loan",
        amount_cents=2000,
        currency="USD",
    )

    loans = await loan_list(pool, lender["id"])
    assert len(loans) == 2, "Both loans must be active — temporal facts do not supersede each other"
    descriptions = {loan["description"] for loan in loans}
    assert descriptions == {"First loan", "Second loan"}
