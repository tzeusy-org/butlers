## Dependency

**This change depends on `finance-data-model-redesign` being merged first.** All tasks below assume the dedicated `finance.transactions` table is the primary query target, and that supporting tables (`finance.merchant_mappings`, `finance.budgets`, `finance.balance_snapshots`, `finance.recurring_groups`, `finance.import_batches`, `finance.categories`, `finance.spending_summaries`) exist. Do not begin implementation until `finance-data-model-redesign` is merged and stable.

## 1. Historical Data Import Pipeline

- [ ] 1.1 Implement bank export format detection in `roster/finance/tools/data_import.py` -- header pattern matching for Chase, Amex, Capital One, and generic CSV formats; return `detected_format` and column mapping
- [ ] 1.2 Implement date format auto-detection and normalization -- support `MM/DD/YYYY`, `YYYY-MM-DD`, `DD/MM/YYYY`, `M/D/YYYY`, `MM-DD-YYYY`; convert to `TIMESTAMPTZ`
- [ ] 1.3 Implement amount normalization -- handle negative-as-debit, separate debit/credit columns, currency symbols, comma-separated thousands; output absolute value with inferred direction
- [ ] 1.4 Implement merchant name normalization -- strip trailing transaction IDs, card numbers, date stamps, location codes; preserve raw description in `metadata.raw_description`
- [ ] 1.5 Implement `import_transactions(file_path, account_id, currency, column_map, dry_run)` tool function -- parse CSV, detect format, normalize data, deduplicate via tiered UNIQUE partial indexes on `finance.transactions`, create `finance.import_batches` row for provenance, call `bulk_record_transactions` in batches of 500
- [ ] 1.6 Implement dry run mode -- parse, validate, detect duplicates, return preview of first 10 transactions without inserting into `finance.transactions`
- [ ] 1.7 Implement progress reporting for large imports (1000+ rows) -- notify at 25%, 50%, 75%, 100% via `notify()`
- [ ] 1.8 Write tests for format detection, normalization, deduplication, batch processing, and dry run mode
- [ ] 1.9 Create `historical-data-import` skill in `roster/finance/.agents/skills/` with SKILL.md documenting the import workflow

## 2. Merchant Auto-Categorization

- [ ] 2.1 Implement `learn_merchant_categories()` in `roster/finance/tools/pattern_recognition.py` -- aggregate category assignments per merchant from `finance.transactions WHERE deleted_at IS NULL`, upsert mappings into `finance.merchant_mappings`
- [ ] 2.2 Implement `suggest_categories(transaction_ids)` -- look up merchants in `finance.merchant_mappings` using `ILIKE` pattern matching, return suggestions with confidence scores
- [ ] 2.2a Implement `recall_merchant_mappings(merchant_pattern?, category?)` tool -- query `finance.merchant_mappings WHERE is_active = true` with optional filters for LLM visibility into learned mappings
- [ ] 2.3 Implement category learning feedback loop -- when a transaction's category is updated via `update_transaction`, refresh the mapping in `finance.merchant_mappings` for that merchant
- [ ] 2.4 Write tests for category learning, suggestion, and confidence calculation

## 3. Statistical Baselines and Anomaly Detection

- [ ] 3.1 Implement `compute_baselines()` in `roster/finance/tools/anomaly_detection.py` -- compute per-merchant (median, stddev) and per-category (weekly velocity) baselines from 6-month rolling window over `finance.transactions WHERE deleted_at IS NULL`; store as memory facts with `predicate='spending_baseline'`
- [ ] 3.2 Implement `anomaly_scan(days_back, sensitivity)` -- compare transactions against baselines; flag amount anomalies, new merchants, and category velocity anomalies; return structured response with anomaly type, severity, and explanation
- [ ] 3.3 Implement `detect_duplicates(days_back)` -- find same-merchant, same-amount transactions on same or adjacent days; exclude tracked subscription charges; return with confidence level
- [ ] 3.4 Implement baseline refresh trigger after bulk imports (50+ transactions)
- [ ] 3.5 Implement graceful handling for insufficient data (return `status="insufficient_data"`)
- [ ] 3.6 Write tests for baseline computation, anomaly scoring, duplicate detection, and edge cases (no data, single transaction, all same merchant)

## 4. Recurring Charge Detection

- [ ] 4.1 Implement `detect_recurring(min_occurrences)` in `roster/finance/tools/pattern_recognition.py` -- query `finance.transactions WHERE deleted_at IS NULL` grouped by merchant, check for regular intervals and amount consistency (within 10% variance), store detected patterns in `finance.recurring_groups`
- [ ] 4.2 Implement confidence scoring for detected patterns -- high (6+ occurrences, <5% variance), medium (3+, <10%), low (otherwise)
- [ ] 4.3 Implement `already_tracked` flag by cross-referencing detected patterns against active subscription facts
- [ ] 4.4 Implement `price_change_detected` flag when detected amount differs from tracked subscription by >5%
- [ ] 4.5 Write tests for recurring detection with various frequencies (monthly, quarterly, yearly), edge cases (irregular intervals, high amount variance)

## 5. Bill Prediction

- [ ] 5.1 Implement `predict_bills(days_ahead)` in `roster/finance/tools/pattern_recognition.py` -- analyze `finance.transactions WHERE deleted_at IS NULL` for payees with 3+ regular payments, compute predicted next date from median interval
- [ ] 5.2 Implement `is_tracked` flag by cross-referencing predictions against existing bill facts
- [ ] 5.3 Implement `amount_drift` detection when predicted amount differs from tracked bill by >10%
- [ ] 5.4 Write tests for bill prediction accuracy, edge cases (irregular payments, ceased payees)

## 6. Budget Management

- [ ] 6.1 Implement `budget_set(category, amount, period, currency, warn_threshold, alert_threshold)` in `roster/finance/tools/budgets.py` -- upsert into `finance.budgets` table (deactivate existing row for same category+period, insert new active row)
- [ ] 6.2 Implement `budget_list()` -- return all rows from `finance.budgets WHERE is_active = true`
- [ ] 6.3 Implement `budget_remove(category, period)` -- deactivate the matching row in `finance.budgets` by setting `is_active = false`
- [ ] 6.4 Implement `budget_status()` -- join `finance.budgets WHERE is_active = true` against spending aggregated from `finance.transactions WHERE direction = 'debit' AND deleted_at IS NULL`, return per-category status (on_track/warning/exceeded) with utilization percentage and period alignment via `DATE_TRUNC`
- [ ] 6.5 Write tests for budget CRUD, status computation, period alignment, and threshold transitions

## 7. Spending Trends and Forecasting

- [ ] 7.1 Implement `spending_trends(comparison, months, category)` in `roster/finance/tools/budgets.py` -- month-over-month and year-over-year comparisons with percentage changes and direction indicators
- [ ] 7.2 Implement `spending_forecast()` -- linear projection for end-of-month spending, per-category forecasts, budget comparison, and first-of-month edge case (use prior month as basis)
- [ ] 7.3 Write tests for trend computation, forecast accuracy, insufficient data handling, and edge cases

## 8. Financial Overview Tools

- [ ] 8.1 Implement `net_worth_snapshot(account, institution, balance, currency, as_of_date)` in `roster/finance/tools/overview.py` -- upsert into `finance.balance_snapshots` using `(account_id, as_of_date)` unique constraint
- [ ] 8.2 Implement `net_worth_history(months)` -- query `finance.balance_snapshots` joined with `finance.accounts`, return per-month account balances with carried-forward logic for missing months, compute total_assets, total_liabilities, net_worth
- [ ] 8.3 Implement `cash_flow(period, months, breakdown)` -- aggregate from `finance.transactions WHERE deleted_at IS NULL` by direction (credits vs debits) by period, compute net and savings_rate, optional category breakdown
- [ ] 8.4 Implement `subscription_audit()` -- combine tracked subscriptions and detected recurring charges, compute annual cost projections, detect changes since last audit
- [ ] 8.5 Implement `flag_tax_deductible(year)` -- flag transactions in tax-relevant categories, return summary with disclaimer
- [ ] 8.6 Write tests for net worth tracking (including carry-forward), cash flow, subscription audit, and tax flagging

## 9. Alert System

- [ ] 9.1 Implement `alert_configure(type, threshold, currency, enabled)` in `roster/finance/tools/alerts.py` -- store as memory fact with `predicate='alert_config'`
- [ ] 9.2 Implement `alert_list()` -- return all active alert configurations
- [ ] 9.3 Implement `detect_price_changes(days_back)` -- compare recent charges for tracked subscription merchants against recorded amounts
- [ ] 9.4 Write tests for alert configuration, price change detection

## 10. Scheduled Tasks and Skills

- [ ] 10.1 Add `anomaly-digest` scheduled task to `butler.toml` -- cron `0 21 * * *`, call `anomaly_scan(days_back=1)`, notify via Telegram if anomalies found
- [ ] 10.2 Add `budget-status-check` scheduled task to `butler.toml` -- cron `0 9 * * 1`, call `budget_status()`, notify via Telegram if any category in warning/exceeded
- [ ] 10.3 Add `subscription-audit-monthly` scheduled task to `butler.toml` -- cron `0 10 1 * *`, call `subscription_audit()`, notify via Telegram with audit summary
- [ ] 10.4 Update `monthly-spending-summary` task prompt to include trend data, budget status, anomaly count, subscription audit summary, and net worth update
- [ ] 10.5 Update `subscription-renewal-alerts` task prompt to include price change detection from `detect_price_changes()`
- [ ] 10.6 Update `upcoming-bills-check` task prompt to include `predict_bills()` for historical pattern-based predictions
- [ ] 10.7 Create `budget-review` skill in `roster/finance/.agents/skills/` -- interactive budget setting, status, and forecast review
- [ ] 10.8 Create `anomaly-triage` skill in `roster/finance/.agents/skills/` -- interactive anomaly review and resolution

## 11. Tool Registration and Module Wiring

- [ ] 11.1 Register all new tool functions (including `recall_merchant_mappings`) in the finance module's `register_tools()` method
- [ ] 11.2 Update `roster/finance/tools/__init__.py` to export all new tool functions (including `recall_merchant_mappings`)
- [ ] 11.3 Update `roster/finance/AGENTS.md` with new tool descriptions, behavioral guidelines for intelligence features, and updated skill inventory

## 12. Validation

- [ ] 12.1 Run full lint pass: `uv run ruff check src/ tests/ roster/ --output-format concise`
- [ ] 12.2 Run full test suite: `uv run pytest tests/ -q --tb=short`
- [ ] 12.3 Verify all scheduled task prompts include explicit `notify()` instructions per the scheduled task output contract
- [ ] 12.4 Verify all intelligence tools return `status="insufficient_data"` gracefully when no historical data is present
- [ ] 12.5 End-to-end test: import a sample CSV, verify baselines are computed, run anomaly scan, check budget status, verify subscription audit
