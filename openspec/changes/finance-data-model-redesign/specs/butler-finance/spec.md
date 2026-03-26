# Finance Butler Role -- Delta for Data Model Redesign

## MODIFIED Requirements

### Requirement: Finance Butler Tool Surface
The finance butler SHALL provide transaction, subscription, and bill tracking tools with primary storage in the dedicated `finance.transactions` table.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the finance butler
- **THEN** it SHALL have access to: `record_transaction`, `track_subscription`, `track_bill`, `list_transactions`, `spending_summary`, `upcoming_bills`, `bulk_record_transactions`, `import_transactions`, `update_transaction`, `delete_transaction`, `merge_duplicates`, `split_transaction`, `bulk_recategorize`, and calendar tools

### Requirement: Finance Data Conventions
Financial data uses precise numeric types, ISO currency codes, and tiered deduplication on the dedicated transaction table.

#### Scenario: Data type conventions
- **WHEN** financial data is recorded
- **THEN** amounts use `NUMERIC(14,2)` (never float), currency uses ISO-4217 uppercase codes (e.g., `USD`, `EUR`), timestamps use `TIMESTAMPTZ` preserving timezone, and direction is inferred as `debit` or `credit` from context

#### Scenario: Composite deduplication for non-email sources
- **WHEN** a transaction is recorded without a `source_message_id` and without an `external_id`
- **THEN** deduplication SHALL use the tiered UNIQUE partial index strategy on `finance.transactions`: Priority 1 `(account_id, external_id)`, Priority 2 `(source_message_id, merchant, amount, posted_at)`, Priority 3 `(account_id, posted_at, amount, merchant)` as fallback
- **AND** the `sha256` composite hash approach SHALL no longer be used
- **AND** the existing `source_message_id`-based deduplication SHALL remain as Priority 2

### Requirement: CRUD-to-SPO migration -- finance domain (bu-ddb.4)
The finance butler migrates transaction tools to use the dedicated `finance.transactions` table as primary storage, with SPO facts as a secondary mirror for memory/recall.

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

#### Scenario: Subscription and bill tools as property fact wrappers
- **WHEN** `track_subscription` is called
- **THEN** it SHALL call `store_fact` with `predicate='subscription'`, `valid_at=NULL`, and metadata containing `{service, amount, currency, frequency, next_renewal, status, auto_renew, payment_method, account_id, source_message_id}`
- **AND** when `track_bill` is called
- **THEN** it SHALL call `store_fact` with `predicate='bill'`, `valid_at=NULL`, and metadata containing `{payee, amount, currency, due_date, frequency, status, payment_method, account_id, paid_at, source_message_id}`
- **AND** `upcoming_bills` SHALL query active facts with `predicate='bill'` filtering by `(metadata->>'due_date')::DATE <= NOW() + INTERVAL '7 days'`
