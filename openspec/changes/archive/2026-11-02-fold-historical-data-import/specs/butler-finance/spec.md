## MODIFIED Requirements

### Requirement: Finance Butler Skills
The finance butler SHALL have bill reminder, spending review, data import, and intelligence skills.

#### Scenario: Skill inventory
- **WHEN** the finance butler operates
- **THEN** it SHALL have access to `bill-reminder` (bill review, urgency triage, and payment reminder workflow), `spending-review` (spending analysis by category, time period, anomaly detection, and trend analysis), `transaction-csv-extraction` (adaptive CSV import through `import_transactions(storage_ref)` with a dry-run preview and an explicit column map, also used for a historical backfill of several statements with one import per stored statement), `budget-review` (interactive budget setting, status checking, and forecast review), `anomaly-triage` (interactive anomaly review and resolution workflow), plus shared skills `butler-memory` and `butler-notifications`

### Requirement: Finance Butler Intelligence Behavioral Guidelines
The finance butler runtime instances SHALL follow additional behavioral guidelines for intelligence features.

#### Scenario: Post-transaction intelligence hook
- **WHEN** a transaction is recorded via `record_transaction`
- **THEN** the runtime SHALL check if the transaction matches a potential untracked subscription (using `detect_recurring` patterns) and surface the observation
- **AND** if a `large_transaction` alert is configured and the amount exceeds the threshold, the runtime SHALL flag it in the response

#### Scenario: Proactive trend surfacing
- **WHEN** the user asks about spending in a category
- **THEN** the runtime SHOULD include trend context (comparison to prior month) alongside the direct answer
- **AND** if budget targets exist for that category, the runtime SHOULD include budget utilization

#### Scenario: Intelligence data sufficiency awareness
- **WHEN** intelligence tools return `status="insufficient_data"`
- **THEN** the runtime SHALL inform the user about the minimum data requirements
- **AND** it SHALL suggest importing historical statements through the `transaction-csv-extraction` skill (one upload and one `import_transactions(storage_ref)` call per statement) if no historical import has been performed
