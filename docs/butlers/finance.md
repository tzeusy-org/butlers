# Finance Butler

Turns financial email (receipts, bills, subscription notices, transaction alerts) and a bank feed
into a structured ledger of transactions, subscriptions, and bills. It provides visibility and
forward-looking reminders; it never gives investment advice, initiates payments, or keeps
double-entry books.

- **Identity and scope:** [`roster/finance/MANIFESTO.md`](../../roster/finance/MANIFESTO.md)
- **Required behavior:** [`butler-finance` spec](../../openspec/specs/butler-finance/spec.md)
  and the `finance-*` specs beside it
- **Schedules, modules, and port:** [`roster/finance/butler.toml`](../../roster/finance/butler.toml)

## SimpleFIN bank feed

The daily `simplefin-sync` job (a deterministic job: no LLM session, no notification) is a no-op
until the owner stores a claimed Access URL:

1. In the dashboard, open `/secrets`, choose **Add credential**, then **System secret**.
2. Enter key `SIMPLEFIN_ACCESS_URL`, paste the claimed Access URL as the value, leave category
   `general`, and set target `finance`.
3. Save. Never place the value in source control, config, tickets, logs, shell commands, or chat.

The job reads that credential from the database only, with no environment fallback. The first
fully validated response must contain exactly one remote account; the job creates one Finance
account bound to the provider's exact `conn_id` and `account_id` and never matches an existing
account by name. Later runs require that exact binding and refuse ambiguous or malformed bindings
before any request.

Each run records only settled, posted transactions with `source = "aggregator"`, and advances
account freshness only after a complete run. A missing credential makes no request; a revoked,
timed-out, incomplete, or malformed response writes nothing and returns a sanitized degraded
result. Replayed provider IDs are idempotent.

v1 limits: one account, 90 days of history on the first run, a five-day retry overlap, settled
transactions only, and no pagination, balance storage, multi-account sync, remote mutation, or
deletion. To roll back, disable or remove the `simplefin-sync` schedule and remove the credential;
imported rows and the provider-bound account stay for audit. Contract:
[`finance-simplefin-bridge` spec](../../openspec/specs/finance-simplefin-bridge/spec.md).

## Renewals and honest absence

A predicted next charge, a declared renewal date, and proof that the source which would observe
the charge is working are three different things; no date alone proves a charge was missed,
paid, or cancelled. Only server-attested evidence from an exact, healthy producer endpoint can
make a renewal measurable, and SimpleFIN, manual, imported, and mixed-provenance rows stay
unmeasurable. The Finance tab reads a state-only expected-signals surface and renders
instrumentation failure as incomplete measurability, never as a missed renewal, failed payment,
cancellation, or all-clear. The producer mapping lives in
[RFC 0029](../../about/legends-and-lore/rfcs/0029-expected-signals-and-honest-absence.md) and the
[`butler-finance` spec](../../openspec/specs/butler-finance/spec.md); see also
[Expected Signals](../concepts/expected-signals.md).

## Implementation Notes

- `roster/finance/tools/overview.py` subscription audit batches charge-date lookups with one
  `LEFT JOIN ... GROUP BY` and `COALESCE(MAX(CASE WHEN ... END), fallback)`; reuse that shape rather
  than per-parent queries.
- `roster/finance/tools/transactions.py` runs composite same-day dedup only with extra provenance
  (`account_id` or `source_message_id`); source-less manual rows stay distinct.
- `roster/finance/tools/facts.py::_TRANSACTION_PREDICATES` must stay a `list`: it is interpolated
  with `!r` into `ARRAY{...}::text[]`, and a tuple renders `ARRAY(...)`.
- Transaction ingestion has two paths with different dedupe: `POST /api/finance/transactions/bulk`
  writes facts directly (`tools/facts.py::bulk_record_transactions`, deduping `source_message_id`
  per predicate and hashing signed amounts, so opposite-sign imports of one event can persist as
  both debit and credit), while the MCP tool goes through `record_transaction` and mirrors to facts.
  Retries can leave soft-deleted ledger rows whose mirrored facts stay `active`; reconciliation
  retracts facts matching a deleted row on merchant, amount, currency, `posted_at` and direction.
- `merchant_mappings` columns are `raw_pattern`, `normalized_merchant`, `learned_from_count` and
  `source`; the legacy `merchant`, `merchant_pattern` and `sample_count` columns do not exist.

### Budget periods

Budget tools and the current CHECK accept `daily`, `weekly`, `monthly`, `quarterly`
and `yearly`. `yearly` denotes the annual January 1 through December 31 calendar
span; `annual` is an unsupported token. Bounds follow the owner's timezone, with
UTC fallback, and use half-open local-midnight windows, including 23/25-hour DST
days. Budget alerts and pressure evidence expire at the same window boundary.

`finance_016` widens the historical `finance_006` CHECK without rewriting any
budget row. Downgrade restores daily/weekly/monthly/yearly only when no quarterly
row remains, including inactive history. Otherwise it refuses without changing
data, the CHECK or the migration stamp; keep the repair installed until quarterly
history is explicitly resolved. The migration never deletes or converts history.

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes financial email and messages here
- [Travel Butler](travel.md) -- stores travel receipts as documents; Finance owns the ledger
