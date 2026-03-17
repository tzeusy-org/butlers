# Finance Butler (delta)

## Purpose
Extends the finance butler's tool surface and deduplication strategy to support bulk transaction ingestion from non-email sources.

## MODIFIED Requirements

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
- **THEN** deduplication MUST use a composite idempotency key computed as `sha256(posted_at|amount|merchant|account_id)`
- **AND** this composite key MUST be passed as the `idempotency_key` parameter to the fact storage layer
- **AND** the existing `source_message_id`-based deduplication MUST remain the primary strategy when `source_message_id` is present

### Requirement: Finance Butler Skills
The finance butler has bill reminder, spending review, and CSV extraction skills.

#### Scenario: Skill inventory
- **WHEN** the finance butler operates
- **THEN** it has access to `bill-reminder` (bill review, urgency triage, and payment reminder workflow), `spending-review` (spending analysis by category, time period, and anomaly detection), and `transaction-csv-extraction` (adaptive LLM-driven CSV import via script generation), plus shared skills `butler-memory` and `butler-notifications`
