# Finance Butler Role -- Delta for Intelligence Enhancements

## MODIFIED Requirements

### Requirement: Finance Butler Tool Surface
The finance butler SHALL provide transaction, subscription, bill tracking, and financial intelligence tools.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the finance butler
- **THEN** it SHALL have access to: `record_transaction`, `track_subscription`, `track_bill`, `list_transactions`, `spending_summary`, `upcoming_bills`, `bulk_record_transactions`, `import_transactions`, `update_transaction`, `delete_transaction`, `merge_duplicates`, `split_transaction`, `bulk_recategorize`, `anomaly_scan`, `detect_duplicates`, `detect_recurring`, `suggest_categories`, `learn_merchant_categories`, `recall_merchant_mappings`, `predict_bills`, `budget_set`, `budget_list`, `budget_remove`, `budget_status`, `spending_trends`, `spending_forecast`, `net_worth_snapshot`, `net_worth_history`, `cash_flow`, `subscription_audit`, `flag_tax_deductible`, `compute_baselines`, `alert_configure`, `alert_list`, `detect_price_changes`, and calendar tools

### Requirement: Finance Butler Schedules
The finance butler SHALL run bill checks, subscription alerts, monthly summaries, and intelligence-driven digests.

#### Scenario: Scheduled task inventory
- **WHEN** the finance butler daemon is running
- **THEN** it SHALL execute six native job schedules: `upcoming-bills-check` (15 21 * * 0), `subscription-renewal-alerts` (20 21 * * 0), `monthly-spending-summary` (0 9 1 * *), `anomaly-digest` (0 21 * * *), `budget-status-check` (0 9 * * 1), and `subscription-audit-monthly` (0 10 1 * *)

### Requirement: Finance Butler Skills
The finance butler SHALL have bill reminder, spending review, data import, and intelligence skills.

#### Scenario: Skill inventory
- **WHEN** the finance butler operates
- **THEN** it SHALL have access to `bill-reminder` (bill review, urgency triage, and payment reminder workflow), `spending-review` (spending analysis by category, time period, anomaly detection, and trend analysis), `transaction-csv-extraction` (adaptive LLM-driven CSV import via script generation), `historical-data-import` (multi-format bank CSV import with format detection, deduplication, and baseline computation), `budget-review` (interactive budget setting, status checking, and forecast review), `anomaly-triage` (interactive anomaly review and resolution workflow), plus shared skills `butler-memory` and `butler-notifications`

### Requirement: Finance Memory Taxonomy
The finance butler SHALL use a merchant-centric memory taxonomy with financial predicates including analytics-specific predicates.

#### Scenario: Memory classification
- **WHEN** the finance butler extracts facts
- **THEN** it SHALL use subjects like merchant names, service names, or "user"; predicates like `preferred_payment_method`, `spending_habit`, `subscription_status`, `price_change`, `merchant_category`, `spending_baseline`, `alert_config`, `anomaly_threshold`, `subscription_audit_date`; permanence `stable` for alert configs and institution relationships; `standard` for baselines, active subscriptions, and patterns; `volatile` for anomaly flags and one-time observations
- **AND** merchant category mappings SHALL be stored in `finance.merchant_mappings` (dedicated table), NOT as memory facts
- **AND** budget targets SHALL be stored in `finance.budgets` (dedicated table), NOT as memory facts
- **AND** account balance snapshots SHALL be stored in `finance.balance_snapshots` (dedicated table), NOT as memory facts

## ADDED Requirements

### Requirement: Finance Butler Intelligence Behavioral Guidelines
The finance butler runtime instances SHALL follow additional behavioral guidelines for intelligence features.

#### Scenario: Post-transaction intelligence hook
- **WHEN** a transaction is recorded via `record_transaction`
- **THEN** the runtime SHALL check if the transaction matches a potential untracked subscription (using `detect_recurring` patterns) and surface the observation
- **AND** if a `large_transaction` alert is configured and the amount exceeds the threshold, the runtime SHALL flag it in the response

#### Scenario: Proactive trend surfacing
- **WHEN** the user asks about spending in a category
- **THEN** the runtime SHOULD include trend context (comparison to prior month) alongside the direct answer
- **AND** if budget targets exist for that category, the runtime SHOULD include budget utilization

#### Scenario: Intelligence data sufficiency awareness
- **WHEN** intelligence tools return `status="insufficient_data"`
- **THEN** the runtime SHALL inform the user about the minimum data requirements
- **AND** it SHALL suggest importing historical data using the `historical-data-import` skill if no historical import has been performed
