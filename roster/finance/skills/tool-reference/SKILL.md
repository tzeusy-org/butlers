---
name: tool-reference
description: Detailed parameter documentation for finance butler MCP tools — consult when precise tool signatures are needed
version: 1.0.0
---

# Finance Tool Reference

Detailed parameter documentation for finance butler tools. The brief tool list in the butler's
system prompt is sufficient for most interactions. Consult this reference when you need precise
parameter names, types, or semantics.

## record_transaction

Record a payment or receipt.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `posted_at` | TIMESTAMPTZ string | Yes | When the transaction was posted. Use ISO 8601 with timezone. |
| `merchant` | string | Yes | Payee or merchant name. |
| `amount` | NUMERIC(14,2) string | Yes | Transaction amount. Never float — always a precise decimal string (e.g., `"23.50"`). |
| `currency` | string | Yes | ISO-4217 code (e.g., `"USD"`, `"EUR"`). Never default to USD without clear signal. |
| `category` | string | No | Spending category (e.g., `"dining"`, `"subscriptions"`, `"groceries"`). |
| `direction` | string | No | `"debit"` (default) or `"credit"` (refund/incoming transfer). |
| `payment_method` | string | No | Card or payment method label (e.g., `"Amex"`, `"Chase Sapphire"`). |
| `account` | string | No | Account label if known (e.g., `"Ally Savings"`, `"Chase Checking"`). |
| `source_message_id` | string | No | Email message ID or other source provenance. Used for deduplication — always pass when ingesting from email. |
| `metadata` | JSONB dict | No | Raw context or partial data that couldn't be fully parsed. Use to preserve provenance for future enrichment. |

**Notes:**
- Tool layer deduplicates on `source_message_id` — do not manually check for duplicates.
- `direction`: infer from context; refunds and incoming transfers are `"credit"`.

---

## list_transactions

Query the transaction ledger.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `start_date` | date string | No | Filter to transactions on or after this date. |
| `end_date` | date string | No | Filter to transactions on or before this date. |
| `category` | string | No | Filter by category. |
| `merchant` | string | No | Filter by merchant name (substring match). |
| `account` | string | No | Filter by account label. |
| `direction` | string | No | `"debit"` or `"credit"`. |
| `min_amount` | decimal string | No | Minimum transaction amount. |
| `max_amount` | decimal string | No | Maximum transaction amount. |
| `limit` | int | No | Max records to return (default: 50). |
| `offset` | int | No | Pagination offset. |

---

## track_subscription

Create or update a recurring service commitment.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `service` | string | Yes | Service name (e.g., `"Netflix"`, `"Spotify"`). Used as the unique key. |
| `amount` | NUMERIC(14,2) string | Yes | Recurring charge amount. |
| `currency` | string | Yes | ISO-4217 code. |
| `frequency` | string | Yes | Billing frequency: `"monthly"`, `"annual"`, `"weekly"`, `"quarterly"`. |
| `next_renewal` | date string | No | Date of next renewal. Compute from current charge date + frequency. |
| `status` | string | No | `"active"` (default), `"cancelled"`, or `"paused"`. |
| `auto_renew` | bool | No | Whether the service auto-renews without action. |
| `source_message_id` | string | No | Provenance for deduplication. |
| `metadata` | JSONB dict | No | Extra context (e.g., plan tier, promotional pricing). |

**Notes:**
- Upsert behavior: if `service` already exists, fields are updated with provided values.
- After creating/updating, create a calendar reminder 7 days before `next_renewal`.

---

## track_bill

Record a payable obligation.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `payee` | string | Yes | Who the bill is owed to. |
| `amount` | NUMERIC(14,2) string | Yes | Amount owed. |
| `currency` | string | Yes | ISO-4217 code. |
| `due_date` | date string | Yes | Payment due date. |
| `frequency` | string | No | `"one_time"` (default), `"monthly"`, `"annual"`, etc. |
| `status` | string | No | `"pending"` (default), `"paid"`, or `"overdue"`. |
| `paid_at` | TIMESTAMPTZ string | No | When payment was made. Required when setting `status="paid"`. |
| `source_message_id` | string | No | Provenance for deduplication. |
| `metadata` | JSONB dict | No | Raw context for partial data. |

**Notes:**
- Create a calendar reminder 3 days before `due_date` (configurable via user's `bill_reminder_preference` memory fact).

---

## upcoming_bills

Surface bills due within a time horizon.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `days_ahead` | int | No | Horizon in days (default: 14). |
| `include_overdue` | bool | No | Include past-due bills (default: true). |

Returns bills with urgency classification: `"overdue"`, `"due_today"`, `"due_soon"`, `"due_upcoming"`.

---

## spending_summary

Aggregate outflow spend over a date range.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `start_date` | date string | Yes | Period start (inclusive). |
| `end_date` | date string | Yes | Period end (inclusive). |
| `group_by` | string | No | Aggregation dimension: `"category"` (default), `"merchant"`, `"week"`, `"month"`, `"day"`. |

Returns grouped totals and a grand total.

---

## Module Tools

Tools provided by enabled modules (calendar, memory, email) are listed in the butler's tool list
at runtime. Key tools:

- **`calendar_create_event`**: Create calendar reminders for bills and renewals.
- **`memory_store_fact`**: Persist durable facts (preferences, patterns, anomalies). See the `butler-memory` skill for entity resolution protocol.
- **`memory_search`**: Retrieve facts by query.
- **`memory_recall`**: Recall facts about a specific topic or subject.
- **`notify`**: Send messages via user-facing channels. See the `butler-notifications` skill for required parameters.
