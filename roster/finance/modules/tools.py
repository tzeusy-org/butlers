"""Finance MCP tool registrations.

All ``@mcp.tool()`` closures live here, extracted from the monolithic
``FinanceModule.register_tools`` method.  Called once during butler
startup via ``register_tools(mcp, module)``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def register_tools(mcp: Any, module: Any) -> None:
    """Register all finance MCP tools on *mcp*, using *module* for pool access."""

    # Import sub-modules (deferred to avoid import-time side effects)
    from butlers.tools.finance import bills as _bills
    from butlers.tools.finance import spending as _spending
    from butlers.tools.finance import subscriptions as _subscriptions
    from butlers.tools.finance import transactions as _transactions

    # =================================================================
    # Transaction tools
    # =================================================================

    @mcp.tool()
    async def record_transaction(
        posted_at: str,
        merchant: str,
        amount: float,
        currency: str,
        category: str,
        description: str | None = None,
        payment_method: str | None = None,
        account_id: str | None = None,
        receipt_url: str | None = None,
        external_ref: str | None = None,
        source_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a transaction in the finance ledger."""
        return await _transactions.record_transaction(
            module._get_pool(),
            posted_at=datetime.fromisoformat(posted_at),
            merchant=merchant,
            amount=amount,
            currency=currency,
            category=category,
            description=description,
            payment_method=payment_method,
            account_id=account_id,
            receipt_url=receipt_url,
            external_ref=external_ref,
            source_message_id=source_message_id,
            metadata=metadata,
        )

    @mcp.tool()
    async def list_transactions(
        start_date: str | None = None,
        end_date: str | None = None,
        category: str | None = None,
        merchant: str | None = None,
        account_id: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return a paginated, filtered list of transactions."""
        return await _transactions.list_transactions(
            module._get_pool(),
            start_date=(datetime.fromisoformat(start_date) if start_date is not None else None),
            end_date=(datetime.fromisoformat(end_date) if end_date is not None else None),
            category=category,
            merchant=merchant,
            account_id=account_id,
            min_amount=min_amount,
            max_amount=max_amount,
            limit=limit,
            offset=offset,
        )

    # =================================================================
    # Subscription tools
    # =================================================================

    @mcp.tool()
    async def track_subscription(
        service: str,
        amount: float,
        currency: str,
        frequency: str,
        next_renewal: str,
        status: str = "active",
        auto_renew: bool = True,
        payment_method: str | None = None,
        account_id: str | None = None,
        source_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or update a subscription lifecycle record."""
        return await _subscriptions.track_subscription(
            module._get_pool(),
            service=service,
            amount=amount,
            currency=currency,
            frequency=frequency,
            next_renewal=next_renewal,
            status=status,
            auto_renew=auto_renew,
            payment_method=payment_method,
            account_id=account_id,
            source_message_id=source_message_id,
            metadata=metadata,
        )

    # =================================================================
    # Bill tools
    # =================================================================

    @mcp.tool()
    async def track_bill(
        payee: str,
        amount: float,
        currency: str,
        due_date: str,
        frequency: str = "one_time",
        status: str = "pending",
        payment_method: str | None = None,
        account_id: str | None = None,
        statement_period_start: str | None = None,
        statement_period_end: str | None = None,
        paid_at: str | None = None,
        source_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or update a bill obligation."""
        return await _bills.track_bill(
            module._get_pool(),
            payee=payee,
            amount=amount,
            currency=currency,
            due_date=due_date,
            frequency=frequency,
            status=status,
            payment_method=payment_method,
            account_id=account_id,
            statement_period_start=statement_period_start,
            statement_period_end=statement_period_end,
            paid_at=paid_at,
            source_message_id=source_message_id,
            metadata=metadata,
        )

    @mcp.tool()
    async def upcoming_bills(
        days_ahead: int = 14,
        include_overdue: bool = False,
    ) -> dict[str, Any]:
        """Query bills due within the requested horizon with urgency classification."""
        return await _bills.upcoming_bills(
            module._get_pool(),
            days_ahead=days_ahead,
            include_overdue=include_overdue,
        )

    # =================================================================
    # Spending tools
    # =================================================================

    @mcp.tool()
    async def spending_summary(
        start_date: str | None = None,
        end_date: str | None = None,
        group_by: str | None = None,
        category_filter: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate outflow spending over a date range."""
        return await _spending.spending_summary(
            module._get_pool(),
            start_date=start_date,
            end_date=end_date,
            group_by=group_by,
            category_filter=category_filter,
            account_id=account_id,
        )
