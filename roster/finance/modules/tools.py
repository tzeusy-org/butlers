"""Finance MCP tool registrations.

All tool closures live here, extracted from the monolithic
``FinanceModule.register_tools`` method.  Called once during butler
startup via ``register_tools(mcp, module, config)``.

Each tool is decorated with ``@_tool("group")`` instead of ``@mcp.tool()``
so that ``FinanceModuleConfig.groups`` can selectively enable/disable
groups of tools.  When ``groups`` is absent or empty, all tools are
registered (backwards compatible).
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


def register_tools(mcp: Any, module: Any, config: Any = None) -> None:
    """Register all finance MCP tools on *mcp*, using *module* for pool access."""
    from butlers.modules.base import group_enabled

    def _tool(group: str):
        if group_enabled(config, group):
            return mcp.tool()
        return lambda fn: fn  # no-op — function defined but not registered

    # Import sub-modules (deferred to avoid import-time side effects)
    import importlib

    from butlers.tools.finance import bills as _bills
    from butlers.tools.finance import facts as _facts
    from butlers.tools.finance import reconciliation as _reconciliation
    from butlers.tools.finance import spending as _spending
    from butlers.tools.finance import subscriptions as _subscriptions
    from butlers.tools.finance import transactions as _transactions

    def _try_import(module_path: str) -> Any:
        """Attempt to import a finance sub-module; return None if not yet implemented.

        Only suppresses ModuleNotFoundError for the target module itself — missing
        module means it's not yet staged.  Errors raised *inside* an existing module
        (broken dependency, syntax error, etc.) are re-raised so they surface instead
        of silently disabling tools.
        """
        try:
            return importlib.import_module(module_path)
        except ModuleNotFoundError as exc:
            if exc.name == module_path:
                return None
            raise

    _data_import = _try_import("butlers.tools.finance.data_import")
    _pattern_recognition = _try_import("butlers.tools.finance.pattern_recognition")
    _anomaly_detection = _try_import("butlers.tools.finance.anomaly_detection")
    _budgets = _try_import("butlers.tools.finance.budgets")
    _overview = _try_import("butlers.tools.finance.overview")
    _alerts = _try_import("butlers.tools.finance.alerts")

    # =================================================================
    # Transaction tools
    # =================================================================

    @_tool("core")
    async def record_transaction(
        posted_at: str,
        merchant: str,
        amount: float,
        currency: str,
        category: str,
        direction: str | None = None,
        description: str | None = None,
        payment_method: str | None = None,
        account_id: str | None = None,
        receipt_url: str | None = None,
        external_ref: str | None = None,
        source_message_id: str | None = None,
        metadata: str | None = None,
    ) -> dict[str, Any]:
        """Record a transaction in the finance ledger.

        posted_at: ISO 8601 datetime with timezone (e.g. "2024-03-15T10:30:00-07:00").
        merchant: Payee or merchant name.
        amount: Transaction amount as a decimal number.
        currency: ISO-4217 code (e.g. "USD", "EUR"). Never assume USD without clear signal.
        category: Spending category (e.g. "dining", "groceries", "subscriptions", "transport").
        direction: Optional explicit direction override: "debit" or "credit".
          When provided, it takes precedence over the amount sign.
        description: Optional transaction description or memo.
        payment_method: Card or payment method label (e.g. "Amex", "Chase Sapphire").
        account_id: Account identifier (e.g. "chase-checking").
        receipt_url: URL to a receipt image or document.
        external_ref: External reference number from the source system.
        source_message_id: Email message ID or source provenance — always pass when ingesting
          from email. Used for deduplication (duplicate inserts are silently skipped).
        metadata: JSON string — dict of additional context for future enrichment.

        Returns: {id, posted_at, merchant, amount, currency, category, direction, ...}
          When an enabled "large_transaction" alert is configured (via alert_configure)
          and the recorded amount exceeds its threshold, the response also includes a
          "large_transaction_alert": {threshold, amount, merchant, exceeds_by} flag.
        """
        return await _transactions.record_transaction(
            module._get_pool(),
            posted_at=datetime.fromisoformat(posted_at),
            merchant=merchant,
            amount=amount,
            currency=currency,
            category=category,
            direction=direction,
            description=description,
            payment_method=payment_method,
            account_id=account_id,
            receipt_url=receipt_url,
            external_ref=external_ref,
            source_message_id=source_message_id,
            metadata=_parse_metadata(metadata),
        )

    @_tool("core")
    async def list_transactions(
        start_date: str | None = None,
        end_date: str | None = None,
        category: str | None = None,
        merchant: str | None = None,
        account_id: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
        direction: str | None = None,
        tags: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return a paginated, filtered list of transactions.

        start_date: ISO 8601 datetime — filter to transactions on or after this date.
        end_date: ISO 8601 datetime — filter to transactions on or before this date.
        category: Filter by spending category (exact match).
        merchant: Filter by merchant name (substring match).
        account_id: Filter by account identifier.
        min_amount: Minimum transaction amount.
        max_amount: Maximum transaction amount.
        direction: "debit" (money out) or "credit" (money in / refund), or omit for both.
        tags: JSON string — array of tag strings; returns only transactions containing
          ALL provided tags.
        limit: Max records to return (default 50).
        offset: Pagination offset (default 0).

        Soft-deleted transactions are always excluded.

        Returns: {transactions: [{id, posted_at, merchant, amount, ...}], total, limit, offset}
        """
        parsed_tags: list[str] | None = None
        if tags is not None:
            import json as _json

            parsed_tags = _json.loads(tags)
        return await _transactions.list_transactions(
            module._get_pool(),
            start_date=(datetime.fromisoformat(start_date) if start_date is not None else None),
            end_date=(datetime.fromisoformat(end_date) if end_date is not None else None),
            category=category,
            merchant=merchant,
            account_id=account_id,
            min_amount=min_amount,
            max_amount=max_amount,
            direction=direction,
            tags=parsed_tags,
            limit=limit,
            offset=offset,
        )

    # =================================================================
    # Subscription tools
    # =================================================================

    @_tool("subscriptions")
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
        """Create or update a subscription lifecycle record.

        Upsert on service name: if a subscription with the same service exists,
        its fields are updated with the provided values.

        service: Service name (e.g. "Netflix", "Spotify"). Used as the unique key.
        amount: Recurring charge amount as a decimal number.
        currency: ISO-4217 code (e.g. "USD").
        frequency: Billing frequency — "weekly", "monthly", "quarterly", "yearly".
        next_renewal: ISO-8601 date string (YYYY-MM-DD) for next renewal date.
        status: "active" (default), "cancelled", or "paused".
        auto_renew: Whether the service auto-renews (default true).
        payment_method: Card or payment method label.
        account_id: Account identifier.
        source_message_id: Source provenance for deduplication.
        metadata: JSON string — dict of additional context (e.g. plan tier, promo pricing).

        Returns: {id, service, amount, currency, frequency, next_renewal, status, ...}
        """
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

    @_tool("bills")
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
        autopay: bool | None = None,
        predicted: bool | None = None,
    ) -> dict[str, Any]:
        """Create or update a bill obligation.

        Dedupes on the normalized payee + due_date, so re-tracking the same payee
        (even with case/spacing variants) updates the existing row.

        payee: Who the bill is owed to. Use a consistent name for a given payee so
            records don't fragment (e.g. always "Endowus", not sometimes
            "Endowus CPF OA Investment").
        amount: Amount owed as a decimal number.
        currency: ISO-4217 code (e.g. "USD").
        due_date: ISO-8601 date string (YYYY-MM-DD) for payment due date.
        frequency: "one_time" (default), "monthly", "weekly", "quarterly", "yearly".
        status: "pending" (default), "paid", or "overdue". A $0 placeholder may
            not be "overdue".
        payment_method: Card or payment method label.
        account_id: Account identifier.
        statement_period_start: ISO-8601 date for statement period start.
        statement_period_end: ISO-8601 date for statement period end.
        paid_at: ISO 8601 datetime when payment was made. Required when status="paid".
        source_message_id: Source provenance for deduplication.
        metadata: JSON string — dict of additional context.
        autopay: Set true for auto-debited bills (GIRO / CPF / card autopay). These
            are reported as no-action FYIs, never as action items. Omit to leave an
            existing row unchanged.
        predicted: Set true only for pattern-based predictions tracked as bills.
            Prefer NOT tracking predictions (predict_bills is read-only); this keeps
            any that are tracked out of the actionable list. Omit to leave unchanged.

        Returns: {id, payee, amount, currency, due_date, frequency, status, autopay,
            predicted, ...}
        """
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
            autopay=autopay,
            predicted=predicted,
        )

    @_tool("bills")
    async def upcoming_bills(
        days_ahead: int = 14,
        include_overdue: bool = False,
    ) -> dict[str, Any]:
        """Query upcoming bills, segmented by whether the owner must act.

        days_ahead: Number of days to look ahead (default 14).
        include_overdue: Include past-due bills in results (default false).

        Returns a dict with three buckets plus totals:
          - needs_action: confirmed bills the owner must pay manually
            (not autopay, not predicted, amount > 0)
          - autopay: auto-debited bills (GIRO/CPF/card) — FYI, no action
          - predicted: pattern-based rows, not confirmed obligations
          - suppressed_placeholders: count of $0 placeholders hidden
          - totals: needs_action_count/amount, autopay_count/amount, predicted_count
        Each item: {bill, urgency, days_until_due}.
        Urgency values: "overdue", "due_today", "due_soon".
        Only needs_action.amount is money the owner must actively move.
        """
        return await _bills.upcoming_bills(
            module._get_pool(),
            days_ahead=days_ahead,
            include_overdue=include_overdue,
        )

    @_tool("bills")
    async def reconcile_bills(
        lookback_days: int = 90,
        payee: str | None = None,
    ) -> dict[str, Any]:
        """Deterministic bill↔payment reconciliation sweep.

        Scans every unsettled (pending/overdue) bill against recorded debit
        transactions in the trailing lookback window.  Catches the
        "payment-recorded-before-bill-existed" case that the inline hook misses.

        lookback_days: Outer scan horizon for transactions (default 90 days).
          The per-bill date window (±45d/+7d) is applied as an inner filter.
        payee: Optional — restrict the sweep to a single payee (exact match on
          bills.payee). Omit to process all unsettled bills.

        Returns:
          auto_settled: [{bill_id, payee, amount, paid_at, txn_id}, ...]
            Bills that were deterministically matched and settled.  Amount is
            backfilled from the transaction when the bill was a $0 placeholder.
          candidates: [{bill_id, payee, due_date, amount, candidates: [...]}, ...]
            Ambiguous matches (multiple candidates or fuzzy payee) that require
            user or LLM confirmation.  Nothing is mutated for these.

        Idempotent — running it multiple times is safe.  A guarded SQL UPDATE
        (WHERE status <> 'paid' AND reconciled_transaction_id IS NULL) prevents
        double-settlement even under concurrent calls.
        """
        return await _reconciliation.reconcile_bills(
            module._get_pool(),
            lookback_days=lookback_days,
            payee=payee,
        )

    @_tool("bills")
    async def compose_bills_digest(
        sweep: dict[str, Any],
        bills: dict[str, Any],
        predictions: dict[str, Any],
    ) -> dict[str, Any]:
        """Compose the weekly upcoming-bills digest from tool outputs.

        Single source of truth for the upcoming-bills-check digest format and
        early-exit logic — do NOT re-derive the message in prose. Pass the raw
        outputs of reconcile_bills(), upcoming_bills(), and predict_bills()
        straight through; this returns the ready-to-send message.

        sweep: output of reconcile_bills() — keys auto_settled, candidates.
        bills: output of upcoming_bills() — keys needs_action, autopay,
          predicted, totals.
        predictions: output of predict_bills() — key predictions (list).

        Returns:
          message: the fully composed Telegram-ready digest string, or null
            when nothing is worth sending (early exit — send nothing and exit).
        """
        message = _bills.compose_upcoming_bills_digest(sweep, bills, predictions)
        return {"message": message}

    # =================================================================
    # Spending tools
    # =================================================================

    @_tool("analytics")
    async def spending_summary(
        start_date: str | None = None,
        end_date: str | None = None,
        group_by: str | None = None,
        category_filter: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate outflow spending over a date range.

        Defaults to the current calendar month when dates are omitted.

        start_date: ISO-8601 date string, period start (inclusive). Optional.
        end_date: ISO-8601 date string, period end (inclusive). Optional.
        group_by: Aggregation dimension — "category" (default), "merchant", "week",
          "month", "day". None returns a single bucket.
        category_filter: Filter to a specific spending category. Optional.
        account_id: Filter to a specific account. Optional.

        Returns: {groups: [{label, total, count}], grand_total, period}
        """
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

    @_tool("facts")
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

    @_tool("facts")
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

    @_tool("facts")
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

    # NOTE: `track_bill_fact` is intentionally NOT registered as a standalone MCP
    # tool. Bill writes flow through `track_bill`, which fires the SPO mirror via
    # the private `facts._write_bill_fact` helper. Exposing a separate bill-fact
    # tool was a footgun (direct calls bypass track_bill's table upsert +
    # reconciliation), so the registration was removed (Track E2 / bu-z0nzz).

    @_tool("facts")
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

    @_tool("core")
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

    @_tool("bulk")
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

    @_tool("bulk")
    async def bulk_record_transactions(
        transactions: str,
        account_id: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Bulk-ingest normalized transaction objects as bitemporal facts.

        Processes up to 500 transactions per call. Embeddings are skipped for
        performance (NULL stored); tsvector (full-text search) is still computed.
        Returns per-row counts for imported, skipped, and errored rows.

        transactions: JSON string — array of transaction objects. Each must have:
          - posted_at: ISO 8601 datetime string (required)
          - merchant: string (required)
          - amount: string-encoded decimal (required); negative=debit, positive=credit
          Optional per row:
          - direction: explicit "debit" or "credit" override for amount sign
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

        Returns: {total, imported, skipped, errors, error_details, large_transaction_alerts}
          large_transaction_alerts lists rows exceeding the configured
          "large_transaction" alert threshold: [{index, threshold, amount, merchant, exceeds_by}].
        error_details entries: [{index, reason}]
          reason: "duplicate" (dedup skip), "cross_source_match" (fuzzy dedup skip),
          "invalid_date", "invalid_amount", "missing_merchant", or "db_error: ..."
        """
        import json as _json

        parsed_txns = _json.loads(transactions)
        if not isinstance(parsed_txns, list):
            raise ValueError("transactions must be a JSON array")
        return await _transactions.bulk_record_transactions(
            module._get_pool(),
            parsed_txns,
            account_id=account_id,
            source=source,
        )

    # =================================================================
    # Transaction CRUD extensions (finance-intelligence)
    # =================================================================

    if hasattr(_transactions, "update_transaction"):

        @_tool("core")
        async def update_transaction(
            transaction_id: str,
            category: str | None = None,
            merchant: str | None = None,
            description: str | None = None,
            metadata: str | None = None,
            expected_version: int | None = None,
            reason: str | None = None,
        ) -> dict[str, Any]:
            """Update fields on an existing transaction record.

            Only provided fields are updated; omitted fields retain their current values.
            When category is changed, category_source is set to 'manual' and
            is_category_locked is set to true (prevents future auto-recategorization).
            Triggers merchant category mapping refresh when category is changed.

            expected_version: When provided and the version column exists (finance_002),
              the update will only succeed if the row's current version matches
              (optimistic locking). Returns a version_conflict error on mismatch.
            reason: Optional human-readable reason for the update, recorded in corrections.
            """
            return await _transactions.update_transaction(
                module._get_pool(),
                transaction_id=transaction_id,
                category=category,
                merchant=merchant,
                description=description,
                metadata=_parse_metadata(metadata),
                expected_version=expected_version,
                reason=reason,
            )

    if hasattr(_transactions, "delete_transaction"):

        @_tool("core")
        async def delete_transaction(
            transaction_id: str,
        ) -> dict[str, Any]:
            """Soft-delete a transaction by setting deleted_at to now().

            Deleted transactions are excluded from all queries and analytics.
            """
            return await _transactions.delete_transaction(
                module._get_pool(),
                transaction_id=transaction_id,
            )

    if hasattr(_transactions, "merge_duplicates"):

        @_tool("bulk")
        async def merge_duplicates(
            keep_id: str,
            duplicate_ids: str | None = None,
        ) -> dict[str, Any]:
            """Merge duplicate transactions, keeping one canonical and soft-deleting the rest.


            keep_id: UUID of the transaction to keep (canonical record).
            duplicate_ids: JSON string — array of transaction IDs to mark as duplicates
              and soft-delete. Each duplicate gets is_duplicate=true and duplicate_of=keep_id
              (when those columns exist from finance_002 migration).

            Merges metadata from all discarded records into the kept record before deletion.
            Records corrections in transaction_corrections for the audit trail.
            """
            import json as _json

            parsed_duplicate_ids: list[str] | None = None
            if duplicate_ids is not None:
                parsed_duplicate_ids = _json.loads(duplicate_ids)
            return await _transactions.merge_duplicates(
                module._get_pool(),
                keep_id=keep_id,
                duplicate_ids=parsed_duplicate_ids,
            )

    if hasattr(_transactions, "split_transaction"):

        @_tool("bulk")
        async def split_transaction(
            transaction_id: str,
            splits: str,
        ) -> dict[str, Any]:
            """Split a single transaction into multiple records with different amounts/categories.

            splits: JSON string — array of split objects, each with:
              - amount: decimal string (required)
              - category: string (required)
              - description: string (optional)
            All split amounts must sum to the original transaction amount.
            The original transaction is soft-deleted after splitting.
            """
            import json as _json

            parsed_splits = _json.loads(splits)
            return await _transactions.split_transaction(
                module._get_pool(),
                transaction_id=transaction_id,
                splits=parsed_splits,
            )

    if hasattr(_transactions, "bulk_recategorize"):

        @_tool("bulk")
        async def bulk_recategorize(
            merchant_pattern: str,
            new_category: str,
            dry_run: bool = False,
            create_rule: bool = False,
        ) -> dict[str, Any]:
            """Reassign category for all transactions matching a merchant pattern (ILIKE).

            Excludes soft-deleted transactions and category-locked transactions
            (is_category_locked=true, from finance_002 migration).

            dry_run=True returns a preview of affected transactions without modifying them.
            create_rule=True upserts a merchant_mappings rule for future auto-categorization.

            Returns: {matched, updated, dry_run, create_rule, sample_transactions}
            """
            return await _transactions.bulk_recategorize(
                module._get_pool(),
                merchant_pattern=merchant_pattern,
                new_category=new_category,
                dry_run=dry_run,
                create_rule=create_rule,
            )

    # =================================================================
    # Historical data import (finance-intelligence)
    # =================================================================

    if _data_import is not None and hasattr(_data_import, "import_transactions"):

        @_tool("bulk")
        async def import_transactions(
            storage_ref: str,
            account_id: str | None = None,
            currency: str = "USD",
            column_map: str | None = None,
            dry_run: bool = False,
        ) -> dict[str, Any]:
            """Import transactions from a bank CSV export stored in blob storage.

            Automatically detects Chase, Amex, Capital One, and generic CSV formats.
            Normalizes dates, amounts, and merchant names before ingestion.

            storage_ref: BlobStore reference to the CSV file (e.g. 's3://bucket/path/file.csv').
              Upload the CSV first via the dashboard or attachment ingestion flow, then pass
              the returned storage_ref here.
            account_id: Account to associate all imported transactions with.
            currency: ISO-4217 currency code for the import (default "USD").
            column_map: JSON string — optional column name overrides for custom CSV formats.
            dry_run: If true, parse and validate without inserting; returns preview of first 10.

            Returns: {total, imported, skipped, errors, import_batch_id, detected_format}
            """
            if module.blob_store is None:
                return {
                    "error": "Blob storage is not configured. "
                    "Set BLOB_S3_ENDPOINT_URL, BLOB_S3_BUCKET, BLOB_S3_ACCESS_KEY_ID, "
                    "and BLOB_S3_SECRET_ACCESS_KEY in the dashboard secrets UI (/secrets) "
                    "to enable CSV import from blob storage.",
                    "status": "blob_store_not_configured",
                }
            return await _data_import.import_transactions(
                module._get_pool(),
                blob_store=module.blob_store,
                storage_ref=storage_ref,
                account_id=account_id,
                currency=currency,
                column_map=_parse_metadata(column_map),
                dry_run=dry_run,
            )

    if _data_import is not None and hasattr(_data_import, "import_transactions_from_file"):

        @_tool("bulk")
        async def import_transactions_from_file(
            file_path: str,
            account_id: str | None = None,
            currency: str = "USD",
            column_map: str | None = None,
            dry_run: bool = False,
        ) -> dict[str, Any]:
            """Import transactions from a bank CSV file on the local filesystem.

            Automatically detects Chase, Amex, Capital One, and generic CSV formats.
            Normalizes dates, amounts, and merchant names before ingestion.
            Auto-applies learned merchant category mappings for uncategorized rows.

            file_path: Absolute path to the CSV file on the local filesystem.
            account_id: Account to associate all imported transactions with.
            currency: ISO-4217 currency code for the import (default "USD").
            column_map: JSON string — optional column name overrides for custom CSV formats.
            dry_run: If true, parse, validate, and detect duplicates without inserting;
              returns preview of first 10 transactions with is_duplicate flags.

            Returns: {total, imported, skipped, errors, import_batch_id, detected_format,
                      merchant_mappings_applied, mv_refreshed, baselines_triggered}
            """
            return await _data_import.import_transactions_from_file(
                module._get_pool(),
                file_path=file_path,
                account_id=account_id,
                currency=currency,
                column_map=_parse_metadata(column_map),
                dry_run=dry_run,
            )

    # =================================================================
    # Merchant pattern recognition (finance-intelligence)
    # =================================================================

    if _pattern_recognition is not None and hasattr(
        _pattern_recognition, "learn_merchant_categories"
    ):

        @_tool("intelligence")
        async def learn_merchant_categories() -> dict[str, Any]:
            """Learn merchant-to-category mappings from existing transaction history.

            Aggregates category assignments per merchant from finance.transactions,
            then upserts mappings into finance.merchant_mappings with confidence scores.
            Should be run after bulk imports to improve auto-categorization.

            Returns: {learned, updated, skipped, total_mappings}
            """
            return await _pattern_recognition.learn_merchant_categories(
                module._get_pool(),
            )

    if _pattern_recognition is not None and hasattr(_pattern_recognition, "suggest_categories"):

        @_tool("intelligence")
        async def suggest_categories(
            transaction_ids: str,
        ) -> dict[str, Any]:
            """Suggest categories for uncategorized transactions using learned mappings.

            Looks up merchants in finance.merchant_mappings via ILIKE pattern matching.
            Returns suggestions with confidence scores for each transaction.

            transaction_ids: JSON string — array of transaction ID strings.
            Returns: {suggestions: [{transaction_id, merchant, suggested_category, confidence}],
                      unmatched_count}
            """
            import json as _json

            parsed_ids = _json.loads(transaction_ids)
            return await _pattern_recognition.suggest_categories(
                module._get_pool(),
                transaction_ids=parsed_ids,
            )

    if _pattern_recognition is not None and hasattr(
        _pattern_recognition, "recall_merchant_mappings"
    ):

        @_tool("intelligence")
        async def recall_merchant_mappings(
            merchant_pattern: str | None = None,
            category: str | None = None,
        ) -> dict[str, Any]:
            """Query learned merchant-to-category mappings from finance.merchant_mappings.

            Provides LLM-visible access to the learned mapping table for inspection
            and manual override decisions. Both filters are optional (returns all active
            mappings when omitted).

            merchant_pattern: Optional ILIKE filter on raw_pattern or normalized_merchant.
            category: Optional exact filter on the mapped category.

            Returns: {mappings: [{raw_pattern, normalized_merchant, category, confidence,
                      learned_from_count, source}], total}
            """
            return await _pattern_recognition.recall_merchant_mappings(
                module._get_pool(),
                merchant_pattern=merchant_pattern,
                category=category,
            )

    if _pattern_recognition is not None and hasattr(_pattern_recognition, "detect_recurring"):

        @_tool("subscriptions")
        async def detect_recurring(
            min_occurrences: int = 3,
        ) -> dict[str, Any]:
            """Detect recurring charge patterns that may represent untracked subscriptions.

            Scans finance.transactions for merchants with 3+ charges at regular intervals
            and consistent amounts (within 10% variance). Detected patterns are stored
            in finance.recurring_groups for future reference.

            min_occurrences: Minimum number of charges required to flag as recurring (default 3).

            Returns: {detected: [{merchant, estimated_frequency, avg_amount, currency,
                      confidence, already_tracked, price_change_detected, occurrences}],
                      total_detected, new_patterns, updated_patterns}
            """
            return await _pattern_recognition.detect_recurring(
                module._get_pool(),
                min_occurrences=min_occurrences,
            )

    if _pattern_recognition is not None and hasattr(_pattern_recognition, "predict_bills"):

        @_tool("bills")
        async def predict_bills(
            days_ahead: int = 30,
        ) -> dict[str, Any]:
            """Predict upcoming bill payments based on historical transaction patterns.

            Analyzes transactions for payees with 3+ regular payments to compute
            predicted next payment dates from median intervals.

            days_ahead: How many days ahead to look for predicted bills (default 30).

            Returns: {predictions: [{payee, predicted_date, predicted_amount, currency,
                      confidence, is_tracked, amount_drift}], total}
            """
            return await _pattern_recognition.predict_bills(
                module._get_pool(),
                days_ahead=days_ahead,
            )

    # =================================================================
    # Anomaly detection and statistical baselines (finance-intelligence)
    # =================================================================

    if _anomaly_detection is not None and hasattr(_anomaly_detection, "compute_baselines"):

        @_tool("analytics")
        async def compute_baselines() -> dict[str, Any]:
            """Compute statistical spending baselines from 6-month rolling history.

            Calculates per-merchant (median, stddev) and per-category (weekly velocity)
            baselines from finance.transactions. Results are stored as memory facts with
            predicate='spending_baseline' for use by anomaly detection.

            Returns: {computed_merchants, computed_categories, as_of}
            """
            return await _anomaly_detection.compute_baselines(
                module._get_pool(),
            )

    if _anomaly_detection is not None and hasattr(_anomaly_detection, "anomaly_scan"):

        @_tool("analytics")
        async def anomaly_scan(
            days_back: int = 7,
            sensitivity: float = 2.5,
        ) -> dict[str, Any]:
            """Scan recent transactions for anomalies against statistical baselines.

            Flags amount anomalies (deviation beyond sensitivity * stddev), new merchants,
            and category velocity anomalies. Each flagged transaction includes anomaly type,
            severity, and a human-readable explanation.

            days_back: Number of days back to scan (default 7).
            sensitivity: Standard deviation multiplier for anomaly threshold (default 2.5).

            Returns: {anomalies: [{transaction_id, merchant, amount, anomaly_type,
                      severity, explanation}], total, status}
            status='insufficient_data' when baselines are not yet established.
            """
            return await _anomaly_detection.anomaly_scan(
                module._get_pool(),
                days_back=days_back,
                sensitivity=sensitivity,
            )

    if _anomaly_detection is not None and hasattr(_anomaly_detection, "detect_duplicates"):

        @_tool("analytics")
        async def detect_duplicates(
            days_back: int = 30,
        ) -> dict[str, Any]:
            """Detect potential duplicate transactions in recent history.

            Finds same-merchant, same-amount transactions on the same or adjacent days.
            Excludes known subscription charges from false-positive detection.

            days_back: Number of days back to scan for duplicates (default 30).

            Returns: {duplicates: [{transaction_ids, merchant, amount, dates, confidence}],
                      total}
            """
            return await _anomaly_detection.detect_duplicates(
                module._get_pool(),
                days_back=days_back,
            )

    # =================================================================
    # Budget management and spending analytics (finance-intelligence)
    # =================================================================

    if _budgets is not None and hasattr(_budgets, "budget_set"):

        @_tool("budgets")
        async def budget_set(
            category: str,
            amount: float,
            period: str,
            currency: str = "USD",
            warn_threshold: float = 0.8,
            alert_threshold: float = 1.0,
        ) -> dict[str, Any]:
            """Set or update a spending budget for a category.

            Deactivates any existing budget for the same (category, period) combination
            and inserts a new active row. Thresholds trigger notifications when spending
            reaches the specified fraction of the budget amount.

            category: Spending category to budget (e.g. "dining", "groceries").
            amount: Budget limit in the given currency.
            period: Budget period — "monthly", "weekly", or "yearly".
            currency: ISO-4217 currency code (default "USD").
            warn_threshold: Fraction of budget that triggers a warning (default 0.8 = 80%).
            alert_threshold: Fraction of budget that triggers an alert (default 1.0 = 100%).

            Returns: {id, category, amount, period, currency, warn_threshold, alert_threshold}
            """
            return await _budgets.budget_set(
                module._get_pool(),
                category=category,
                amount=amount,
                period=period,
                currency=currency,
                warn_threshold=warn_threshold,
                alert_threshold=alert_threshold,
            )

    if _budgets is not None and hasattr(_budgets, "budget_list"):

        @_tool("budgets")
        async def budget_list() -> dict[str, Any]:
            """List all active spending budgets.

            Returns all rows from finance.budgets WHERE is_active = true.

            Returns: {budgets: [{id, category, amount, period, currency,
                      warn_threshold, alert_threshold}], total}
            """
            return await _budgets.budget_list(module._get_pool())

    if _budgets is not None and hasattr(_budgets, "budget_remove"):

        @_tool("budgets")
        async def budget_remove(
            category: str,
            period: str,
        ) -> dict[str, Any]:
            """Deactivate an existing budget for a (category, period) combination.

            Sets is_active = false on the matching row. The budget record is preserved
            for historical reference but excluded from active checks.

            Returns: {deactivated, category, period}
            """
            return await _budgets.budget_remove(
                module._get_pool(),
                category=category,
                period=period,
            )

    if _budgets is not None and hasattr(_budgets, "budget_status"):

        @_tool("budgets")
        async def budget_status() -> dict[str, Any]:
            """Check current spending against all active budgets.

            Joins active budgets against spending aggregated from transactions in the
            current period. Returns per-category status with utilization percentage.

            Returns: {categories: [{category, budget_amount, spent, utilization,
                      status, currency}], total_categories, as_of}
            status values: "on_track" | "warning" | "exceeded"
            """
            return await _budgets.budget_status(module._get_pool())

    if _budgets is not None and hasattr(_budgets, "spending_trends"):

        @_tool("analytics")
        async def spending_trends(
            comparison: str = "month_over_month",
            months: int = 6,
            category: str | None = None,
        ) -> dict[str, Any]:
            """Analyze spending trends over time with period-over-period comparisons.

            comparison: "month_over_month" or "year_over_year".
            months: Number of months of history to include (default 6).
            category: Optional filter to a specific spending category.

            Returns: {periods: [{period, amount, change_pct, direction}],
                      trend_direction, avg_monthly_spend}
            """
            return await _budgets.spending_trends(
                module._get_pool(),
                comparison=comparison,
                months=months,
                category=category,
            )

    if _budgets is not None and hasattr(_budgets, "spending_forecast"):

        @_tool("analytics")
        async def spending_forecast() -> dict[str, Any]:
            """Forecast end-of-month spending based on current-month trajectory.

            Uses linear projection: (current_spend / days_elapsed) * days_in_month.
            Includes per-category forecasts and comparison against budget targets where set.
            Handles first-of-month edge case by using prior month as the basis.

            Returns: {total_forecast, days_elapsed, days_in_month, current_spend,
                      categories: [{category, current_spend, forecast, budget_amount,
                      budget_status}], as_of, status}
            status='insufficient_data' when less than 3 days of current-month data exists.
            """
            return await _budgets.spending_forecast(module._get_pool())

    # =================================================================
    # Financial overview tools (finance-intelligence)
    # =================================================================

    if _overview is not None and hasattr(_overview, "net_worth_snapshot"):

        @_tool("analytics")
        async def net_worth_snapshot(
            account: str,
            institution: str,
            balance: float,
            currency: str = "USD",
            as_of_date: str | None = None,
        ) -> dict[str, Any]:
            """Record a point-in-time account balance snapshot.

            Upserts into finance.balance_snapshots using the (account_id, as_of_date)
            unique constraint. Used to track net worth over time.

            account: Account name or identifier (e.g. "Checking", "Credit Card").
            institution: Financial institution name (e.g. "Chase", "Fidelity").
            balance: Account balance (positive for assets, negative for liabilities).
            currency: ISO-4217 currency code (default "USD").
            as_of_date: ISO-8601 date string (defaults to today).

            Returns: {id, account, institution, balance, currency, as_of_date}
            """
            return await _overview.net_worth_snapshot(
                module._get_pool(),
                account=account,
                institution=institution,
                balance=balance,
                currency=currency,
                as_of_date=as_of_date,
            )

    if _overview is not None and hasattr(_overview, "net_worth_history"):

        @_tool("analytics")
        async def net_worth_history(
            months: int = 12,
        ) -> dict[str, Any]:
            """Retrieve net worth history across all tracked accounts.

            Queries finance.balance_snapshots joined with finance.accounts, applying
            carry-forward logic for months where no snapshot was recorded. Computes
            total_assets, total_liabilities, and net_worth per month.

            months: Number of months of history to return (default 12).

            Returns: {history: [{month, accounts: [{name, balance, currency}],
                      total_assets, total_liabilities, net_worth}], as_of}
            """
            return await _overview.net_worth_history(
                module._get_pool(),
                months=months,
            )

    if _overview is not None and hasattr(_overview, "cash_flow"):

        @_tool("analytics")
        async def cash_flow(
            period: str = "monthly",
            months: int = 6,
            breakdown: bool = False,
        ) -> dict[str, Any]:
            """Analyze cash flow by aggregating income vs. expenses over time.

            Aggregates transaction credits (income) vs. debits (expenses) by period,
            computing net flow and savings rate. Optional category breakdown available.

            period: Aggregation period — "monthly" or "weekly".
            months: Number of months of history to include (default 6).
            breakdown: If true, include per-category spending breakdown per period.

            Returns: {periods: [{period, income, expenses, net, savings_rate,
                      categories (if breakdown=true)}], avg_net, avg_savings_rate, as_of}
            """
            return await _overview.cash_flow(
                module._get_pool(),
                period=period,
                months=months,
                breakdown=breakdown,
            )

    if _overview is not None and hasattr(_overview, "subscription_audit"):

        @_tool("subscriptions")
        async def subscription_audit() -> dict[str, Any]:
            """Audit all subscriptions — tracked and auto-detected recurring charges.

            Combines explicitly tracked subscriptions with patterns detected by
            detect_recurring(). Computes annual cost projections and identifies
            changes (price increases, newly detected services) since the last audit.

            Returns: {tracked: [{service, amount, frequency, annual_cost, currency}],
                      detected: [{merchant, estimated_frequency, avg_amount, annual_cost}],
                      total_annual_cost, new_since_last_audit, changes_detected, as_of}
            """
            return await _overview.subscription_audit(module._get_pool())

    if _overview is not None and hasattr(_overview, "flag_tax_deductible"):

        @_tool("intelligence")
        async def flag_tax_deductible(
            year: int | None = None,
        ) -> dict[str, Any]:
            """Identify potentially tax-deductible transactions for a given tax year.

            Queries transactions for the specified year and cross-references against
            categories marked is_tax_relevant in finance.categories. Returns flagged
            transactions with their tax_category and a summary.

            year: Tax year to query (defaults to current year).

            Returns: {transactions: [{id, merchant, amount, category, tax_category,
                      posted_at}], total_amount, year, disclaimer}
            """
            return await _overview.flag_tax_deductible(
                module._get_pool(),
                year=year,
            )

    # =================================================================
    # Alert system (finance-intelligence)
    # =================================================================

    if _alerts is not None and hasattr(_alerts, "alert_configure"):

        @_tool("intelligence")
        async def alert_configure(
            type: str,
            threshold: float | None = None,
            currency: str = "USD",
            enabled: bool = True,
        ) -> dict[str, Any]:
            """Configure a spending alert rule.

            Stores alert configuration as a memory fact with predicate='alert_config'.
            Alert types: "large_transaction" (amount threshold), "budget_exceeded",
            "new_merchant", "price_change" (subscription price change detection).

            type: Alert type identifier.
            threshold: Amount threshold for large_transaction alerts (ignored for other types).
            currency: ISO-4217 currency code (default "USD").
            enabled: Whether the alert is active (default true).

            Returns: {type, threshold, currency, enabled, fact_id}
            """
            return await _alerts.alert_configure(
                module._get_pool(),
                type=type,
                threshold=threshold,
                currency=currency,
                enabled=enabled,
            )

    if _alerts is not None and hasattr(_alerts, "alert_list"):

        @_tool("intelligence")
        async def alert_list() -> dict[str, Any]:
            """List all configured alert rules.

            Returns all active alert configurations stored as memory facts
            with predicate='alert_config'.

            Returns: {alerts: [{type, threshold, currency, enabled, fact_id}], total}
            """
            return await _alerts.alert_list(module._get_pool())

    if _alerts is not None and hasattr(_alerts, "detect_price_changes"):

        @_tool("subscriptions")
        async def detect_price_changes(
            days_back: int = 60,
        ) -> dict[str, Any]:
            """Detect price changes in tracked subscription charges.

            Compares recent charges for tracked subscription merchants against
            their recorded amounts in finance.subscriptions. Flags changes > 5%.

            days_back: How many days back to scan for charges (default 60).

            Returns: {changes: [{service, tracked_amount, recent_charge, change_pct,
                      direction, last_seen_at}], total}
            """
            return await _alerts.detect_price_changes(
                module._get_pool(),
                days_back=days_back,
            )
