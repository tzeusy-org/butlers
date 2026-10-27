## ADDED Requirements

### Requirement: SPO transaction backfill

The finance butler SHALL provide an on-demand, idempotent backfill, `backfill_spo_transactions(pool, batch_size=500, scope='finance')` in `roster/finance/tools/backfill.py`, that copies active transaction facts (`predicate IN ('transaction_debit', 'transaction_credit')`, `validity = 'active'`, matching `scope`) from the facts table into `finance.transactions`. It is a runtime tool, not Alembic migration SQL.

#### Scenario: Facts are extracted defensively
- **WHEN** the backfill processes a transaction fact
- **THEN** it SHALL extract `merchant`, `amount`, `currency`, `category`, `description`, `payment_method`, `account_id`, `source_message_id`, `external_ref`, and `receipt_url` from the fact's JSONB metadata, deriving `direction` from metadata or the predicate name when absent
- **AND** a fact that fails extraction or casting SHALL be recorded as a `SkippedRow` with its reason and skipped, not raised as a hard error
- **AND** each inserted row SHALL carry `backfilled_from_fact_id` in its `metadata`

#### Scenario: Backfill deduplicates against existing transactions
- **WHEN** a fact matches an existing `finance.transactions` row on the same tiered key `record_transaction` uses (`source_message_id` with merchant, amount, and `posted_at`; else `external_ref` with `account_id`; else the `posted_at`, merchant, amount, and currency composite)
- **THEN** the backfill SHALL NOT insert a second row
- **AND** running the backfill again SHALL insert nothing new

#### Scenario: Backfilled rows take the default source
- **WHEN** the backfill inserts a row
- **THEN** it SHALL NOT set the `source` column, so the row inherits the table default `'manual'`

### Requirement: [TARGET-STATE] SPO transaction mirror retirement

Once the dedicated-table write path has run stably, the finance butler SHALL stop mirroring transaction writes to the facts table and SHALL retire the SPO-based transaction tools, keeping existing facts as read-only history.

#### Scenario: Mirror write and SPO tools are retired
- **WHEN** the SPO mirror is retired
- **THEN** `record_transaction` SHALL no longer mirror writes to the facts table
- **AND** existing transaction facts SHALL remain in place, read-only, for memory recall
- **AND** `record_transaction_fact` and `list_transaction_facts` SHALL be removed from the MCP tool surface
