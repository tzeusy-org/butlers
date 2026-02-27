# Finance Butler: Permanent Definition

Status: Normative (Target State)
Last updated: 2026-02-22
Primary owner: Product/Domain

## 1. Role
The Finance Butler is the specialist butler for personal-finance email ingestion and structured financial tracking.

It transforms high-signal financial messages (receipts, invoices, bills, transaction alerts, subscription lifecycle notifications, statements, and tax-related artifacts) into queryable domain records so spend, obligations, and renewal risk are visible and actionable.

## 2. Design Goals
- Convert noisy financial email streams into normalized, structured records.
- Preserve source provenance from email artifact to finance record for auditability.
- Support fast daily workflows: "What did I spend?", "What bills are due?", "What renews soon?".
- Enable recurring financial awareness through scheduled digests and proactive reminders.
- Keep role boundaries strict: finance decides finance semantics; delivery still routes through `notify` and Messenger.

## 2.1 Base Contract Overrides
Inherits unchanged:
- All clauses in `docs/roles/base_butler.md` apply unless explicitly listed in `Overrides`.

Overrides: none.

Additions:
- This role defines a dedicated finance-domain persistence schema (section 5) in addition to base core tables.
- This role defines finance-specific MCP tools (section 4) for transactions, bills, subscriptions, and spend summaries.
- This role defines explicit Switchboard classification signals for finance routing (section 7).
- This role requires calendar integration for due-date and renewal reminder workflows (section 8).

## 3. Scope and Boundaries
### In scope
- Ingestion of finance-category email artifacts into structured domain entities.
- Transaction recording and categorization.
- Subscription lifecycle tracking (renewals, pauses, cancellations, price changes).
- Bill tracking and due-date awareness.
- Financial account metadata tracking (institution, type, masked identifiers).
- Spend summaries over configurable windows.
- Scheduled reminders for near-term financial obligations.

### Out of scope
- Direct outbound channel delivery (owned by Messenger through `notify`).
- Tax filing computation, legal advice, and jurisdiction-specific compliance determinations.
- Portfolio optimization or investment trading execution.
- Payment initiation, bank transfers, or direct account mutation at financial institutions.
- Accounting-grade double-entry ledger semantics (future expansion only).

## 4. Tool Surface Contract

All tools below are additive to base core tools.

### 4.1 `record_transaction`
Signature:
`record_transaction(posted_at, merchant, amount, currency, category?, description?, payment_method?, account_id?, receipt_url?, external_ref?, source_message_id?, metadata?) -> TransactionRecord`

Behavior:
- Creates a normalized transaction row in `finance.transactions`.
- Supports positive/negative amount conventions; `direction` is inferred when omitted.
- Uses `source_message_id` + normalized payload hash for dedupe when available.

Return type:
- `TransactionRecord` (section 4.7).

### 4.2 `track_subscription`
Signature:
`track_subscription(service, amount, currency, frequency, next_renewal, status?, auto_renew?, payment_method?, account_id?, source_message_id?, metadata?) -> SubscriptionRecord`

Behavior:
- Creates or updates a subscription lifecycle record.
- Accepted `status`: `active`, `cancelled`, `paused`.
- Normalizes renewal timestamps into canonical date boundaries.

Return type:
- `SubscriptionRecord` (section 4.7).

### 4.3 `track_bill`
Signature:
`track_bill(payee, amount, currency, due_date, frequency?, status?, payment_method?, account_id?, statement_period_start?, statement_period_end?, paid_at?, source_message_id?, metadata?) -> BillRecord`

Behavior:
- Creates or updates bill obligations in `finance.bills`.
- Accepted `status`: `pending`, `paid`, `overdue`.
- `overdue` state is set automatically by scheduled checks when `due_date` has passed and status remains `pending`.

Return type:
- `BillRecord` (section 4.7).

### 4.4 `list_transactions`
Signature:
`list_transactions(start_date?, end_date?, category?, merchant?, account_id?, min_amount?, max_amount?, limit?, offset?) -> TransactionListResponse`

Behavior:
- Returns paginated transactions filtered by time window, category, merchant, account, and amount range.
- Sorted by `posted_at DESC` by default.

Return type:
- `TransactionListResponse` (section 4.7).

### 4.5 `spending_summary`
Signature:
`spending_summary(start_date?, end_date?, group_by?, category_filter?, account_id?) -> SpendingSummaryResponse`

Behavior:
- Aggregates outflow spending across a date range.
- Supported grouping: `category`, `merchant`, `week`, `month`.
- Excludes refunded/credit-direction entries unless explicitly requested by optional flags in future revisions.

Return type:
- `SpendingSummaryResponse` (section 4.7).

### 4.6 `upcoming_bills`
Signature:
`upcoming_bills(days_ahead?, include_overdue?) -> UpcomingBillsResponse`

Behavior:
- Returns bills due within the requested horizon (default: 14 days).
- Optionally includes already overdue obligations.
- Provides urgency classification (`due_today`, `due_soon`, `overdue`).

Return type:
- `UpcomingBillsResponse` (section 4.7).

### 4.7 Canonical Return Types

`TransactionRecord`:
- `id: uuid`
- `posted_at: timestamptz`
- `merchant: text`
- `description: text | null`
- `amount: numeric(14,2)`
- `currency: char(3)`
- `direction: text` (`debit` | `credit`)
- `category: text`
- `payment_method: text | null`
- `account_id: uuid | null`
- `receipt_url: text | null`
- `external_ref: text | null`
- `source_message_id: text | null`
- `metadata: jsonb`
- `created_at: timestamptz`
- `updated_at: timestamptz`

`SubscriptionRecord`:
- `id: uuid`
- `service: text`
- `amount: numeric(14,2)`
- `currency: char(3)`
- `frequency: text`
- `next_renewal: date`
- `status: text` (`active` | `cancelled` | `paused`)
- `auto_renew: boolean`
- `payment_method: text | null`
- `account_id: uuid | null`
- `source_message_id: text | null`
- `statement_period_start: date | null`
- `statement_period_end: date | null`
- `paid_at: timestamptz | null`
- `metadata: jsonb`
- `created_at: timestamptz`
- `updated_at: timestamptz`

`BillRecord`:
- `id: uuid`
- `payee: text`
- `amount: numeric(14,2)`
- `currency: char(3)`
- `due_date: date`
- `frequency: text`
- `status: text` (`pending` | `paid` | `overdue`)
- `payment_method: text | null`
- `account_id: uuid | null`
- `source_message_id: text | null`
- `metadata: jsonb`
- `created_at: timestamptz`
- `updated_at: timestamptz`

`TransactionListResponse`:
- `items: TransactionRecord[]`
- `total: integer`
- `limit: integer`
- `offset: integer`

`SpendingSummaryResponse`:
- `start_date: date`
- `end_date: date`
- `currency: char(3)`
- `total_spend: numeric(14,2)`
- `groups: [{ key: text, amount: numeric(14,2), count: integer }]`

`UpcomingBillsResponse`:
- `as_of: timestamptz`
- `window_days: integer`
- `items: [{ bill: BillRecord, urgency: text, days_until_due: integer }]`
- `totals: { due_soon: integer, overdue: integer, amount_due: numeric(14,2) }`

## 5. Persistence Contract

### 5.1 Core Tables (Base Contract)
Inherited: `state`, `scheduled_tasks`, `sessions`.

### 5.2 Domain Schema
All finance-domain tables live in schema `finance` in one-db topology (`[butler.db].name = "butlers"`, `[butler.db].schema = "finance"`).

#### 5.2.1 `finance.accounts`
Purpose: financial account registry for transaction/bill/subscription linkage.

| Column | Type | Constraints / Notes |
|---|---|---|
| `id` | `uuid` | Primary key, default `gen_random_uuid()` |
| `institution` | `text` | Not null |
| `type` | `text` | Not null; check: `checking|savings|credit|investment` |
| `name` | `text` | Nullable, user-friendly label |
| `last_four` | `char(4)` | Nullable; masked account identifier |
| `currency` | `char(3)` | Not null, default `USD` |
| `metadata` | `jsonb` | Not null, default `'{}'::jsonb` |
| `created_at` | `timestamptz` | Not null, default `now()` |
| `updated_at` | `timestamptz` | Not null, default `now()` |

Indexes:
- `idx_accounts_institution` on (`institution`)
- `idx_accounts_type` on (`type`)
- Unique recommended: (`institution`, `type`, `last_four`) where `last_four IS NOT NULL`

#### 5.2.2 `finance.transactions`
Purpose: immutable transaction ledger for spend and payment activity derived from alerts/receipts/statements.

| Column | Type | Constraints / Notes |
|---|---|---|
| `id` | `uuid` | Primary key, default `gen_random_uuid()` |
| `account_id` | `uuid` | Nullable FK -> `finance.accounts(id)` (`ON DELETE SET NULL`) |
| `source_message_id` | `text` | Nullable, source email/provider message ID |
| `posted_at` | `timestamptz` | Not null |
| `merchant` | `text` | Not null |
| `description` | `text` | Nullable |
| `amount` | `numeric(14,2)` | Not null |
| `currency` | `char(3)` | Not null |
| `direction` | `text` | Not null; check: `debit|credit` |
| `category` | `text` | Not null |
| `payment_method` | `text` | Nullable |
| `receipt_url` | `text` | Nullable |
| `external_ref` | `text` | Nullable provider transaction id |
| `metadata` | `jsonb` | Not null, default `'{}'::jsonb` |
| `created_at` | `timestamptz` | Not null, default `now()` |
| `updated_at` | `timestamptz` | Not null, default `now()` |

Indexes:
- `idx_transactions_posted_at` on (`posted_at DESC`)
- `idx_transactions_merchant` on (`merchant`)
- `idx_transactions_category` on (`category`)
- `idx_transactions_account_id` on (`account_id`)
- `idx_transactions_source_message_id` on (`source_message_id`)
- `idx_transactions_metadata_gin` using GIN (`metadata`)

Dedupe key recommendation:
- Unique partial index on (`source_message_id`, `merchant`, `amount`, `posted_at`) where `source_message_id IS NOT NULL`.

#### 5.2.3 `finance.subscriptions`
Purpose: recurring service commitments and lifecycle state.

| Column | Type | Constraints / Notes |
|---|---|---|
| `id` | `uuid` | Primary key, default `gen_random_uuid()` |
| `service` | `text` | Not null |
| `amount` | `numeric(14,2)` | Not null |
| `currency` | `char(3)` | Not null |
| `frequency` | `text` | Not null; check: `weekly|monthly|quarterly|yearly|custom` |
| `next_renewal` | `date` | Not null |
| `status` | `text` | Not null; check: `active|cancelled|paused` |
| `auto_renew` | `boolean` | Not null, default `true` |
| `payment_method` | `text` | Nullable |
| `account_id` | `uuid` | Nullable FK -> `finance.accounts(id)` (`ON DELETE SET NULL`) |
| `source_message_id` | `text` | Nullable |
| `metadata` | `jsonb` | Not null, default `'{}'::jsonb` |
| `created_at` | `timestamptz` | Not null, default `now()` |
| `updated_at` | `timestamptz` | Not null, default `now()` |

Indexes:
- `idx_subscriptions_next_renewal` on (`next_renewal`)
- `idx_subscriptions_status` on (`status`)
- `idx_subscriptions_service` on (`service`)

#### 5.2.4 `finance.bills`
Purpose: payable obligations with due-date status and recurrence.

| Column | Type | Constraints / Notes |
|---|---|---|
| `id` | `uuid` | Primary key, default `gen_random_uuid()` |
| `payee` | `text` | Not null |
| `amount` | `numeric(14,2)` | Not null |
| `currency` | `char(3)` | Not null |
| `due_date` | `date` | Not null |
| `frequency` | `text` | Not null; check: `one_time|weekly|monthly|quarterly|yearly|custom` |
| `status` | `text` | Not null; check: `pending|paid|overdue` |
| `payment_method` | `text` | Nullable |
| `account_id` | `uuid` | Nullable FK -> `finance.accounts(id)` (`ON DELETE SET NULL`) |
| `source_message_id` | `text` | Nullable |
| `statement_period_start` | `date` | Nullable |
| `statement_period_end` | `date` | Nullable |
| `paid_at` | `timestamptz` | Nullable |
| `metadata` | `jsonb` | Not null, default `'{}'::jsonb` |
| `created_at` | `timestamptz` | Not null, default `now()` |
| `updated_at` | `timestamptz` | Not null, default `now()` |

Indexes:
- `idx_bills_due_date` on (`due_date`)
- `idx_bills_status` on (`status`)
- `idx_bills_payee` on (`payee`)
- `idx_bills_account_id` on (`account_id`)

#### 5.2.5 `finance.budgets` (Optional Future)
Purpose: category-period spending caps and progress tracking.

| Column | Type | Constraints / Notes |
|---|---|---|
| `id` | `uuid` | Primary key, default `gen_random_uuid()` |
| `category` | `text` | Not null |
| `period` | `text` | Not null; check: `weekly|monthly|quarterly|yearly|custom` |
| `period_start` | `date` | Not null |
| `period_end` | `date` | Not null |
| `limit_amount` | `numeric(14,2)` | Not null |
| `current_spend` | `numeric(14,2)` | Not null, default `0` |
| `currency` | `char(3)` | Not null, default `USD` |
| `metadata` | `jsonb` | Not null, default `'{}'::jsonb` |
| `created_at` | `timestamptz` | Not null, default `now()` |
| `updated_at` | `timestamptz` | Not null, default `now()` |

Indexes:
- `idx_budgets_category_period` on (`category`, `period`, `period_start`)
- `idx_budgets_period_window` on (`period_start`, `period_end`)

### 5.3 Data Integrity Rules
- All domain tables must maintain immutable `created_at` and monotonic `updated_at`.
- Financial amounts are stored as `NUMERIC(14,2)`; floating-point amount storage is prohibited.
- Currency must be ISO-4217 uppercase (`CHAR(3)`).
- Bill and subscription status transitions are forward-safe and auditable (no silent mutation without `updated_at`).
- Every record ingested from email should preserve source provenance in `source_message_id` and `metadata`.

## 6. Scheduled Tasks

### 6.1 Upcoming Bills Check
- **Schedule:** Daily at 08:00 local timezone.
- **Job name:** `upcoming_bills_check`
- **Behavior:** Query `upcoming_bills(days_ahead=14, include_overdue=true)`. Generate urgency-ranked summary and send through `notify`.

### 6.2 Subscription Renewal Alerts
- **Schedule:** Daily at 08:30 local timezone.
- **Job name:** `subscription_renewal_alerts`
- **Behavior:** Find active subscriptions renewing in <= 7 days. Produce reminder summary with service, amount, and renewal date.

### 6.3 Monthly Spending Summary
- **Schedule:** First day of month at 09:00 local timezone.
- **Job name:** `monthly_spending_summary`
- **Behavior:** Run `spending_summary` for prior calendar month grouped by category + merchant. Deliver top spend drivers and MoM delta highlights.

## 7. Classification Signals for Switchboard Routing

Switchboard should classify toward Finance Butler when sender, subject, and content cues cross finance confidence thresholds.

### 7.1 Sender-Domain Signals
Strong sender patterns:
- `*@chase.com`, `*@alerts.chase.com`
- `*@paypal.com`
- `*@amazon.com`, `*@amazonpay.com`
- `*@venmo.com`
- `*@wise.com`
- `*@stripe.com`
- `*@bill.com`
- `*@amex.com`, `*@americanexpress.com`

### 7.2 Subject-Line Signals
Strong subject patterns:
- `"Your receipt"`
- `"Payment confirmed"`
- `"Statement ready"`
- `"Your invoice"`
- `"Payment due"`
- `"Subscription renewed"`
- `"Price change notice"`
- `"Auto-renewal reminder"`
- `"Transaction alert"`

### 7.3 Body/Attachment Cues
- Currency amounts adjacent to merchant/payee language (`charged`, `paid`, `invoice`, `total`, `balance`).
- Due-date language (`due on`, `payment due`, `late fee`, `minimum payment`).
- Recurrence language (`renews on`, `every month`, `annual plan`).
- Financial attachments (`invoice.pdf`, `statement.pdf`, tax forms such as `1099`, `W-2`).

### 7.4 Routing Safety Rules
- Finance should win tie-breaks against General when explicit payment/billing semantics are present.
- Finance should not capture travel itineraries unless the primary intent is billing/refund/payment resolution.
- Ambiguous commerce/relationship messages should defer to Switchboard confidence policy and fallback routing contract.

## 8. Module Dependencies and Runtime Configuration

### 8.1 Required Modules
- `calendar` (due-date and renewal reminder scheduling support).
- `memory` (cross-email normalization and long-horizon spend/obligation context).

### 8.2 Recommended Modules
- `email` (finance-category ingestion source).

### 8.3 Example `butler.toml`

```toml
[butler]
name = "finance"
port = 40105
description = "Personal finance specialist for receipts, bills, subscriptions, and transaction alerts."

[butler.runtime]
model = "gpt-5-mini"
max_concurrent_sessions = 3

[runtime]
type = "codex"

[butler.db]
name = "butlers"
schema = "finance"

[butler.switchboard]
url = "http://localhost:40100/mcp"
advertise = true
liveness_ttl_s = 300
route_contract_min = "route.v1"
route_contract_max = "route.v1"

[modules.email]

[modules.calendar]
provider = "google"
calendar_id = "primary"

[modules.calendar.conflicts]
policy = "suggest"

[modules.memory]

[[butler.schedule]]
name = "upcoming-bills-check"
cron = "0 9 1 * *"
dispatch_mode = "job"
job_name = "upcoming_bills_check"

[[butler.schedule]]
name = "subscription-renewal-alerts"
cron = "15 9 1 * *"
dispatch_mode = "job"
job_name = "subscription_renewal_alerts"

[[butler.schedule]]
name = "monthly-spending-summary"
cron = "30 9 1 * *"
dispatch_mode = "job"
job_name = "monthly_spending_summary"
```

## 9. Dashboard API Contract (Target State)

Read-only endpoints for operator/user inspection:

| Endpoint | Purpose |
|---|---|
| `GET /api/finance/transactions` | Filtered transaction listing |
| `GET /api/finance/subscriptions` | Subscription registry and renewal dates |
| `GET /api/finance/bills` | Bill obligations with due-date status |
| `GET /api/finance/accounts` | Account metadata listing |
| `GET /api/finance/spending-summary` | Aggregated spend metrics |
| `GET /api/finance/upcoming-bills` | Near-term obligations + overdue surface |

Write mutations remain MCP-tool owned.

## 10. Observability and Safety Invariants
- Metrics must include ingestion volume, parse success/failure, classification confidence, and tool latency.
- Finance sessions should emit token/cost telemetry for summary workloads.
- PII-sensitive fields in metadata should be redacted in logs and traces.
- Alerting should trigger when overdue bill count increases rapidly or parse failure ratio crosses threshold.

## 11. Change Control Rules
- Any finance tool signature changes require matching updates in this role spec.
- Any schema changes require explicit migration planning and backward-compatibility notes.
- Classification signal changes must be coordinated with Switchboard routing policy docs.

## 12. Additional AI-Generated Ideas

The following ideas extend the normative spec above. They are non-normative proposals for consideration in future iterations.

### 12.1 Transaction Intelligence
- **Merchant normalization**: Auto-map raw merchant strings (e.g., "AMZN*MARKETPLACE WA" → "Amazon") using a learnable mapping table in `finance.merchant_aliases`. User corrections feed back to improve future classifications.
- **Auto-categorization**: Infer transaction categories from merchant + amount patterns using a category-rules engine. User-correctable overrides train future classifications via memory facts.
- **Anomaly detection**: Flag unusual transactions — abnormally large amounts for a merchant, first-time merchants, duplicate charges within short windows — and surface via `notify` with configurable sensitivity thresholds.

### 12.2 Spending Insights
- **Spending velocity alerts**: Proactive notification when daily or weekly spending pace significantly exceeds historical norms for the same period (e.g., "You've spent 2x your typical Tuesday by noon").
- **Recurring charge detection**: Auto-detect recurring transactions from patterns (same merchant, similar amount, regular intervals) and surface as potential untracked subscriptions for user confirmation.
- **Category trend analysis**: Month-over-month and year-over-year comparisons per category with percentage changes, trend direction, and notable movers in monthly digest.

### 12.3 Financial Planning
- **Savings goals**: Track progress toward user-defined savings targets with projected completion dates based on current pace and income patterns.
- **Net worth snapshots**: Periodic aggregate of account balances over time for wealth trajectory visualization. Requires optional `finance.account_snapshots` table.
- **Cash flow projection**: Forward-looking estimate of available funds based on known upcoming bills, subscriptions, and average income patterns over a configurable horizon.

### 12.4 Tax and Compliance
- **Year-end tax summary**: Auto-generate categorized summary of tax-relevant transactions (charitable donations, medical expenses, business deductions) with receipt linkage for documentation.
- **Receipt archival**: Proactive extraction and storage of receipt URLs/attachments linked to transactions for tax documentation, warranty tracking, and return eligibility windows.

### 12.5 Budget System (promotion from §5.2.5 Optional Future)
- **Budget alerts**: Real-time notifications when category spending approaches (80%) or exceeds budget limits, with configurable thresholds.
- **Rolling budgets**: Automatic budget period creation with optional unused-allowance carry-forward.
- **Budget vs. actual dashboard**: Visual comparison of budgeted vs. actual spending per category per period via dashboard API endpoint.

### 12.6 Multi-Currency
- **Currency conversion**: Automatic exchange rate lookup for cross-currency transactions with configurable base currency for unified reporting.
- **Travel spending isolation**: Group transactions by trip or location for travel expense tracking, with optional per-trip summary generation.

### 12.7 Integration Enhancements
- **Statement import skill**: Guided workflow for bulk importing transactions from bank/credit card CSV or OFX statement exports.
- **Financial health score**: Composite metric based on bill payment timeliness, savings rate, subscription churn, and budget adherence — surfaced in monthly digest.
- **Shared expense tracking**: Split transactions across household members with running balance tracking and settlement reminders.

## 13. Non-Normative Note
This document defines the target-state finance role contract. It may temporarily lead implementation while migrations/tools are staged, but implementation should converge to this specification.
