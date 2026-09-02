## Context

Finance has two different kinds of recurrence data:

1. **Inferred recurring charges.** `detect_recurring()` groups all active debit transactions by
   merchant, calculates intervals, sets `last_seen_date` to the latest charge date, and sets
   `next_expected_date` to that date plus the rounded median interval. The query does not select
   account or provenance and the merchant-unique upsert can combine sources.
2. **Tracked renewals.** `track_subscription()` accepts a caller-supplied `next_renewal`, optional
   `source_message_id`, and free-form metadata. The annual `subscription-renewal` candidate is a
   forward-looking reminder about that declared date; it is not an absence detector.

RFC 0029's landed implementation supplies connector and owner expected-signal producers, but its
`core_210` schema and helper are connector-type-only. The endpoint-bound contract below is
**[TARGET-STATE]** and remains owned by continued `bu-8cdl1.3`; this Finance specification does not
implement it. In that target state, a connector runtime is
identified by the exact `(connector_type, endpoint_identity)` pair in
`public.v_qa_connector_state`; `owner` is reserved for an explicitly owner-entered observation.
Finance must preserve the server-derived source endpoint and establish that full authority before
any elapsed recurrence date can mean absence.

## Goals / Non-Goals

**Goals:**

- Inventory every live transaction/subscription writer and the provenance that survives storage.
- Define exactly when Finance can use an endpoint-bound `connector:gmail` or `owner`, and when it
  must use `unknown` so RFC 0029 yields `unmeasurable`.
- Bind derived recurring groups to the complete producer set of their contributing transactions.
- Preserve tracked-renewal declarations and existing proactive policies without inferring payment
  or cancellation state.
- Provide executable liveness/elapsed-time evidence without adding runtime behavior in this
  specification lane.

**Non-Goals:**

- Mapping SimpleFIN to Gmail, `owner`, or a guessed connector.
- Treating `source_message_id`, `transactions.source`, import metadata, or merchant matching as
  server authority.
- Adding a scheduled-job producer class to RFC 0029.
- Emitting a new message when a recurrence is absent.
- Inferring that a missing charge means paid, unpaid, cancelled, paused, or stopped.

## Decisions

### 1. Current writer and derivation inventory

| Path | Current write/derivation | Provenance retained | Authority result |
|---|---|---|---|
| Routed Gmail extraction | Finance runtime calls public `record_transaction()` / `track_subscription()` | Switchboard has server-derived `source_endpoint_identity`, but the writers retain only optional caller-supplied `source_message_id`; transaction `source` is still `manual`; free-form metadata | unprovable today; future attestation must preserve the exact endpoint and may map to `connector:gmail` |
| Conversational/manual tools | the same public writers | transaction `source=manual`; no server principal on transaction/subscription | unprovable today; future server-attested owner entry may map to `owner` |
| Subscription property-fact MCP writer | registered `track_subscription_fact` calls `facts.track_subscription_fact()` and supersedes a `scope=finance`, `predicate=subscription` property fact | caller-supplied `source_message_id` and metadata are copied into fact metadata | outside current `subscription_audit()`/renewal inputs; unmeasurable if a future reader consumes it without reserved attestation |
| Bulk/CSV import | `bulk_record_transactions()` loops through public `record_transaction()` | caller `source` becomes free-form `metadata.import_source`; transaction `source` remains `manual` | unmeasurable until server-attested owner provenance exists |
| SimpleFIN | deterministic `simplefin-sync` calls internal `_record_transaction(source="aggregator")` | `source=aggregator`, provider name/binding metadata, stable external ID, account `last_synced_at` | unmeasurable: this in-process scheduled job has no RFC 0029 connector heartbeat |
| API or bank-sync vocabulary | schema accepts `api` and `bank_sync` source labels | no current source-specific server attestation or liveness binding | unmeasurable |
| SPO backfill | direct transaction insert from historic Finance facts | `backfilled_from_fact_id`, optional copied `source_message_id`; database default source | unmeasurable; do not backfill authority |
| Split/correction paths | child transaction inserts copy values/metadata but omit authoritative writer origin | source defaults can replace original provenance | unmeasurable unless the future attestation is explicitly preserved and validated |
| `detect_recurring()` | groups debit transactions by merchant across accounts/sources | persists merchant, amount/frequency, `last_seen_date`, `next_expected_date`; no producer set | current recurring group is unmeasurable |
| `subscription_audit()` tracked row | combines declared subscription with a fuzzy latest merchant transaction | declared date plus a merchant-substring last charge; contributing transaction source is dropped | last-charge/renewal absence is unprovable |
| `subscription_audit()` detected row | reads `recurring_groups` | derived dates only | inherits the group's unmeasurable state |

The existing source fields remain useful for audit and deduplication. They are not upgraded into
expected-signal authority because every public tool can supply `source_message_id` and free-form
metadata, `record_transaction()` writes `source=manual` even for routed email, and a merchant group
can contain more than one account/source.

### 2. [TARGET-STATE] Reserved server attestation

Future adoption SHALL use a reserved, server-written provenance object that public MCP/API input
cannot set or override. For Gmail observations it preserves the exact server-derived
`source_endpoint_identity`; the expected signal carries the same value as required
`producer_endpoint_identity`. For transaction observations the attestation also identifies
`producer`, ingress/source kind, and the server writer. For subscription declarations it
additionally distinguishes the source of the declared schedule from the producer expected to
observe a charge.

The only currently supported expected-signal producer mappings are:

- server-attested Gmail ingress -> `connector:gmail` plus the exact Gmail
  `producer_endpoint_identity`;
- server-attested direct owner entry/import -> `owner`.

For `connector:gmail`, liveness is queried by both `connector_type='gmail'` and the exact
`endpoint_identity`. A healthy sibling endpoint cannot make a dead, stale, missing, or unreadable
endpoint measurable, regardless of row order. Owner-backed signals carry no endpoint identity.

The `owner` mapping means only that the ledger expected another explicitly owner-entered
observation. It never authorizes wording that a merchant failed to charge, a payment failed, or a
subscription changed state.

Alternative rejected: treat any non-null `source_message_id` as Gmail. The field is a public tool
argument and its documented meaning includes generic message/provider provenance.

### 3. Authoritative source matrix

The JSON block is the machine-readable planning contract used by
`tests/contracts/test_finance_recurrence_producer_mapping.py`.

<!-- finance-recurrence-producer-map:start -->
```json
[
  {
    "source_id": "gmail_transaction_attested",
    "record_kind": "transaction_observation",
    "evidence": "reserved server Gmail ingress attestation",
    "producer": "connector:gmail",
    "producer_endpoint_identity": "required:source_endpoint_identity",
    "mapping": "mapped",
    "kill_mode": "heartbeat"
  },
  {
    "source_id": "owner_transaction_attested",
    "record_kind": "transaction_observation",
    "evidence": "reserved server owner attestation",
    "producer": "owner",
    "producer_endpoint_identity": null,
    "mapping": "mapped",
    "kill_mode": "attestation"
  },
  {
    "source_id": "gmail_subscription_attested",
    "record_kind": "tracked_renewal_expectation",
    "evidence": "reserved server Gmail ingress attestation",
    "producer": "connector:gmail",
    "producer_endpoint_identity": "required:source_endpoint_identity",
    "mapping": "mapped",
    "kill_mode": "heartbeat"
  },
  {
    "source_id": "owner_subscription_attested",
    "record_kind": "tracked_renewal_expectation",
    "evidence": "reserved server owner attestation",
    "producer": "owner",
    "producer_endpoint_identity": null,
    "mapping": "mapped",
    "kill_mode": "attestation"
  },
  {
    "source_id": "current_email_message_id",
    "record_kind": "transaction_or_subscription",
    "evidence": "caller-supplied source_message_id without server ingress attestation",
    "producer": null,
    "producer_endpoint_identity": null,
    "mapping": "unmeasurable",
    "kill_mode": "none"
  },
  {
    "source_id": "current_manual_or_bulk",
    "record_kind": "transaction_or_subscription",
    "evidence": "source=manual or caller import_source without server owner attestation",
    "producer": null,
    "producer_endpoint_identity": null,
    "mapping": "unmeasurable",
    "kill_mode": "none"
  },
  {
    "source_id": "simplefin_aggregator",
    "record_kind": "transaction_observation",
    "evidence": "source=aggregator and provider=simplefin without connector heartbeat",
    "producer": null,
    "producer_endpoint_identity": null,
    "mapping": "unmeasurable",
    "kill_mode": "none"
  },
  {
    "source_id": "api_or_bank_sync",
    "record_kind": "transaction_observation",
    "evidence": "generic schema source label without exact registered producer",
    "producer": null,
    "producer_endpoint_identity": null,
    "mapping": "unmeasurable",
    "kill_mode": "none"
  },
  {
    "source_id": "legacy_backfill_or_split",
    "record_kind": "transaction_observation",
    "evidence": "copied or defaulted provenance",
    "producer": null,
    "producer_endpoint_identity": null,
    "mapping": "unmeasurable",
    "kill_mode": "none"
  },
  {
    "source_id": "current_recurring_group",
    "record_kind": "inferred_recurrence",
    "evidence": "derived dates without contributing producer set",
    "producer": null,
    "producer_endpoint_identity": null,
    "mapping": "unmeasurable",
    "kill_mode": "none"
  },
  {
    "source_id": "mixed_recurring_group",
    "record_kind": "inferred_recurrence",
    "evidence": "two or more contributing producers",
    "producer": null,
    "producer_endpoint_identity": null,
    "mapping": "unmeasurable",
    "kill_mode": "none"
  },
  {
    "source_id": "declared_renewal_date_only",
    "record_kind": "tracked_renewal_expectation",
    "evidence": "next_renewal declaration without observation producer",
    "producer": null,
    "producer_endpoint_identity": null,
    "mapping": "schedule_only",
    "kill_mode": "none"
  },
  {
    "source_id": "subscription_fact_writer",
    "record_kind": "subscription_property_fact",
    "evidence": "registered track_subscription_fact with caller source_message_id and metadata",
    "producer": null,
    "producer_endpoint_identity": null,
    "mapping": "outside_current_inputs",
    "kill_mode": "none"
  }
]
```
<!-- finance-recurrence-producer-map:end -->

### 4. Recurring-group producer resolution

Each `recurring_groups` signal derives its producer identity from every active debit transaction
that contributed to the group and from no other field. Connector identity is the pair of producer
and endpoint, so two Gmail endpoints are two producers for this purpose. Exactly one recognized,
server-attested pair maps through to the expected signal. An empty set, a source-less row,
unsupported source, copied provenance, or two or more producer/endpoint pairs yields `unknown`,
which RFC 0029 persists as `unmeasurable`.

The grouping key remains the existing merchant key in this change. Its breadth is a safety fact:
same-merchant transactions from different accounts or sources make the group mixed. The evaluator
MUST NOT use the newest row, first row, majority source, account `last_synced_at`, a healthy Gmail
runtime, or the generic transaction `source` column to break the tie.

`last_seen_date` remains the latest contributing transaction date and `next_expected_date` remains
that date plus the rounded median positive interval. Those dates describe the model's expectation;
they do not identify the source and do not imply a payment state.

### 5. Tracked renewal versus inferred recurrence

`subscriptions.next_renewal` is an explicit schedule declaration in the dedicated table. The
separate `track_subscription_fact` MCP writer produces a Finance property fact that current
`subscription_audit()` and renewal jobs do not read; it is outside current recurrence inputs, not a
second authority. If a future consumer begins reading those facts, their caller-controlled
`source_message_id` and metadata are unmeasurable until reserved server attestation is added.

The existing annual
`subscription-renewal` candidate (active yearly subscriptions within 14 days) may continue to use
that declaration because it says what is scheduled, not that an expected observation is missing.
The Finance dashboard may likewise display the declared date.

After the date passes, a missing matching transaction is an absence question. It may be evaluated
only when the subscription and relevant observations resolve to exactly one producer. A manual
declaration plus Gmail observations, a later update from another source, or a fuzzy merchant match
with unknown provenance is mixed/unprovable and therefore unmeasurable.

An inferred recurring group is never promoted to a tracked subscription, paid/cancelled state, or
missed-renewal claim. `subscription_audit()` may continue to label it `detected_untracked`, and
`predict_bills()` may continue to surface a future untracked pattern under the existing policy.

### 6. State and output matrix

| Source resolution | Producer evidence | Expected date | RFC 0029 state | Owner-facing absence output |
|---|---|---|---|---|
| Exactly one mapped connector endpoint | exact endpoint heartbeat healthy and current | not elapsed | `present` | none |
| Exactly one mapped connector endpoint | exact endpoint heartbeat healthy and current | elapsed | `absent` | none unless a separately approved existing policy consumes absence |
| Exactly one mapped connector endpoint | exact endpoint stale, dead/offline, unhealthy, missing, or unreadable; sibling health irrelevant | any | `unmeasurable` | none |
| Exactly one attested `owner` source | attestation valid | not elapsed | `present` | none |
| Exactly one attested `owner` source | attestation valid | elapsed | `absent` | none unless a separately approved existing policy consumes absence |
| Owner source | attestation missing/caller-asserted | any | `unmeasurable` | none |
| Missing, unsupported, mixed, or unprovable source | any | any | `unmeasurable` | none |
| Declared renewal date without observation producer | declaration valid | future 14-day window | not an absence signal | existing annual renewal reminder only |

No current approved Finance policy consumes an elapsed recurrence signal to say "missed renewal",
"merchant did not charge", "payment failed", "paid", "cancelled", or "stopped". Therefore a
healthy elapsed signal may become `absent` in the ledger but produces no new candidate or dashboard
verdict. Existing policies remain bounded to:

- a `subscription-renewal` candidate for an active yearly tracked subscription within 14 days;
- a `bill-predicted` candidate for an untracked regular payment predicted inside the next 30 days;
- observed price-change candidates only when an actual recent charge exists.

### 7. Signal keys and dashboard contract

The downstream adoption uses stable keys:

- `finance:recurrence:{recurring-group-id}` for inferred recurring groups;
- `finance:subscription-renewal:{subscription-id}` for tracked renewal expectations.

Connector-backed rows carry required `producer_endpoint_identity`; owner-backed rows carry null.
An unmeasurable signal is an ingestion/provenance condition. APIs and the Finance tab MUST NOT
render it as a missed charge, payment status, cancellation, stopped subscription, or calm complete
all-clear. The declared next-renewal date and detected-untracked label remain visible as their own
facts, separate from measurability.

## Risks / Trade-offs

- [Risk] Nearly every current Finance recurrence is unmeasurable. -> Mitigation: do not backfill;
  add reserved attestations at trusted writers before enabling absence evaluation.
- [Risk] SimpleFIN is the strongest continuous transaction source but lacks RFC 0029 connector
  liveness. -> Mitigation: keep it unmeasurable; a scheduled-producer extension requires a separate
  RFC and behavior tests.
- [Risk] Merchant grouping combines accounts/sources and suppresses useful signals. -> Mitigation:
  fail closed now; changing the grouping key is a separate recurrence-model decision.
- [Risk] A declared renewal reminder is accidentally gated as if it were absence. -> Mitigation:
  preserve the explicit future-date policy and gate only claims about a missing observation.

## Migration Plan and Ownership

1. Land this source mapping and RFC/OpenSpec contract without enabling runtime behavior.
2. **Continued bu-8cdl1.3** adds `producer_endpoint_identity` to the shared expected-signals
   schema/API and makes the helper and connector measurability query the exact type/endpoint pair.
3. **Continued bu-8cdl1.3** transitions existing Health call sites and rows: owner signals retain
   null endpoints; connector signals without server-provable endpoints become unmeasurable; any
   backfill requires exact source evidence and never guesses the sole/sibling endpoint or preserves
   the type-only fallback.
4. **Continued bu-8cdl1.3** adds migrated-PostgreSQL tests covering the schema migration, Health
   compatibility, exact endpoint liveness, and dead-A/healthy-B row-order invariance.
5. Later Finance apply work adds reserved server attestation to trusted Gmail and owner
   transaction/subscription entry paths, preserving server-derived `source_endpoint_identity` for
   Gmail.
6. Later Finance apply work derives the complete producer/endpoint set for each recurring group
   and tracked renewal expectation; keep the property-fact writer outside current inputs unless
   explicitly adopted.
7. Later Finance apply work persists the RFC 0029 tri-state under the defined signal keys.
8. Later Finance apply work adds migrated-PostgreSQL tests for endpoint-specific producer death,
   mixed sources, and elapsed dates before any consumer is allowed to read absence.

Rollback disables Finance expected-signal evaluation while retaining provenance. It MUST NOT fall
back to elapsed-time-only or inferred payment-state wording.
