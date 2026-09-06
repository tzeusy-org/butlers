"""Tests for roster/finance/tools/alerts.py.

Covers:
- alert_configure: valid types, threshold requirements, supersession, disabled alerts
- alert_list: empty result, single alert, multiple alerts
- detect_price_changes: no subscriptions, no recent charges, price increase,
  price decrease, within threshold (no flag), zero tracked amount, multiple services
- register_obligations: warn-by derivation, unknown-door flag, pre-charge price
  change flag, idempotence (bu-8cdl1.10 slice 2)
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

_docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_DDL_SHARED_ENTITIES = """
CREATE TABLE IF NOT EXISTS public.entities (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL DEFAULT '',
    canonical_name  VARCHAR NOT NULL DEFAULT '',
    name            TEXT NOT NULL DEFAULT '',
    entity_type     VARCHAR NOT NULL DEFAULT 'other',
    aliases         TEXT[] NOT NULL DEFAULT '{}',
    metadata        JSONB DEFAULT '{}'::jsonb,
    roles           TEXT[] NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
_DDL_FACTS = """
CREATE TABLE IF NOT EXISTS facts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject             TEXT NOT NULL,
    predicate           TEXT NOT NULL,
    content             TEXT NOT NULL,
    embedding           TEXT,
    search_vector       TSVECTOR,
    importance          FLOAT NOT NULL DEFAULT 5.0,
    confidence          FLOAT NOT NULL DEFAULT 1.0,
    decay_rate          FLOAT NOT NULL DEFAULT 0.002,
    permanence          TEXT NOT NULL DEFAULT 'stable',
    source_butler       TEXT,
    source_episode_id   UUID,
    supersedes_id       UUID REFERENCES facts(id) ON DELETE SET NULL,
    validity            TEXT NOT NULL DEFAULT 'active',
    scope               TEXT NOT NULL DEFAULT 'global',
    reference_count     INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at   TIMESTAMPTZ,
    tags                JSONB DEFAULT '[]'::jsonb,
    metadata            JSONB DEFAULT '{}'::jsonb,
    entity_id           UUID REFERENCES public.entities(id),
    object_entity_id    UUID REFERENCES public.entities(id),
    valid_at            TIMESTAMPTZ DEFAULT NULL,
    tenant_id           TEXT NOT NULL DEFAULT 'owner',
    request_id          TEXT,
    idempotency_key     TEXT,
    observed_at         TIMESTAMPTZ DEFAULT now(),
    invalid_at          TIMESTAMPTZ,
    retention_class     TEXT NOT NULL DEFAULT 'operational',
    sensitivity         TEXT NOT NULL DEFAULT 'normal',
    embedding_model_version TEXT DEFAULT 'unknown'
)
"""
_DDL_SUBSCRIPTIONS = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service           TEXT NOT NULL,
    amount            NUMERIC(14, 2) NOT NULL,
    currency          CHAR(3) NOT NULL,
    frequency         TEXT NOT NULL
                          CHECK (frequency IN (
                              'weekly', 'monthly', 'quarterly', 'yearly', 'custom'
                          )),
    next_renewal      DATE NOT NULL,
    status            TEXT NOT NULL
                          CHECK (status IN ('active', 'cancelled', 'paused')),
    auto_renew        BOOLEAN NOT NULL DEFAULT true,
    payment_method    TEXT,
    account_id        UUID,
    source_message_id TEXT,
    cancellation_url    TEXT,
    notice_period_days  INTEGER,
    cancel_by           DATE,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
_DDL_OBLIGATION_LEDGER = """
CREATE TABLE IF NOT EXISTS obligation_ledger (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subscription_id         UUID NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    period                  DATE NOT NULL,
    warn_by                 DATE,
    unknown_door            BOOLEAN NOT NULL DEFAULT false,
    price_change_amount     NUMERIC(14, 2),
    price_change_direction  TEXT
                                 CHECK (price_change_direction IS NULL
                                     OR price_change_direction IN ('increase', 'decrease')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_obligation_ledger_subscription_period UNIQUE (subscription_id, period)
)
"""
_DDL_TRANSACTIONS = """
CREATE TABLE IF NOT EXISTS transactions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id        UUID,
    source_message_id TEXT,
    posted_at         TIMESTAMPTZ NOT NULL,
    merchant          TEXT NOT NULL,
    description       TEXT,
    amount            NUMERIC(14, 2) NOT NULL,
    currency          CHAR(3) NOT NULL,
    direction         TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    category          TEXT NOT NULL,
    payment_method    TEXT,
    receipt_url       TEXT,
    external_ref      TEXT,
    deleted_at        TIMESTAMPTZ,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
# Mirrors the production unique partial index on the facts table so that
# supersession tests exercise the real uniqueness constraint.
_DDL_FACTS_UNIQUE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS facts_active_property_unique
    ON facts(scope, subject, predicate, valid_at)
    WHERE entity_id IS NULL AND validity = 'active'
"""


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with facts + subscriptions + transactions tables."""
    async with provisioned_postgres_pool() as p:
        await p.execute(_DDL_SHARED_ENTITIES)
        await p.execute(_DDL_FACTS)
        await p.execute(_DDL_FACTS_UNIQUE_INDEX)
        await p.execute(_DDL_SUBSCRIPTIONS)
        await p.execute(_DDL_OBLIGATION_LEDGER)
        await p.execute(_DDL_TRANSACTIONS)
        yield p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_subscription(
    pool,
    *,
    service: str = "Netflix",
    amount: str = "15.49",
    currency: str = "USD",
    status: str = "active",
    next_renewal: str = "2099-12-31",
    cancellation_url: str | None = None,
    notice_period_days: int | None = None,
    cancel_by: str | None = None,
    metadata: dict | None = None,
) -> str:
    from datetime import date

    renewal_date = date.fromisoformat(next_renewal)
    cancel_by_date = date.fromisoformat(cancel_by) if cancel_by is not None else None
    row = await pool.fetchrow(
        """
        INSERT INTO subscriptions (
            service, amount, currency, frequency, next_renewal, status,
            cancellation_url, notice_period_days, cancel_by, metadata
        )
        VALUES ($1, $2, $3, 'monthly', $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        service,
        amount,
        currency,
        renewal_date,
        status,
        cancellation_url,
        notice_period_days,
        cancel_by_date,
        metadata or {},
    )
    return str(row["id"])


async def _insert_transaction(
    pool,
    *,
    merchant: str,
    amount: str,
    currency: str = "USD",
    direction: str = "debit",
    category: str = "subscriptions",
    posted_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> None:
    if posted_at is None:
        posted_at = datetime.now(UTC) - timedelta(days=5)
    await pool.execute(
        """
        INSERT INTO transactions
            (merchant, amount, currency, direction, category, posted_at, deleted_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        merchant,
        amount,
        currency,
        direction,
        category,
        posted_at,
        deleted_at,
    )


# ---------------------------------------------------------------------------
# alert_configure tests
# ---------------------------------------------------------------------------


class TestAlertConfigure:
    """Tests for alert_configure."""

    async def test_configure_large_transaction_alert(self, pool):
        """Configure a large_transaction alert with threshold returns expected dict."""
        from butlers.tools.finance.alerts import alert_configure

        result = await alert_configure(
            pool,
            alert_type="large_transaction",
            threshold=500.0,
            currency="USD",
            enabled=True,
        )

        assert result["type"] == "large_transaction"
        assert result["threshold"] == 500.0
        assert result["currency"] == "USD"
        assert result["enabled"] is True
        assert result["fact_id"] is not None

    async def test_configure_budget_exceeded_alert(self, pool):
        """Configure a budget_exceeded alert (no threshold required)."""
        from butlers.tools.finance.alerts import alert_configure

        result = await alert_configure(
            pool,
            alert_type="budget_exceeded",
            enabled=True,
        )

        assert result["type"] == "budget_exceeded"
        assert result["threshold"] is None
        assert result["enabled"] is True

    async def test_configure_new_merchant_alert(self, pool):
        """Configure a new_merchant alert."""
        from butlers.tools.finance.alerts import alert_configure

        result = await alert_configure(pool, alert_type="new_merchant")
        assert result["type"] == "new_merchant"
        assert result["enabled"] is True

    async def test_configure_price_change_alert(self, pool):
        """Configure a price_change alert."""
        from butlers.tools.finance.alerts import alert_configure

        result = await alert_configure(pool, alert_type="price_change")
        assert result["type"] == "price_change"
        assert result["enabled"] is True

    async def test_configure_disabled_alert(self, pool):
        """Configure an alert with enabled=False."""
        from butlers.tools.finance.alerts import alert_configure

        result = await alert_configure(pool, alert_type="new_merchant", enabled=False)
        assert result["enabled"] is False

    async def test_configure_invalid_type_raises(self, pool):
        """Configuring an invalid alert type raises ValueError."""
        from butlers.tools.finance.alerts import alert_configure

        with pytest.raises(ValueError, match="Invalid alert type"):
            await alert_configure(pool, alert_type="unknown_type")

    async def test_configure_large_transaction_without_threshold_raises(self, pool):
        """large_transaction alert without threshold raises ValueError."""
        from butlers.tools.finance.alerts import alert_configure

        with pytest.raises(ValueError, match="threshold is required"):
            await alert_configure(pool, alert_type="large_transaction")

    async def test_configure_supersedes_existing(self, pool):
        """Re-configuring same alert type supersedes the previous fact."""
        from butlers.tools.finance.alerts import alert_configure

        first = await alert_configure(pool, alert_type="large_transaction", threshold=500.0)
        second = await alert_configure(pool, alert_type="large_transaction", threshold=1000.0)

        assert second["fact_id"] != first["fact_id"]
        assert second["threshold"] == 1000.0

        # Old fact should be superseded
        old_validity = await pool.fetchval(
            "SELECT validity FROM facts WHERE id = $1::uuid",
            first["fact_id"],
        )
        assert old_validity == "superseded"

    async def test_configure_stores_fact_with_correct_predicate(self, pool):
        """Configured alert creates a fact with predicate='alert_config'."""
        from butlers.tools.finance.alerts import alert_configure

        result = await alert_configure(pool, alert_type="price_change")

        row = await pool.fetchrow(
            "SELECT subject, predicate, content, validity FROM facts WHERE id = $1::uuid",
            result["fact_id"],
        )
        assert row["predicate"] == "alert_config"
        assert row["subject"] == "price_change"
        assert row["content"] == "price_change"
        assert row["validity"] == "active"

    async def test_configure_with_non_usd_currency(self, pool):
        """Alert configuration preserves non-USD currency codes."""
        from butlers.tools.finance.alerts import alert_configure

        result = await alert_configure(
            pool, alert_type="large_transaction", threshold=1000.0, currency="EUR"
        )
        assert result["currency"] == "EUR"


# ---------------------------------------------------------------------------
# alert_list tests
# ---------------------------------------------------------------------------


class TestAlertList:
    """Tests for alert_list."""

    async def test_list_empty_when_no_alerts(self, pool):
        """alert_list returns empty list when no alerts configured."""
        from butlers.tools.finance.alerts import alert_list

        result = await alert_list(pool)
        assert result["alerts"] == []
        assert result["total"] == 0

    async def test_list_single_alert(self, pool):
        """alert_list returns configured alert."""
        from butlers.tools.finance.alerts import alert_configure, alert_list

        await alert_configure(pool, alert_type="new_merchant")
        result = await alert_list(pool)

        assert result["total"] == 1
        assert result["alerts"][0]["type"] == "new_merchant"

    async def test_list_multiple_alerts(self, pool):
        """alert_list returns all configured alerts."""
        from butlers.tools.finance.alerts import alert_configure, alert_list

        await alert_configure(pool, alert_type="large_transaction", threshold=500.0)
        await alert_configure(pool, alert_type="new_merchant")
        await alert_configure(pool, alert_type="price_change")

        result = await alert_list(pool)
        assert result["total"] == 3

        types = {a["type"] for a in result["alerts"]}
        assert types == {"large_transaction", "new_merchant", "price_change"}

    async def test_list_excludes_superseded_alerts(self, pool):
        """alert_list returns only active (latest) alert config per type."""
        from butlers.tools.finance.alerts import alert_configure, alert_list

        await alert_configure(pool, alert_type="large_transaction", threshold=500.0)
        await alert_configure(pool, alert_type="large_transaction", threshold=1000.0)

        result = await alert_list(pool)
        # Should have only one large_transaction alert (the active one)
        lt_alerts = [a for a in result["alerts"] if a["type"] == "large_transaction"]
        assert len(lt_alerts) == 1
        assert lt_alerts[0]["threshold"] == 1000.0

    async def test_list_preserves_alert_fields(self, pool):
        """alert_list returns all alert fields correctly."""
        from butlers.tools.finance.alerts import alert_configure, alert_list

        await alert_configure(
            pool,
            alert_type="large_transaction",
            threshold=750.0,
            currency="GBP",
            enabled=False,
        )
        result = await alert_list(pool)

        alert = result["alerts"][0]
        assert alert["type"] == "large_transaction"
        assert alert["threshold"] == 750.0
        assert alert["currency"] == "GBP"
        assert alert["enabled"] is False
        assert alert["fact_id"] is not None


# ---------------------------------------------------------------------------
# detect_price_changes tests
# ---------------------------------------------------------------------------


class TestDetectPriceChanges:
    """Tests for detect_price_changes."""

    async def test_no_subscriptions_returns_empty(self, pool):
        """detect_price_changes returns empty when no subscriptions are tracked."""
        from butlers.tools.finance.alerts import detect_price_changes

        result = await detect_price_changes(pool)
        assert result["changes"] == []
        assert result["total"] == 0

    async def test_no_recent_transactions_returns_empty(self, pool):
        """detect_price_changes returns empty when no recent charges found."""
        from butlers.tools.finance.alerts import detect_price_changes

        await _insert_subscription(pool, service="Netflix", amount="15.49")

        result = await detect_price_changes(pool, days_back=30)
        assert result["changes"] == []
        assert result["total"] == 0

    async def test_price_increase_flagged(self, pool):
        """Price increase greater than 5% is flagged as a change."""
        from butlers.tools.finance.alerts import detect_price_changes

        await _insert_subscription(pool, service="Netflix", amount="15.49")
        await _insert_transaction(pool, merchant="Netflix", amount="17.99")

        result = await detect_price_changes(pool)
        assert result["total"] == 1
        change = result["changes"][0]
        assert change["service"] == "Netflix"
        assert change["tracked_amount"] == 15.49
        assert change["recent_charge"] == 17.99
        assert change["direction"] == "increase"
        assert change["change_pct"] > 5.0

    async def test_price_decrease_flagged(self, pool):
        """Price decrease greater than 5% is flagged as a change."""
        from butlers.tools.finance.alerts import detect_price_changes

        await _insert_subscription(pool, service="Spotify", amount="9.99")
        await _insert_transaction(pool, merchant="Spotify", amount="6.99")

        result = await detect_price_changes(pool)
        assert result["total"] == 1
        change = result["changes"][0]
        assert change["direction"] == "decrease"
        assert change["change_pct"] < 0.0

    async def test_price_within_threshold_not_flagged(self, pool):
        """Price change within 5% threshold is not flagged."""
        from butlers.tools.finance.alerts import detect_price_changes

        await _insert_subscription(pool, service="Hulu", amount="10.00")
        # 3% change — below 5% threshold
        await _insert_transaction(pool, merchant="Hulu", amount="10.30")

        result = await detect_price_changes(pool)
        assert result["total"] == 0

    async def test_exact_match_not_flagged(self, pool):
        """Exact same amount is not flagged as a price change."""
        from butlers.tools.finance.alerts import detect_price_changes

        await _insert_subscription(pool, service="Disney+", amount="7.99")
        await _insert_transaction(pool, merchant="Disney+", amount="7.99")

        result = await detect_price_changes(pool)
        assert result["total"] == 0

    async def test_only_recent_transactions_checked(self, pool):
        """Only transactions within days_back window are considered."""
        from butlers.tools.finance.alerts import detect_price_changes

        await _insert_subscription(pool, service="HBO Max", amount="15.00")
        # Insert an old transaction with a different amount (outside window)
        old_time = datetime.now(UTC) - timedelta(days=90)
        await _insert_transaction(pool, merchant="HBO Max", amount="20.00", posted_at=old_time)

        result = await detect_price_changes(pool, days_back=30)
        assert result["total"] == 0

    async def test_cancelled_subscriptions_excluded(self, pool):
        """Cancelled subscriptions are not checked for price changes."""
        from butlers.tools.finance.alerts import detect_price_changes

        await _insert_subscription(pool, service="Peacock", amount="5.00", status="cancelled")
        await _insert_transaction(pool, merchant="Peacock", amount="9.99")

        result = await detect_price_changes(pool)
        assert result["total"] == 0

    async def test_multiple_subscriptions_independently_checked(self, pool):
        """Multiple active subscriptions are each checked independently."""
        from butlers.tools.finance.alerts import detect_price_changes

        await _insert_subscription(pool, service="Netflix", amount="15.49")
        await _insert_subscription(pool, service="Spotify", amount="9.99")
        await _insert_subscription(pool, service="Hulu", amount="10.00")

        # Netflix: price increase (flagged)
        await _insert_transaction(pool, merchant="Netflix", amount="18.99")
        # Spotify: no change (not flagged)
        await _insert_transaction(pool, merchant="Spotify", amount="9.99")
        # Hulu: no recent transactions

        result = await detect_price_changes(pool)
        assert result["total"] == 1
        assert result["changes"][0]["service"] == "Netflix"

    async def test_deleted_transactions_excluded(self, pool):
        """Soft-deleted transactions are excluded from price change detection."""
        from butlers.tools.finance.alerts import detect_price_changes

        await _insert_subscription(pool, service="Apple TV+", amount="6.99")
        # Insert a deleted transaction with a higher amount
        await _insert_transaction(
            pool,
            merchant="Apple TV+",
            amount="15.00",
            deleted_at=datetime.now(UTC),
        )

        result = await detect_price_changes(pool)
        assert result["total"] == 0

    async def test_result_includes_last_seen_at(self, pool):
        """Price change result includes last_seen_at timestamp."""
        from butlers.tools.finance.alerts import detect_price_changes

        await _insert_subscription(pool, service="Amazon Prime", amount="14.99")
        await _insert_transaction(pool, merchant="Amazon Prime", amount="17.99")

        result = await detect_price_changes(pool)
        assert result["total"] == 1
        change = result["changes"][0]
        assert "last_seen_at" in change
        assert change["last_seen_at"] is not None

    async def test_price_change_pct_calculation(self, pool):
        """change_pct is computed correctly."""
        from butlers.tools.finance.alerts import detect_price_changes

        # 15.49 -> 17.99: (17.99 - 15.49) / 15.49 * 100 ≈ +16.14%
        await _insert_subscription(pool, service="Netflix", amount="15.49")
        await _insert_transaction(pool, merchant="Netflix", amount="17.99")

        result = await detect_price_changes(pool)
        change = result["changes"][0]
        expected_pct = round((17.99 - 15.49) / 15.49 * 100, 2)
        assert abs(change["change_pct"] - expected_pct) < 0.01

    async def test_days_back_parameter_respected(self, pool):
        """days_back parameter controls the transaction look-back window."""
        from butlers.tools.finance.alerts import detect_price_changes

        await _insert_subscription(pool, service="YouTube Premium", amount="13.99")
        # Insert transaction 45 days ago
        older_time = datetime.now(UTC) - timedelta(days=45)
        await _insert_transaction(
            pool, merchant="YouTube Premium", amount="17.99", posted_at=older_time
        )

        # With 30-day window: not found
        result_30 = await detect_price_changes(pool, days_back=30)
        assert result_30["total"] == 0

        # With 60-day window: found and flagged
        result_60 = await detect_price_changes(pool, days_back=60)
        assert result_60["total"] == 1

    async def test_uses_most_recent_transaction(self, pool):
        """When multiple transactions exist, uses the most recent one for comparison."""
        from butlers.tools.finance.alerts import detect_price_changes

        await _insert_subscription(pool, service="iCloud", amount="2.99")
        # Older transaction with large price difference
        await _insert_transaction(
            pool,
            merchant="iCloud",
            amount="9.99",
            posted_at=datetime.now(UTC) - timedelta(days=20),
        )
        # Most recent transaction with same price as tracked
        await _insert_transaction(
            pool,
            merchant="iCloud",
            amount="2.99",
            posted_at=datetime.now(UTC) - timedelta(days=5),
        )

        result = await detect_price_changes(pool)
        # Most recent matches tracked amount — no change
        assert result["total"] == 0

    async def test_currency_preserved_in_result(self, pool):
        """Currency from subscription is preserved in the price change result."""
        from butlers.tools.finance.alerts import detect_price_changes

        await _insert_subscription(pool, service="Spotify UK", amount="9.99", currency="GBP")
        await _insert_transaction(pool, merchant="Spotify UK", amount="12.99", currency="GBP")

        result = await detect_price_changes(pool)
        assert result["total"] == 1
        assert result["changes"][0]["currency"] == "GBP"


# ---------------------------------------------------------------------------
# Large transaction alert flag on record_transaction / bulk_record_transactions
# ---------------------------------------------------------------------------


class TestLargeTransactionAlertFlag:
    """The recording response surfaces a large_transaction_alert flag.

    Exercises the real record_transaction / bulk_record_transactions paths
    against the real alert_configure threshold lookup (finance-alerts spec
    'Large Transaction Alerts > Transaction exceeds threshold').
    """

    async def test_flag_emitted_when_amount_exceeds_threshold(self, pool):
        from butlers.tools.finance.alerts import alert_configure
        from butlers.tools.finance.transactions import record_transaction

        await alert_configure(pool, alert_type="large_transaction", threshold=500.0)

        result = await record_transaction(
            pool,
            posted_at=datetime.now(UTC),
            merchant="Best Buy",
            amount=-600.0,
            currency="USD",
            category="electronics",
        )

        alert = result["large_transaction_alert"]
        assert alert["threshold"] == 500.0
        assert alert["amount"] == 600.0
        assert alert["merchant"] == "Best Buy"
        assert alert["exceeds_by"] == 100.0

    async def test_flag_absent_when_amount_below_threshold(self, pool):
        from butlers.tools.finance.alerts import alert_configure
        from butlers.tools.finance.transactions import record_transaction

        await alert_configure(pool, alert_type="large_transaction", threshold=500.0)

        result = await record_transaction(
            pool,
            posted_at=datetime.now(UTC),
            merchant="Cafe",
            amount=-300.0,
            currency="USD",
            category="dining",
        )

        assert "large_transaction_alert" not in result

    async def test_flag_absent_when_amount_equals_threshold(self, pool):
        from butlers.tools.finance.alerts import alert_configure
        from butlers.tools.finance.transactions import record_transaction

        await alert_configure(pool, alert_type="large_transaction", threshold=500.0)

        result = await record_transaction(
            pool,
            posted_at=datetime.now(UTC),
            merchant="Exactly Five Hundred",
            amount=-500.0,
            currency="USD",
            category="misc",
        )

        assert "large_transaction_alert" not in result

    async def test_flag_absent_when_no_alert_configured(self, pool):
        from butlers.tools.finance.transactions import record_transaction

        result = await record_transaction(
            pool,
            posted_at=datetime.now(UTC),
            merchant="Big Spend",
            amount=-5000.0,
            currency="USD",
            category="misc",
        )

        assert "large_transaction_alert" not in result

    async def test_flag_absent_when_alert_disabled(self, pool):
        from butlers.tools.finance.alerts import alert_configure
        from butlers.tools.finance.transactions import record_transaction

        await alert_configure(pool, alert_type="large_transaction", threshold=500.0, enabled=False)

        result = await record_transaction(
            pool,
            posted_at=datetime.now(UTC),
            merchant="Big Spend",
            amount=-5000.0,
            currency="USD",
            category="misc",
        )

        assert "large_transaction_alert" not in result

    async def test_bulk_record_surfaces_large_transaction_alerts(self, pool):
        from butlers.tools.finance.alerts import alert_configure
        from butlers.tools.finance.transactions import bulk_record_transactions

        await alert_configure(pool, alert_type="large_transaction", threshold=500.0)

        now = datetime.now(UTC)
        result = await bulk_record_transactions(
            pool,
            transactions=[
                {
                    "posted_at": now.isoformat(),
                    "merchant": "Cafe",
                    "amount": "-12.00",
                    "category": "dining",
                },
                {
                    "posted_at": now.isoformat(),
                    "merchant": "Best Buy",
                    "amount": "-750.00",
                    "category": "electronics",
                },
            ],
        )

        alerts = result["large_transaction_alerts"]
        assert len(alerts) == 1
        assert alerts[0]["index"] == 1
        assert alerts[0]["merchant"] == "Best Buy"
        assert alerts[0]["threshold"] == 500.0
        assert alerts[0]["amount"] == 750.0
        assert alerts[0]["exceeds_by"] == 250.0


# ---------------------------------------------------------------------------
# register_obligations tests (bu-8cdl1.10 slice 2)
# ---------------------------------------------------------------------------


async def _get_obligation(pool, subscription_id: str):
    return await pool.fetchrow(
        "SELECT * FROM obligation_ledger WHERE subscription_id = $1",
        subscription_id,
    )


class TestRegisterObligations:
    """Tests for register_obligations against the finance-alerts behavior matrix."""

    async def test_known_renewal_warns_at_cancel_by_minus_notice(self, pool):
        """happy: cancellation_url, cancel_by, and notice_period_days all
        known -> warn_by is derived."""
        from butlers.tools.finance.alerts import register_obligations

        sub_id = await _insert_subscription(
            pool,
            service="Netflix",
            next_renewal="2026-10-01",
            cancellation_url="https://netflix.example/cancel",
            notice_period_days=7,
            cancel_by="2026-09-25",
        )

        result = await register_obligations(pool)

        assert result == {"registered": 1, "unknown_door": 0, "price_changes": 0}
        obligation = await _get_obligation(pool, sub_id)
        assert obligation["period"].isoformat() == "2026-10-01"
        assert obligation["warn_by"].isoformat() == "2026-09-18"
        assert obligation["unknown_door"] is False

    async def test_missing_door_metadata_sets_unknown_door_flag(self, pool):
        """missing metadata: no cancel_by/notice_period_days -> unknown_door flag,
        prompting owner enrichment, instead of a silently wrong warn_by."""
        from butlers.tools.finance.alerts import register_obligations

        sub_id = await _insert_subscription(pool, service="Hulu", next_renewal="2026-11-01")

        result = await register_obligations(pool)

        assert result == {"registered": 1, "unknown_door": 1, "price_changes": 0}
        obligation = await _get_obligation(pool, sub_id)
        assert obligation["unknown_door"] is True
        assert obligation["warn_by"] is None

    async def test_partial_door_metadata_also_sets_unknown_door_flag(self, pool):
        """A cancel_by with no notice_period_days is still an incomplete door."""
        from butlers.tools.finance.alerts import register_obligations

        sub_id = await _insert_subscription(
            pool, service="Disney+", next_renewal="2026-11-15", cancel_by="2026-11-10"
        )

        result = await register_obligations(pool)

        assert result["unknown_door"] == 1
        obligation = await _get_obligation(pool, sub_id)
        assert obligation["unknown_door"] is True
        assert obligation["warn_by"] is None

    async def test_missing_cancellation_url_sets_unknown_door_flag(self, pool):
        """cancel_by and notice_period_days known but no cancellation_url is
        still an incomplete door -- the owner has dates but nowhere to act on
        them, so this must not be treated as a fully known door."""
        from butlers.tools.finance.alerts import register_obligations

        sub_id = await _insert_subscription(
            pool,
            service="HBO Max",
            next_renewal="2026-11-20",
            notice_period_days=7,
            cancel_by="2026-11-13",
        )

        result = await register_obligations(pool)

        assert result == {"registered": 1, "unknown_door": 1, "price_changes": 0}
        obligation = await _get_obligation(pool, sub_id)
        assert obligation["unknown_door"] is True
        assert obligation["warn_by"] is None

    async def test_price_change_warns_pre_charge_when_next_amount_known(self, pool):
        """price change: metadata.next_amount known and different from the tracked
        amount -> a pre-charge warning, ahead of any posted transaction."""
        from butlers.tools.finance.alerts import register_obligations

        sub_id = await _insert_subscription(
            pool,
            service="Spotify",
            amount="9.99",
            next_renewal="2026-10-05",
            metadata={"next_amount": "12.99"},
        )

        result = await register_obligations(pool)

        assert result["price_changes"] == 1
        obligation = await _get_obligation(pool, sub_id)
        assert obligation["price_change_amount"] == Decimal("12.99")
        assert obligation["price_change_direction"] == "increase"

    async def test_no_price_change_flag_when_next_amount_matches_tracked(self, pool):
        """metadata.next_amount equal to the tracked amount is not a price change."""
        from butlers.tools.finance.alerts import register_obligations

        sub_id = await _insert_subscription(
            pool,
            service="iCloud",
            amount="2.99",
            next_renewal="2026-10-05",
            metadata={"next_amount": "2.99"},
        )

        result = await register_obligations(pool)

        assert result["price_changes"] == 0
        obligation = await _get_obligation(pool, sub_id)
        assert obligation["price_change_amount"] is None
        assert obligation["price_change_direction"] is None

    async def test_cancelled_subscription_is_not_registered(self, pool):
        """Only active subscriptions accrue a forward obligation."""
        from butlers.tools.finance.alerts import register_obligations

        await _insert_subscription(
            pool, service="Peacock", next_renewal="2026-10-01", status="cancelled"
        )

        result = await register_obligations(pool)

        assert result == {"registered": 0, "unknown_door": 0, "price_changes": 0}

    async def test_idempotent_rerun_does_not_duplicate_row(self, pool):
        """idempotence: re-running for the same subscription+period upserts in
        place rather than inserting a second row."""
        from butlers.tools.finance.alerts import register_obligations

        sub_id = await _insert_subscription(
            pool,
            service="Netflix",
            next_renewal="2026-10-01",
            cancellation_url="https://netflix.example/cancel",
            notice_period_days=7,
            cancel_by="2026-09-25",
        )

        await register_obligations(pool)
        result = await register_obligations(pool)

        assert result == {"registered": 1, "unknown_door": 0, "price_changes": 0}
        rows = await pool.fetch(
            "SELECT id FROM obligation_ledger WHERE subscription_id = $1", sub_id
        )
        assert len(rows) == 1

    async def test_idempotent_rerun_updates_changed_door_fields(self, pool):
        """A re-run after the owner enriches the cancellation door updates the
        existing row's warn_by/unknown_door rather than leaving it stale."""
        from butlers.tools.finance.alerts import register_obligations

        sub_id = await _insert_subscription(pool, service="Netflix", next_renewal="2026-10-01")
        await register_obligations(pool)

        await pool.execute(
            "UPDATE subscriptions SET cancellation_url = 'https://netflix.example/cancel', "
            "notice_period_days = 7, cancel_by = '2026-09-25' WHERE id = $1",
            sub_id,
        )
        result = await register_obligations(pool)

        assert result == {"registered": 1, "unknown_door": 0, "price_changes": 0}
        rows = await pool.fetch(
            "SELECT id, warn_by, unknown_door FROM obligation_ledger WHERE subscription_id = $1",
            sub_id,
        )
        assert len(rows) == 1
        assert rows[0]["unknown_door"] is False
        assert rows[0]["warn_by"].isoformat() == "2026-09-18"
