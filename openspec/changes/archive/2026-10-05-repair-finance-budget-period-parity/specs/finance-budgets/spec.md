## MODIFIED Requirements

### Requirement: Budget Target Management
The system SHALL allow setting, updating, and querying category-level budget targets stored in the `finance.budgets` table.

#### Scenario: Setting a budget target
- **WHEN** `budget_set(category, amount, period, currency, warn_threshold=0.8, alert_threshold=1.0)` is called
- **THEN** the system SHALL upsert a row in `finance.budgets` with `category`, `amount NUMERIC(14,2)`, `currency CHAR(3)`, `period`, `warn_threshold FLOAT`, `alert_threshold FLOAT`, and `is_active = true`
- **AND** `period` SHALL be one of `daily`, `weekly`, `monthly`, `quarterly`, `yearly`
- **AND** if a budget for the same category and period already exists (enforced by `uq_budget_category_period` unique index on `(category, period) WHERE is_active = true`), the existing row SHALL be deactivated (`is_active = false`) and a new row inserted

#### Scenario: Listing active budgets
- **WHEN** `budget_list()` is called
- **THEN** the system SHALL return all rows from `finance.budgets WHERE is_active = true`
- **AND** each budget SHALL include: `id`, `category`, `amount`, `currency`, `period`, `warn_threshold`, `alert_threshold`, `created_at`

#### Scenario: Removing a budget
- **WHEN** `budget_remove(category, period)` is called
- **THEN** the system SHALL deactivate the matching row in `finance.budgets` by setting `is_active = false`
- **AND** subsequent `budget_list()` calls SHALL NOT include the deactivated budget

#### Scenario: Unsupported budget period
- **WHEN** a budget is set or removed with a period outside `daily`, `weekly`, `monthly`, `quarterly`, `yearly`, including `annual`
- **THEN** the tool SHALL reject the call with an `Unsupported period` error naming the supported periods before accessing the database
- **AND** `yearly` SHALL be the canonical token for an annual January 1 through December 31 calendar span

#### Scenario: Failed budget replacement preserves the active target
- **WHEN** insertion of a replacement budget fails after the prior matching budget is deactivated
- **THEN** the complete replacement SHALL roll back and the prior budget SHALL remain active with its original history intact

### Requirement: Budget Status Checking
The system SHALL provide a `budget_status` tool that compares current spending against budget targets and returns per-category status.

#### Scenario: Budget status within limits
- **WHEN** `budget_status()` is called and a category's spending is below the warn threshold
- **THEN** the status for that category SHALL be `"on_track"`
- **AND** the response SHALL include: `category`, `budget_amount`, `spent`, `remaining`, `utilization_pct`, `status`, `period_start`, `period_end`

#### Scenario: Budget status at warning level
- **WHEN** a category's spending exceeds `warn_threshold * budget_amount` but is below `alert_threshold * budget_amount`
- **THEN** the status for that category SHALL be `"warning"`

#### Scenario: Budget status exceeded
- **WHEN** a category's spending equals or exceeds `alert_threshold * budget_amount`
- **THEN** the status for that category SHALL be `"exceeded"`

#### Scenario: Period alignment
- **WHEN** `budget_status()` computes spending for a budget period
- **THEN** it SHALL use calendar bounds with `DATE_TRUNC`-equivalent alignment to the budget's period (weekly from Monday, monthly from 1st, quarterly from quarter start), measured on the owner's configured calendar timezone (UTC when unset or unreadable); daily starts on the current local date and yearly on January 1
- **AND** spending SHALL be aggregated from `finance.transactions WHERE direction = 'debit' AND deleted_at IS NULL` with matching `category` column, joined against `finance.budgets WHERE is_active = true` on `category`
- **AND** each window SHALL include its opening local midnight and exclude the midnight after its final date; quarterly windows SHALL span the complete calendar quarter, including leap days and year transitions

#### Scenario: Daily windows follow the owner's calendar day
- **WHEN** spending for a daily budget is checked
- **THEN** `period_start` and `period_end` SHALL both be the current owner-local date
- **AND** only debit spending within that day's half-open midnight-to-next-midnight instant window SHALL count, preserving category, currency and deleted-transaction filtering
- **AND** a daylight-saving transition SHALL retain the same calendar-day semantics even when the instant window is 23 or 25 hours
