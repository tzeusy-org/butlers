# Finance Butler Role -- Delta for Intelligence Enhancements and Data Model Redesign

## MODIFIED Requirements

### Requirement: Finance Butler Tool Surface
The finance butler SHALL provide transaction, subscription, bill tracking, and financial intelligence tools with primary storage in the dedicated `finance.transactions` table.

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

### Requirement: Finance Data Conventions
Financial data uses precise numeric types, ISO currency codes, and tiered deduplication on the dedicated transaction table.

#### Scenario: Data type conventions
- **WHEN** financial data is recorded
- **THEN** amounts use `NUMERIC(14,2)` (never float), currency uses ISO-4217 uppercase codes (e.g., `USD`, `EUR`), timestamps use `TIMESTAMPTZ` preserving timezone, and direction is inferred as `debit` or `credit` from context

#### Scenario: Composite deduplication for non-email sources
- **WHEN** a transaction is recorded without a `source_message_id` and without an `external_id`
- **THEN** deduplication SHALL use the tiered UNIQUE partial index strategy on `finance.transactions`: Priority 1 `(account_id, external_id)`, Priority 2 `(source_message_id, merchant, amount, posted_at)`, Priority 3 `(account_id, posted_at, amount, merchant)` as fallback
- **AND** the `sha256` composite hash approach SHALL no longer be used
- **AND** the existing `source_message_id`-based deduplication SHALL remain as Priority 2

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

### Requirement: CRUD-to-SPO migration -- finance domain
The finance butler migrates transaction tools to use the dedicated `finance.transactions` table as primary storage, with SPO facts as a secondary mirror for memory/recall.

#### Scenario: Transaction tools as dedicated table wrappers with SPO mirroring
- **WHEN** `record_transaction` is called
- **THEN** it SHALL first check for an existing duplicate using the tiered dedup key hierarchy on `finance.transactions`
- **AND** if a duplicate is found, the existing transaction ID SHALL be returned (idempotent dedup)
- **AND** if no duplicate is found, it SHALL INSERT into `finance.transactions` with all applicable columns
- **AND** it SHALL fire a background task to mirror the write to `public.facts` with `predicate='transaction_{direction}'`, `valid_at=posted_at`, `entity_id=owner_entity_id`, `scope='finance'`, and metadata containing all transaction fields
- **AND** the SPO mirror write SHALL be fire-and-forget (failure does not roll back the primary insert)
- **AND** `list_transactions` SHALL query `finance.transactions WHERE deleted_at IS NULL` ordered by `posted_at DESC`

#### Scenario: spending_summary as dedicated table aggregation
- **WHEN** `spending_summary` is called for a date range
- **THEN** it SHALL aggregate `amount` (typed `NUMERIC(14,2)`) from `finance.transactions` where `direction = 'debit' AND deleted_at IS NULL AND posted_at BETWEEN start_date AND end_date`
- **AND** grouping by `category` SHALL use the typed column directly (no JSONB extraction)
- **AND** the response shape SHALL be identical to the original implementation

#### Scenario: Account tools as property fact wrappers
- **WHEN** `track_subscription` or account tools are called
- **THEN** account facts SHALL use `predicate='account'`, `valid_at=NULL`, and `content="{institution} {type} ****{last_four}"` as the stable supersession differentiator
- **AND** multiple distinct accounts (different content values) SHALL coexist as active property facts

#### Scenario: Subscription and bill tools as property fact wrappers
- **WHEN** `track_subscription` is called
- **THEN** it SHALL call `store_fact` with `predicate='subscription'`, `valid_at=NULL`, and metadata containing `{service, amount, currency, frequency, next_renewal, status, auto_renew, payment_method, account_id, source_message_id}`
- **AND** when `track_bill` is called
- **THEN** it SHALL call `store_fact` with `predicate='bill'`, `valid_at=NULL`, and metadata containing `{payee, amount, currency, due_date, frequency, status, payment_method, account_id, paid_at, source_message_id}`
- **AND** `upcoming_bills` SHALL query active facts with `predicate='bill'` filtering by `(metadata->>'due_date')::DATE <= NOW() + INTERVAL '7 days'`
