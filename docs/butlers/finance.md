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
| **Runtime** | codex (gpt-5.4-mini) |

## Schedule

Finance's intelligence schedules are deterministic `dispatch_mode="job"` handlers (no LLM prompt)
that propose insight candidates through the Switchboard insight broker rather than notifying directly;
delivery is subject to the owner's insight verbosity/budget/quiet-hours settings. The SimpleFIN bridge
is a separate deterministic ledger-sync job and does not use the broker.

| Task | Cron | Description |
|------|------|-------------|
| `insight-scan` | `0 7 * * *` | Spending anomalies (category-level), upcoming bills, budget thresholds, annual subscription renewals, and subscription price changes. |
| `bill-reconciliation-sweep` | `15 21 * * 0` | Runs `reconcile_bills()` (auto-settle matched bills), then surfaces auto-settled bills, ambiguous matches, and untracked recurring patterns as candidates. |
| `anomaly-insight-scan` | `0 21 * * *` | Per-transaction anomaly detection (amount outliers, new merchants, category velocity spikes), capped at 10 candidates/run. |
| `monthly-finance-digest` | `0 9 1 * *` | Consolidated monthly candidate: prior-month total spend, top 3 categories, budget status, and subscription audit summary. |
| `simplefin-sync` | `17 4 * * *` | Bounded, one-account SimpleFIN v2 ledger synchronization; it never sends a notification or starts an LLM session. |

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

## SimpleFIN Bridge v1

The daily `simplefin-sync` job remains a no-op until an owner stores the claimed
Access URL through the dashboard:

1. Open `/secrets`, choose **Add credential**, then **System secret**.
2. Enter key `SIMPLEFIN_ACCESS_URL`, paste the claimed Access URL as the value,
   leave category as `general`, and set target to `finance`.
3. Save the credential. Do not place the value in source control,
   configuration files, tickets, logs, shell commands, or chat.

The job resolves that Finance-local credential from the database only, with no
environment fallback. On the first successful request, it requires exactly one
remote account, validates the complete response, and creates one Finance
account using the remote connection/account labels, currency, and exact
non-secret `conn_id` plus `account_id` provider binding. It never guesses an
existing account from a display name. Later runs require that exact binding and
refuse ambiguous or malformed local bindings before HTTP.

Each daily run records only settled, posted transactions, carries
`source = "aggregator"` and limited provider provenance, and advances account
freshness only after a complete successful run. Missing credentials make no
request; revoked, timed-out, incomplete, or malformed upstream responses make
no transaction write and return only a sanitized degraded result. A successful
first run returns `account_created = true`; replayed provider IDs do not increase
the `recorded` count.

This v1 boundary is intentionally narrow: one automatically registered account,
first-run history limited to 90 days, a five-day retry overlap, no
pagination/cursors, balance storage, pending lifecycle, multi-account sync,
remote mutation, or deletion. To roll back, disable or remove the
`simplefin-sync` schedule and remove the Finance credential; existing imported
ledger rows and the provider-bound account remain available for audit.

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

## Recurrence and renewal source authority

Finance keeps three ideas separate: a predicted next charge, a declared renewal date, and proof
that the source which would observe a charge is working. `recurring_groups.last_seen_date` and
`next_expected_date` are calculated from transaction intervals; `subscriptions.next_renewal` is a
tracked declaration. None of those dates alone proves that a charge was missed, paid, cancelled,
paused, or stopped.

| Input | Target expected-signal producer | Current disposition |
|---|---|---|
| Server-attested Gmail transaction/renewal evidence | `connector:gmail` plus exact `producer_endpoint_identity` | measurable only while that exact endpoint is healthy; a healthy sibling never substitutes |
| Server-attested direct owner observation/declaration | `owner` | measurable only with server-derived owner attestation; semantics remain "not recorded by owner" |
| Current `source_message_id` or `source=manual` row | none | unmeasurable; both values can arise through public tool inputs/defaults |
| CSV/bulk, API/bank-sync, backfill, or split row | none | unmeasurable without reserved server attestation |
| SimpleFIN `source=aggregator` row | none | unmeasurable under RFC 0029 because the scheduled bridge has no connector heartbeat |
| Current or mixed `recurring_groups` row | none | unmeasurable until all contributing transactions prove exactly one producer |
| `track_subscription_fact` property fact | none | outside current subscription audit/renewal readers; unmeasurable if later consumed without reserved attestation |

Stale, dead/offline, unhealthy, missing, unsupported, mixed, or unreadable producer evidence is
`unmeasurable`. It must not produce "missed renewal", owner-behavior, or inferred payment-state
copy. A healthy elapsed signal can be recorded as `absent`, but no current Finance alert consumes
that state.

The existing dashboard and alert behavior stays narrower. An active yearly subscription with a
declared renewal inside 14 days may still show and emit the existing forward-looking
`subscription-renewal` candidate. An untracked regular payment predicted inside 30 days may still
produce the existing `bill-predicted` candidate. The Finance tab may show declared renewal dates
and `detected_untracked` patterns, but must not turn missing instrumentation into a missed charge,
payment result, cancellation, stopped-subscription verdict, or complete all-clear.

After runtime adoption of this contract, operator triage starts with the expected signal's exact
producer and `producer_endpoint_identity`, then liveness for that exact pair. Do not use a healthy
Gmail sibling endpoint for a dead account or for SimpleFIN/manual/imported evidence, and do not
infer authority from a message ID, merchant name, account freshness, or generic source label.

The Finance tab reads a state-only expected-signals surface. Instrumentation failure is rendered
as incomplete measurability and never as a missed renewal, failed payment, cancellation, or
all-clear. The registered `track_subscription_fact` property writer remains outside these readers.

## Verification

To confirm the Finance Butler's domain tables, scheduled tasks, and ingestion pipeline are working as described:

```bash
# 1. Confirm the butler is listening on the expected port
curl -s http://localhost:41105/health | python3 -m json.tool
# Expected: {"status": "ok", ...} with no error fields

# 2. Verify the four core finance domain tables exist in the finance schema
psql -h localhost -U butlers -d butlers -c \
  "SELECT table_name FROM information_schema.tables
   WHERE table_schema = 'finance'
   ORDER BY table_name;"
# Expected: accounts, bills, subscriptions, transactions (plus budgets for future use)

# 3. Confirm amount precision is NUMERIC(14,2) not float
psql -h localhost -U butlers -d butlers -c \
  "SELECT column_name, data_type, numeric_precision, numeric_scale
   FROM information_schema.columns
   WHERE table_schema = 'finance' AND table_name = 'transactions'
   AND column_name = 'amount';"
# Expected: data_type = 'numeric', numeric_precision = 14, numeric_scale = 2

# 4. Verify scheduled tasks are seeded from butler.toml
psql -h localhost -U butlers -d butlers -c \
  "SELECT name, cron, source, enabled FROM finance.scheduled_tasks ORDER BY name;"
# Expected: anomaly-insight-scan, bill-reconciliation-sweep, insight-scan, monthly-finance-digest,
# simplefin-sync
# all present with source='toml' and enabled=true

# 5. Confirm source_message_id deduplication is indexed for email provenance
psql -h localhost -U butlers -d butlers -c \
  "SELECT indexname FROM pg_indexes
   WHERE schemaname = 'finance' AND tablename = 'transactions'
   AND indexdef ILIKE '%source_message_id%';"
# Expected: at least one index covering source_message_id

# 6. Verify bills urgency classification works (pending/paid/overdue status values)
psql -h localhost -U butlers -d butlers -c \
  "SELECT status, COUNT(*) FROM finance.bills GROUP BY status;"
# Expected: rows show 'pending', 'paid', and/or 'overdue' — no unexpected status values
```

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes financial emails and messages here
- [Travel Butler](travel.md) -- handles travel-specific expenses; Finance tracks the broader financial picture
- [Messenger Butler](messenger.md) -- delivers financial alerts and summaries
