# Finance Budgets

## Purpose
Category-level budget targets with threshold-based proactive alerts, spending trend analysis (month-over-month and year-over-year), and end-of-month spending forecasting.

## ADDED Requirements

### Requirement: Budget Target Management
The system SHALL allow setting, updating, and querying category-level budget targets stored in the `finance.budgets` table.

#### Scenario: Setting a budget target
- **WHEN** `budget_set(category, amount, period, currency, warn_threshold=0.8, alert_threshold=1.0)` is called
- **THEN** the system SHALL upsert a row in `finance.budgets` with `category`, `amount NUMERIC(14,2)`, `currency CHAR(3)`, `period`, `warn_threshold FLOAT`, `alert_threshold FLOAT`, and `is_active = true`
- **AND** `period` SHALL be one of `weekly`, `monthly`, `quarterly`
- **AND** if a budget for the same category and period already exists (enforced by `uq_budget_category_period` unique index on `(category, period) WHERE is_active = true`), the existing row SHALL be deactivated (`is_active = false`) and a new row inserted

#### Scenario: Listing active budgets
- **WHEN** `budget_list()` is called
- **THEN** the system SHALL return all rows from `finance.budgets WHERE is_active = true`
- **AND** each budget SHALL include: `id`, `category`, `amount`, `currency`, `period`, `warn_threshold`, `alert_threshold`, `created_at`

#### Scenario: Removing a budget
- **WHEN** `budget_remove(category, period)` is called
- **THEN** the system SHALL deactivate the matching row in `finance.budgets` by setting `is_active = false`
- **AND** subsequent `budget_list()` calls SHALL NOT include the deactivated budget

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
- **THEN** it SHALL use `DATE_TRUNC` aligned to the budget's period (weekly from Monday, monthly from 1st, quarterly from quarter start)
- **AND** spending SHALL be aggregated from `finance.transactions WHERE direction = 'debit' AND deleted_at IS NULL` with matching `category` column, joined against `finance.budgets WHERE is_active = true` on `category`

### Requirement: Spending Trend Analysis
The system SHALL provide a `spending_trends` tool that compares spending across time periods with percentage changes and trend direction.

#### Scenario: Month-over-month comparison
- **WHEN** `spending_trends(comparison="mom", months=6)` is called
- **THEN** the system SHALL return per-month spending totals for the last N months
- **AND** each month SHALL include: `period` (YYYY-MM), `total_spend`, `change_amount` (vs. prior month), `change_pct` (vs. prior month), `direction` (one of `up`, `down`, `flat`)
- **AND** `flat` SHALL be used when `abs(change_pct) < 5%`

#### Scenario: Year-over-year comparison
- **WHEN** `spending_trends(comparison="yoy")` is called
- **THEN** the system SHALL compare the current month's spending against the same month in the prior year
- **AND** the response SHALL include: `current_period`, `prior_period`, `current_spend`, `prior_spend`, `change_amount`, `change_pct`, `direction`

#### Scenario: Category-level trends
- **WHEN** `spending_trends(comparison="mom", category="dining")` is called with a category filter
- **THEN** the trend analysis SHALL be scoped to transactions matching that category only
- **AND** the response shape SHALL be identical to the unfiltered response

#### Scenario: Insufficient data for comparison
- **WHEN** `spending_trends()` is called but there is less than 2 months of transaction data
- **THEN** the response SHALL include `status="insufficient_data"` and a `message` explaining the minimum data requirement

### Requirement: Spending Forecasting
The system SHALL provide a `spending_forecast` tool that predicts end-of-month spending based on current trajectory and historical patterns.

#### Scenario: Linear projection forecast
- **WHEN** `spending_forecast()` is called mid-month
- **THEN** the system SHALL compute a linear projection: `(current_month_spend / days_elapsed) * days_in_month`
- **AND** the response SHALL include: `as_of_date`, `days_elapsed`, `days_remaining`, `current_spend`, `projected_total`, `daily_average`

#### Scenario: Category-level forecast
- **WHEN** `spending_forecast()` is called
- **THEN** the response SHALL include per-category projections for each category with spending in the current month
- **AND** each category projection SHALL include: `category`, `current_spend`, `projected_total`, `historical_average` (average monthly spend for that category over the last 6 months)

#### Scenario: Forecast vs. budget comparison
- **WHEN** `spending_forecast()` is called and budget targets exist
- **THEN** for each category with a budget, the forecast SHALL include: `budget_amount`, `projected_utilization_pct`, `on_track` (boolean, true if projected_total <= budget_amount)

#### Scenario: First-of-month edge case
- **WHEN** `spending_forecast()` is called on the 1st of the month with no spending data for the current month
- **THEN** the projection SHALL use the prior month's total as the forecast
- **AND** a `basis` field SHALL be set to `"prior_month"` instead of `"linear_projection"`
