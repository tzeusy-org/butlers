# Bulk Transaction Ingestion

## Purpose
Bulk import endpoint and MCP tool for recording multiple normalized transactions in a single call, with composite deduplication for sources that lack email provenance.

## ADDED Requirements

### Requirement: Bulk transaction ingestion HTTP endpoint
The dashboard API SHALL expose a bulk transaction import endpoint that accepts an array of normalized transaction objects, persists them via the fact layer, and returns per-row results.

#### Scenario: Successful bulk import
- **WHEN** a POST request is sent to `/api/finance/transactions/bulk` with a JSON body containing a `transactions` array of 1–500 normalized transaction objects
- **THEN** each transaction MUST be persisted via `record_transaction_fact` (or equivalent fact-layer logic) with the same amount/direction/precision conventions
- **AND** the response MUST include `total`, `imported`, `skipped`, and `errors` integer counts
- **AND** HTTP status MUST be 200 on success (even if some rows were skipped or errored)

#### Scenario: Composite deduplication for CSV-sourced rows
- **WHEN** a transaction in the bulk request has no `source_message_id`
- **THEN** the endpoint MUST compute a composite idempotency key as `sha256(posted_at|amount|merchant|account_id)` (with `account_id` defaulting to empty string when absent)
- **AND** if a fact with the same idempotency key already exists, the row MUST be counted as `skipped` (not `imported` or `errors`)
- **AND** no duplicate fact MUST be created

#### Scenario: Source message ID deduplication preserved
- **WHEN** a transaction in the bulk request includes a `source_message_id`
- **THEN** deduplication MUST use the existing `source_message_id`-based logic from `record_transaction_fact`
- **AND** the composite key MUST NOT be used as a fallback

#### Scenario: Per-row error reporting
- **WHEN** a transaction in the bulk request has invalid data (e.g., unparseable date, missing required field)
- **THEN** that row MUST be counted in `errors` and included in `error_details` with its array `index` and `reason`
- **AND** valid rows in the same batch MUST still be processed (no all-or-nothing rollback)

#### Scenario: Batch size limit
- **WHEN** a bulk request contains more than 500 transactions
- **THEN** the endpoint MUST return HTTP 422 with a descriptive error message
- **AND** no transactions MUST be persisted

#### Scenario: Account ID association
- **WHEN** the bulk request includes a top-level `account_id` field
- **THEN** all transactions in the batch MUST inherit that `account_id` unless individually overridden
- **AND** the `account_id` MUST be included in the composite dedup key

#### Scenario: Source tagging
- **WHEN** the bulk request includes a top-level `source` field (e.g., `"csv-import"`)
- **THEN** each persisted fact MUST include `"import_source": <value>` in its metadata JSONB
- **AND** this field MUST be queryable via `list_transaction_facts` metadata filters

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
- **AND** `amount` sign convention MUST match `record_transaction_fact`: negative = debit, positive = credit

#### Scenario: Amount precision
- **WHEN** an amount value is provided in a normalized transaction
- **THEN** it MUST be stored as a string-encoded `NUMERIC(14,2)` value
- **AND** floating-point amounts MUST be quantized to 2 decimal places before storage
- **AND** no floating-point intermediate representation MUST be used in persistence
