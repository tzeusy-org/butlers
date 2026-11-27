# Bulk Transaction Ingestion

## Purpose
Bulk import MCP tool for recording multiple normalized transactions in a single call, with composite deduplication for sources that lack email provenance.

## Requirements

### Requirement: Bulk transaction ingestion
The Finance daemon SHALL expose bulk ingestion through `bulk_record_transactions`
and stored statement ingestion through `import_transactions`. The retired dashboard
`POST /api/finance/transactions/bulk` route SHALL NOT be exposed.

#### Scenario: Successful bulk import
- **WHEN** `bulk_record_transactions` receives 1–500 normalized transaction objects
- **THEN** each transaction MUST be recorded in `finance.transactions` through the canonical writer
- **AND** the response MUST include `total`, `imported`, `skipped`, and `errors`, counting only fresh rows as imported

#### Scenario: Composite deduplication for CSV-sourced rows
- **WHEN** a transaction in the bulk request has no `source_message_id`
- **THEN** deduplication MUST use the `record_transaction` composite key `(account_id, posted_at, amount, merchant)`
- **AND** if a matching transaction already exists (from an earlier request or earlier in the same batch), the row MUST be counted as `skipped` (not `imported` or `errors`)
- **AND** no duplicate transaction row MUST be created

#### Scenario: Source message ID deduplication preserved
- **WHEN** a transaction in the bulk request includes a `source_message_id`
- **THEN** deduplication MUST use the existing `source_message_id`-based logic from `record_transaction`
- **AND** the composite key MUST NOT be used as a fallback

#### Scenario: Per-row error reporting
- **WHEN** a transaction in the bulk request has invalid data (e.g., unparseable date, missing required field)
- **THEN** that row MUST be counted in `errors` and included in `error_details` with its array `index` and `reason`
- **AND** valid rows in the same batch MUST still be processed (no all-or-nothing rollback), including when a row fails with a per-row error such as an unresolvable `account_id` or invalid `direction`

#### Scenario: Batch size limit
- **WHEN** a bulk request contains more than 500 transactions
- **THEN** the tool MUST raise a descriptive validation error
- **AND** no transactions MUST be persisted

#### Scenario: Account ID association
- **WHEN** the bulk request includes a top-level `account_id` field
- **THEN** all transactions in the batch MUST inherit that `account_id` unless individually overridden
- **AND** the `account_id` MUST be part of the composite dedup key

#### Scenario: Source tagging
- **WHEN** the bulk request includes a top-level `source` field (e.g., `"csv-import"`)
- **THEN** each recorded transaction MUST include `"import_source": <value>` in its metadata JSONB
- **AND** this field MUST be visible in the transaction's `metadata` returned by `GET /transactions`

### Requirement: Bulk record transactions MCP tool
The finance butler MUST expose a `bulk_record_transactions` MCP tool that wraps the same bulk ingestion logic for direct use by butler runtime sessions.

#### Scenario: MCP tool invocation
- **WHEN** `bulk_record_transactions` is called with a `transactions` array of normalized objects
- **THEN** it MUST process them identically to the canonical ledger writer (same deduplication and per-row error handling)
- **AND** the tool MUST accept optional `account_id` and `source` parameters for the batch

#### Scenario: MCP tool batch limit
- **WHEN** `bulk_record_transactions` is called with more than 500 transactions
- **THEN** it MUST return an error result (not silently truncate)

### Requirement: Normalized transaction object schema
All normalized bulk ingestion calls MUST accept the same normalized transaction schema.

#### Scenario: Required and optional fields
- **WHEN** a normalized transaction object is submitted
- **THEN** `posted_at` (ISO 8601 datetime string), `merchant` (string), and `amount` (string-encoded decimal) MUST be required
- **AND** `currency` MUST default to `"USD"` when absent
- **AND** `category`, `description`, `payment_method`, `source_message_id`, and `metadata` MUST be optional
- **AND** `amount` sign convention MUST match `record_transaction`: negative = debit, positive = credit

#### Scenario: Amount precision
- **WHEN** an amount value is provided in a normalized transaction
- **THEN** it MUST be stored as a string-encoded `NUMERIC(14,2)` value
- **AND** floating-point amounts MUST be quantized to 2 decimal places before storage
- **AND** no floating-point intermediate representation MUST be used in persistence

### Requirement: Bulk ingestion embedding strategy
Transactions are structured ledger rows in `finance.transactions`; no embedding is computed or stored for them. Bulk ingestion MUST NOT introduce a per-row embedding step.

#### Scenario: Bulk ingestion performance target
- **WHEN** a batch of 500 transactions is submitted to `bulk_record_transactions`
- **THEN** the tool MUST complete within 10 seconds under normal conditions
- **AND** the response MUST NOT time out for batches up to the 500-row limit

### Requirement: Partial failure and re-run semantics
Re-running an import after a partial failure (e.g., daemon stopped mid-import) MUST produce a clear, non-confusing result.

#### Scenario: Re-run after partial failure
- **WHEN** a CSV import script is re-run after a previous partial failure
- **THEN** previously imported rows MUST be deduped as `skipped`
- **AND** the response `error_details` MUST distinguish dedup skips from validation errors by including a `reason` field (e.g., `"duplicate"` vs `"invalid_date"`)
- **AND** the skill MUST instruct the LLM to explain to the user that skipped rows are previously imported duplicates, not data loss
