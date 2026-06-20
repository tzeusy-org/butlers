# Finance Butler Role

## Purpose
The Finance butler tracks transactions, subscriptions, and bills as primary
storage in dedicated `finance.*` tables (with a fire-and-forget SPO fact mirror
for memory/recall), runs scheduled bill/subscription/anomaly digests, and
provides deterministic financial intelligence — including bill↔payment
reconciliation that settles paid bills automatically without manual entry.
## Requirements
### Requirement: Finance Butler Tool Surface
The finance butler SHALL provide transaction, subscription, bill tracking, bill↔payment reconciliation, and financial intelligence tools with primary storage in dedicated `finance.*` tables.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the finance butler
- **THEN** it SHALL have access to: `record_transaction`, `track_subscription`, `track_bill`, `reconcile_bills`, `list_transactions`, `spending_summary`, `upcoming_bills`, `bulk_record_transactions`, `import_transactions`, `update_transaction`, `delete_transaction`, `merge_duplicates`, `split_transaction`, `bulk_recategorize`, `anomaly_scan`, `detect_duplicates`, `detect_recurring`, `suggest_categories`, `learn_merchant_categories`, `recall_merchant_mappings`, `predict_bills`, `budget_set`, `budget_list`, `budget_remove`, `budget_status`, `spending_trends`, `spending_forecast`, `net_worth_snapshot`, `net_worth_history`, `cash_flow`, `subscription_audit`, `flag_tax_deductible`, `compute_baselines`, `alert_configure`, `alert_list`, `detect_price_changes`, and calendar tools
- **AND** the finance butler SHALL NOT expose a standalone `track_bill_fact` tool; all bill writes (table + SPO mirror) go through `track_bill`

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
Financial data SHALL use precise numeric types, ISO currency codes, and tiered deduplication on the dedicated transaction table.

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

### Requirement: CRUD-to-SPO migration -- finance domain (bu-ddb.4)
The finance butler SHALL store transactions and bills in dedicated `finance.*` tables as primary storage, with SPO facts as a secondary fire-and-forget mirror for memory/recall.

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

#### Scenario: Bill tools as dedicated table primary with SPO mirror
- **WHEN** `track_bill` is called
- **THEN** the bill SHALL be upserted into `finance.bills` (match on `(payee, due_date)`) as primary storage, including the `reconciled_transaction_id` column
- **AND** it SHALL fire a fire-and-forget mirror to `public.facts` with `predicate='bill'`, `valid_at=NULL`, and metadata containing `{payee, amount, currency, due_date, frequency, status, payment_method, account_id, paid_at, reconciled_transaction_id, source_message_id}`
- **AND** the SPO mirror write SHALL not roll back the primary upsert on failure
- **AND** `upcoming_bills` SHALL query `finance.bills WHERE status IN ('pending','overdue')` filtered by the due-date horizon

#### Scenario: Subscription tools as property fact wrappers
- **WHEN** `track_subscription` is called
- **THEN** it SHALL call `store_fact` with `predicate='subscription'`, `valid_at=NULL`, and metadata containing `{service, amount, currency, frequency, next_renewal, status, auto_renew, payment_method, account_id, source_message_id}`

### Requirement: Deterministic bill↔payment reconciliation

The finance butler SHALL provide a deterministic reconciliation primitive that
matches recorded debit transactions to pending or overdue bills and settles
high-confidence matches automatically. Matching logic SHALL be implemented in
tool/daemon code (pure SQL/Python), not delegated to LLM judgment.

#### Scenario: Reconcile tool surfaces auto-settled and ambiguous matches
- **WHEN** `reconcile_bills` is called
- **THEN** it SHALL scan `finance.bills` rows with `status IN ('pending','overdue')`
  and `reconciled_transaction_id IS NULL` against recent `debit` rows in
  `finance.transactions`
- **AND** it SHALL auto-settle every high-confidence match and return them under
  `auto_settled`
- **AND** it SHALL return ambiguous matches under `candidates` without mutating
  those bills
- **AND** the operation SHALL be idempotent: a transaction already linked via a
  bill's `reconciled_transaction_id` is skipped, and a bill with `status='paid'`
  is never re-settled

#### Scenario: High-confidence match is auto-settled with provenance
- **WHEN** a debit transaction is the **sole** candidate for a bill, the payee
  matches the merchant exactly (after normalization), the payment falls within
  the bill's due-date window, AND either the transaction amount matches the bill
  amount within tolerance OR the bill amount is the `$0.00` placeholder
- **THEN** the bill SHALL be updated to `status='paid'`
- **AND** a `$0.00` placeholder amount SHALL be backfilled from the transaction
  amount
- **AND** `paid_at` SHALL be set from the transaction's `posted_at`, and
  `payment_method` SHALL be filled from the transaction when the bill lacks one
- **AND** the bill's `reconciled_transaction_id` SHALL be set to the transaction
  id and its metadata SHALL record `reconciliation: "auto"` with a timestamp

#### Scenario: Ambiguous match is surfaced, never auto-applied
- **WHEN** a debit transaction has more than one candidate bill, OR the payee
  matches only fuzzily, OR an explicit bill amount is outside tolerance of the
  transaction amount
- **THEN** the match SHALL be returned as a `confirm`-tier candidate
- **AND** no bill SHALL be mutated until a subsequent explicit settlement action

#### Scenario: Credits never settle bills
- **WHEN** the transaction is a `credit` (e.g. refund, incoming transfer)
- **THEN** it SHALL NOT be considered a candidate to settle any bill

#### Scenario: Multiple same-payee bills in window are never auto-settled
- **WHEN** more than one unsettled bill for the same payee falls within the
  date window of a debit transaction
- **THEN** the match SHALL be `confirm`-tier and no bill SHALL be auto-settled
- **AND** when exactly one same-payee bill is in-window among several, the
  in-window bill SHALL be selected by closest anchor date (`statement_period_end`
  if set, else `due_date`) to the transaction's `posted_at`

### Requirement: Settlement on payment via record_transaction

When a debit transaction is recorded, the finance butler SHALL attempt
deterministic reconciliation for that transaction and report the outcome in the
tool response, so the recording session can settle or confirm without depending
on cross-session memory.

#### Scenario: record_transaction returns reconciliation outcome
- **WHEN** `record_transaction` records a `debit`
- **THEN** its response SHALL include a `bill_reconciliation` field
- **AND** when a high-confidence match exists, the matching bill SHALL be
  auto-settled and reported under `bill_reconciliation.auto_settled`
- **AND** when only ambiguous matches exist, they SHALL be reported under
  `bill_reconciliation.candidates` with no bill mutated
- **AND** when no candidate exists, `bill_reconciliation` SHALL be empty/absent
- **AND** the reconciliation check SHALL run as deterministic in-process logic
  with no LLM involvement

### Requirement: Scheduled reconciliation sweep

The finance butler SHALL periodically reconcile stale pending and overdue bills
against recent transactions as a backstop for payments recorded without an
inline match.

#### Scenario: upcoming-bills-check reconciles before reporting
- **WHEN** the `upcoming-bills-check` scheduled task runs
- **THEN** it SHALL call `reconcile_bills` before composing its digest
- **AND** auto-settled bills SHALL be reported in the digest
- **AND** ambiguous candidates and still-unpaid past-due bills SHALL be surfaced
  to the owner via `notify()`

#### Scenario: Payment recorded before its bill is reconciled by the sweep
- **WHEN** a debit transaction was recorded before any matching bill existed
- **AND** a matching bill is later created (e.g. from a statement email)
- **THEN** `reconcile_bills` SHALL match the bill against the already-recorded
  transaction by scanning bill→transaction over the lookback horizon
- **AND** a high-confidence match SHALL be auto-settled on that sweep

### Requirement: Settlement-state integrity in runtime behavior

The finance butler runtime SHALL NOT record bill settlement as free-text
metadata without the corresponding structured status change.

#### Scenario: Payment evidence updates structured status, not just prose
- **WHEN** the runtime determines from any signal that a tracked bill has been
  paid
- **THEN** it SHALL settle the bill through the structured path (auto-settle via
  reconciliation, or `track_bill(status="paid", ...)`)
- **AND** it SHALL NOT leave the bill `status='pending'` while writing a "paid"
  note into `metadata`

