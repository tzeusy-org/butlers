## Why

The Finance butler currently records transactions, tracks subscriptions/bills, and produces basic spending summaries -- but it has no analytical intelligence. It cannot detect anomalies, identify recurring charges from patterns, auto-categorize merchants, forecast spending, or provide trend analysis. The user has full historical transaction data exports available for import, which means the butler can work with years of rich data to establish baselines and detect patterns. Without intelligence features, the butler is a ledger; with them, it becomes a financial advisor that surfaces insights proactively.

## What Changes

- **Anomaly detection engine**: Flag transactions that deviate from established patterns (unusual merchant, amount, time-of-day, frequency). Configurable sensitivity thresholds stored in memory.
- **Recurring charge auto-detection**: Analyze transaction history to identify subscription-like patterns (same merchant, similar amount, regular intervals) and offer to create subscription records automatically.
- **Merchant auto-categorization**: Maintain a learned merchant-to-category mapping from historical data, apply it to new transactions that arrive without a category, and allow user corrections that feed back into the mapping.
- **Duplicate transaction detection**: Flag potential duplicate charges (same merchant, same amount, same day or adjacent days) for user review.
- **Budget enforcement**: Category-level budget targets with proactive alerts when spending approaches or exceeds limits. Configurable thresholds (e.g., warn at 80%, alert at 100%).
- **Spending trend analysis**: Month-over-month and year-over-year comparisons with percentage changes and trend direction indicators.
- **Spending forecasting**: Based on current-month trajectory and historical patterns, predict end-of-month spending totals per category.
- **Net worth tracking**: Manual entry of account balances with historical tracking over time.
- **Cash flow analysis**: Income vs. expenses over configurable periods with surplus/deficit calculation.
- **Subscription audit**: Aggregate all detected and tracked recurring charges with annual cost projection.
- **Bill prediction**: Predict upcoming bills from historical payment patterns (payee, typical amount, typical due date).
- **Historical data import pipeline**: Bulk CSV/export import with format normalization across different bank export formats, deduplication on import, and retroactive analytics to establish baselines.
- **Configurable alerts**: Large transaction alerts (threshold-based), subscription price change detection, upcoming bill reminders from historical due dates, and automated monthly spending summaries.
- **Tax-relevant categorization**: Flag and tag transactions as potentially tax-deductible based on category and merchant patterns.

## Capabilities

### New Capabilities
- `finance-anomaly-detection`: Transaction anomaly detection engine -- flags unusual merchants, amounts, times, and frequencies against established baselines. Includes duplicate charge detection.
- `finance-pattern-recognition`: Recurring charge auto-detection from transaction history, merchant auto-categorization with learned mappings, and bill prediction from historical payment patterns.
- `finance-budgets`: Category-level budget targets with threshold-based proactive alerts, spending trend analysis (MoM/YoY), and end-of-month spending forecasting.
- `finance-overview`: Net worth tracking (manual balance entries over time), cash flow analysis (income vs. expenses), subscription audit with annual cost projection, and tax-relevant expense flagging.
- `finance-data-import`: Historical data import pipeline -- multi-format CSV normalization, deduplication on import, and retroactive baseline analytics. Extends the existing `transaction-csv-extraction` skill with format detection and bulk processing.
- `finance-alerts`: Configurable alert system -- large transaction alerts, subscription price change detection, bill reminders from historical patterns, and automated periodic spending summaries.

### Modified Capabilities
- `butler-finance`: Add new tool surface entries for analytics tools (anomaly scan, trend analysis, budget management, forecast, subscription audit, net worth, cash flow). Update scheduled task inventory with new alert-driven schedules. Add `recall_merchant_mappings` tool for LLM visibility into `finance.merchant_mappings`. Extend memory taxonomy with analytics-specific predicates (spending_baseline, anomaly_threshold, alert_config).

## Impact

- **Database**: Uses dedicated tables provided by `finance-data-model-redesign`: `finance.budgets` (category, amount, period, threshold), `finance.balance_snapshots` (account, balance, as_of_date), `finance.merchant_mappings` (merchant pattern, category, confidence, source), `finance.recurring_groups` (detected subscription patterns). Alert configurations stored as memory facts. All in the `finance` schema.
- **Tools**: 8-12 new MCP tools for analytics queries (anomaly_scan, detect_recurring, spending_trends, spending_forecast, budget_set, budget_status, net_worth_snapshot, net_worth_history, cash_flow, subscription_audit, flag_tax_deductible). Existing `record_transaction` and `bulk_record_transactions` gain post-insert hooks for anomaly checking and auto-categorization.
- **Skills**: New skills for interactive budget review, anomaly triage, and historical data import workflows. Update existing `spending-review` skill with trend and forecast capabilities.
- **Scheduled tasks**: New schedules for anomaly digest (daily), budget status check (weekly), and subscription audit (monthly). Existing `monthly-spending-summary` enhanced with trend data.
- **Memory**: New predicates for `spending_baseline`, `anomaly_threshold`, `alert_config`, `subscription_audit_date`. Permanence: `stable` for alert configs, `standard` for baselines, `volatile` for anomaly flags. Note: merchant category mappings, budget targets, and balance snapshots are stored in dedicated tables (`finance.merchant_mappings`, `finance.budgets`, `finance.balance_snapshots`) per the `finance-data-model-redesign` dependency, not as memory facts.
- **Import pipeline**: Extends the existing `transaction-csv-extraction` skill with multi-format bank export detection (Chase, Amex, Capital One, generic CSV), header mapping, and configurable date/amount format parsing. The user has full historical exports available, so the pipeline must handle large batch imports (thousands of rows) efficiently.
- **Dependencies**: Depends on `finance-data-model-redesign` (must be merged first). No new external dependencies. All analytics are SQL-driven aggregations over `finance.transactions` and its supporting tables. Merchant categorization uses `finance.merchant_mappings`, not an external API.
