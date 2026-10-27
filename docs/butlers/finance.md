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

## Subscription Door Provenance

`finance_017` adds `cancellation_url_source` and `status_source` (`owner` or `extracted`) to
`subscriptions`. `track_subscription` derives the source from the server-held runtime routing
context, never a tool argument; a routed email, a missing context or an unreadable attestation is
`extracted`. An extracted write never overwrites an owner-set cancellation link or status, and
existing rows read as `extracted`. The renewal warning shows an extracted link as its registrable
domain, defanged (`example[.]com`) so no chat client can auto-link it, with "(link from an email, not verified)", carries no URL in its metadata
(`door_verified: false`), and an extracted `cancelled` or `paused` status still warns and asks the
owner to confirm. The domain is derived offline (last two labels, or three under a short list of
multi-label suffixes), so it is a display label, not an authorization input. Only the cancellation door is defanged; other free text in the message, such as the service name, is not (bu-svqpzo.17).

## Proposed Extracted-Field Safety

The current cancellation-door protection above is delivered. It does not protect `next_renewal`,
amount/currency or free-text service/payee contributions through later insight delivery. The active
[Finance extracted-field safety contract](../../openspec/changes/archive/2026-10-25-finance-extracted-field-safety/proposal.md)
records the security-maintenance successor authorized under existing authority by ROOT decision 3736. Its canonical specifications and archive are prepared in this PR; actual merge, post-merge implementation handoff and .16/.17 delivery remain pending. No runtime fix is claimed.

The governing model keeps raw stored values, server-derived origins, a baseline date and price pair,
and unresolved extracted claims. A later extracted date cannot suppress an earlier still-actionable
renewal warning before the SQL filter. An authenticated owner confirms exact supplied values through
the existing registered `track_subscription` tool; the GET-only Finance dashboard is not a new edit
or approval surface. Legacy origins stay extracted, and unsafe rollback must preserve authority and
claims or refuse atomically.

The governing rendering rule reuses the delivered canonical Messenger renderer. Owner-only,
independently unexposed output keeps ordinary rendering. A mixed-origin or independently exposed
message makes **all** final links inert, including an owner-set cancellation URL, while retaining
its raw stored value and `door_verified: true`. This is the explicit governing qualification to the
existing owner-door scenario, not a claim that current Finance direct notification already enforces
it. The actual broker delay/digest/retry and production notification bridge need implementation.
Successful in-place Telegram amendments are a distinct consumer: preserve the complete replacement
protection through canonical rendering, or refuse before provider execution and retain the union
through the existing safe fold path. Ordinary send protection does not establish protected edits.

See the [complete source, behavior and handoff matrix](../concepts/finance-extracted-field-safety.md)
for the .16/.17 packets, migration/race/rollback and final-render proof. Spec authoring changes no
runtime settings, providers, tables or notification behavior.

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes financial email and messages here
- [Travel Butler](travel.md) -- stores travel receipts as documents; Finance owns the ledger

### Subscription field claims and current warnings

The Finance 019 migration preserves the stored renewal and complete amount/currency
pair as a baseline. Extracted disagreements become deduplicated, unresolved field
claims with the available server-attested producer binding. They do not replace a
baseline or become owner confirmation through metadata or a message identifier.
Legacy origins default to `extracted`. The existing status and cancellation-door
protections still apply. The subscription read API exposes origins, epochs and
claims alongside the unchanged raw values; it adds no confirmation endpoint.

The existing registered `track_subscription` tool requires an explicit date and
complete amount/currency pair. An authenticated owner invocation commits those
supplied values and advances confirmation epochs, resolving older pending claims
without erasing their values. Attestation, logical-identity serialization, row
locking and writes share one transaction. Concurrent first inserts cannot create
two logical subscriptions. Ambiguous legacy identities stop the migration before
mutation. Downgrade locks both subscriptions and bills and refuses any surviving
row; an actually empty boundary can return to its predecessor without losing
history. The current writer fails closed against that predecessor's missing field
columns. No deployment, duplicate repair or migration execution is implied by source.

Annual renewal scans filter each baseline and unresolved date into the existing
owner-local today-through-14-days window before choosing the earliest date. The
selected date drives the obligation period, priority, stable key and expiry at the
following owner-local midnight. Elapsed claims cannot hide an actionable claim.
Reports and differing complete prices remain explicitly unverified; a currency
mismatch never causes conversion or a percentage comparison. The existing posted
transaction price detector retains its greater-than-five-percent, 60-day behavior.
Metadata-only next-price hints are presented as unverified reports.

Daily and urgent Switchboard jobs use one daemon-held, fixed-purpose currentness
reader. The private exchange binds the exact subscription UUID, selected native
date, confirmation epoch and current published boot/catalog to the real registered
Finance handler. MCP initialize and discovery carry a pending bearer only. They
cannot consume it or query Finance. The handler consumes the one-use claim before
its fixed owning read. There is no generic RPC, owner/fact impersonation, dashboard
key, cross-domain SQL exception or new grant. Missing publication, unsupported
routing, changed identity, replay, malformed input or outage produces UNKNOWN.
The normal broker preserves its as-of qualification; a known current supersession
withdraws the warning, including a confirmation committed during a hold or synthesis.

Each participating daemon owns the whole operation, including client exit,
acquisition, transaction, query and native pool release. One original five-second
absolute deadline bounds the caller. Incomplete cleanup stays retained and
quarantined across repeated cancellation; it cannot return a late success or allow
another read. A failed release/close is not healthy readmission. Shutdown revokes
admission first, joins both the existing Chronicler and Finance owners against the
same resource deadline, and defers the dependent module/pool tail once if necessary.
An unresponsive dependency remains reported incomplete with ownership retained.
Genuine PostgreSQL, deployment and combined-head hosted proof are separate from
software controls. Name-bearing output/edit handling remains the dependent .17
work; this substrate does not claim that delivery.
