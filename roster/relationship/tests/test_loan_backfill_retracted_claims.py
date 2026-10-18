"""bu-boqjzb: the loan backfill never quietly undoes an owner's retraction.

``cost_claim_retract`` leaves the loan fact active, and the live-unique index is partial, so a
daily backfill would insert a fresh live claim for a retracted loan. The backfill now skips a
``loan:{fact_id}`` whose claim was retracted and has no live row. Unit tests drive the skip logic;
the DB-backed test runs the real retract-then-backfill round trip (CI-only).
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.relationship.loans import backfill_loan_cost_claims

_FACT = uuid.UUID("00000000-0000-0000-0000-0000000000f1")
_ENTITY = uuid.UUID("00000000-0000-0000-0000-0000000000e1")


def _fact_row(fact_id: uuid.UUID = _FACT) -> dict:
    return {
        "id": fact_id,
        "content": "Synthetic loan",
        "metadata": {"currency": "SGD", "amount_cents": 1200, "direction": "lent"},
        "entity_id": _ENTITY,
        "canonical_name": "Sample Person",
    }


async def _backfill(retracted: list[str], rows: list[dict] | None = None):
    pool = MagicMock()
    pool.fetch = AsyncMock(
        side_effect=[
            rows if rows is not None else [_fact_row()],
            [{"claim_key": k} for k in retracted],
        ]
    )
    assert_claim = AsyncMock()
    with patch("butlers.core.cost_claims.assert_claim", assert_claim):
        counts = await backfill_loan_cost_claims(pool)
    return counts, assert_claim


async def test_a_retracted_loan_claim_is_not_reasserted():
    counts, assert_claim = await _backfill([f"loan:{_FACT}"])

    assert counts["skipped_retracted"] == 1 and counts["asserted"] == 0
    assert_claim.assert_not_awaited()


async def test_a_loan_without_a_retraction_is_still_asserted():
    counts, assert_claim = await _backfill([])

    assert counts["asserted"] == 1 and counts["skipped_retracted"] == 0
    assert assert_claim.await_args.kwargs["claim_key"] == f"loan:{_FACT}"


async def test_only_the_retracted_loan_is_skipped():
    other = uuid.UUID("00000000-0000-0000-0000-0000000000f2")
    counts, assert_claim = await _backfill([f"loan:{_FACT}"], rows=[_fact_row(), _fact_row(other)])

    assert (counts["asserted"], counts["skipped_retracted"]) == (1, 1)
    assert assert_claim.await_args.kwargs["claim_key"] == f"loan:{other}"


async def test_a_settled_loan_fact_is_never_projected():
    settled = {**_fact_row(), "metadata": {**_fact_row()["metadata"], "settled": True}}
    counts, assert_claim = await _backfill([], rows=[settled])

    assert counts["skipped_settled"] == 1 and counts["asserted"] == 0
    assert_claim.assert_not_awaited()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")
async def test_retract_then_backfill_leaves_the_claim_retracted(postgres_container):
    from butlers.core.cost_claims import retract_claim

    # create_migrated_test_db runs Alembic through asyncio.run, which cannot be called from
    # this running loop; a worker thread gets its own loop.
    db_url = await asyncio.to_thread(
        create_migrated_test_db, postgres_container, migration_db_name(), chains=["core"]
    )
    admin = await asyncpg.connect(db_url)
    pool = None
    try:
        await admin.execute(
            """
            CREATE TABLE facts (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(), predicate text, scope text,
                validity text, valid_at timestamptz, content text, metadata jsonb, entity_id uuid
            )
            """
        )
        await admin.execute("GRANT SELECT ON facts TO butler_relationship_rw")
        entity = await admin.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) "
            "VALUES ('Sample Person', 'person') RETURNING id"
        )
        await admin.execute(
            "INSERT INTO facts (predicate, scope, validity, valid_at, content, metadata, entity_id)"
            " VALUES ('loan', 'relationship', 'active', now(), 'Synthetic loan',"
            ' \'{"currency": "SGD", "amount_cents": 1200, "direction": "lent"}\', $1)',
            entity,
        )

        async def setup(conn: asyncpg.Connection) -> None:
            await conn.execute("SET ROLE butler_relationship_rw")

        pool = await asyncpg.create_pool(
            db_url, min_size=1, max_size=2, init=register_jsonb_codec, setup=setup
        )
        assert (await backfill_loan_cost_claims(pool))["asserted"] == 1
        claim_id = await admin.fetchval("SELECT id FROM public.cost_claims")

        await retract_claim(pool, claim_id, "owner chose to drop it")
        again = await backfill_loan_cost_claims(pool)

        assert again["skipped_retracted"] == 1 and again["asserted"] == 0
        live = await admin.fetchval(
            "SELECT count(*) FROM public.cost_claims "
            "WHERE superseded_at IS NULL AND retracted_at IS NULL"
        )
        assert live == 0
        assert Decimal(await admin.fetchval("SELECT count(*) FROM public.cost_claims")) == 1

        # loan_settle: supersede the loan fact, store a settled successor, retract the old claim.
        second = await admin.fetchval(
            "INSERT INTO facts (predicate, scope, validity, valid_at, content, metadata, entity_id)"
            " VALUES ('loan', 'relationship', 'active', now(), 'Second loan',"
            ' \'{"currency": "SGD", "amount_cents": 500, "direction": "lent"}\', $1)'
            " RETURNING id",
            entity,
        )
        assert (await backfill_loan_cost_claims(pool))["asserted"] == 1
        second_claim = await admin.fetchval(
            "SELECT id FROM public.cost_claims WHERE claim_key = $1", f"loan:{second}"
        )
        await admin.execute("UPDATE facts SET validity = 'superseded' WHERE id = $1", second)
        await admin.execute(
            "INSERT INTO facts (predicate, scope, validity, valid_at, content, metadata, entity_id)"
            " VALUES ('loan', 'relationship', 'active', now(), 'Second loan',"
            ' \'{"currency": "SGD", "amount_cents": 500, "direction": "lent",'
            ' "settled": true}\', $1)',
            entity,
        )
        await retract_claim(pool, second_claim, "asserter_marked_settled")

        settled = await backfill_loan_cost_claims(pool)

        assert settled["asserted"] == 0 and settled["skipped_settled"] == 1
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM public.cost_claims "
                "WHERE superseded_at IS NULL AND retracted_at IS NULL"
            )
            == 0
        )
    finally:
        if pool is not None:
            await pool.close()
        await admin.close()
