# Finance Butler Role

## Purpose
The Finance butler (port 40105) is a personal finance specialist for receipts, bills, subscriptions, and transaction alerts.

## ADDED Requirements

### Requirement: Finance Butler Identity and Runtime
The finance butler handles personal finance tracking with precise numeric types and currency handling.

#### Scenario: Identity and port
- **WHEN** the finance butler is running
- **THEN** it operates on port 40105 with description "Personal finance specialist for receipts, bills, subscriptions, and transaction alerts."
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema is `finance` within the consolidated `butlers` database

#### Scenario: Switchboard registration
- **WHEN** the finance butler starts
- **THEN** it registers with the switchboard at `http://localhost:40100/mcp` with `advertise = true`, `liveness_ttl_s = 300`, and route contract version range `route.v1` to `route.v1`

#### Scenario: Module profile
- **WHEN** the finance butler starts
- **THEN** it loads modules: `email`, `calendar` (Google provider, suggest conflicts policy), and `memory`

### Requirement: Finance Butler Tool Surface
The finance butler provides transaction, subscription, and bill tracking tools.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the finance butler
- **THEN** it has access to: `record_transaction`, `track_subscription`, `track_bill`, `list_transactions`, `spending_summary`, `upcoming_bills`, and calendar tools

### Requirement: Finance Data Conventions
Financial data uses precise numeric types and ISO currency codes.

#### Scenario: Data type conventions
- **WHEN** financial data is recorded
- **THEN** amounts use `NUMERIC(14,2)` (never float), currency uses ISO-4217 uppercase codes (e.g., `USD`, `EUR`), timestamps use `TIMESTAMPTZ` preserving timezone, and direction is inferred as `debit` or `credit` from context

### Requirement: Finance Butler Schedules
The finance butler runs bill checks, subscription alerts, and monthly summaries.

#### Scenario: Scheduled task inventory
- **WHEN** the finance butler daemon is running
- **THEN** it executes three native job schedules: `upcoming-bills-check` (0 8 * * *), `subscription-renewal-alerts` (30 8 * * *), and `monthly-spending-summary` (0 9 1 * *)

### Requirement: Finance Butler Skills
The finance butler has bill reminder and spending review skills.

#### Scenario: Skill inventory
- **WHEN** the finance butler operates
- **THEN** it has access to `bill-reminder` (bill review, urgency triage, and payment reminder workflow) and `spending-review` (spending analysis by category, time period, and anomaly detection), plus shared skills `butler-memory` and `butler-notifications`

### Requirement: Finance Memory Taxonomy
The finance butler uses a merchant-centric memory taxonomy with financial predicates.

#### Scenario: Memory classification
- **WHEN** the finance butler extracts facts
- **THEN** it uses subjects like merchant names, service names, or "user"; predicates like `preferred_payment_method`, `spending_habit`, `subscription_status`, `price_change`, `merchant_category`; permanence `stable` for recurring obligations and institution relationships, `standard` for active subscriptions and patterns, `volatile` for one-time transactions
