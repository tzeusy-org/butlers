# Finance Overview

## Purpose
Net worth tracking (manual balance entries over time), cash flow analysis (income vs. expenses), subscription audit with annual cost projection, and tax-relevant expense flagging.

## ADDED Requirements

### Requirement: Net Worth Tracking
The system SHALL track account balances over time through user-reported snapshots stored in the `finance.balance_snapshots` table.

#### Scenario: Recording a balance snapshot
- **WHEN** `net_worth_snapshot(account, institution, balance, currency, as_of_date=None)` is called
- **THEN** the system SHALL upsert a row in `finance.balance_snapshots` with `account_id` (referencing `finance.accounts`), `balance NUMERIC(14,2)`, `currency CHAR(3)`, `as_of_date DATE` (default: today), and `source = 'manual'`
- **AND** the upsert SHALL use the `uq_balance_snapshot_account_date` unique index on `(account_id, as_of_date)` -- if a snapshot exists for the same account and date, the balance SHALL be updated
- **AND** credit account balances SHALL be stored as negative values (representing debt)

#### Scenario: Net worth history query
- **WHEN** `net_worth_history(months=12)` is called
- **THEN** the system SHALL query `finance.balance_snapshots` joined with `finance.accounts` to return the most recent balance snapshot per account per month for the requested period
- **AND** the response SHALL include: `snapshots` (list per month of `{period, accounts: [{account, institution, balance, currency}], total_assets, total_liabilities, net_worth}`)
- **AND** `total_assets` SHALL sum all positive balances, `total_liabilities` SHALL sum all negative balances (absolute value), and `net_worth` SHALL be `total_assets - total_liabilities`

#### Scenario: Missing months in net worth history
- **WHEN** a month has no balance snapshots for an account
- **THEN** the system SHALL carry forward the most recent prior snapshot for that account
- **AND** the carried-forward entry SHALL include `carried_forward=true` to distinguish it from a fresh report

### Requirement: Cash Flow Analysis
The system SHALL provide a `cash_flow` tool that computes income vs. expenses over configurable periods.

#### Scenario: Monthly cash flow
- **WHEN** `cash_flow(period="monthly", months=6)` is called
- **THEN** the system SHALL aggregate from `finance.transactions WHERE deleted_at IS NULL` by month, separating credits (income/refunds) from debits (expenses)
- **AND** each month SHALL include: `period` (YYYY-MM), `income` (sum of credit transactions), `expenses` (sum of debit transactions), `net` (income - expenses), `savings_rate` (net / income as percentage, or null if income is zero)

#### Scenario: Cash flow by category
- **WHEN** `cash_flow(period="monthly", months=6, breakdown=true)` is called
- **THEN** in addition to the per-month totals, each month SHALL include a `categories` list
- **AND** each category entry SHALL contain: `category`, `income`, `expenses`, `net`

#### Scenario: Annual cash flow summary
- **WHEN** `cash_flow(period="yearly")` is called
- **THEN** the system SHALL aggregate by calendar year for all available years of data
- **AND** the response shape SHALL be identical to monthly cash flow but with `period` as YYYY

### Requirement: Subscription Audit
The system SHALL provide a `subscription_audit` tool that aggregates all detected and tracked recurring charges with annual cost projection.

#### Scenario: Comprehensive subscription listing
- **WHEN** `subscription_audit()` is called
- **THEN** the system SHALL combine: (a) all active subscription facts, and (b) all recurring patterns detected by `detect_recurring` that are not yet tracked
- **AND** each entry SHALL include: `service` (or merchant), `amount`, `currency`, `frequency`, `annual_cost` (projected), `status` (one of `tracked_active`, `tracked_paused`, `detected_untracked`), `last_charge_date`, `next_expected_date`

#### Scenario: Annual cost projection
- **WHEN** annual cost is computed for a subscription
- **THEN** the projection SHALL multiply the amount by the appropriate frequency factor: weekly=52, monthly=12, quarterly=4, yearly=1
- **AND** the response SHALL include a `total_annual_cost` summing all active and detected subscriptions

#### Scenario: Subscription changes since last audit
- **WHEN** `subscription_audit()` is called
- **THEN** the response SHALL include a `changes_since_last_audit` section listing: new subscriptions detected, cancelled subscriptions, and price changes detected (comparing current amount vs. amount at last audit)
- **AND** `last_audit_date` SHALL be tracked as a memory fact

### Requirement: Tax-Relevant Expense Flagging
The system SHALL flag transactions that may be tax-deductible based on category and merchant patterns.

#### Scenario: Category-based tax flagging
- **WHEN** `flag_tax_deductible(year=2025)` is called
- **THEN** the system SHALL query all debit transactions for the specified year
- **AND** transactions in categories commonly associated with deductions (e.g., `medical`, `charitable`, `education`, `home_office`, `business_expense`) SHALL be flagged
- **AND** each flagged transaction SHALL include: `transaction_id`, `merchant`, `amount`, `category`, `tax_category` (mapped from spending category), `confidence`

#### Scenario: Configurable tax categories
- **WHEN** the user stores custom tax-relevant categories via `budget_set` or memory facts
- **THEN** the tax flagging system SHALL use those custom categories in addition to the default set
- **AND** custom categories SHALL take precedence over defaults

#### Scenario: Tax summary
- **WHEN** `flag_tax_deductible(year=2025)` completes
- **THEN** the response SHALL include a `summary` with: `total_flagged_amount`, `flagged_count`, `by_tax_category` (grouped totals), and `disclaimer` (stating this is not tax advice and should be reviewed by a professional)

#### Scenario: Tax advice scope boundary
- **WHEN** the user asks for tax advice or filing assistance
- **THEN** the butler SHALL decline with a clear scope boundary explanation
- **AND** it SHALL offer to provide the flagged transaction list for the user's accountant or tax software
