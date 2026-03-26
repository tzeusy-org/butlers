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
    import importlib

    from butlers.tools.finance import bills as _bills
    from butlers.tools.finance import facts as _facts
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
        performance (NULL stored); tsvector (full-text search) is still computed.
        Returns per-row counts for imported, skipped, and errored rows.

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
          reason: "duplicate" (dedup skip), "cross_source_match" (fuzzy dedup skip),
          "invalid_date", "invalid_amount", "missing_merchant", or "db_error: ..."
        """
        import json as _json

        parsed_txns = _json.loads(transactions)
        if not isinstance(parsed_txns, list):
            raise ValueError("transactions must be a JSON array")
        return await _facts.bulk_record_transactions(
            module._get_pool(),
            parsed_txns,
            account_id=account_id,
            source=source,
        )

    # =================================================================
    # Transaction CRUD extensions (finance-intelligence)
    # =================================================================

    if hasattr(_transactions, "update_transaction"):

        @mcp.tool()
        async def update_transaction(
            transaction_id: str,
            category: str | None = None,
            merchant: str | None = None,
            description: str | None = None,
            metadata: str | None = None,
        ) -> dict[str, Any]:
            """Update fields on an existing transaction record.

            Only provided fields are updated; omitted fields retain their current values.
            Triggers merchant category mapping refresh when category is changed.
            """
            return await _transactions.update_transaction(
                module._get_pool(),
                transaction_id=transaction_id,
                category=category,
                merchant=merchant,
                description=description,
                metadata=_parse_metadata(metadata),
            )

    if hasattr(_transactions, "delete_transaction"):

        @mcp.tool()
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

        @mcp.tool()
        async def merge_duplicates(
            keep_id: str,
            discard_id: str,
        ) -> dict[str, Any]:
            """Merge two duplicate transactions, keeping one and soft-deleting the other.

            Merges metadata from the discarded record into the kept record before deletion.
            """
            return await _transactions.merge_duplicates(
                module._get_pool(),
                keep_id=keep_id,
                discard_id=discard_id,
            )

    if hasattr(_transactions, "split_transaction"):

        @mcp.tool()
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

        @mcp.tool()
        async def bulk_recategorize(
            merchant_pattern: str,
            new_category: str,
            dry_run: bool = False,
        ) -> dict[str, Any]:
            """Reassign category for all transactions matching a merchant pattern (ILIKE).

            dry_run=True returns a preview of affected transactions without modifying them.
            Returns: {matched, updated, dry_run, sample_transactions}
            """
            return await _transactions.bulk_recategorize(
                module._get_pool(),
                merchant_pattern=merchant_pattern,
                new_category=new_category,
                dry_run=dry_run,
            )

    # =================================================================
    # Historical data import (finance-intelligence)
    # =================================================================

    if _data_import is not None and hasattr(_data_import, "import_transactions"):

        @mcp.tool()
        async def import_transactions(
            file_path: str,
            account_id: str | None = None,
            currency: str = "USD",
            column_map: str | None = None,
            dry_run: bool = False,
        ) -> dict[str, Any]:
            """Import transactions from a bank CSV export file.

            Automatically detects Chase, Amex, Capital One, and generic CSV formats.
            Normalizes dates, amounts, and merchant names before ingestion.

            file_path: Path to the CSV file to import.
            account_id: Account to associate all imported transactions with.
            currency: ISO-4217 currency code for the import (default "USD").
            column_map: JSON string — optional column name overrides for custom CSV formats.
            dry_run: If true, parse and validate without inserting; returns preview of first 10.

            Returns: {total, imported, skipped, errors, import_batch_id, detected_format}
            """
            return await _data_import.import_transactions(
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
        async def budget_list() -> dict[str, Any]:
            """List all active spending budgets.

            Returns all rows from finance.budgets WHERE is_active = true.

            Returns: {budgets: [{id, category, amount, period, currency,
                      warn_threshold, alert_threshold}], total}
            """
            return await _budgets.budget_list(module._get_pool())

    if _budgets is not None and hasattr(_budgets, "budget_remove"):

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
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

        @mcp.tool()
        async def alert_list() -> dict[str, Any]:
            """List all configured alert rules.

            Returns all active alert configurations stored as memory facts
            with predicate='alert_config'.

            Returns: {alerts: [{type, threshold, currency, enabled, fact_id}], total}
            """
            return await _alerts.alert_list(module._get_pool())

    if _alerts is not None and hasattr(_alerts, "detect_price_changes"):

        @mcp.tool()
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
