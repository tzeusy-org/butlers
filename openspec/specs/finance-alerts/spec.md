# Finance Alerts

## Purpose
Configurable alert system -- large transaction alerts, subscription price change detection, bill reminders from historical patterns, and automated periodic spending summaries.

## ADDED Requirements

### Requirement: Alert Configuration
The system SHALL allow configuring financial alert preferences stored as memory facts.

#### Scenario: Setting a large transaction alert threshold
- **WHEN** `alert_configure(type="large_transaction", threshold=500, currency="USD", enabled=true)` is called
- **THEN** the system SHALL store a memory fact with `predicate='alert_config'`, `content='large_transaction'`, and `metadata={threshold, currency, enabled}`
- **AND** if an alert config for the same type already exists, it SHALL be superseded

#### Scenario: Listing active alert configurations
- **WHEN** `alert_list()` is called
- **THEN** the system SHALL return all active alert_config facts
- **AND** each alert SHALL include: `type`, configuration parameters, and `enabled` status
- **AND** the alert types accepted by `alert_configure` (validated against `_VALID_ALERT_TYPES`) SHALL be: `large_transaction`, `budget_exceeded`, `new_merchant`, `price_change`
- **AND** subscription price-change detection, bill reminders, and anomaly digests are delivered through dedicated tools (`detect_price_changes`, `predict_bills`, `anomaly_scan`) and scheduled tasks rather than as configurable `alert_configure` types

#### Scenario: Disabling an alert
- **WHEN** `alert_configure(type="large_transaction", enabled=false)` is called
- **THEN** the alert config SHALL be updated with `enabled=false`
- **AND** the scheduled check for that alert type SHALL skip processing when disabled

### Requirement: Large Transaction Alerts
The system SHALL flag transactions exceeding a configurable amount threshold.

#### Scenario: Transaction exceeds threshold
- **WHEN** a new transaction is recorded (via `record_transaction` or `bulk_record_transactions`) and a `large_transaction` alert is configured and enabled
- **THEN** if the transaction amount exceeds the configured threshold, the system SHALL include a `large_transaction_alert` flag in the transaction recording response
- **AND** the flag SHALL include: `threshold`, `amount`, `merchant`, `exceeds_by` (amount - threshold)

#### Scenario: Large transaction digest via scheduled task
- **WHEN** the daily anomaly digest scheduled task runs
- **THEN** it SHALL include any large transactions from the past 24 hours that exceeded the threshold
- **AND** the digest SHALL be sent via `notify(channel="telegram", intent="send")`

### Requirement: Subscription Price Change Detection
The system SHALL detect when a recurring charge changes amount compared to the tracked subscription or historical median.

#### Scenario: Price increase detection
- **WHEN** `detect_price_changes(days_back=30)` is called or during the scheduled subscription renewal alert
- **THEN** the system SHALL compare recent transaction amounts for tracked subscription merchants against the subscription's recorded amount
- **AND** if the transaction amount differs from the tracked amount by more than the `_PRICE_CHANGE_THRESHOLD` (5%), it SHALL flag a price change
- **AND** the flag SHALL include: `service`, `previous_amount`, `new_amount`, `change_pct`, `change_direction` (one of `increase`, `decrease`)

#### Scenario: Price change notification
- **WHEN** a price change is detected during a scheduled scan
- **THEN** the system SHALL notify the user via `notify(channel="telegram", intent="send")` with the service name, old amount, new amount, and percentage change
- **AND** the notification SHALL suggest updating the subscription record

### Requirement: Bill Reminders from Historical Patterns
The system SHALL generate bill reminders based on historical payment patterns, supplementing the existing `upcoming_bills` tool.

#### Scenario: Historical bill reminder
- **WHEN** the scheduled `upcoming-bills-check` task runs
- **THEN** in addition to querying tracked bill facts, it SHALL call `predict_bills(days_ahead=30)` (the tool default) to identify predicted bills from historical patterns
- **AND** predicted bills not already tracked SHALL be included in the reminder with a `source="predicted"` flag

#### Scenario: Predicted bill accuracy feedback
- **WHEN** a predicted bill reminder is generated
- **THEN** the reminder SHALL include the prediction's `confidence` level
- **AND** high-confidence predictions (6+ historical occurrences) SHALL be presented as likely upcoming bills
- **AND** medium-confidence predictions SHALL be presented as possible upcoming bills

### Requirement: Automated Periodic Summaries
The system SHALL generate enhanced periodic financial summaries incorporating intelligence data.

#### Scenario: Enhanced monthly spending summary
- **WHEN** the `monthly-spending-summary` scheduled task fires on the 1st of the month
- **THEN** in addition to the existing spending breakdown, the summary SHALL include:
  - Spending trend (change vs. prior month, with direction and percentage)
  - Budget status for all active budgets (on_track/warning/exceeded)
  - Anomaly count for the past month
  - Subscription audit summary (total monthly recurring cost, any price changes detected)
  - Net worth update (if snapshots were recorded in the past month)

#### Scenario: Weekly budget digest
- **WHEN** the weekly budget check scheduled task fires
- **THEN** it SHALL call `budget_status()` and notify the user only if any category is in `warning` or `exceeded` status
- **AND** the notification SHALL include: category, budget amount, spent amount, utilization percentage, and days remaining in the period
- **AND** if all categories are `on_track`, no notification SHALL be sent

#### Scenario: Daily anomaly digest
- **WHEN** the daily anomaly digest scheduled task fires
- **THEN** it SHALL call `anomaly_scan(days_back=1)` and notify the user only if anomalies are found
- **AND** the notification SHALL group anomalies by type and include the total count
- **AND** if no anomalies are found, no notification SHALL be sent

### Requirement: Alert Scheduled Task Definitions
The system SHALL register new scheduled tasks for intelligence-driven alerts.

#### Scenario: Daily anomaly digest schedule
- **WHEN** the finance butler daemon starts
- **THEN** it SHALL register a scheduled task `anomaly-digest` with cron `0 21 * * *` (daily at 9 PM)
- **AND** the task prompt SHALL instruct the runtime to call `anomaly_scan(days_back=1)` and notify via Telegram if anomalies are found

#### Scenario: Weekly budget check schedule
- **WHEN** the finance butler daemon starts
- **THEN** it SHALL register a scheduled task `budget-status-check` with cron `0 9 * * 1` (Monday at 9 AM)
- **AND** the task prompt SHALL instruct the runtime to call `budget_status()` and notify via Telegram if any category is in warning or exceeded status

#### Scenario: Monthly subscription audit schedule
- **WHEN** the finance butler daemon starts
- **THEN** it SHALL register a scheduled task `subscription-audit-monthly` with cron `0 10 1 * *` (1st of month at 10 AM)
- **AND** the task prompt SHALL instruct the runtime to call `subscription_audit()` and notify via Telegram with the audit summary including total annual recurring cost
