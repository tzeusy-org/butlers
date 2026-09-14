"""Behavior matrix for Finance's deterministic shared-claim reconciliation."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.finance.claim_reconciliation import reconcile_cost_claims

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


@pytest.fixture
def db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "finance"],
        schemas={"finance": "finance"},
    )


async def _finance_pool(db_url: str) -> asyncpg.Pool:
    async def setup(conn: asyncpg.Connection) -> None:
        await conn.execute("SET ROLE butler_finance_rw")
        await conn.execute("SET search_path TO finance, public")

    return await asyncpg.create_pool(db_url, min_size=1, max_size=2, setup=setup)


async def _role_conn(db_url: str, role: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(db_url)
    await conn.execute(f"SET ROLE {role}")
    await conn.execute("SET search_path TO finance, public")
    return conn


async def _claim(db_url: str, *, key: str, currency: str = "SGD") -> uuid.UUID:
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("SET ROLE butler_relationship_rw")
        row = await conn.fetchrow(
            """
            INSERT INTO public.cost_claims
                (claim_key, asserted_by, kind, direction, amount, currency,
                 counterparty_label, expected_on, description)
            VALUES ($1, 'relationship', 'receivable', 'inbound', 25, $2,
                    'Alex', current_date, 'Lunch')
            RETURNING id
            """,
            key,
            currency,
        )
        return row["id"]
    finally:
        await conn.close()


async def test_no_account_is_unverifiable_not_unreconciled(db_url: str) -> None:
    claim_id = await _claim(db_url, key=f"test:no-account:{uuid.uuid4()}")
    pool = await _finance_pool(db_url)
    try:
        result = await reconcile_cost_claims(pool)
        row = await pool.fetchrow(
            "SELECT state, unverifiable_reason, evidence_horizon_at "
            "FROM public.cost_claim_resolutions WHERE claim_id = $1",
            claim_id,
        )
        assert result["outcomes"] == {"unverifiable": 1}
        assert dict(row) == {
            "state": "unverifiable",
            "unverifiable_reason": "no_account",
            "evidence_horizon_at": None,
        }
    finally:
        await pool.close()


async def test_one_fresh_matching_credit_settles_and_binds(db_url: str) -> None:
    claim_id = await _claim(db_url, key=f"test:settled:{uuid.uuid4()}")
    pool = await _finance_pool(db_url)
    try:
        account_id = await pool.fetchval(
            """
            INSERT INTO accounts (institution, type, name, currency, last_synced_at)
            VALUES ('Test', 'checking', 'Feed', 'SGD', now()) RETURNING id
            """
        )
        transaction_id = await pool.fetchval(
            """
            INSERT INTO transactions
                (account_id, posted_at, merchant, amount, currency, direction, category)
            VALUES ($1, $2, 'Alex', 25, 'SGD', 'credit', 'income') RETURNING id
            """,
            account_id,
            datetime.now(UTC),
        )
        result = await reconcile_cost_claims(pool)
        row = await pool.fetchrow(
            "SELECT state, matched_amount, matched_currency, match_refs "
            "FROM public.cost_claim_resolutions WHERE claim_id = $1",
            claim_id,
        )
        assert result["outcomes"] == {"settled": 1}
        assert row["state"] == "settled"
        assert row["matched_amount"] == 25
        assert row["matched_currency"] == "SGD"
        assert str(transaction_id) in row["match_refs"]
        assert (
            await pool.fetchval(
                "SELECT transaction_id FROM claim_match_bindings WHERE claim_id = $1", claim_id
            )
            == transaction_id
        )
    finally:
        await pool.close()


async def test_two_claims_cannot_bind_one_transaction(db_url: str) -> None:
    first = await _claim(db_url, key=f"test:first:{uuid.uuid4()}")
    second = await _claim(db_url, key=f"test:second:{uuid.uuid4()}")
    pool = await _finance_pool(db_url)
    try:
        account_id = await pool.fetchval(
            "INSERT INTO accounts (institution, type, currency, last_synced_at) "
            "VALUES ('Test', 'checking', 'SGD', now()) RETURNING id"
        )
        await pool.execute(
            """
            INSERT INTO transactions
                (account_id, posted_at, merchant, amount, currency, direction, category)
            VALUES ($1, now(), 'Alex', 25, 'SGD', 'credit', 'income')
            """,
            account_id,
        )
        await reconcile_cost_claims(pool)
        rows = await pool.fetch(
            "SELECT claim_id, state, unmatched_reason FROM public.cost_claim_resolutions "
            "WHERE claim_id = ANY($1::uuid[]) ORDER BY claim_id",
            [first, second],
        )
        assert {row["state"] for row in rows} == {"settled", "ambiguous"}
        assert {row["unmatched_reason"] for row in rows} == {None, "transaction_already_bound"}
        assert await pool.fetchval("SELECT count(*) FROM claim_match_bindings") == 1

        settled_id = next(row["claim_id"] for row in rows if row["state"] == "settled")
        relationship = await _role_conn(db_url, "butler_relationship_rw")
        try:
            await relationship.execute(
                "UPDATE public.cost_claims SET retracted_at = now(), "
                "retraction_reason = 'source closed' WHERE id = $1",
                settled_id,
            )
        finally:
            await relationship.close()
        await reconcile_cost_claims(pool)
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM claim_match_bindings WHERE claim_id = $1", settled_id
            )
            == 0
        )
        assert await pool.fetchval("SELECT count(*) FROM claim_match_bindings") == 1
    finally:
        await pool.close()


@pytest.mark.parametrize(
    ("last_synced_at", "expected_reason"),
    [(None, "never_synced"), (datetime(2020, 1, 1, tzinfo=UTC), "feed_stale")],
)
async def test_unusable_account_feed_is_unverifiable(
    db_url: str, last_synced_at: datetime | None, expected_reason: str
) -> None:
    claim_id = await _claim(db_url, key=f"test:blind:{uuid.uuid4()}")
    pool = await _finance_pool(db_url)
    try:
        await pool.execute(
            "INSERT INTO accounts (institution, type, currency, last_synced_at) "
            "VALUES ('Test', 'checking', 'SGD', $1)",
            last_synced_at,
        )
        await reconcile_cost_claims(pool)
        row = await pool.fetchrow(
            "SELECT state, unverifiable_reason FROM public.cost_claim_resolutions "
            "WHERE claim_id = $1",
            claim_id,
        )
        assert (row["state"], row["unverifiable_reason"]) == (
            "unverifiable",
            expected_reason,
        )
    finally:
        await pool.close()


async def test_fresh_feed_distinguishes_no_candidate_multiple_and_currency_mismatch(
    db_url: str,
) -> None:
    no_candidate = await _claim(db_url, key=f"test:none:{uuid.uuid4()}")
    multiple = await _claim(db_url, key=f"test:multiple:{uuid.uuid4()}")
    mismatch = await _claim(db_url, key=f"test:mismatch:{uuid.uuid4()}")
    pool = await _finance_pool(db_url)
    try:
        account_id = await pool.fetchval(
            "INSERT INTO accounts (institution, type, currency, last_synced_at) "
            "VALUES ('Test', 'checking', 'SGD', now()) RETURNING id"
        )
        # Two eligible candidates force ambiguity for every SGD claim with the
        # same counterparty/amount. Make the no-candidate claim distinct first.
        relationship = await _role_conn(db_url, "butler_relationship_rw")
        await relationship.execute(
            "UPDATE public.cost_claims SET retracted_at = now(), retraction_reason = 'test phase' "
            "WHERE id IN ($1, $2)",
            multiple,
            mismatch,
        )
        await reconcile_cost_claims(pool)
        assert (
            await pool.fetchval(
                "SELECT unmatched_reason FROM public.cost_claim_resolutions WHERE claim_id = $1",
                no_candidate,
            )
            == "no_candidate_in_window"
        )

        try:
            await relationship.execute(
                "UPDATE public.cost_claims SET retracted_at = NULL, retraction_reason = NULL "
                "WHERE id IN ($1, $2)",
                multiple,
                mismatch,
            )
            await relationship.execute(
                "UPDATE public.cost_claims SET retracted_at = now(), retraction_reason = 'test phase' "
                "WHERE id = $1",
                no_candidate,
            )
        finally:
            await relationship.close()
        for merchant in ("Alex", "Alex Lunch"):
            await pool.execute(
                """
                INSERT INTO transactions
                    (account_id, posted_at, merchant, amount, currency, direction, category)
                VALUES ($1, now(), $2, 25, 'SGD', 'credit', 'income')
                """,
                account_id,
                merchant,
            )
        await reconcile_cost_claims(pool)
        multi_row = await pool.fetchrow(
            "SELECT state, unmatched_reason, match_refs FROM public.cost_claim_resolutions "
            "WHERE claim_id = $1",
            multiple,
        )
        assert (multi_row["state"], multi_row["unmatched_reason"]) == (
            "ambiguous",
            "multiple_candidates",
        )
        assert len(json.loads(multi_row["match_refs"])) == 2

        # Remove the same-currency candidates and leave an otherwise matching
        # USD credit: no implicit conversion is permitted.
        await pool.execute("DELETE FROM transactions")
        await pool.execute(
            """
            INSERT INTO transactions
                (posted_at, merchant, amount, currency, direction, category)
            VALUES (now(), 'Alex', 25, 'USD', 'credit', 'income')
            """
        )
        await reconcile_cost_claims(pool)
        mismatch_row = await pool.fetchrow(
            "SELECT state, unmatched_reason FROM public.cost_claim_resolutions WHERE claim_id = $1",
            mismatch,
        )
        assert (mismatch_row["state"], mismatch_row["unmatched_reason"]) == (
            "ambiguous",
            "currency_mismatch",
        )
    finally:
        await pool.close()


async def test_debit_and_transfer_do_not_settle_inbound_claim(db_url: str) -> None:
    claim_id = await _claim(db_url, key=f"test:excluded:{uuid.uuid4()}")
    pool = await _finance_pool(db_url)
    try:
        account_id = await pool.fetchval(
            "INSERT INTO accounts (institution, type, currency, last_synced_at) "
            "VALUES ('Test', 'checking', 'SGD', now()) RETURNING id"
        )
        await pool.execute(
            """
            INSERT INTO transactions
                (account_id, posted_at, merchant, amount, currency, direction, category)
            VALUES
                ($1, now(), 'Alex', 25, 'SGD', 'debit', 'income'),
                ($1, now(), 'Alex transfer', 25, 'SGD', 'credit', 'transfer')
            """,
            account_id,
        )
        await reconcile_cost_claims(pool)
        assert (
            await pool.fetchval(
                "SELECT state FROM public.cost_claim_resolutions WHERE claim_id = $1", claim_id
            )
            == "unreconciled"
        )
    finally:
        await pool.close()


async def test_concurrent_sweep_loser_exits_without_writing(db_url: str) -> None:
    claim_id = await _claim(db_url, key=f"test:locked:{uuid.uuid4()}")
    blocker = await _role_conn(db_url, "butler_finance_rw")
    pool = await _finance_pool(db_url)
    try:
        await blocker.fetchval(
            "SELECT pg_advisory_lock(hashtextextended($1, 0))",
            "finance:cost-claim-reconciliation",
        )
        before = await pool.fetchval(
            "SELECT decided_at FROM public.cost_claim_resolutions WHERE claim_id = $1", claim_id
        )
        result = await reconcile_cost_claims(pool)
        after = await pool.fetchval(
            "SELECT decided_at FROM public.cost_claim_resolutions WHERE claim_id = $1", claim_id
        )
        assert result == {"acquired": False, "processed": 0, "outcomes": {}}
        assert after == before
    finally:
        await blocker.execute(
            "SELECT pg_advisory_unlock(hashtextextended($1, 0))",
            "finance:cost-claim-reconciliation",
        )
        await blocker.close()
        await pool.close()
