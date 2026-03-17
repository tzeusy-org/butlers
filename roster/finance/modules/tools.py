"""Finance MCP tool registrations.

All ``@mcp.tool()`` closures live here, extracted from the monolithic
``FinanceModule.register_tools`` method.  Called once during butler
startup via ``register_tools(mcp, module)``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def _parse_metadata(metadata: str | None) -> dict[str, Any] | None:
    """Parse a JSON string into a dict, passthrough None."""
    if metadata is None:
        return None
    return json.loads(metadata)


def register_tools(mcp: Any, module: Any) -> None:
    """Register all finance MCP tools on *mcp*, using *module* for pool access."""

    # Import sub-modules (deferred to avoid import-time side effects)
    from butlers.tools.finance import bills as _bills
    from butlers.tools.finance import facts as _facts
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
        metadata: str | None = None,
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
            metadata=_parse_metadata(metadata),
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
        metadata: str | None = None,
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
            metadata=_parse_metadata(metadata),
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
        metadata: str | None = None,
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
            metadata=_parse_metadata(metadata),
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

    # =================================================================
    # SPO fact-layer tools (bu-ddb.4)
    # =================================================================

    @mcp.tool()
    async def record_transaction_fact(
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
        metadata: str | None = None,
    ) -> dict[str, Any]:
        """Record a transaction as a bitemporal SPO fact anchored to the owner entity.

        Direction is inferred from amount sign: negative = debit (money out),
        positive = credit (money in / refund). Amount precision is preserved as
        a string-encoded NUMERIC in the fact metadata.

        When source_message_id is provided, duplicate inserts return the existing
        fact ID without creating a new record.
        """
        return await _facts.record_transaction_fact(
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
            metadata=_parse_metadata(metadata),
        )

    @mcp.tool()
    async def list_transaction_facts(
        start_date: str | None = None,
        end_date: str | None = None,
        category: str | None = None,
        merchant: str | None = None,
        account_id: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
        direction: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return a paginated, filtered list of transaction facts from the SPO store.

        direction: 'debit' (money out) or 'credit' (money in / refund), or None for both.
        Results are sorted by valid_at DESC.
        """
        if direction is not None and direction not in ("debit", "credit"):
            raise ValueError("direction must be one of 'debit', 'credit', or None")
        return await _facts.list_transaction_facts(
            module._get_pool(),
            start_date=(datetime.fromisoformat(start_date) if start_date is not None else None),
            end_date=(datetime.fromisoformat(end_date) if end_date is not None else None),
            category=category,
            merchant=merchant,
            account_id=account_id,
            min_amount=min_amount,
            max_amount=max_amount,
            direction=direction,
            limit=limit,
            offset=offset,
        )

    @mcp.tool()
    async def track_account_fact(
        institution: str,
        type: str,
        currency: str = "USD",
        name: str | None = None,
        last_four: str | None = None,
        metadata: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a financial account as a property fact (supersession).

        Each unique (institution, type, last_four) combination is stored as an
        independent active fact so multiple accounts per institution coexist.
        Re-submitting the same account updates it in-place via supersession.
        """
        return await _facts.track_account_fact(
            module._get_pool(),
            institution=institution,
            type=type,
            currency=currency,
            name=name,
            last_four=last_four,
            metadata=_parse_metadata(metadata),
        )

    @mcp.tool()
    async def track_subscription_fact(
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
        metadata: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a subscription commitment as a property fact (supersession).

        frequency: weekly | monthly | quarterly | yearly | custom
        status: active | cancelled | paused
        next_renewal: ISO-8601 date string (YYYY-MM-DD).
        Different services coexist as independent facts; same service is updated in-place.
        """
        return await _facts.track_subscription_fact(
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
            metadata=_parse_metadata(metadata),
        )

    @mcp.tool()
    async def track_bill_fact(
        payee: str,
        amount: float,
        currency: str,
        due_date: str,
        frequency: str = "one_time",
        status: str = "pending",
        payment_method: str | None = None,
        account_id: str | None = None,
        paid_at: str | None = None,
        source_message_id: str | None = None,
        metadata: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a bill obligation as a property fact (supersession).

        frequency: one_time | weekly | monthly | quarterly | yearly | custom
        status: pending | paid | overdue
        due_date: ISO-8601 date string (YYYY-MM-DD).
        paid_at: ISO-8601 datetime string when status is 'paid'.
        Different bills (payee + due_date) coexist; same bill is updated in-place.
        """
        return await _facts.track_bill_fact(
            module._get_pool(),
            payee=payee,
            amount=amount,
            currency=currency,
            due_date=due_date,
            frequency=frequency,
            status=status,
            payment_method=payment_method,
            account_id=account_id,
            paid_at=paid_at,
            source_message_id=source_message_id,
            metadata=_parse_metadata(metadata),
        )

    @mcp.tool()
    async def spending_summary_facts(
        start_date: str | None = None,
        end_date: str | None = None,
        group_by: str | None = None,
        category_filter: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate outflow (debit) spending from SPO transaction facts over a date range.

        Returns the same shape as spending_summary() but reads from the fact store.
        Amounts in groups are string-encoded for NUMERIC precision.
        group_by: category | merchant | week | month, or None (single bucket).
        Defaults to the current calendar month when start_date/end_date are omitted.
        When group_by='category', uses inferred_category overlay when present.
        When group_by='merchant', uses normalized_merchant overlay when present.
        """
        return await _facts.spending_summary_facts(
            module._get_pool(),
            start_date=start_date,
            end_date=end_date,
            group_by=group_by,
            category_filter=category_filter,
            account_id=account_id,
        )

    # =================================================================
    # Merchant normalization and bulk update tools
    # =================================================================

    @mcp.tool()
    async def list_distinct_merchants(
        start_date: str | None = None,
        end_date: str | None = None,
        min_count: int | None = None,
        unnormalized_only: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return distinct merchants from active transaction facts with aggregate stats.

        Groups by normalized_merchant when present, falling back to original merchant.
        Returns: {items: [{merchant, normalized_merchant, count, total_amount}],
        total, limit, offset}

        start_date / end_date: ISO-8601 date strings (optional filter)
        min_count: Only return merchants with at least this many transactions (HAVING)
        unnormalized_only: Only return merchants where normalized_merchant is not set
        limit: Max results per page (default 500, max 1000)
        offset: Pagination offset
        """
        return await _facts.list_distinct_merchants(
            module._get_pool(),
            start_date=start_date,
            end_date=end_date,
            min_count=min_count,
            unnormalized_only=unnormalized_only,
            limit=limit,
            offset=offset,
        )

    @mcp.tool()
    async def bulk_update_transactions(
        ops: str,
    ) -> dict[str, Any]:
        """Apply bulk metadata overlay to matching transaction facts.

        ops: JSON string — array of update operations, each with shape:
          {"match": {"merchant_pattern": "<ILIKE>"}, "set": {"normalized_merchant": "...",
          "inferred_category": "..."}}

        Constraints:
        - Maximum 200 ops per call.
        - Only 'normalized_merchant' and 'inferred_category' keys may be set.
        - Uses JSONB overlay so original merchant/category columns are NEVER modified.
        - merchant_pattern uses ILIKE (case-insensitive, % wildcards supported).

        Returns: {updated_total, results: [{pattern, set, matched, updated}]}
        """
        import json as _json

        parsed_ops = _json.loads(ops)
        return await _facts.bulk_update_transactions(
            module._get_pool(),
            ops=parsed_ops,
        )

    # =================================================================
    # Bulk transaction ingestion (embedding-bypass path)
    # =================================================================

    @mcp.tool()
    async def bulk_record_transactions(
        transactions: str,
        account_id: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Bulk-ingest normalized transaction objects as bitemporal facts.

        Processes up to 500 transactions per call. Embeddings are skipped for
        performance (zero vector stored); tsvector (full-text search) is still
        computed. Returns per-row counts for imported, skipped, and errored rows.

        transactions: JSON string — array of transaction objects. Each must have:
          - posted_at: ISO 8601 datetime string (required)
          - merchant: string (required)
          - amount: string-encoded decimal (required); negative=debit, positive=credit
          Optional per row:
          - currency: ISO-4217 code (default "USD")
          - category: string (default "uncategorized")
          - description: string
          - payment_method: string
          - account_id: per-row account_id (overrides top-level account_id)
          - source_message_id: string (uses email-based dedup when present)
          - metadata: dict of additional fields

        account_id: Top-level account_id inherited by all rows unless overridden
          per row. Included in composite dedup key.

        source: Stored as import_source in fact metadata for all rows.
          Use e.g. "csv-import" to tag the ingestion origin.

        Returns: {total, imported, skipped, errors, error_details}
        error_details entries: [{index, reason}]
          reason: "duplicate" (dedup skip), "invalid_date", "invalid_amount",
          "missing_merchant"
        """
        import json as _json

        parsed_txns = _json.loads(transactions)
        if not isinstance(parsed_txns, list):
            raise ValueError("transactions must be a JSON array")
        if len(parsed_txns) > 500:
            raise ValueError(f"Batch too large: {len(parsed_txns)} exceeds maximum of 500")
        return await _facts.bulk_record_transactions(
            module._get_pool(),
            transactions=parsed_txns,
            account_id=account_id,
            source=source,
        )
