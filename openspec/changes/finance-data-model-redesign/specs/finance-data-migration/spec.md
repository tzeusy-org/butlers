# Finance Data Migration

## ADDED Requirements

### Requirement: Four-phase migration from SPO-primary to dedicated-table-primary
The migration from SPO fact storage to dedicated table storage SHALL follow four phases to ensure zero data loss and backward compatibility.

#### Scenario: Phase 1 -- Schema enhancement (non-breaking)
- **WHEN** the migration `finance_002` runs
- **THEN** it SHALL add all new columns to `finance.transactions` via `ALTER TABLE ADD COLUMN IF NOT EXISTS`
- **AND** it SHALL create all new tables (`categories`, `merchant_mappings`, `recurring_groups`, `import_batches`, `balance_snapshots`, `budgets`, `transaction_corrections`)
- **AND** it SHALL create the `spending_summaries` materialized view
- **AND** it SHALL create all new indexes
- **AND** it SHALL seed default categories idempotently
- **AND** all additions SHALL be backward-compatible (no existing columns removed or renamed, all new columns have defaults)

#### Scenario: Phase 2 -- Backfill from SPO facts
- **WHEN** Phase 1 is complete
- **THEN** a backfill query SHALL insert existing transaction facts from `public.facts` into `finance.transactions`
- **AND** it SHALL filter facts by `predicate IN ('transaction_debit', 'transaction_credit')`, `validity = 'active'`, and `scope = 'finance'`
- **AND** it SHALL extract `merchant`, `amount`, `currency`, `direction`, `category`, `description`, `payment_method`, `account_id`, `source_message_id` from JSONB metadata
- **AND** it SHALL use `COALESCE` for optional fields and defensive casts for numeric/uuid fields
- **AND** it SHALL deduplicate against existing rows using `NOT EXISTS` on `(posted_at, merchant, amount)`
- **AND** rows that fail JSONB extraction or casting SHALL be logged and skipped, not treated as hard errors
- **AND** backfilled rows SHALL have `source = 'bulk'` for identification

#### Scenario: Phase 3 -- Dual-write transition
- **WHEN** Phase 2 is complete and the backfilled data is validated
- **THEN** `record_transaction` SHALL write to `finance.transactions` as the primary store
- **AND** it SHALL fire a background task to mirror the write to `public.facts` for memory/recall compatibility
- **AND** intelligence tools SHALL query `finance.transactions` exclusively
- **AND** memory tools (`memory_recall`, `memory_search`) SHALL continue to query `public.facts` for financial context
- **AND** if the SPO mirror write fails, the error SHALL be logged but the dedicated table write SHALL NOT be rolled back

#### Scenario: Phase 4 -- Deprecate SPO transaction writes
- **WHEN** Phase 3 has run for a validation period and intelligence features are stable
- **THEN** the SPO mirror write in `record_transaction` SHALL be removed
- **AND** existing facts in `public.facts` SHALL remain in place (read-only) for historical memory/recall
- **AND** SPO-based transaction tool functions (`record_transaction_fact`, `list_transaction_facts`) SHALL be removed from the MCP tool surface

### Requirement: Migration Alembic structure
The schema changes SHALL be implemented as an Alembic migration following the project's migration conventions.

#### Scenario: Migration file placement
- **WHEN** the migration is created
- **THEN** it SHALL be placed at `roster/finance/migrations/versions/002_intelligence_tables.py`
- **AND** it SHALL have `revision = "finance_002"` and `down_revision = "finance_001"`
- **AND** it SHALL include both `upgrade()` and `downgrade()` functions

#### Scenario: Migration downgrade safety
- **WHEN** the migration is downgraded
- **THEN** the `downgrade()` function SHALL drop the materialized view, new tables, and new indexes
- **AND** it SHALL remove added columns from `finance.transactions` and `finance.accounts`
- **AND** it SHALL NOT drop existing tables or columns from `finance_001`
