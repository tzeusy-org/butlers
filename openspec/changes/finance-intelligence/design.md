## Context

The Finance butler currently operates as a structured ledger: it records transactions (individual and bulk CSV import), tracks subscriptions and bills via manual entry, and produces basic spending summaries with category/merchant/week/month grouping. After the `finance-data-model-redesign` change, all transactional data lives in the dedicated `finance.transactions` table with typed columns, B-tree indexes, and supporting tables (`finance.merchant_mappings`, `finance.budgets`, `finance.recurring_groups`, etc.). The user has full historical transaction data exports from their banks, meaning the butler can work with years of data to establish statistical baselines.

The existing tool surface (post data-model-redesign):
- `record_transaction` / `bulk_record_transactions` / `list_transactions` -- CRUD on `finance.transactions`
- `track_subscription` / `track_bill` / `upcoming_bills` -- manual obligation tracking (SPO property facts)
- `spending_summary` -- SQL aggregation over `finance.transactions` by category/merchant/week/month
- `transaction-csv-extraction` skill -- LLM-driven CSV parsing into `bulk_record_transactions`

The missing layer is analytical intelligence: the butler records data but does not analyze it for patterns, anomalies, trends, or forecasts. The user must ask the right questions; the butler never proactively surfaces insights.

## Goals / Non-Goals

**Goals:**
- Build SQL-driven analytics that run over `finance.transactions` (dedicated table) without external dependencies
- Establish pattern baselines from historical data that improve as more data is ingested
- Provide proactive intelligence via scheduled digest tasks and post-insert hooks
- Support large historical imports (10k+ transactions) with deduplication and retroactive analytics
- Keep all intelligence explainable -- every flag, alert, or categorization includes the reasoning
- Store learned mappings (merchant categories, spending baselines) in dedicated tables (`finance.merchant_mappings`) and memory facts (baselines, anomaly thresholds) so they persist and evolve

**Non-Goals:**
- Machine learning models or external ML APIs -- all analytics use SQL aggregations and statistical heuristics
- Investment advice, portfolio tracking, or wealth management
- Real-time streaming analytics -- intelligence runs on query or on schedule, not as a live pipeline
- Multi-currency normalization (exchange rate conversion) -- amounts are compared within the same currency
- Integration with bank APIs for live transaction feeds -- the butler works with imported data and email-extracted transactions
- Double-entry accounting or formal bookkeeping compliance

## Decisions

### 1. Analytics as SQL aggregations over `finance.transactions` (dedicated table), not SPO facts

All intelligence tools query the dedicated `finance.transactions` table (promoted by the `finance-data-model-redesign` change) with SQL window functions, statistical aggregations, and typed column access. No JSONB extraction or separate materialized analytics tables (except the `finance.spending_summaries` materialized view provided by the redesign for dashboard performance).

**Rationale**: The dedicated table has typed columns (`amount NUMERIC(14,2)`, `merchant TEXT`, `category TEXT`, `posted_at TIMESTAMPTZ`) with B-tree indexes that support range queries, aggregations, and window functions directly. PostgreSQL window functions (`LAG`, `LEAD`, `STDDEV`, `PERCENTILE_CONT`) operate on typed columns without casting. Running these over JSONB metadata extraction (`(metadata->>'amount')::numeric`) in the SPO table would defeat indexing and force sequential scans at scale.

**Alternative considered**: Querying SPO facts (`shared.facts`) with expression indexes on JSONB fields. Rejected because: (a) no type safety -- malformed values corrupt the index, (b) 7+ expression indexes needed on a shared table, (c) every query still pays JSONB extraction cost, (d) `shared.facts` serves all butlers so heavy analytical queries affect non-finance consumers.

**Dependency**: This decision depends on `finance-data-model-redesign` being merged first, which promotes `finance.transactions` as the primary query target and adds the indexes and columns intelligence tools require.

### 2. Merchant category mappings stored in `finance.merchant_mappings` (dedicated table), with LLM recall tool

Learned merchant-to-category mappings are stored in the `finance.merchant_mappings` table (provided by `finance-data-model-redesign`) with columns `raw_pattern`, `normalized_merchant`, `category`, `confidence`, `learned_from_count`, `source`, and a `UNIQUE INDEX ON lower(raw_pattern) WHERE is_active = true`. A `recall_merchant_mappings(merchant_pattern?, category?)` tool provides LLM-visible query access to the mappings.

**Rationale**: Merchant mapping lookups are high-frequency (called on every transaction import for auto-categorization) and pattern-matching intensive (`ILIKE`). A dedicated table with a unique index on the normalized pattern is dramatically faster than scanning SPO facts with `predicate='merchant_category_mapping'` and extracting patterns from JSONB metadata. The `recall_merchant_mappings()` tool preserves LLM visibility into the learned mappings without requiring the memory fact layer.

**Alternative considered**: SPO facts with `predicate='merchant_category_mapping'`. Rejected because: (a) lookups require scanning all facts with that predicate and extracting patterns from metadata, (b) `ILIKE` pattern matching on JSONB-extracted strings is not indexable, (c) high-frequency auto-categorization calls would be unnecessarily slow.

**Dependency**: The `finance.merchant_mappings` table is created by `finance-data-model-redesign` migration `finance_002`.

### 3. Anomaly detection uses statistical deviation from rolling baselines, not fixed rules

Anomalies are detected by comparing each transaction against a rolling baseline (median and standard deviation of amount per merchant, category spending velocity per week, time-of-day patterns). A transaction is flagged when it deviates by more than a configurable number of standard deviations (default: 2.5).

**Rationale**: Fixed thresholds ("flag anything over $500") do not adapt to individual spending patterns. A user who regularly buys $800 groceries should not get flagged, while a user who normally spends $50 on dining should be alerted to a $200 dinner. Statistical baselines adapt automatically.

**Alternative considered**: User-defined fixed thresholds only. Rejected as the primary mechanism but retained as a complementary override (the `finance-alerts` capability supports configurable absolute thresholds alongside statistical detection).

### 4. Recurring charge detection uses temporal pattern matching on `finance.transactions`

To detect untracked subscriptions, the system queries `finance.transactions` grouped by merchant, looking for: (a) 3+ charges from the same merchant, (b) with similar amounts (within 10% variance), (c) at regular intervals (weekly/monthly/quarterly/yearly, with tolerance). Detected patterns are stored in `finance.recurring_groups` and surfaced as suggestions, not auto-created as subscriptions.

**Rationale**: Auto-creating subscription records risks false positives (e.g., frequent coffee shop visits). Surfacing suggestions lets the user confirm or dismiss. The 3-charge minimum and amount variance tolerance reduce noise.

### 5. Budget targets stored in `finance.budgets` (dedicated table) with scheduled threshold checks

Budget targets are stored in the `finance.budgets` table (provided by `finance-data-model-redesign`) with typed columns `category TEXT`, `amount NUMERIC(14,2)`, `currency CHAR(3)`, `period TEXT`, `warn_threshold FLOAT`, `alert_threshold FLOAT`, and a `UNIQUE INDEX ON (category, period) WHERE is_active = true`. A scheduled task runs weekly to compare actual spending (from `finance.transactions`) against budgets and notifies the user when thresholds are crossed.

**Rationale**: Budget status checks require joining actual spending against budget targets. With a dedicated table, this is a simple `JOIN` on `category` with typed column comparison. With SPO property facts, the query must extract `metadata->>'category'`, `metadata->>'amount'`, and `metadata->>'period'` from JSONB, making the join predicate non-indexable. Budget enforcement is a hot path (checked weekly, and post-insert when implemented).

**Trade-off**: A transaction that pushes spending over budget will not be flagged until the next scheduled check (up to 7 days delay). Acceptable for v1; post-insert budget check can be added as a future enhancement.

**Dependency**: The `finance.budgets` table is created by `finance-data-model-redesign` migration `finance_002`.

### 6. Historical import extends existing CSV extraction with format detection

The import pipeline builds on the existing `transaction-csv-extraction` skill by adding: (a) bank format detection (header pattern matching for Chase, Amex, Capital One, and generic CSV), (b) column mapping normalization, (c) date format auto-detection, (d) deduplication against existing rows in `finance.transactions` using the tiered UNIQUE partial index strategy (Priority 1: `(account_id, external_id)`, Priority 2: `(source_message_id, merchant, amount, posted_at)`, Priority 3: `(account_id, posted_at, amount, merchant)` fallback), (e) post-import baseline calculation, and (f) import batch tracking via `finance.import_batches`.

**Rationale**: The existing `bulk_record_transactions` tool already handles batch ingestion with per-row validation and idempotency via the tiered dedup indexes. The gap is in pre-processing: different banks export different column names, date formats, and amount conventions (some use negative for debits, some have separate debit/credit columns). Format detection fills this gap without requiring the user to manually map columns.

### 7. Net worth tracking as manual balance snapshots, not automated

Net worth is tracked through user-reported account balance snapshots stored in the `finance.balance_snapshots` table (provided by `finance-data-model-redesign`) with columns `account_id`, `balance NUMERIC(14,2)`, `currency`, `as_of_date`, `source`, and a `UNIQUE INDEX ON (account_id, as_of_date)`. The butler does not connect to bank APIs.

**Rationale**: Automated bank balance fetching requires Plaid or similar integrations, OAuth token management, and ongoing API costs. The user-federated model means each instance would need its own credentials. Manual snapshots (even monthly) provide sufficient trend data for net worth tracking. This aligns with the butler's manifesto: "awareness without the work" -- periodic balance updates are minimal effort.

**Dependency**: The `finance.balance_snapshots` table is created by `finance-data-model-redesign` migration `finance_002`.

### 8. Spending forecasting uses linear projection from current-month trajectory

End-of-month spending forecast is calculated as: `(current_spend / days_elapsed) * days_in_month`. Category-level forecasts use the same formula per category. Historical monthly averages provide a secondary "expected" value for comparison.

**Rationale**: Simple linear projection is surprisingly accurate for regular spending and easy to explain ("at your current pace, you'll spend $X by month end"). More sophisticated models (seasonal adjustment, exponential smoothing) add complexity without proportional accuracy gains for personal finance.

## Risks / Trade-offs

- **[Cold start problem]** -- Analytics require sufficient historical data to establish baselines. New users with no imported data will get no anomaly detection or trend analysis until enough transactions accumulate. Mitigation: The historical import pipeline is prioritized as the first implementation task. Post-import, baselines are retroactively calculated. Document minimum data requirements (suggest 3+ months of history).

- **[False positive anomaly fatigue]** -- Overly sensitive anomaly detection floods the user with alerts, causing them to ignore real anomalies. Mitigation: Default threshold is conservative (2.5 standard deviations). Users can adjust sensitivity. Anomalies are surfaced in digests rather than real-time alerts, reducing notification fatigue.

- **[Merchant name inconsistency across banks]** -- The same merchant appears under different names in different bank exports (e.g., "AMZN*Marketplace" vs "Amazon.com" vs "AMAZON DIGITAL"). Mitigation: Merchant category mapping uses pattern matching (substring/prefix), not exact match. The LLM runtime can also normalize merchant names during CSV extraction.

- **[Performance on large transaction sets]** -- Statistical queries (STDDEV, PERCENTILE_CONT, window functions) over 50k+ transactions may be slow. Mitigation: Queries are bounded by date range (baselines use rolling 6-month windows). B-tree indexes on `posted_at`, `merchant`, `category`, and the composite `idx_txn_debit_category_posted` partial index keep scans efficient. The `finance.spending_summaries` materialized view handles the most common aggregation pattern.

- **[Budget period alignment]** -- Users may want weekly, bi-weekly, or monthly budgets, but spending data arrives at irregular intervals. Mitigation: Budget periods are configurable (weekly/monthly/quarterly). Spending is aggregated to match the budget period using `DATE_TRUNC`.

- **[Tax categorization accuracy]** -- Automated tax-relevant flagging is inherently imprecise and varies by jurisdiction. Mitigation: The butler flags potential deductibles but explicitly disclaims tax advice. Flagged transactions are suggestions for user review, not definitive classifications. The butler's scope discipline (from AGENTS.md) already prohibits tax filing advice.

## Dependencies

This change depends on **`finance-data-model-redesign`**, which must be merged and stable before implementation begins. That change provides:

- **`finance.transactions`** as the primary query target with typed columns (`amount NUMERIC(14,2)`, `merchant TEXT`, `category TEXT`, `posted_at TIMESTAMPTZ`) and B-tree indexes for range queries, aggregations, and window functions
- **`finance.merchant_mappings`** table for learned merchant-to-category lookup (replaces SPO facts with `predicate='merchant_category_mapping'`)
- **`finance.budgets`** table for category-level budget targets (replaces SPO property facts with `predicate='budget_target'`)
- **`finance.balance_snapshots`** table for net worth tracking (replaces temporal facts with `predicate='account_balance'`)
- **`finance.recurring_groups`** table for detected subscription patterns
- **`finance.import_batches`** table for import provenance tracking
- **`finance.categories`** table for hierarchical category taxonomy with tax-relevance flags
- **`finance.spending_summaries`** materialized view for pre-aggregated monthly spending
- **`finance.transaction_corrections`** table for edit audit trail
- Tiered deduplication via UNIQUE partial indexes on `finance.transactions`
- Soft-delete lifecycle (`deleted_at`) and optimistic locking (`version`) on `finance.transactions`

All intelligence tools in this change query `finance.transactions` and its supporting tables directly, using typed columns and B-tree indexes. No SPO fact queries are used for transactional data access.
