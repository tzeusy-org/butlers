"""Tests for feed-vs-email transaction reconciliation + account freshness (bu-8bnn9).

Follow-up from PR #3589 (bu-ep4ks.16 slice 1). Covers the honest
not-configured state before any optional SimpleFIN account sync, the
merchant-fuzzy/amount/date matcher, and per-account freshness classification
(never_synced / stale / healthy).
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.finance.feed_reconciliation import (
    account_feed_freshness,
    reconcile_feed_vs_email,
)

_docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core + finance migrations (including 012) applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "finance"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE bills, transactions, subscriptions, accounts CASCADE")
    yield p
    await p.close()


async def _insert_account(
    pool: asyncpg.Pool,
    *,
    institution: str = "Test Bank",
    account_type: str = "checking",
    name: str = "Primary",
    last_synced_at: datetime | None = None,
) -> uuid.UUID:
    row = await pool.fetchrow(
        """
        INSERT INTO accounts (institution, type, name, currency, last_synced_at)
        VALUES ($1, $2, $3, 'USD', $4)
        RETURNING id
        """,
        institution,
        account_type,
        name,
        last_synced_at,
    )
    return row["id"]


async def _insert_transaction(
    pool: asyncpg.Pool,
    *,
    account_id: uuid.UUID | None,
    merchant: str,
    amount: str,
    posted_at: datetime,
    source: str = "manual",
    source_message_id: str | None = None,
    direction: str = "debit",
) -> uuid.UUID:
    """Insert a transaction directly.

    ``source`` defaults to ``'manual'`` -- the same default ``record_transaction``
    relies on for email-parsed rows today (it never sets this column
    explicitly; ``source_message_id`` is the real "came from email" signal).
    Pass ``source="aggregator"`` to simulate a feed-sourced row.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO transactions
            (account_id, merchant, amount, currency, direction, category,
             posted_at, source, source_message_id)
        VALUES ($1, $2, $3, 'USD', $4, 'shopping', $5, $6, $7)
        RETURNING id
        """,
        account_id,
        merchant,
        Decimal(amount),
        direction,
        posted_at,
        source,
        source_message_id,
    )
    return row["id"]


# ---------------------------------------------------------------------------
# account_feed_freshness
# ---------------------------------------------------------------------------


async def test_freshness_never_synced_is_degraded(pool: asyncpg.Pool):
    await _insert_account(pool, last_synced_at=None)

    accounts = await account_feed_freshness(pool)

    assert len(accounts) == 1
    assert accounts[0]["degraded"] is True
    assert accounts[0]["reason"] == "never_synced"
    assert accounts[0]["last_synced_at"] is None


async def test_freshness_stale_sync_is_degraded(pool: asyncpg.Pool):
    stale_at = datetime.now(UTC) - timedelta(hours=48)
    await _insert_account(pool, last_synced_at=stale_at)

    accounts = await account_feed_freshness(pool, staleness_threshold_hours=24)

    assert accounts[0]["degraded"] is True
    assert accounts[0]["reason"] == "stale"


async def test_freshness_recent_sync_is_healthy(pool: asyncpg.Pool):
    recent_at = datetime.now(UTC) - timedelta(hours=1)
    await _insert_account(pool, last_synced_at=recent_at)

    accounts = await account_feed_freshness(pool, staleness_threshold_hours=24)

    assert accounts[0]["degraded"] is False
    assert accounts[0]["reason"] is None


# ---------------------------------------------------------------------------
# reconcile_feed_vs_email
# ---------------------------------------------------------------------------


async def test_reconcile_reports_not_configured_with_no_synced_accounts(pool: asyncpg.Pool):
    await _insert_account(pool, last_synced_at=None)

    result = await reconcile_feed_vs_email(pool)

    assert result["configured"] is False
    assert result["matched"] == []
    assert result["unmatched_feed"] == []


async def test_reconcile_matches_feed_and_email_transaction(pool: asyncpg.Pool):
    account_id = await _insert_account(pool, last_synced_at=datetime.now(UTC))
    today = datetime.now(UTC)

    await _insert_transaction(
        pool,
        account_id=account_id,
        merchant="Trader Joes",
        amount="45.00",
        posted_at=today,
        source="aggregator",
    )
    await _insert_transaction(
        pool,
        account_id=account_id,
        merchant="Trader Joes #4521",
        amount="45.00",
        posted_at=today + timedelta(days=1),
        source_message_id="msg-1",
    )

    result = await reconcile_feed_vs_email(pool)

    assert result["configured"] is True
    assert len(result["matched"]) == 1
    assert result["matched"][0]["feed"]["merchant"] == "Trader Joes"
    assert result["matched"][0]["email"]["merchant"] == "Trader Joes #4521"
    assert result["unmatched_feed"] == []
    assert result["unmatched_email_count"] == 0


async def test_reconcile_leaves_non_matching_feed_transaction_unmatched(pool: asyncpg.Pool):
    account_id = await _insert_account(pool, last_synced_at=datetime.now(UTC))
    today = datetime.now(UTC)

    await _insert_transaction(
        pool,
        account_id=account_id,
        merchant="Uncommon Vendor XYZ",
        amount="12.34",
        posted_at=today,
        source="aggregator",
    )
    await _insert_transaction(
        pool,
        account_id=account_id,
        merchant="Completely Different Merchant",
        amount="99.99",
        posted_at=today,
        source_message_id="msg-2",
    )

    result = await reconcile_feed_vs_email(pool)

    assert len(result["unmatched_feed"]) == 1
    assert result["unmatched_feed"][0]["merchant"] == "Uncommon Vendor XYZ"
    assert result["matched"] == []
    assert result["unmatched_email_count"] == 1
