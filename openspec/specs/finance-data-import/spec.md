# Finance Data Import

## Purpose
Historical data import pipeline -- multi-format CSV normalization, deduplication on import, and retroactive baseline analytics. Extends the existing `transaction-csv-extraction` skill with format detection and bulk processing.

## Requirements

### Requirement: Bank Export Format Detection
The system SHALL auto-detect common bank export CSV formats by analyzing header rows and column patterns.

#### Scenario: Known format detection
- **WHEN** `import_transactions(storage_ref, account_id=None)` is called with a CSV blob reference
- **THEN** the system SHALL read the header row and match it against known bank format signatures
- **AND** supported formats SHALL include at minimum: Chase (Date, Description, Amount, Balance), Amex (Date, Description, Amount, Extended Details), Capital One (Transaction Date, Posted Date, Card No., Description, Category, Debit, Credit), and a generic CSV format (requires at least date, description/merchant, and amount columns)
- **AND** the detected format SHALL be returned in the response as `detected_format`

#### Scenario: Unknown format fallback
- **WHEN** the CSV header does not match any known format signature
- **THEN** the system SHALL attempt generic CSV parsing by searching for columns containing date-like, amount-like, and description-like values
- **AND** if generic parsing fails, the system SHALL return an error with `status="unrecognized_format"` and include the detected headers for the user to provide a manual column mapping

#### Scenario: Manual column mapping override
- **WHEN** `import_transactions(storage_ref, column_map={date: "Col1", merchant: "Col2", amount: "Col3"})` is called with an explicit column mapping
- **THEN** the system SHALL use the provided mapping instead of auto-detection
- **AND** required mapped columns SHALL be: `date`, `merchant` (or `description`), and `amount`
- **AND** separate amount columns SHALL be named by `debit` and `credit` when `amount_sign="split_cols"` is supplied
- **AND** optional mapped columns SHALL include: `category`, `currency`, `account`, `reference`

### Requirement: Explicit Parsing Options
The daemon SHALL accept closed-form parsing overrides and validate them before retrieving the blob.

#### Scenario: Semicolon and day-first positive-debit export
- **WHEN** `import_transactions(storage_ref, delimiter=";", date_format="%d/%m/%Y", amount_sign="positive_debit")` imports a semicolon-separated CSV
- **THEN** all CSV readers SHALL use the specified delimiter
- **AND** the explicit strptime format SHALL bypass date auto-detection
- **AND** positive amounts SHALL become absolute debit amounts and negative amounts SHALL become absolute credit amounts

#### Scenario: Supported parsing overrides
- **WHEN** parsing options are supplied
- **THEN** `delimiter` SHALL accept only comma (default), semicolon, tab, or pipe
- **AND** `date_format` SHALL be a nonempty valid strptime format
- **AND** `amount_sign` SHALL accept only `negative_debit`, `positive_debit`, or `split_cols`, overriding the detected convention
- **AND** `split_cols` SHALL require both `debit` and `credit` CSV column names in `column_map`
- **AND** signed overrides SHALL use the `amount` column even when `debit` and `credit` columns coexist; absence of a signed amount column SHALL produce an actionable row error

#### Scenario: Invalid parsing options
- **WHEN** an option is invalid, such as `delimiter="ab"`, an unknown `amount_sign`, or an invalid `date_format` (including repeated directives such as `%Y/%Y`)
- **THEN** the response SHALL contain `status="validation_error"` and an actionable error
- **AND** no blob read or database operation SHALL occur

### Requirement: Data Normalization
The system SHALL normalize transaction data from different bank formats into the canonical `finance.transactions` table schema.

#### Scenario: Date format normalization
- **WHEN** transactions are parsed from a CSV
- **THEN** the system SHALL auto-detect and parse date formats including: `MM/DD/YYYY`, `YYYY-MM-DD`, `DD/MM/YYYY`, `M/D/YYYY`, `MM-DD-YYYY`
- **AND** all dates SHALL be converted to `TIMESTAMPTZ` with midnight UTC when no time component is present

#### Scenario: Amount normalization
- **WHEN** amounts are parsed from a CSV
- **THEN** the system SHALL handle: negative values as debits, positive as credits (Chase-style); separate debit/credit columns (Capital One-style); amounts with or without currency symbols; amounts with comma-separated thousands (e.g., `1,234.56`)
- **AND** all amounts SHALL be stored as absolute values with direction inferred per the existing `_infer_direction` convention

#### Scenario: Merchant name normalization
- **WHEN** merchant names are extracted from CSV descriptions
- **THEN** the system SHALL strip common noise patterns: trailing transaction IDs, card numbers (****XXXX), date stamps, and location codes
- **AND** the original raw description SHALL be preserved in `metadata.raw_description`

#### Scenario: Currency inference
- **WHEN** the CSV does not include a currency column
- **THEN** the system SHALL default to the currency specified in `import_transactions(currency="USD")` if provided
- **AND** if no currency is specified and the account_id is linked to a known account fact, the account's currency SHALL be used
- **AND** if neither is available, `USD` SHALL be used as the fallback with a warning in the response

### Requirement: Import Deduplication
The system SHALL prevent duplicate transactions during import by checking against existing rows in `finance.transactions` using the tiered deduplication strategy.

#### Scenario: Tiered deduplication on import
- **WHEN** transactions are imported from a CSV
- **THEN** each transaction SHALL be checked against existing rows in `finance.transactions` using the tiered UNIQUE partial index strategy: Priority 1 `(account_id, external_id)` if an external ID is available, Priority 2 `(source_message_id, merchant, amount, posted_at)` if a source message ID is available, Priority 3 `(account_id, posted_at, amount, merchant)` as fallback for CSV imports without stable IDs
- **AND** matching transactions SHALL be skipped and counted in the `skipped` total of the response

#### Scenario: Import response with dedup summary
- **WHEN** `import_transactions()` completes
- **THEN** the response SHALL include: `total` (rows in CSV), `imported` (new transactions created), `skipped` (duplicates detected), `errors` (rows that failed validation), `error_details` (list of `{row, reason}`), `detected_format`, `date_range` (earliest and latest transaction dates), and `categories_used` (list of categories assigned)

#### Scenario: Dry run mode
- **WHEN** `import_transactions(storage_ref, dry_run=true)` is called
- **THEN** the system SHALL parse and validate the CSV, detect duplicates, and return the import summary
- **AND** no transaction rows SHALL be created in `finance.transactions`
- **AND** the response SHALL include a `preview` of at most the first 10 parsed transactions, each with an `is_duplicate` flag
- **AND** learned merchant-category mappings SHALL be applied without inserting transactions

### Requirement: Blob-only Import Boundary
The session-facing tool SHALL accept a blob `storage_ref` from the routed attachment list. CSV content SHALL be parsed in the daemon, outside model context.

#### Scenario: Host filesystem tool retirement
- **WHEN** finance MCP tools are registered
- **THEN** `import_transactions` SHALL be available in the bulk group
- **AND** no `import_transactions_from_file` tool SHALL be registered or exported

#### Scenario: Untrusted bounded output
- **WHEN** a preview or parse error contains CSV cells
- **THEN** the capability documentation SHALL label those values as untrusted file content
- **AND** previews SHALL contain at most ten rows, with at most twenty error details in dry runs and fifty in imports
- **AND** the returned projection SHALL limit strings to 256 characters, keys to 64 characters, nested objects to eight input fields and 2 KiB, lists to fifty items and 8 KiB, and the complete response to 32 KiB measured as ASCII-escaped JSON
- **AND** these limits SHALL apply to date-detection samples, error reasons and raw cells, preview cells, categories, blob failures, and echoed inputs
- **AND** `output_truncated`, per-row `truncated` flags, `truncation` counts, and `output_limits` SHALL disclose clipping or omission; summary counts SHALL retain the complete population
- **AND** projection limits SHALL NOT truncate the cells used internally for parsing, duplicate checks, persistence, or analytics

### Requirement: Retroactive Baseline Analytics
The system SHALL compute analytics baselines after a historical import to enable immediate anomaly detection and trend analysis.

#### Scenario: Post-import spending summary refresh
- **WHEN** one or more new transactions are imported outside dry-run mode
- **THEN** the daemon SHALL refresh the spending_summaries materialized view when present
- **AND** the response SHALL report `mv_refreshed`

#### Scenario: Post-import baseline computation
- **WHEN** `import_transactions()` successfully imports 50+ transactions
- **THEN** the system SHALL automatically trigger `compute_baselines()` to establish per-merchant and per-category baselines from the imported data
- **AND** the import response SHALL include `baselines_triggered=true`

#### Scenario: Post-import merchant categorization learning
- **WHEN** imported transactions include category data
- **THEN** the system SHALL trigger `learn_merchant_categories()` to build the merchant-to-category mapping in `finance.merchant_mappings` from the imported data
- **AND** the import response SHALL include `categories_learned` with the count of new mappings upserted into `finance.merchant_mappings`

### Requirement: Large Import Performance
The system SHALL handle large historical imports (10,000+ transactions) efficiently.

#### Scenario: Batch processing
- **WHEN** a CSV contains more than 500 rows
- **THEN** the system SHALL process the import in batches of 500 transactions (matching the `bulk_record_transactions` batch limit)
- **AND** each batch SHALL be committed independently so that a failure in batch N does not roll back batches 1 through N-1
- **AND** the response SHALL include `batches_processed` count

#### Scenario: Progress reporting for large imports
- **WHEN** a CSV contains more than 1,000 rows
- **THEN** the system SHALL provide progress updates via `notify()` at 25%, 50%, 75%, and 100% completion
- **AND** each progress update SHALL include: `processed`, `total`, `imported_so_far`, `skipped_so_far`, `errors_so_far`
