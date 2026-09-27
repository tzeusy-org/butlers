## MODIFIED Requirements

### Requirement: Single transaction creation with auto-categorization
Creating a single transaction SHALL check for duplicates, apply merchant mapping, and record the transaction with post-insert hooks.

#### Scenario: Deduplication check on create
- **WHEN** `record_transaction` is called
- **THEN** it SHALL check for an existing duplicate using the tiered dedup key hierarchy: (1) `(account_id, external_id)`, (2) `(source_message_id, merchant, amount, posted_at)`, (3) `(account_id, posted_at, amount, merchant)` as fallback
- **AND** if a duplicate is found, the existing transaction ID SHALL be returned without creating a new row

#### Scenario: Auto-categorization via merchant mapping
- **WHEN** a transaction is created without an explicit category (or with category `'uncategorized'`)
- **THEN** the system SHALL look up the merchant in `finance.merchant_mappings` using `ILIKE` pattern matching
- **AND** if a mapping is found, the category SHALL be set from the mapping with `category_source = 'auto'`
- **AND** if no mapping is found, the category SHALL remain `'uncategorized'`

#### Scenario: Safe account-label resolution
- **WHEN** a transaction tool receives a non-UUID account label
- **THEN** normalized direct or documented composite labels SHALL resolve only when exactly one account candidate matches
- **AND** fuzzy containment SHALL be considered only for normalized labels of at least four characters and SHALL resolve only when exactly one account candidate matches
- **AND** ambiguous candidates or labels that do not meet these rules SHALL not select an account and SHALL yield actionable guidance to pass an account UUID or omit `account_id` when the account is unknown

#### Scenario: Unknown category fallback under the category foreign-key schema
- **WHEN** `record_transaction` is called with a category and the migrated `categories` taxonomy table exists (enforcing `transactions.category -> categories.name`)
- **THEN** the tool SHALL resolve the supplied category against `categories` case-insensitively and store the canonical `name` when a match is found
- **AND** if no match is found, the category SHALL be stored as the seeded `'uncategorized'` bucket instead of leaking a raw `ForeignKeyViolationError` from the tool layer, with `category_source = 'manual'`
- **AND** the original supplied value SHALL be preserved in `metadata.original_category`, and a structured warning `{code: 'unknown_category', field: 'category', stored_as: 'uncategorized'}` SHALL be appended to `metadata.warnings`
- **AND** on legacy schemas without the `categories` table, the free-form category value SHALL be preserved unchanged

#### Scenario: Post-insert SPO mirror write
- **WHEN** a transaction is successfully inserted into `finance.transactions`
- **THEN** a background task SHALL mirror the transaction to `public.facts` with the appropriate predicate (`'transaction_debit'` or `'transaction_credit'`)
- **AND** the mirror write SHALL be fire-and-forget (failure does not roll back the primary insert)

#### Scenario: Inline bill reconciliation hook on debit insert
- **WHEN** a fresh debit transaction is inserted, the system SHALL call `match_transaction_to_bills()` against open bills
- **THEN** if the match tier is `'auto_settle'` (single in-window candidate with an exact payee match), the system SHALL settle that bill and include `bill_reconciliation.auto_settled` (with `bill_id`, `payee`, `amount`, `paid_at`, `txn_id`) in the `record_transaction` response
- **AND** if the match tier is `'confirm'` (multiple candidates), the response SHALL include `bill_reconciliation.candidates` (a list of `{bill_id, payee, due_date, amount}`) for user confirmation
- **AND** the hook SHALL be best-effort: any reconciliation failure is logged but never rolls back or fails the primary insert
- **AND** the batch counterpart is the `reconcile_bills()` MCP sweep tool, used as a backstop by the weekly `bill-reconciliation-sweep` job (see `butler-finance/spec.md` for its schedule)
