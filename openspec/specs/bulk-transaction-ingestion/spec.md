# Bulk Transaction Ingestion

## Purpose
Bulk import endpoint and MCP tool for recording multiple normalized transactions in a single call, with composite deduplication for sources that lack email provenance.

## Requirements

### Requirement: Bulk transaction ingestion HTTP endpoint
The dashboard API SHALL expose a bulk transaction import endpoint that accepts an array of normalized transaction objects, records them in `finance.transactions` via `record_transaction` (the store `GET /transactions` reads), and returns per-row results.

#### Scenario: Successful bulk import
- **WHEN** a POST request is sent to `/api/finance/transactions/bulk` with a JSON body containing a `transactions` array of 1–500 normalized transaction objects
- **THEN** each transaction MUST be recorded in `finance.transactions` via `record_transaction` with the same amount/direction/precision conventions
- **AND** the response MUST include `total`, `imported`, `skipped`, and `errors` integer counts, where `imported` counts only freshly inserted rows and duplicates count only in `skipped`
- **AND** HTTP status MUST be 200 on success (even if some rows were skipped or errored)

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
- **THEN** the endpoint MUST return HTTP 422 with a descriptive error message
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
- **THEN** it MUST process them identically to the HTTP endpoint (same dedup, same per-row error handling, same response shape)
- **AND** the tool MUST accept optional `account_id` and `source` parameters matching the HTTP endpoint

#### Scenario: MCP tool batch limit
- **WHEN** `bulk_record_transactions` is called with more than 500 transactions
- **THEN** it MUST return an error result (not silently truncate)

### Requirement: Normalized transaction object schema
All bulk ingestion paths (HTTP and MCP) MUST accept the same normalized transaction schema.

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
- **WHEN** a batch of 500 transactions is submitted to the bulk endpoint
- **THEN** the endpoint MUST complete within 10 seconds under normal conditions
- **AND** the response MUST NOT time out for batches up to the 500-row limit

### Requirement: Partial failure and re-run semantics
Re-running an import after a partial failure (e.g., API went down mid-import) MUST produce a clear, non-confusing result.

#### Scenario: Re-run after partial failure
- **WHEN** a CSV import script is re-run after a previous partial failure
- **THEN** previously imported rows MUST be deduped as `skipped`
- **AND** the response `error_details` MUST distinguish dedup skips from validation errors by including a `reason` field (e.g., `"duplicate"` vs `"invalid_date"`)
- **AND** the skill MUST instruct the LLM to explain to the user that skipped rows are previously imported duplicates, not data loss
