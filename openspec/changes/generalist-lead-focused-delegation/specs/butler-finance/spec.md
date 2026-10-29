## MODIFIED Requirements

### Requirement: Finance Butler Tool Surface
The finance butler SHALL provide transaction, subscription, bill tracking, bill↔payment reconciliation, and financial intelligence tools with primary storage in dedicated `finance.*` tables. It SHALL register these tools as finance capabilities with the control plane and SHALL NOT spawn a reasoning session of its own; goal invocations reach them only through grants.

Source: RFC 0038 D2 and D7
Scope: v1-mandatory

#### Scenario: Tool inventory
- **WHEN** the finance butler registers its capabilities with the control plane
- **THEN** it SHALL register: `record_transaction`, `track_subscription`, `track_bill`, `reconcile_bills`, `list_transactions`, `spending_summary`, `upcoming_bills`, `bulk_record_transactions`, `import_transactions`, `update_transaction`, `delete_transaction`, `merge_duplicates`, `split_transaction`, `bulk_recategorize`, `anomaly_scan`, `detect_duplicates`, `detect_recurring`, `suggest_categories`, `learn_merchant_categories`, `recall_merchant_mappings`, `predict_bills`, `budget_set`, `budget_list`, `budget_remove`, `budget_status`, `spending_trends`, `spending_forecast`, `net_worth_snapshot`, `net_worth_history`, `cash_flow`, `subscription_audit`, `flag_tax_deductible`, `compute_baselines`, `alert_configure`, `alert_list`, `detect_price_changes`, and calendar tools
- **AND** the finance butler SHALL NOT expose a standalone `track_bill_fact` tool; all bill writes (table + SPO mirror) go through `track_bill`

#### Scenario: A goal invocation uses finance tools
- **WHEN** a goal invocation needs a finance tool
- **THEN** it calls that tool only if its grant contains the matching finance capability, and the finance butler executes it only with a valid broker assertion

### Requirement: Finance Butler Skills
The finance butler SHALL have bill reminder, spending review, data import, and intelligence skills, held as versioned finance guidance that any goal invocation may find through `guidance.search` and load through `guidance.load`. Loading a finance skill SHALL NOT grant any finance capability.

Source: RFC 0038 D2 and D3
Scope: v1-mandatory

#### Scenario: Skill inventory
- **WHEN** the finance butler's guidance is indexed
- **THEN** it SHALL include `bill-reminder` (bill review, urgency triage, and payment reminder workflow), `spending-review` (spending analysis by category, time period, anomaly detection, and trend analysis), `transaction-csv-extraction` (adaptive CSV import through the `import_transactions` capability with a dry-run preview and an explicit column map), `historical-data-import` (multi-format bank CSV import with format detection, deduplication, and baseline computation), `budget-review` (interactive budget setting, status checking, and forecast review), `anomaly-triage` (interactive anomaly review and resolution workflow), plus shared skills `butler-memory` and `butler-notifications`

#### Scenario: No finance skill depends on a shell
- **WHEN** a finance skill describes an import or analysis workflow
- **THEN** every step it prescribes is a call to a finance capability, because goal invocations have no shell, filesystem, or web fetch
