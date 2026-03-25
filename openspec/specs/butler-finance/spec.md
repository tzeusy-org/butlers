# Finance Butler Role

## Purpose
The Finance butler (port 41105) is a personal finance specialist for receipts, bills, subscriptions, and transaction alerts.

## ADDED Requirements

### Requirement: Finance Butler Identity and Runtime
The finance butler handles personal finance tracking with precise numeric types and currency handling.

#### Scenario: Identity and port
- **WHEN** the finance butler is running
- **THEN** it operates on port 41105 with description "Personal finance specialist for receipts, bills, subscriptions, and transaction alerts."
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema is `finance` within the consolidated `butlers` database

#### Scenario: Switchboard registration
- **WHEN** the finance butler starts
- **THEN** it registers with the switchboard at `http://localhost:41100/mcp` with `advertise = true`, `liveness_ttl_s = 300`, and route contract version range `route.v1` to `route.v1`

#### Scenario: Module profile
- **WHEN** the finance butler starts
- **THEN** it loads modules: `email`, `calendar` (Google provider, suggest conflicts policy), and `memory`

### Requirement: Finance Butler Tool Surface
The finance butler provides transaction, subscription, and bill tracking tools.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the finance butler
- **THEN** it has access to: `record_transaction`, `track_subscription`, `track_bill`, `list_transactions`, `spending_summary`, `upcoming_bills`, `bulk_record_transactions`, and calendar tools

### Requirement: Finance Data Conventions
Financial data uses precise numeric types and ISO currency codes.

#### Scenario: Data type conventions
- **WHEN** financial data is recorded
- **THEN** amounts use `NUMERIC(14,2)` (never float), currency uses ISO-4217 uppercase codes (e.g., `USD`, `EUR`), timestamps use `TIMESTAMPTZ` preserving timezone, and direction is inferred as `debit` or `credit` from context

#### Scenario: Composite deduplication for non-email sources
- **WHEN** a transaction is recorded without a `source_message_id` (e.g., from CSV import)
- **THEN** deduplication MUST use a composite idempotency key computed as `sha256(posted_at|amount|merchant|account_id)` with canonicalized inputs (UTC ISO 8601 at second precision, quantized decimal amount, case-sensitive merchant, lowercased account_id or empty string)
- **AND** this composite key MUST be passed as the `idempotency_key` parameter to the fact storage layer
- **AND** the existing `source_message_id`-based deduplication MUST remain the primary strategy when `source_message_id` is present

### Requirement: Finance Butler Schedules
The finance butler runs bill checks, subscription alerts, monthly summaries, and insight scans.

#### Scenario: Scheduled task inventory
- **WHEN** the finance butler daemon is running
- **THEN** it executes four scheduled tasks: `upcoming-bills-check` (0 8 * * *), `subscription-renewal-alerts` (30 8 * * *), `monthly-spending-summary` (0 9 1 * *), and `insight-scan` (0 7 30 * * *, job: evaluate financial domain data and generate insight candidates)

### Requirement: Finance Butler Skills
The finance butler has bill reminder and spending review skills.

#### Scenario: Skill inventory
- **WHEN** the finance butler operates
- **THEN** it has access to `bill-reminder` (bill review, urgency triage, and payment reminder workflow), `spending-review` (spending analysis by category, time period, and anomaly detection), and `transaction-csv-extraction` (adaptive LLM-driven CSV import via script generation), plus shared skills `butler-memory` and `butler-notifications`

### Requirement: Finance Memory Taxonomy
The finance butler uses a merchant-centric memory taxonomy with financial predicates.

#### Scenario: Memory classification
- **WHEN** the finance butler extracts facts
- **THEN** it uses subjects like merchant names, service names, or "user"; predicates like `preferred_payment_method`, `spending_habit`, `subscription_status`, `price_change`, `merchant_category`; permanence `stable` for recurring obligations and institution relationships, `standard` for active subscriptions and patterns, `volatile` for one-time transactions

### Requirement: CRUD-to-SPO migration — finance domain (bu-ddb.4)
The finance butler migrates 4 dedicated CRUD tables (transactions, accounts, subscriptions, bills) to temporal SPO facts. All facts use `scope='finance'` and `entity_id = owner_entity_id`. Full predicate taxonomy and metadata schemas are in `openspec/changes/crud-to-spo-migration/specs/predicate-taxonomy.md`.

#### Scenario: Transaction tools as temporal fact wrappers with deduplication
- **WHEN** `record_transaction` is called
- **THEN** it MUST first check for an existing active fact matching `(entity_id, predicate IN ('transaction_debit','transaction_credit'), scope='finance', metadata->>'source_message_id', metadata->>'merchant', metadata->>'amount', valid_at=posted_at)`
- **AND** if a matching fact exists, the insert MUST be skipped and the existing fact ID returned (idempotent dedup)
- **AND** if no match, it MUST call `store_fact` with `predicate='transaction_{direction}'`, `valid_at=posted_at`, `entity_id=owner_entity_id`, `scope='finance'`, and metadata containing all transaction fields
- **AND** `amount` in metadata MUST be stored as a string (e.g. `"47.32"`) to preserve `NUMERIC(14,2)` precision
- **AND** `list_transactions` MUST query facts with `predicate IN ('transaction_debit','transaction_credit')` ordered by `valid_at DESC`

#### Scenario: spending_summary as JSONB aggregation on facts
- **WHEN** `spending_summary` is called for a date range
- **THEN** it MUST aggregate `(metadata->>'amount')::NUMERIC` across facts with `predicate IN ('transaction_debit','transaction_credit')` and `valid_at BETWEEN start_date AND end_date`
- **AND** grouping by `metadata->>'category'` MUST produce the per-category breakdown
- **AND** the response shape MUST be identical to the original CRUD-table implementation

#### Scenario: Account tools as property fact wrappers
- **WHEN** `track_subscription` or account tools are called
- **THEN** account facts MUST use `predicate='account'`, `valid_at=NULL`, and `content="{institution} {type} ****{last_four}"` as the stable supersession differentiator
- **AND** multiple distinct accounts (different content values) MUST coexist as active property facts

#### Scenario: Subscription and bill tools as property fact wrappers
- **WHEN** `track_subscription` is called
- **THEN** it MUST call `store_fact` with `predicate='subscription'`, `valid_at=NULL`, and metadata containing `{service, amount, currency, frequency, next_renewal, status, auto_renew, payment_method, account_id, source_message_id}`
- **AND** when `track_bill` is called
- **THEN** it MUST call `store_fact` with `predicate='bill'`, `valid_at=NULL`, and metadata containing `{payee, amount, currency, due_date, frequency, status, payment_method, account_id, paid_at, source_message_id}`
- **AND** `upcoming_bills` MUST query active facts with `predicate='bill'` filtering by `(metadata->>'due_date')::DATE <= NOW() + INTERVAL '7 days'`

### Requirement: Finance Insight Scan Job
The finance butler's `insight-scan` job SHALL evaluate financial domain data and produce insight candidates covering spending anomalies, upcoming bills, budget threshold warnings, and subscription renewal alerts. All candidates are submitted via the Switchboard's `propose_insight_candidate()` MCP tool — the butler does not write to `shared.insight_candidates` directly.

#### Scenario: Insight-scan job handler registration
- **WHEN** the finance butler starts
- **THEN** it SHALL register an `insight-scan` job handler that is invokable by the scheduler's `job` dispatch mode

#### Scenario: Candidate submission via Switchboard MCP
- **WHEN** the `insight-scan` job generates a candidate
- **THEN** it SHALL submit the candidate by calling the Switchboard's `propose_insight_candidate()` MCP tool
- **AND** if the tool returns `{"status": "filtered"}`, the butler SHALL skip remaining candidates (verbosity is off)
- **AND** if the tool returns `{"status": "error"}`, the butler SHALL log the error and continue with remaining candidates

#### Scenario: Spending anomaly insights
- **WHEN** the insight-scan job evaluates spending patterns
- **THEN** it SHALL generate candidates when a spending category's current-month total exceeds the 3-month rolling average by more than 30%
- **AND** categories exceeding the average by more than 100% SHALL have priority 80
- **AND** categories exceeding the average by 50-100% SHALL have priority 65
- **AND** categories exceeding the average by 30-50% SHALL have priority 50
- **AND** the `dedup_key` SHALL be `finance:spending-anomaly:{category}:{year-month}`
- **AND** `expires_at` SHALL be the end of the current calendar month
- **AND** categories with fewer than 3 months of history SHALL be excluded
- **AND** the message SHALL include the category, current amount, average amount, and percentage above average

#### Scenario: Upcoming bill insights
- **WHEN** the insight-scan job evaluates tracked bills
- **THEN** it SHALL generate candidates for bills due within 3 days that have not been marked as paid
- **AND** bills due within 1 day SHALL have priority 92 (time-critical)
- **AND** bills due within 3 days SHALL have priority 75
- **AND** the `dedup_key` SHALL be `finance:bill-due:{bill-id}:{due-date}`
- **AND** `expires_at` SHALL be the bill's due date
- **AND** `cooldown_days` SHALL be 1

#### Scenario: Budget threshold insights
- **WHEN** the insight-scan job evaluates monthly spending against user-set budgets (if any)
- **THEN** it SHALL generate candidates when total spending reaches 80% of a budget target
- **AND** spending at 90%+ of budget SHALL have priority 70
- **AND** spending at 80-90% of budget SHALL have priority 50
- **AND** the `dedup_key` SHALL be `finance:budget-threshold:{budget-name}:{year-month}`
- **AND** `expires_at` SHALL be the end of the current calendar month

#### Scenario: Subscription renewal insights
- **WHEN** the insight-scan job evaluates tracked subscriptions
- **THEN** it SHALL generate candidates for annual subscriptions renewing within 14 days
- **AND** renewal within 3 days SHALL have priority 75
- **AND** renewal within 14 days SHALL have priority 55
- **AND** the `dedup_key` SHALL be `finance:subscription-renewal:{subscription-id}:{renewal-date}`
- **AND** `expires_at` SHALL be the renewal date
- **AND** monthly subscriptions SHALL NOT generate insight candidates (too frequent — the existing `subscription-renewal-alerts` schedule handles these)
