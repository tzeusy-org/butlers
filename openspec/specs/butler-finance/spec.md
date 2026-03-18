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
The finance butler runs bill checks, subscription alerts, and monthly summaries.

#### Scenario: Scheduled task inventory
- **WHEN** the finance butler daemon is running
- **THEN** it executes three native job schedules: `upcoming-bills-check` (0 8 * * *), `subscription-renewal-alerts` (30 8 * * *), and `monthly-spending-summary` (0 9 1 * *)

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
