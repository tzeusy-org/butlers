# Finance Butler

> **Purpose:** Personal finance specialist that transforms financial email signals (receipts, bills, subscription notices, transaction alerts) into structured, queryable records.
> **Audience:** Contributors and operators.
> **Prerequisites:** [Concepts](../concepts/butler-lifecycle.md), [Architecture](../architecture/butler-daemon.md).

## Overview

The Finance Butler watches the user's financial email so they do not have to. Every receipt, invoice, renewal notice, and transaction alert is read, recorded, and structured into queryable domain records. By the time the user asks "What did I spend on restaurants last month?", the answer is already waiting.

The butler tracks three primary financial entities: **transactions** (individual payments and receipts), **subscriptions** (recurring service commitments with renewal lifecycle), and **bills** (payable obligations with due dates and urgency). It also maintains a financial account registry for linking records to specific accounts.

The Finance Butler does not offer investment advice, initiate payments, file taxes, or perform accounting-grade double-entry bookkeeping. It provides visibility, awareness, and reminders.

## Profile

| Property | Value |
|----------|-------|
| **Port** | 41105 |
| **Schema** | `finance` |
| **Modules** | email, calendar, memory, finance |
| **Runtime** | codex (gpt-5.1) |

## Schedule

| Task | Cron | Description |
|------|------|-------------|
| `upcoming-bills-check` | `15 21 * * 0` | Surface bills due in the next 14 days, ranked by urgency (overdue first, then due today, this week, within 14 days). Delivered via Telegram. |
| `subscription-renewal-alerts` | `20 21 * * 0` | Scan for subscriptions renewing within 7 days. Flag services where the user may want to review before auto-renewal. Delivered via Telegram. |
| `monthly-spending-summary` | `0 9 1 * *` | Monthly spending summary for the previous calendar month, grouped by category, with top spend drivers and month-over-month comparison. Delivered via Telegram. |

## Tools

**Transaction Recording**
- `record_transaction` -- Record a payment or receipt with merchant, amount, currency, category, payment method, and source provenance.
- `bulk_record_transactions` -- Batch-ingest up to 500 transactions with per-row validation and idempotency.
- `list_transactions` -- Query the transaction ledger with filters for date range, category, merchant, account, and amount bounds.

**Subscription Tracking**
- `track_subscription` -- Create or update a recurring service commitment (active, cancelled, paused) with renewal date and frequency.

**Bill Management**
- `track_bill` -- Record a payable obligation with payee, amount, due date, and status (pending, paid, overdue). Overdue status is set automatically by scheduled checks.
- `upcoming_bills` -- Surface bills due within a horizon (default 14 days) with urgency classification.

**Spending Analysis**
- `spending_summary` -- Aggregate outflow spending over a date range, grouped by category, merchant, week, or month.

**Calendar** -- Creates due-date reminders 3 days before bill payment and renewal reminders 7 days before subscription auto-renewal.

## Key Behaviors

**Email Ingestion.** The primary data source is financial email. The butler extracts structured data from receipts, invoices, statements, and subscription lifecycle notifications. `source_message_id` is always preserved for deduplication and audit provenance.

**Data Conventions.** Financial amounts use `NUMERIC(14,2)` (never floats). Currency is ISO-4217 uppercase three-letter codes. Timestamps preserve timezone information. Direction (debit/credit) is inferred from context.

**Proactive Pattern Detection.** When logging a transaction, the butler checks whether it matches a pattern suggesting an untracked subscription (same merchant, similar amount, recurring interval) and offers to create a subscription record.

**Switchboard Classification Signals.** Switchboard routes messages to Finance based on sender-domain signals (chase.com, paypal.com, amazon.com, stripe.com, etc.), subject-line signals ("Your receipt", "Payment confirmed", "Statement ready"), and body content cues (currency amounts near merchant/payee language, due-date language, recurrence language).

## Persistence

The finance schema contains four core domain tables:

- **`finance.accounts`** -- Financial account registry (institution, type, masked identifiers).
- **`finance.transactions`** -- Immutable transaction ledger with GIN-indexed JSONB metadata.
- **`finance.subscriptions`** -- Recurring service commitments with lifecycle status.
- **`finance.bills`** -- Payable obligations with due-date tracking and status transitions.

A `finance.budgets` table is defined for future implementation of category-period spending caps.

## Interaction Patterns

**Conversational logging.** Users say "Coffee and lunch at Blue Bottle, $23.50" via Telegram and the butler records the transaction with appropriate categorization.

**Email-driven ingestion.** Financial emails are routed by Switchboard, parsed for structured data, and recorded without requiring user interaction. The user is notified via Telegram for significant events (subscription renewals, statement arrivals).

**Spending queries.** Users ask "How much did I spend last month?" or "What are my active subscriptions?" and receive data-backed answers from the transaction ledger and subscription registry.

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes financial emails and messages here
- [Travel Butler](travel.md) -- handles travel-specific expenses; Finance tracks the broader financial picture
- [Messenger Butler](messenger.md) -- delivers financial alerts and summaries
