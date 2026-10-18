## MODIFIED Requirements

### Requirement: Finance Memory Taxonomy
The finance butler SHALL use a merchant-centric memory taxonomy with financial predicates including analytics-specific predicates. Spending baselines SHALL NOT be stored as memory facts: category spend baselines live in `metric_baselines` (personal baselines) and merchant baselines are computed inline.

#### Scenario: Memory classification
- **WHEN** the finance butler extracts facts
- **THEN** it SHALL use subjects like merchant names, service names, or "user"; predicates like `preferred_payment_method`, `spending_habit`, `subscription_status`, `price_change`, `merchant_category`, `alert_config`, `anomaly_threshold`, `subscription_audit_date`; permanence `stable` for alert configs and institution relationships; `standard` for active subscriptions and patterns; `volatile` for anomaly flags and one-time observations
- **AND** merchant category mappings SHALL be stored in `finance.merchant_mappings` (dedicated table), NOT as memory facts
- **AND** budget targets SHALL be stored in `finance.budgets` (dedicated table), NOT as memory facts
- **AND** account balance snapshots SHALL be stored in `finance.balance_snapshots` (dedicated table), NOT as memory facts
