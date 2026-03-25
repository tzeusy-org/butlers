# Finance Butler — Insight Scan

## Purpose
Adds an insight-scan scheduled task to the Finance butler that generates proactive insight candidates from financial domain data.

## MODIFIED Requirements

### Requirement: Finance Butler Schedules
The finance butler runs bill checks, subscription alerts, monthly summaries, and insight scans.

#### Scenario: Scheduled task inventory
- **WHEN** the finance butler daemon is running
- **THEN** it executes four scheduled tasks: `upcoming-bills-check` (0 8 * * *), `subscription-renewal-alerts` (30 8 * * *), `monthly-spending-summary` (0 9 1 * *), and `insight-scan` (0 7 30 * * *, job: evaluate financial domain data and generate insight candidates)

## ADDED Requirements

### Requirement: Finance Insight Scan Job
The finance butler's `insight-scan` job SHALL evaluate financial domain data and produce insight candidates covering spending anomalies, upcoming bills, budget threshold warnings, and subscription renewal alerts. All candidates are submitted via the Switchboard's `propose_insight_candidate()` MCP tool — the butler does not write to `shared.insight_candidates` directly.

#### Scenario: Insight-scan job handler registration
- **WHEN** the finance butler starts
- **THEN** it SHALL register an `insight-scan` job handler that is invokable by the scheduler's `job` dispatch mode

#### Scenario: Candidate submission via Switchboard MCP
- **WHEN** the `insight-scan` job generates a candidate
- **THEN** it SHALL submit the candidate by calling the Switchboard's `propose_insight_candidate()` MCP tool
- **AND** if the tool returns `{"status": "filtered"}`, the butler SHALL skip remaining candidates (verbosity is off)
- **AND** if the tool returns `{"status": "error"}`, the butler SHALL log the error and continue with remaining candidates

#### Scenario: Spending anomaly insights
- **WHEN** the insight-scan job evaluates spending patterns
- **THEN** it SHALL generate candidates when a spending category's current-month total exceeds the 3-month rolling average by more than 30%
- **AND** categories exceeding the average by more than 100% SHALL have priority 80
- **AND** categories exceeding the average by 50-100% SHALL have priority 65
- **AND** categories exceeding the average by 30-50% SHALL have priority 50
- **AND** the `dedup_key` SHALL be `finance:spending-anomaly:{category}:{year-month}`
- **AND** `expires_at` SHALL be the end of the current calendar month
- **AND** categories with fewer than 3 months of history SHALL be excluded
- **AND** the message SHALL include the category, current amount, average amount, and percentage above average

#### Scenario: Upcoming bill insights
- **WHEN** the insight-scan job evaluates tracked bills
- **THEN** it SHALL generate candidates for bills due within 3 days that have not been marked as paid
- **AND** bills due within 1 day SHALL have priority 92 (time-critical)
- **AND** bills due within 3 days SHALL have priority 75
- **AND** the `dedup_key` SHALL be `finance:bill-due:{bill-id}:{due-date}`
- **AND** `expires_at` SHALL be the bill's due date
- **AND** `cooldown_days` SHALL be 1

#### Scenario: Budget threshold insights
- **WHEN** the insight-scan job evaluates monthly spending against user-set budgets (if any)
- **THEN** it SHALL generate candidates when total spending reaches 80% of a budget target
- **AND** spending at 90%+ of budget SHALL have priority 70
- **AND** spending at 80-90% of budget SHALL have priority 50
- **AND** the `dedup_key` SHALL be `finance:budget-threshold:{budget-name}:{year-month}`
- **AND** `expires_at` SHALL be the end of the current calendar month

#### Scenario: Subscription renewal insights
- **WHEN** the insight-scan job evaluates tracked subscriptions
- **THEN** it SHALL generate candidates for annual subscriptions renewing within 14 days
- **AND** renewal within 3 days SHALL have priority 75
- **AND** renewal within 14 days SHALL have priority 55
- **AND** the `dedup_key` SHALL be `finance:subscription-renewal:{subscription-id}:{renewal-date}`
- **AND** `expires_at` SHALL be the renewal date
- **AND** monthly subscriptions SHALL NOT generate insight candidates (too frequent — the existing `subscription-renewal-alerts` schedule handles these)
