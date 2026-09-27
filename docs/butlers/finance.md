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

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes financial email and messages here
- [Travel Butler](travel.md) -- stores travel receipts as documents; Finance owns the ledger
