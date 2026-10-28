"""Tests for butlers.tools.finance — subscription and bill tracking tools."""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

_docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not _docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core + finance migrations applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "finance"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """Return an asyncpg pool with finance tables cleared between tests."""
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    # Truncate in FK-safe order (children first)
    await p.execute("TRUNCATE TABLE bills, subscriptions, accounts CASCADE")
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# track_subscription tests
# ---------------------------------------------------------------------------


async def _registered_subscription_write(pool, *, source_context=None, owner=False, **values):
    """Genuine registered HTTP invocation; observe committed native SQL afterward.

    These are trusted fixture inputs, never production tool authority arguments.
    No provenance attestor or owning storage function is replaced.
    """
    import json
    from contextlib import AsyncExitStack
    from pathlib import Path
    from types import SimpleNamespace
    from uuid import UUID, uuid4

    import httpx
    from fastmcp import Client, FastMCP
    from fastmcp.client.transports import StreamableHttpTransport

    from butlers.config import load_config
    from butlers.core import fact_authority
    from butlers.core import tool_call_capture as capture
    from butlers.daemon import ButlerDaemon
    from butlers.modules.registry import default_registry

    if owner:
        # The module's migrated DB retains the unique owner across test cases.
        # Reuse that real entity instead of attempting another singleton insert.
        entity = await pool.fetchval("SELECT id FROM public.entities WHERE 'owner'=ANY(roles)")
        if entity is None:
            entity = await pool.fetchval(
                "INSERT INTO public.entities(canonical_name,entity_type,roles) "
                "VALUES('Synthetic subscription owner','person',ARRAY['owner']) RETURNING id"
            )
        source_context = {"source_entity_id": str(entity), "request_context": {}}
    daemon = ButlerDaemon(
        Path(__file__).parents[3] / "roster/finance", db=SimpleNamespace(pool=pool)
    )
    daemon.config = load_config(daemon.config_dir)
    daemon.mcp = FastMCP("subscription-write-fixture")
    daemon._modules = [default_registry()._modules["finance"]()]
    daemon._module_configs = daemon._validate_module_configs()
    await daemon._register_module_tools()
    app = ButlerDaemon._build_mcp_http_app(daemon.mcp, butler_name="finance")
    raw = app._app._app
    session = str(uuid4())
    capture.set_runtime_session_routing_context(session, source_context or {})
    invocation = await fact_authority.register_invocation(
        "finance", session, source_endpoint=None, routed=True
    )
    values = dict(values)
    values["next_renewal"] = str(values["next_renewal"])
    if isinstance(values.get("metadata"), dict):
        values["metadata"] = json.dumps(values["metadata"])
    try:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(raw.router.lifespan_context(raw))
            async with Client(
                StreamableHttpTransport(
                    "http://synthetic.invalid/mcp",
                    headers={fact_authority.INVOCATION_HEADER: invocation},
                    httpx_client_factory=lambda **kw: httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), **kw
                    ),
                )
            ) as peer:
                result = (await peer.call_tool("track_subscription", values)).data
        async with pool.acquire() as observer:
            row = await observer.fetchrow(
                "SELECT * FROM subscriptions WHERE id=$1", UUID(result["id"])
            )
            return dict(row) | {"id": str(row["id"])}
    finally:
        fact_authority.settle_invocation(invocation)
        capture.clear_runtime_session_routing_context(session)


class TestTrackSubscription:
    """Tests for the track_subscription tool."""

    async def test_create_new_subscription(self, pool):
        """Creating a subscription returns a SubscriptionRecord dict."""
        from butlers.tools.finance import track_subscription

        renewal = datetime.now(UTC).date() + timedelta(days=30)
        result = await track_subscription(
            pool=pool,
            service="Netflix",
            amount=15.49,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal,
        )

        assert result["service"] == "Netflix"
        assert float(result["amount"]) == 15.49
        assert result["currency"] == "USD"
        assert result["frequency"] == "monthly"
        assert result["status"] == "active"
        assert result["auto_renew"] is True
        assert result["id"] is not None

    async def test_create_with_all_optional_fields(self, pool):
        """Creating subscription with all optional fields persists them."""
        from butlers.tools.finance import track_subscription

        renewal = datetime.now(UTC).date() + timedelta(days=7)
        result = await track_subscription(
            pool=pool,
            service="Spotify",
            amount=9.99,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal,
            status="active",
            auto_renew=False,
            payment_method="Visa ending in 4242",
            source_message_id="email-msg-001",
            metadata={"plan": "premium"},
        )

        assert result["auto_renew"] is False
        assert result["payment_method"] == "Visa ending in 4242"
        assert result["source_message_id"] == "email-msg-001"
        assert result["metadata"]["plan"] == "premium"

    async def test_create_with_cancellation_door_fields(self, pool):
        """Cancellation door fields (URL, notice period, cancel-by) persist."""
        from butlers.tools.finance import track_subscription

        renewal = datetime.now(UTC).date() + timedelta(days=30)
        cancel_by = renewal - timedelta(days=7)
        result = await track_subscription(
            pool=pool,
            service="Netflix",
            amount=15.49,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal,
            cancellation_url="https://netflix.com/cancelplan",
            notice_period_days=7,
            cancel_by=cancel_by,
        )

        assert result["cancellation_url"] == "https://netflix.com/cancelplan"
        assert result["notice_period_days"] == 7
        assert result["cancel_by"] == cancel_by

    async def test_create_without_cancellation_door_fields_is_null_not_error(self, pool):
        """Missing cancellation door metadata is a valid null state, not an error."""
        from butlers.tools.finance import track_subscription

        result = await track_subscription(
            pool=pool,
            service="Legacy Gym Membership",
            amount=29.99,
            currency="USD",
            frequency="monthly",
            next_renewal=datetime.now(UTC).date() + timedelta(days=30),
        )

        assert result["cancellation_url"] is None
        assert result["notice_period_days"] is None
        assert result["cancel_by"] is None

    async def test_update_preserves_cancellation_door_fields_when_omitted(self, pool):
        """Re-upserting without door fields keeps the previously-set values (COALESCE)."""
        from butlers.tools.finance import track_subscription

        renewal_1 = datetime.now(UTC).date() + timedelta(days=30)
        first = await track_subscription(
            pool=pool,
            service="Hulu",
            amount=7.99,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal_1,
            cancellation_url="https://hulu.com/cancel",
            notice_period_days=14,
        )
        assert first["cancellation_url"] == "https://hulu.com/cancel"

        renewal_2 = datetime.now(UTC).date() + timedelta(days=31)
        second = await track_subscription(
            pool=pool,
            service="Hulu",
            amount=8.99,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal_2,
        )

        assert second["id"] == first["id"]
        assert second["cancellation_url"] == "https://hulu.com/cancel"
        assert second["notice_period_days"] == 14

    async def test_caller_metadata_cannot_assert_expected_signal_authority(self, pool):
        track_subscription = _registered_subscription_write

        result = await track_subscription(
            pool=pool,
            service="Forged provenance",
            amount=9.99,
            currency="USD",
            frequency="monthly",
            next_renewal=datetime.now(UTC).date() + timedelta(days=30),
            metadata={
                "expected_signal_source": {
                    "producer": "connector:gmail",
                    "producer_endpoint_identity": "gmail:user:forged",
                }
            },
        )

        assert "expected_signal_source" not in result["metadata"]

    async def test_internal_attestation_persists_exact_gmail_endpoint(self, pool):
        track_subscription = _registered_subscription_write

        result = await track_subscription(
            pool=pool,
            service="Attested provenance",
            amount=9.99,
            currency="USD",
            frequency="monthly",
            next_renewal=datetime.now(UTC).date() + timedelta(days=30),
            source_context={
                "request_context": {
                    "source_channel": "email",
                    "source_endpoint_identity": "gmail:user:owner@example.invalid",
                }
            },
        )

        assert result["metadata"]["expected_signal_source"] == {
            "producer": "connector:gmail",
            "producer_endpoint_identity": "gmail:user:owner@example.invalid",
        }

    async def test_unproven_update_removes_prior_gmail_authority_from_renewal_signal(self, pool):
        from butlers.tools.finance.overview import subscription_audit

        track_subscription = _registered_subscription_write

        gmail = {
            "request_context": {
                "source_channel": "email",
                "source_endpoint_identity": "gmail:user:owner@example.invalid",
            }
        }
        first = await track_subscription(
            pool=pool,
            service="Rotating provenance",
            amount=9.99,
            currency="USD",
            frequency="monthly",
            next_renewal=datetime.now(UTC).date() + timedelta(days=30),
            metadata={"plan": "old"},
            source_context=gmail,
        )
        updated = await track_subscription(
            pool=pool,
            service="Rotating provenance",
            amount=10.99,
            currency="USD",
            frequency="monthly",
            next_renewal=datetime.now(UTC).date() + timedelta(days=31),
            metadata={"plan": "new"},
        )

        assert updated["id"] == first["id"]
        assert updated["metadata"]["plan"] == "new"
        assert "expected_signal_source" not in updated["metadata"]

        await subscription_audit(pool)
        signal = await pool.fetchrow(
            "SELECT producer, producer_endpoint_identity, measurability, "
            "unmeasurable_reason FROM public.expected_signals WHERE signal_key = $1",
            f"finance:subscription-renewal:{updated['id']}",
        )
        assert dict(signal) == {
            "producer": "unknown",
            "producer_endpoint_identity": None,
            "measurability": "unmeasurable",
            "unmeasurable_reason": "producer_unknown",
        }

    @pytest.mark.parametrize("endpoint", [None, "", "email:user:owner@example.invalid"])
    async def test_malformed_gmail_owner_context_clears_prior_authority(
        self,
        pool,
        monkeypatch: pytest.MonkeyPatch,
        endpoint: str | None,
    ):
        from butlers.tools.finance.expected_signals import (
            runtime_signal_source,
        )
        from butlers.tools.finance.overview import subscription_audit

        track_subscription = _registered_subscription_write

        first = await track_subscription(
            pool=pool,
            service="Malformed Gmail replacement",
            amount=9.99,
            currency="USD",
            frequency="monthly",
            next_renewal=datetime.now(UTC).date() + timedelta(days=30),
            source_context={
                "request_context": {
                    "source_channel": "email",
                    "source_endpoint_identity": "gmail:user:owner@example.invalid",
                }
            },
        )
        source_context = {
            "source_entity_id": "00000000-0000-0000-0000-000000000001",
            "request_context": {"source_channel": "email", "source_endpoint_identity": endpoint},
        }
        current_source = await runtime_signal_source(pool)
        assert current_source is None
        updated = await track_subscription(
            pool=pool,
            service="Malformed Gmail replacement",
            amount=10.99,
            currency="USD",
            frequency="monthly",
            next_renewal=datetime.now(UTC).date() + timedelta(days=31),
            source_context=source_context,
        )

        assert updated["id"] == first["id"]
        assert "expected_signal_source" not in updated["metadata"]
        await subscription_audit(pool)
        signal = await pool.fetchrow(
            "SELECT producer, producer_endpoint_identity, measurability "
            "FROM public.expected_signals WHERE signal_key = $1",
            f"finance:subscription-renewal:{updated['id']}",
        )
        assert dict(signal) == {
            "producer": "unknown",
            "producer_endpoint_identity": None,
            "measurability": "unmeasurable",
        }

    async def test_owner_update_replaces_prior_gmail_authority_for_renewal_signal(self, pool):
        from butlers.tools.finance.overview import subscription_audit

        track_subscription = _registered_subscription_write

        gmail = {
            "request_context": {
                "source_channel": "email",
                "source_endpoint_identity": "gmail:user:owner@example.invalid",
            }
        }
        first = await track_subscription(
            pool=pool,
            service="Owner replacement",
            amount=9.99,
            currency="USD",
            frequency="monthly",
            next_renewal=datetime.now(UTC).date() + timedelta(days=30),
            source_context=gmail,
        )
        updated = await track_subscription(
            pool=pool,
            service="Owner replacement",
            amount=11.99,
            currency="USD",
            frequency="monthly",
            next_renewal=datetime.now(UTC).date() + timedelta(days=31),
            owner=True,
        )

        assert updated["id"] == first["id"]
        assert updated["metadata"]["expected_signal_source"] == {
            "producer": "owner",
            "producer_endpoint_identity": None,
        }

        await subscription_audit(pool)
        signal = await pool.fetchrow(
            "SELECT producer, producer_endpoint_identity, measurability, "
            "unmeasurable_reason FROM public.expected_signals WHERE signal_key = $1",
            f"finance:subscription-renewal:{updated['id']}",
        )
        assert signal["producer"] == "owner"
        assert signal["producer_endpoint_identity"] is None
        assert signal["measurability"] in {"present", "absent"}
        assert signal["unmeasurable_reason"] is None

    async def test_upsert_updates_existing_on_service_frequency_match(self, pool):
        """Calling track_subscription twice with same service+frequency updates in place."""
        track_subscription = _registered_subscription_write

        renewal_1 = datetime.now(UTC).date() + timedelta(days=30)
        first = await track_subscription(
            pool=pool,
            service="Adobe Creative Cloud",
            amount=54.99,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal_1,
        )

        renewal_2 = datetime.now(UTC).date() + timedelta(days=31)
        second = await track_subscription(
            owner=True,
            pool=pool,
            service="Adobe Creative Cloud",
            amount=59.99,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal_2,
            status="active",
        )

        # Same record — same id
        assert first["id"] == second["id"]
        # Amount updated
        assert float(second["amount"]) == 59.99
        # Renewal date updated
        assert second["next_renewal"] == renewal_2

    async def test_different_frequency_creates_new_record(self, pool):
        """Same service but different frequency creates a separate record."""
        from butlers.tools.finance import track_subscription

        renewal = datetime.now(UTC).date() + timedelta(days=365)
        await track_subscription(
            pool=pool,
            service="Adobe Creative Cloud",
            amount=54.99,
            currency="USD",
            frequency="monthly",
            next_renewal=datetime.now(UTC).date() + timedelta(days=30),
        )
        yearly = await track_subscription(
            pool=pool,
            service="Adobe Creative Cloud",
            amount=599.99,
            currency="USD",
            frequency="yearly",
            next_renewal=renewal,
        )

        # Verify two distinct records exist in the database
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE service = $1",
            "Adobe Creative Cloud",
        )
        assert count == 2
        assert yearly["frequency"] == "yearly"

    async def test_status_cancelled(self, pool):
        """Cancelled status is accepted and persisted."""
        from butlers.tools.finance import track_subscription

        result = await track_subscription(
            pool=pool,
            service="Hulu",
            amount=7.99,
            currency="USD",
            frequency="monthly",
            next_renewal=datetime.now(UTC).date() + timedelta(days=15),
            status="cancelled",
        )
        assert result["status"] == "cancelled"

    async def test_status_paused(self, pool):
        """Paused status is accepted and persisted."""
        from butlers.tools.finance import track_subscription

        result = await track_subscription(
            pool=pool,
            service="Disney+",
            amount=10.99,
            currency="USD",
            frequency="monthly",
            next_renewal=datetime.now(UTC).date() + timedelta(days=20),
            status="paused",
        )
        assert result["status"] == "paused"

    async def test_invalid_status_raises(self, pool):
        """Invalid status raises ValueError."""
        from butlers.tools.finance import track_subscription

        with pytest.raises(ValueError, match="Invalid status"):
            await track_subscription(
                pool=pool,
                service="Bad Service",
                amount=5.00,
                currency="USD",
                frequency="monthly",
                next_renewal=datetime.now(UTC).date() + timedelta(days=30),
                status="expired",  # invalid
            )

    async def test_invalid_frequency_raises(self, pool):
        """Invalid frequency raises ValueError."""
        from butlers.tools.finance import track_subscription

        with pytest.raises(ValueError, match="Invalid frequency"):
            await track_subscription(
                pool=pool,
                service="Bad Service",
                amount=5.00,
                currency="USD",
                frequency="biweekly",  # invalid
                next_renewal=datetime.now(UTC).date() + timedelta(days=14),
            )

    async def test_renewal_date_string_normalized(self, pool):
        """ISO string renewal date is normalized to a date object."""
        from butlers.tools.finance import track_subscription

        renewal_str = (datetime.now(UTC).date() + timedelta(days=30)).isoformat()
        result = await track_subscription(
            pool=pool,
            service="Dropbox",
            amount=11.99,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal_str,
        )
        assert result["next_renewal"] == date.fromisoformat(renewal_str)

    async def test_account_id_linkage(self, pool):
        """Account ID can be linked to a subscription."""
        from butlers.tools.finance import track_subscription

        acct = await pool.fetchrow("""
            INSERT INTO accounts (institution, type, currency)
            VALUES ('Chase', 'credit', 'USD')
            RETURNING id
        """)
        account_id = acct["id"]

        result = await track_subscription(
            pool=pool,
            service="Amazon Prime",
            amount=14.99,
            currency="USD",
            frequency="monthly",
            next_renewal=datetime.now(UTC).date() + timedelta(days=30),
            account_id=account_id,
        )
        # _row_to_dict serializes UUIDs to strings
        assert result["account_id"] == str(account_id)

    async def test_metadata_merged_on_update(self, pool):
        """On upsert, metadata is merged (not replaced)."""
        from butlers.tools.finance import track_subscription

        renewal = datetime.now(UTC).date() + timedelta(days=30)
        await track_subscription(
            pool=pool,
            service="Notion",
            amount=8.00,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal,
            metadata={"plan": "personal"},
        )
        updated = await track_subscription(
            pool=pool,
            service="Notion",
            amount=8.00,
            currency="USD",
            frequency="monthly",
            next_renewal=renewal,
            metadata={"seats": 1},
        )
        # Both keys should be present after merge
        assert "plan" in updated["metadata"]
        assert "seats" in updated["metadata"]


# ---------------------------------------------------------------------------
# track_bill tests
# ---------------------------------------------------------------------------


class TestTrackBill:
    """Tests for the track_bill tool."""

    async def test_create_new_bill(self, pool):
        """Creating a bill returns a BillRecord dict."""
        from butlers.tools.finance import track_bill

        due = datetime.now(UTC).date() + timedelta(days=7)
        result = await track_bill(
            pool=pool,
            payee="PG&E",
            amount=84.00,
            currency="USD",
            due_date=due,
        )

        assert result["payee"] == "PG&E"
        assert float(result["amount"]) == 84.00
        assert result["currency"] == "USD"
        assert result["due_date"] == due
        assert result["status"] == "pending"
        assert result["frequency"] == "one_time"
        assert result["id"] is not None

    async def test_create_with_all_optional_fields(self, pool):
        """Creating bill with all optional fields persists them."""
        from butlers.tools.finance import track_bill

        due = datetime.now(UTC).date() + timedelta(days=5)
        period_start = datetime.now(UTC).date().replace(day=1)
        period_end = datetime.now(UTC).date()
        paid_time = datetime.now(UTC)

        result = await track_bill(
            pool=pool,
            payee="Comcast",
            amount=89.99,
            currency="USD",
            due_date=due,
            frequency="monthly",
            status="paid",
            payment_method="Auto-pay Checking",
            statement_period_start=period_start,
            statement_period_end=period_end,
            paid_at=paid_time,
            source_message_id="email-bill-001",
            metadata={"account_number": "****1234"},
        )

        assert result["frequency"] == "monthly"
        assert result["status"] == "paid"
        assert result["payment_method"] == "Auto-pay Checking"
        assert result["statement_period_start"] == period_start
        assert result["statement_period_end"] == period_end
        assert result["source_message_id"] == "email-bill-001"
        assert result["metadata"]["account_number"] == "****1234"

    async def test_upsert_updates_existing_on_payee_due_date_match(self, pool):
        """Calling track_bill twice with same payee+due_date updates in place."""
        from butlers.tools.finance import track_bill

        due = datetime.now(UTC).date() + timedelta(days=10)
        first = await track_bill(
            pool=pool,
            payee="Rent",
            amount=1800.00,
            currency="USD",
            due_date=due,
            status="pending",
        )
        second = await track_bill(
            pool=pool,
            payee="Rent",
            amount=1800.00,
            currency="USD",
            due_date=due,
            status="paid",
            paid_at=datetime.now(UTC),
        )

        assert first["id"] == second["id"]
        assert second["status"] == "paid"

    async def test_different_due_date_creates_new_record(self, pool):
        """Same payee but different due_date creates a separate record."""
        from butlers.tools.finance import track_bill

        due_1 = datetime.now(UTC).date() + timedelta(days=5)
        due_2 = datetime.now(UTC).date() + timedelta(days=35)
        await track_bill(pool=pool, payee="Internet", amount=60.00, currency="USD", due_date=due_1)
        await track_bill(pool=pool, payee="Internet", amount=60.00, currency="USD", due_date=due_2)

        count = await pool.fetchval("SELECT COUNT(*) FROM bills WHERE payee = $1", "Internet")
        assert count == 2

    async def test_payee_name_variants_collapse_to_one_row(self, pool):
        """Case/whitespace/trailing-period variants of a payee dedupe via payee_key."""
        from butlers.tools.finance import track_bill

        due = datetime.now(UTC).date() + timedelta(days=9)
        first = await track_bill(
            pool=pool, payee="Tailscale Inc.", amount=5.00, currency="USD", due_date=due
        )
        second = await track_bill(
            pool=pool, payee="tailscale  inc", amount=6.00, currency="USD", due_date=due
        )

        assert first["id"] == second["id"]
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM bills WHERE payee_key = $1", "tailscale inc"
        )
        assert count == 1
        assert float(second["amount"]) == 6.00

    async def test_autopay_and_predicted_persist(self, pool):
        """autopay/predicted flags persist and default to false when omitted."""
        from butlers.tools.finance import track_bill

        due = datetime.now(UTC).date() + timedelta(days=4)
        flagged = await track_bill(
            pool=pool,
            payee="Endowus",
            amount=1702.00,
            currency="SGD",
            due_date=due,
            autopay=True,
        )
        assert flagged["autopay"] is True
        assert flagged["predicted"] is False

        # Omitting autopay on a later upsert must NOT clobber the existing flag.
        again = await track_bill(
            pool=pool, payee="Endowus", amount=1702.00, currency="SGD", due_date=due
        )
        assert again["autopay"] is True

    async def test_zero_amount_overdue_rejected(self, pool):
        """A $0 placeholder cannot be overdue (storage-layer guard)."""
        import asyncpg

        from butlers.tools.finance import track_bill

        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await track_bill(
                pool=pool,
                payee="Arta Finance",
                amount=0.00,
                currency="USD",
                due_date=datetime.now(UTC).date() - timedelta(days=3),
                status="overdue",
            )

    async def test_status_paid(self, pool):
        """Paid status is accepted and persisted."""
        from butlers.tools.finance import track_bill

        result = await track_bill(
            pool=pool,
            payee="Water Bill",
            amount=40.00,
            currency="USD",
            due_date=datetime.now(UTC).date() - timedelta(days=2),
            status="paid",
        )
        assert result["status"] == "paid"

    async def test_status_overdue(self, pool):
        """Overdue status is accepted and persisted."""
        from butlers.tools.finance import track_bill

        result = await track_bill(
            pool=pool,
            payee="Gas Bill",
            amount=50.00,
            currency="USD",
            due_date=datetime.now(UTC).date() - timedelta(days=5),
            status="overdue",
        )
        assert result["status"] == "overdue"

    async def test_invalid_status_raises(self, pool):
        """Invalid status raises ValueError."""
        from butlers.tools.finance import track_bill

        with pytest.raises(ValueError, match="Invalid status"):
            await track_bill(
                pool=pool,
                payee="Phone",
                amount=50.00,
                currency="USD",
                due_date=datetime.now(UTC).date() + timedelta(days=3),
                status="unpaid",  # invalid
            )

    async def test_invalid_frequency_raises(self, pool):
        """Invalid frequency raises ValueError."""
        from butlers.tools.finance import track_bill

        with pytest.raises(ValueError, match="Invalid frequency"):
            await track_bill(
                pool=pool,
                payee="Phone",
                amount=50.00,
                currency="USD",
                due_date=datetime.now(UTC).date() + timedelta(days=3),
                frequency="biweekly",  # invalid
            )

    async def test_due_date_string_accepted(self, pool):
        """ISO string due_date is normalized to a date object."""
        from butlers.tools.finance import track_bill

        due_str = (datetime.now(UTC).date() + timedelta(days=7)).isoformat()
        result = await track_bill(
            pool=pool,
            payee="Credit Card",
            amount=300.00,
            currency="USD",
            due_date=due_str,
        )
        assert result["due_date"] == date.fromisoformat(due_str)

    async def test_paid_at_string_accepted(self, pool):
        """ISO string paid_at is normalized to a datetime."""
        from butlers.tools.finance import track_bill

        paid_str = datetime.now(UTC).isoformat()
        result = await track_bill(
            pool=pool,
            payee="Electric",
            amount=70.00,
            currency="USD",
            due_date=datetime.now(UTC).date() + timedelta(days=1),
            status="paid",
            paid_at=paid_str,
        )
        assert result["paid_at"] is not None


# ---------------------------------------------------------------------------
# track_bill payment destination fingerprint tests (bu-s11n0s.21)
# ---------------------------------------------------------------------------

# Synthetic destinations and keys only: nothing here is a real account or secret.
_DEST_F1 = "GB82 WEST 1234 5698 7654 32"
_DEST_F2 = "012-345-6789"
_DEST_HANDLE = "$SyntheticPayee"
_RAW_DESTINATIONS = (_DEST_F1, _DEST_F2, _DEST_HANDLE)


@pytest.fixture
def destination_key():
    """Configure a synthetic fingerprint key for the test and clear it afterwards."""
    from butlers.tools.finance.payment_destinations import configure_destination_key

    configure_destination_key("synthetic-key-for-tests-only")
    yield
    configure_destination_key(None)


def _days(offset: int) -> date:
    return datetime.now(UTC).date() + timedelta(days=offset)


async def _destination_rows(pool, payee_key: str) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT * FROM payment_destinations WHERE payee_key = $1 ORDER BY first_seen_at",
        payee_key,
    )


async def _assert_no_raw_destination(pool, raw_values) -> None:
    """Scan every text/jsonb-bearing row of bills, payment_destinations and the SPO mirror."""
    import asyncio

    from butlers.tools.finance import bills as bills_module

    # The SPO mirror is a fire-and-forget background task; let it land before scanning.
    pending = list(bills_module._background_tasks)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    # The mirror swallows its own errors, so prove it landed before scanning its rows:
    # five bills were tracked, each a distinct subject, and none may be missing here.
    mirrored = await pool.fetchval(
        "SELECT count(*) FROM public.facts WHERE predicate = 'bill' AND subject LIKE 'owner:bill:%'"
    )
    assert mirrored >= 4, "SPO mirror rows did not land; the public.facts scan would be vacuous"

    needles = set()
    for raw in raw_values:
        needles.add(raw.lower())
        needles.add("".join(ch for ch in raw if ch.isalnum() or ch in "$@").lower())
    scanned = 0
    scans = {
        "bills": "SELECT row_to_json(t)::text AS doc FROM bills t",
        "payment_destinations": "SELECT row_to_json(t)::text AS doc FROM payment_destinations t",
        "public.facts": "SELECT row_to_json(t)::text AS doc FROM public.facts t",
    }
    for table, query in scans.items():
        rows = await pool.fetch(query)
        for row in rows:
            scanned += 1
            doc = row["doc"].lower()
            doc_compact = "".join(ch for ch in doc if ch.isalnum() or ch in "$@")
            for needle in needles:
                assert needle not in doc, f"raw destination found in {table}"
                assert needle not in doc_compact, f"raw destination found in {table}"
    assert scanned > 0


class TestTrackBillPaymentDestination:
    """track_bill fingerprints payment destinations per counterparty."""

    @pytest.fixture(scope="module")
    def migrated_db_url(self, postgres_container) -> str:
        """Add the memory chain so the SPO mirror's ``facts`` table exists to be scanned."""
        return create_migrated_test_db(
            postgres_container,
            migration_db_name(),
            chains=["core", "memory", "finance"],
        )

    @pytest.fixture(autouse=True)
    def _mirror_embeddings(self, monkeypatch):
        """Stub the embedding engine so the SPO mirror write does not load a model."""
        engine = MagicMock()
        engine.embed.return_value = [0.1] * 384
        engine.model_name = "test-model"
        from butlers.tools.finance import facts as facts_module

        monkeypatch.setattr(facts_module, "_get_embedding_engine", lambda: engine)

    @pytest.fixture(autouse=True)
    async def _clear_destinations(self, pool):
        """The shared ``pool`` fixture does not truncate payment_destinations."""
        await pool.execute("TRUNCATE TABLE payment_destinations")

    async def _bill(self, pool, payee, due, destination, **kwargs):
        from butlers.tools.finance import track_bill

        return await track_bill(
            pool=pool,
            payee=payee,
            amount=100.00,
            currency="USD",
            due_date=due,
            payment_destination=destination,
            **kwargs,
        )

    async def test_changed_destination_is_new_for_counterparty(self, pool, destination_key):
        """Three bills to F1 build a baseline; a bill to F2 is new_for_counterparty."""
        first = await self._bill(pool, "Acme Utilities", _days(1), _DEST_F1)
        assert first["destination_verdict"] == "unverifiable"
        for offset in (31, 61):
            again = await self._bill(pool, "Acme Utilities", _days(offset), _DEST_F1)
            assert again["destination_verdict"] == "known"

        changed = await self._bill(pool, "Acme Utilities", _days(91), _DEST_F2)

        assert changed["destination_verdict"] == "new_for_counterparty"
        rows = await _destination_rows(pool, "acme utilities")
        assert len(rows) == 2
        assert {r["kind"] for r in rows} == {"iban", "account"}

    async def test_known_destination_increments_seen_count(self, pool, destination_key):
        """A bill to an already-seen destination is known and adds no row."""
        await self._bill(pool, "Acme Utilities", _days(1), _DEST_F1)
        await self._bill(pool, "Acme Utilities", _days(31), _DEST_F2)
        before = {
            r["kind"]: r["seen_count"] for r in await _destination_rows(pool, "acme utilities")
        }

        result = await self._bill(pool, "Acme Utilities", _days(61), _DEST_F1)

        assert result["destination_verdict"] == "known"
        rows = await _destination_rows(pool, "acme utilities")
        assert len(rows) == 2
        after = {r["kind"]: r["seen_count"] for r in rows}
        assert after["iban"] == before["iban"] + 1
        assert after["account"] == before["account"]

    async def test_first_destination_for_a_payee_is_unverifiable_baseline(
        self, pool, destination_key
    ):
        """The first destination ever seen for a payee is stored as its baseline."""
        result = await self._bill(pool, "Brand New Payee", _days(5), _DEST_HANDLE)

        assert result["destination_verdict"] == "unverifiable"
        assert result["destination_reason"] == "no_prior_destination"
        rows = await _destination_rows(pool, "brand new payee")
        assert len(rows) == 1
        assert rows[0]["kind"] == "handle"
        assert rows[0]["last4"] == "payee"[-4:]
        assert rows[0]["seen_count"] == 1

    async def test_no_destination_leaves_columns_null(self, pool, destination_key):
        """Existing callers are unchanged: no destination, no columns, no rows."""
        result = await self._bill(pool, "Plain Payee", _days(5), None)

        assert result["destination_fingerprint"] is None
        assert result["destination_verdict"] is None
        assert "destination_reason" not in result
        assert result["payee"] == "Plain Payee"
        assert await pool.fetchval("SELECT count(*) FROM payment_destinations") == 0

    async def test_missing_key_fails_closed_and_stores_nothing(self, pool):
        """With no fingerprint key the verdict is unverifiable and nothing is stored."""
        from butlers.tools.finance.payment_destinations import configure_destination_key

        configure_destination_key(None)

        result = await self._bill(pool, "Keyless Payee", _days(5), _DEST_F1)

        assert result["destination_verdict"] == "unverifiable"
        assert result["destination_reason"] == "fingerprint_key_unavailable"
        assert result["destination_fingerprint"] is None
        assert await pool.fetchval("SELECT count(*) FROM payment_destinations") == 0
        assert _DEST_F1 not in repr(result)
        assert "GB82" not in repr(result)

    async def test_reingesting_the_same_bill_does_not_double_count(self, pool, destination_key):
        """Same payee, due date and destination: seen_count is unchanged on re-ingest."""
        first = await self._bill(pool, "Acme Utilities", _days(10), _DEST_F1)
        again = await self._bill(pool, "Acme Utilities", _days(10), _DEST_F1)

        assert again["id"] == first["id"]
        assert again["destination_verdict"] == first["destination_verdict"]
        rows = await _destination_rows(pool, "acme utilities")
        assert len(rows) == 1
        assert rows[0]["seen_count"] == 1

    async def test_unverifiable_reingest_keeps_a_stamped_verdict(self, pool, destination_key):
        """A key-less or garbled re-ingest never erases a new_for_counterparty warning."""
        from butlers.tools.finance.payment_destinations import configure_destination_key

        await self._bill(pool, "Acme Utilities", _days(1), _DEST_F1)
        flagged = await self._bill(pool, "Acme Utilities", _days(61), _DEST_F2)
        assert flagged["destination_verdict"] == "new_for_counterparty"
        counts_before = {
            r["kind"]: r["seen_count"] for r in await _destination_rows(pool, "acme utilities")
        }

        configure_destination_key(None)
        no_key = await self._bill(pool, "Acme Utilities", _days(61), _DEST_F2)
        garbled = await self._bill(pool, "Acme Utilities", _days(61), "--")
        configure_destination_key("synthetic-key-for-tests-only")
        restored = await self._bill(pool, "Acme Utilities", _days(61), _DEST_F2)

        assert no_key["destination_reason"] == "fingerprint_key_unavailable"
        assert garbled["destination_reason"] == "unparseable_destination"
        for result in (no_key, garbled, restored):
            assert result["destination_verdict"] == "new_for_counterparty"
            assert result["destination_fingerprint"] == flagged["destination_fingerprint"]
        counts_after = {
            r["kind"]: r["seen_count"] for r in await _destination_rows(pool, "acme utilities")
        }
        assert counts_after == counts_before

    async def test_no_raw_destination_is_stored_anywhere(self, pool, destination_key):
        """After the other cases, no table carries a raw destination string."""
        await self._bill(pool, "Acme Utilities", _days(1), _DEST_F1)
        await self._bill(pool, "Acme Utilities", _days(31), _DEST_F1)
        await self._bill(pool, "Acme Utilities", _days(61), _DEST_F2)
        await self._bill(pool, "Handle Payee", _days(5), _DEST_HANDLE)
        await self._bill(pool, "Acme Utilities", _days(1), _DEST_F1)  # re-ingest
        await self._bill(pool, "Plain Payee", _days(7), None)

        await _assert_no_raw_destination(pool, _RAW_DESTINATIONS)


# ---------------------------------------------------------------------------
# upcoming_bills tests
# ---------------------------------------------------------------------------


class TestUpcomingBills:
    """Tests for the segmented upcoming_bills tool.

    A plain bill (not autopay, not predicted, amount > 0) lands in the
    ``needs_action`` bucket; ``autopay`` / ``predicted`` rows and $0 placeholders
    are diverted out of it.
    """

    async def test_empty_returns_empty_buckets(self, pool):
        """No bills returns empty buckets with zero totals."""
        from butlers.tools.finance import upcoming_bills

        result = await upcoming_bills(pool=pool)

        assert result["needs_action"] == []
        assert result["autopay"] == []
        assert result["predicted"] == []
        assert result["suppressed_placeholders"] == 0
        assert result["totals"]["needs_action_count"] == 0
        assert result["totals"]["needs_action_amount"] == "0.00"
        assert result["window_days"] == 14

    async def test_bill_due_within_horizon_included(self, pool):
        """Bill with due_date within days_ahead is included in needs_action."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due = datetime.now(UTC).date() + timedelta(days=5)
        await track_bill(pool=pool, payee="Rent", amount=1800.00, currency="USD", due_date=due)

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert len(result["needs_action"]) == 1
        assert result["needs_action"][0]["bill"]["payee"] == "Rent"

    async def test_bill_beyond_horizon_excluded(self, pool):
        """Bill with due_date beyond horizon is not included."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due = datetime.now(UTC).date() + timedelta(days=20)
        await track_bill(
            pool=pool, payee="Distant Bill", amount=100.00, currency="USD", due_date=due
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert len(result["needs_action"]) == 0

    async def test_urgency_due_today(self, pool):
        """Bill due today gets urgency=due_today."""
        from butlers.tools.finance import track_bill, upcoming_bills

        await track_bill(
            pool=pool,
            payee="Phone",
            amount=50.00,
            currency="USD",
            due_date=datetime.now(UTC).date(),
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert len(result["needs_action"]) == 1
        assert result["needs_action"][0]["urgency"] == "due_today"
        assert result["needs_action"][0]["days_until_due"] == 0

    async def test_urgency_due_soon(self, pool):
        """Bill due within horizon but not today gets urgency=due_soon."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due = datetime.now(UTC).date() + timedelta(days=7)
        await track_bill(pool=pool, payee="Internet", amount=60.00, currency="USD", due_date=due)

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert len(result["needs_action"]) == 1
        assert result["needs_action"][0]["urgency"] == "due_soon"
        assert result["needs_action"][0]["days_until_due"] == 7

    async def test_urgency_overdue_by_status(self, pool):
        """Bill with status=overdue gets urgency=overdue."""
        from butlers.tools.finance import track_bill, upcoming_bills

        past_due = datetime.now(UTC).date() - timedelta(days=3)
        await track_bill(
            pool=pool,
            payee="Gas",
            amount=45.00,
            currency="USD",
            due_date=past_due,
            status="overdue",
        )

        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        overdue_items = [i for i in result["needs_action"] if i["urgency"] == "overdue"]
        assert len(overdue_items) == 1
        assert overdue_items[0]["days_until_due"] < 0

    async def test_urgency_overdue_by_past_pending(self, pool):
        """Bill past due_date with status=pending is classified as overdue."""
        from butlers.tools.finance import track_bill, upcoming_bills

        past_due = datetime.now(UTC).date() - timedelta(days=2)
        await track_bill(
            pool=pool,
            payee="Water",
            amount=30.00,
            currency="USD",
            due_date=past_due,
            status="pending",
        )

        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        overdue_items = [i for i in result["needs_action"] if i["urgency"] == "overdue"]
        assert len(overdue_items) == 1

    async def test_include_overdue_false_excludes_past_bills(self, pool):
        """include_overdue=False excludes bills past the due date."""
        from butlers.tools.finance import track_bill, upcoming_bills

        past_due = datetime.now(UTC).date() - timedelta(days=2)
        await track_bill(
            pool=pool,
            payee="Old Bill",
            amount=30.00,
            currency="USD",
            due_date=past_due,
            status="overdue",
        )

        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=False)
        assert len(result["needs_action"]) == 0

    async def test_include_overdue_true_includes_past_bills(self, pool):
        """include_overdue=True includes bills that are past due."""
        from butlers.tools.finance import track_bill, upcoming_bills

        past_due = datetime.now(UTC).date() - timedelta(days=5)
        await track_bill(
            pool=pool,
            payee="Old Overdue",
            amount=75.00,
            currency="USD",
            due_date=past_due,
            status="overdue",
        )

        result = await upcoming_bills(pool=pool, days_ahead=14, include_overdue=True)
        assert len(result["needs_action"]) == 1

    async def test_paid_bills_excluded(self, pool):
        """Paid bills are not included in upcoming_bills."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due = datetime.now(UTC).date() + timedelta(days=3)
        await track_bill(
            pool=pool,
            payee="Already Paid",
            amount=100.00,
            currency="USD",
            due_date=due,
            status="paid",
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert len(result["needs_action"]) == 0

    async def test_totals_needs_action_count(self, pool):
        """totals.needs_action_count counts actionable items."""
        from butlers.tools.finance import track_bill, upcoming_bills

        await track_bill(
            pool=pool,
            payee="Bill A",
            amount=50.00,
            currency="USD",
            due_date=datetime.now(UTC).date() + timedelta(days=3),
        )
        await track_bill(
            pool=pool,
            payee="Bill B",
            amount=100.00,
            currency="USD",
            due_date=datetime.now(UTC).date() + timedelta(days=10),
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert result["totals"]["needs_action_count"] == 2

    async def test_totals_needs_action_amount(self, pool):
        """totals.needs_action_amount sums only actionable bill amounts."""
        from butlers.tools.finance import track_bill, upcoming_bills

        await track_bill(
            pool=pool,
            payee="Rent",
            amount=1800.00,
            currency="USD",
            due_date=datetime.now(UTC).date() + timedelta(days=5),
        )
        await track_bill(
            pool=pool,
            payee="Internet",
            amount=60.00,
            currency="USD",
            due_date=datetime.now(UTC).date() + timedelta(days=8),
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert Decimal(result["totals"]["needs_action_amount"]) == Decimal("1860.00")

    async def test_autopay_diverted_from_needs_action(self, pool):
        """Autopay bills are FYIs, never actionable, and excluded from the owed total."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due = datetime.now(UTC).date() + timedelta(days=4)
        await track_bill(pool=pool, payee="Manual Bill", amount=63.00, currency="SGD", due_date=due)
        await track_bill(
            pool=pool,
            payee="Endowus",
            amount=1702.00,
            currency="SGD",
            due_date=due,
            autopay=True,
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert len(result["needs_action"]) == 1
        assert result["needs_action"][0]["bill"]["payee"] == "Manual Bill"
        assert len(result["autopay"]) == 1
        assert result["autopay"][0]["bill"]["payee"] == "Endowus"
        # Owed total is the manual bill only — the autopay amount is excluded.
        assert Decimal(result["totals"]["needs_action_amount"]) == Decimal("63.00")
        assert Decimal(result["totals"]["autopay_amount"]) == Decimal("1702.00")

    async def test_predicted_diverted_from_needs_action(self, pool):
        """Predicted rows surface as heads-up, not as confirmed obligations."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due = datetime.now(UTC).date() + timedelta(days=6)
        await track_bill(
            pool=pool,
            payee="KCUTS",
            amount=15.00,
            currency="SGD",
            due_date=due,
            predicted=True,
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert result["needs_action"] == []
        assert len(result["predicted"]) == 1
        assert result["predicted"][0]["bill"]["payee"] == "KCUTS"
        assert Decimal(result["totals"]["needs_action_amount"]) == Decimal("0.00")

    async def test_zero_amount_placeholder_suppressed(self, pool):
        """$0 placeholders are counted but never surfaced in any bucket."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due = datetime.now(UTC).date() + timedelta(days=2)
        await track_bill(
            pool=pool,
            payee="Arta Finance",
            amount=0.00,
            currency="USD",
            due_date=due,
            status="pending",
        )

        result = await upcoming_bills(pool=pool, days_ahead=14)
        assert result["needs_action"] == []
        assert result["autopay"] == []
        assert result["predicted"] == []
        assert result["suppressed_placeholders"] == 1

    async def test_response_has_as_of_and_window_days(self, pool):
        """Response includes as_of timestamp and window_days."""
        from butlers.tools.finance import upcoming_bills

        result = await upcoming_bills(pool=pool, days_ahead=7)
        assert "as_of" in result
        assert result["window_days"] == 7
        # as_of should be parseable
        datetime.fromisoformat(result["as_of"])

    async def test_custom_days_ahead(self, pool):
        """Custom days_ahead changes the query horizon."""
        from butlers.tools.finance import track_bill, upcoming_bills

        # Bill due in 30 days — out of default 14-day window but in 60-day window
        due_30 = datetime.now(UTC).date() + timedelta(days=30)
        await track_bill(
            pool=pool,
            payee="Quarterly Bill",
            amount=200.00,
            currency="USD",
            due_date=due_30,
            frequency="quarterly",
        )

        result_14 = await upcoming_bills(pool=pool, days_ahead=14)
        result_60 = await upcoming_bills(pool=pool, days_ahead=60)

        assert len(result_14["needs_action"]) == 0
        assert len(result_60["needs_action"]) == 1

    async def test_needs_action_sorted_by_due_date(self, pool):
        """needs_action items are returned sorted by due_date ascending."""
        from butlers.tools.finance import track_bill, upcoming_bills

        due_a = datetime.now(UTC).date() + timedelta(days=8)
        due_b = datetime.now(UTC).date() + timedelta(days=3)
        due_c = datetime.now(UTC).date() + timedelta(days=12)
        await track_bill(pool=pool, payee="Bill A", amount=10.00, currency="USD", due_date=due_a)
        await track_bill(pool=pool, payee="Bill B", amount=20.00, currency="USD", due_date=due_b)
        await track_bill(pool=pool, payee="Bill C", amount=30.00, currency="USD", due_date=due_c)

        result = await upcoming_bills(pool=pool, days_ahead=14)
        due_dates = [item["bill"]["due_date"] for item in result["needs_action"]]
        assert due_dates == sorted(due_dates)


# ---------------------------------------------------------------------------
# Subscription field provenance (bu-svqpzo.10)
# ---------------------------------------------------------------------------

_GMAIL_ROUTING_CONTEXT = {
    "source_entity_id": "00000000-0000-0000-0000-000000000001",
    "request_context": {
        "source_channel": "email",
        "source_endpoint_identity": "gmail:user:owner@example.invalid",
    },
}


class TestSubscriptionFieldProvenance:
    """cancellation_url and status keep who wrote them; email never outranks the owner."""

    @staticmethod
    def _as_owner(monkeypatch) -> None:
        from butlers.tools.finance import subscriptions as subscriptions_module
        from butlers.tools.finance.expected_signals import FinanceSignalSource

        async def _owner(_pool):
            return FinanceSignalSource("owner")

        monkeypatch.setattr(subscriptions_module, "runtime_signal_source", _owner)

    @staticmethod
    def _as_routed_gmail(monkeypatch) -> None:
        from butlers.tools.finance import expected_signals as expected_signals_module

        monkeypatch.setattr(
            expected_signals_module,
            "get_current_runtime_session_routing_context",
            lambda: _GMAIL_ROUTING_CONTEXT,
        )

    async def _track(self, pool, **overrides):
        from butlers.tools.finance.subscriptions import track_subscription

        kwargs = {
            "pool": pool,
            "service": "Provenance Co",
            "amount": 12.0,
            "currency": "USD",
            "frequency": "yearly",
            "next_renewal": datetime.now(UTC).date() + timedelta(days=30),
        }
        kwargs.update(overrides)
        return await track_subscription(**kwargs)

    async def test_routed_gmail_write_is_stored_as_extracted(self, pool, monkeypatch):
        self._as_routed_gmail(monkeypatch)

        result = await self._track(
            pool, cancellation_url="https://example-phish.test/cancel", status="cancelled"
        )

        assert result["cancellation_url"] == "https://example-phish.test/cancel"
        assert result["cancellation_url_source"] == "extracted"
        assert result["status_source"] == "extracted"

    async def test_routed_gmail_write_never_overwrites_owner_set_values(self, pool, monkeypatch):
        with monkeypatch.context() as owner_context:
            self._as_owner(owner_context)
            owned = await self._track(pool, cancellation_url="https://vendor.example/cancel")
        assert owned["cancellation_url_source"] == "owner"
        assert owned["status_source"] == "owner"

        self._as_routed_gmail(monkeypatch)
        result = await self._track(
            pool, cancellation_url="https://example-phish.test/cancel", status="cancelled"
        )

        assert result["id"] == owned["id"]
        assert result["cancellation_url"] == "https://vendor.example/cancel"
        assert result["cancellation_url_source"] == "owner"
        assert result["status"] == "active"
        assert result["status_source"] == "owner"

    async def test_owner_write_replaces_extracted_values_and_takes_ownership(
        self, pool, monkeypatch
    ):
        self._as_routed_gmail(monkeypatch)
        first = await self._track(
            pool, cancellation_url="https://example-phish.test/cancel", status="cancelled"
        )
        self._as_owner(monkeypatch)

        result = await self._track(pool, cancellation_url="https://vendor.example/cancel")

        assert result["id"] == first["id"]
        assert result["cancellation_url"] == "https://vendor.example/cancel"
        assert result["cancellation_url_source"] == "owner"
        assert result["status"] == "active"
        assert result["status_source"] == "owner"

    async def test_attestation_lookup_failure_is_treated_as_extracted(self, pool, monkeypatch):
        from butlers.tools.finance import subscriptions as subscriptions_module

        async def _unavailable(_pool):
            raise RuntimeError("attestation store down")

        with monkeypatch.context() as owner_context:
            self._as_owner(owner_context)
            owned = await self._track(pool, cancellation_url="https://vendor.example/cancel")
        monkeypatch.setattr(subscriptions_module, "runtime_signal_source", _unavailable)

        result = await self._track(
            pool, cancellation_url="https://example-phish.test/cancel", status="cancelled"
        )

        assert result["id"] == owned["id"]
        assert result["cancellation_url"] == "https://vendor.example/cancel"
        assert result["status"] == "active"

    async def test_attestation_lookup_failure_on_insert_stores_extracted(self, pool, monkeypatch):
        from butlers.tools.finance import subscriptions as subscriptions_module

        async def _unavailable(_pool):
            raise RuntimeError("attestation store down")

        monkeypatch.setattr(subscriptions_module, "runtime_signal_source", _unavailable)

        result = await self._track(pool, cancellation_url="https://example-phish.test/cancel")

        assert result["cancellation_url_source"] == "extracted"
        assert result["status_source"] == "extracted"


class TestProtectedDateAndPriceClaims:
    @pytest.mark.parametrize("order", ["owner-first", "extracted-first", "concurrent", "rollback"])
    async def test_native_committed_pair_claims_owner_confirmation_and_rollback(
        self, pool, monkeypatch, order
    ):
        import asyncio
        from contextvars import ContextVar
        from decimal import Decimal

        from butlers.tools.finance import subscriptions as subs
        from butlers.tools.finance.expected_signals import FinanceSignalSource

        actor = ContextVar("synthetic_actor", default=None)

        async def attestation(connection):
            # Every actual owning write attests using its held TX connection.
            assert connection.is_in_transaction()
            return actor.get()

        monkeypatch.setattr(subs, "runtime_signal_source", attestation)
        today = datetime.now(UTC).date()

        async def write(owner, renewal, amount=10, currency="USD"):
            token = actor.set(
                FinanceSignalSource("owner")
                if owner
                else FinanceSignalSource("connector:gmail", "gmail:synthetic")
            )
            try:
                return await subs.track_subscription(
                    pool, "Synthetic fields", amount, currency, "yearly", renewal
                )
            finally:
                actor.reset(token)

        if order == "extracted-first":
            await write(False, today + timedelta(days=3), 99, "EUR")
            await write(True, today + timedelta(days=30))
        else:
            await write(True, today + timedelta(days=30))
        if order == "concurrent":
            await asyncio.gather(
                *(write(False, today + timedelta(days=3), 99, "EUR") for _ in range(2))
            )
        elif order == "rollback":
            async with pool.acquire() as conn:
                tx = conn.transaction()
                await tx.start()
                try:
                    await conn.execute(
                        "UPDATE subscriptions SET field_claims=$1::jsonb",
                        [
                            {
                                "field": "price",
                                "value": {"amount": "999", "currency": "EUR"},
                                "epoch": 0,
                                "resolved": False,
                            }
                        ],
                    )
                finally:
                    await tx.rollback()
        else:
            await write(False, today + timedelta(days=3), 99, "EUR")
        async with pool.acquire() as observer:
            row = await observer.fetchrow(
                "SELECT * FROM subscriptions WHERE service='Synthetic fields'"
            )
            assert row["amount"] == Decimal("10") and row["currency"] == "USD"
            assert row["next_renewal"] == today + timedelta(days=30)
            assert row["price_source"] == row["renewal_source"] == "owner"
            if order == "rollback":
                assert row["field_claims"] == []
            else:
                assert len(row["field_claims"]) == 2
                assert row["field_claims"][1]["value"] == {"amount": "99", "currency": "EUR"}
                assert (
                    await subs.subscription_warning_still_relevant(
                        observer, row["id"], today + timedelta(days=3), row["date_epoch"]
                    )
                    is True
                )
        epoch = row["date_epoch"]
        await write(True, today + timedelta(days=20), 12, "USD")
        async with pool.acquire() as observer:
            confirmed = await observer.fetchrow(
                "SELECT * FROM subscriptions WHERE id=$1", row["id"]
            )
            assert confirmed["date_epoch"] == epoch + 1
            assert all(claim["resolved"] for claim in confirmed["field_claims"])
            assert (
                await subs.subscription_warning_still_relevant(
                    observer, row["id"], today + timedelta(days=3), epoch
                )
                is False
            )


async def test_attestation_sql_error_rolls_back_only_its_savepoint_and_commits_extracted(
    pool, monkeypatch
):
    from butlers.tools.finance import subscriptions

    async def unavailable(connection):
        # A genuine native SQL failure with the legacy attestor's absorbed-error
        # behavior. Without the writer's savepoint the next write must abort.
        try:
            await connection.fetchval("SELECT 1 / 0")
        except asyncpg.DivisionByZeroError:
            return None

    monkeypatch.setattr(subscriptions, "runtime_signal_source", unavailable)
    result = await subscriptions.track_subscription(
        pool, "Unattested synthetic", 10, "USD", "yearly", datetime.now(UTC).date()
    )
    async with pool.acquire() as observer:
        stored = await observer.fetchrow("SELECT * FROM subscriptions WHERE id=$1", result["id"])
        assert stored["renewal_source"] == stored["price_source"] == "extracted"
        assert stored["field_claims"] == []
