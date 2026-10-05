## MODIFIED Requirements

### Requirement: Category-level budget targets
The `finance.budgets` table SHALL store budget targets with configurable thresholds for spending alerts.

#### Scenario: Budget structure
- **WHEN** a budget is created
- **THEN** it SHALL include `category TEXT`, `amount NUMERIC(14,2)`, `currency CHAR(3)` (default `'USD'`), `period TEXT` (one of `'weekly'`, `'monthly'`, `'yearly'`, `'daily'`, `'quarterly'`, enforced by the `budgets_period_check` CHECK constraint installed in `finance_006` and widened in `finance_016`), `warn_threshold FLOAT` (default `0.8`), and `alert_threshold FLOAT` (default `1.0`)

#### Scenario: Budget uniqueness
- **WHEN** a budget is created for a category and period
- **THEN** `uq_budget_category_period` SHALL enforce `UNIQUE (category, period)` with partial condition `WHERE is_active = true`
- **AND** deactivated budgets SHALL NOT conflict with new active budgets

#### Scenario: Budget period repair preserves existing data
- **WHEN** a database with the historical `finance_006` daily/weekly/monthly/yearly CHECK is upgraded through the forward budget period repair
- **THEN** the current CHECK SHALL additionally admit `quarterly` without deleting, renaming, converting or changing any active or inactive budget row
- **AND** the category foreign key and partial active-budget uniqueness SHALL remain enforced
- **AND** fresh migration replay and repeated repair application SHALL produce the same supported periods and preserve existing rows

#### Scenario: Budget period downgrade refuses quarterly history
- **WHEN** the period repair is downgraded while any quarterly budget exists, including inactive history
- **THEN** downgrade SHALL refuse with an actionable error and preserve all data, the current CHECK and the migration revision stamp
- **AND** concurrent writes SHALL NOT permit a quarterly row to escape this refusal and constraint enforcement

#### Scenario: Budget period downgrade preserves legacy periods
- **WHEN** the period repair is downgraded and no quarterly row exists
- **THEN** it SHALL restore precisely the historical daily/weekly/monthly/yearly CHECK without changing existing rows
- **AND** quarterly SHALL again be rejected by the database until re-upgrade restores quarterly admission
