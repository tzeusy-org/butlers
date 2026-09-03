# RFC 0029: Expected Signals and Honest Absence

**Status:** Accepted
**Date:** 2026-09-03

## Context

Elapsed time is not evidence of owner behavior when the instrument that would
produce an observation is unavailable. Existing gap detectors compare a last
observation with a cadence independently of connector liveness, allowing a dead
connector to produce claims such as "you have not logged a measurement."

## Decision

`public.expected_signals` is the shared deterministic ledger for expected
observations. A producer-owned key records the producer, expected cadence, last
observation, evaluation time, and exactly one state:

- `present`: the producer is measurable and the cadence has not elapsed;
- `absent`: the producer is measurable and the cadence has elapsed;
- `unmeasurable`: producer liveness or health cannot support an absence claim.

Connector producers
are written as `connector:<connector_type>` and carry a required
`producer_endpoint_identity` copied from server-derived source provenance. They
join the read-only `public.v_qa_connector_state` projection by the exact
`(connector_type, endpoint_identity)` pair. A connector is measurable only when
that exact registered runtime is healthy with a heartbeat inside the canonical
five-minute liveness window. A healthy sibling endpoint never substitutes,
regardless of row order. Missing, stale, offline, paused, degraded, errored, or
unreadable exact-endpoint liveness is `unmeasurable`. The special `owner`
producer covers explicitly owner-entered observations, has no external
instrument dependency, and carries no endpoint.

`core_211` adds the endpoint column and exact-pair liveness lookup. Legacy
connector rows without a server-proven endpoint are migrated to
`unmeasurable`; no sole/sibling endpoint is guessed and there is no type-only
compatibility fallback. Owner rows carry a null endpoint.

The table is globally keyed and row-level security binds updates to the runtime
role that first claimed the key. All roles may read the tri-state projection;
they may not overwrite another role's signal. The core migration serializes
database-global creation because the core chain runs once per target schema.

Gap detectors MUST persist the tri-state before proposing an owner-facing
candidate. They may propose absence only from `absent`; `unmeasurable` is an
instrument condition and suppresses owner-behavior wording. API failure returns
an explicit degraded envelope, and clients render instrument failure rather than
an empty all-clear.

## Initial adoption

Health measurement gaps are the first adopted detector. Their expected cadence
is the existing two-times-median warning boundary. Measurement provenance binds
Google Health and Home Assistant observations to their connector liveness;
purely manual histories use `owner`; unknown provenance is unmeasurable.
The boundary is inclusive: evaluation exactly at the expected timestamp is
`absent`. A history containing both connector producers is unknown and therefore
unmeasurable; row order never selects authority.

Finance recurrence and tracked-renewal absence use the mapping defined by
`finance-recurrence-producer-mapping` (bu-4gzka). A
server-attested Gmail source maps to `connector:gmail` plus the exact
server-derived `producer_endpoint_identity`; an explicitly server-attested owner
source maps to `owner`. `source_message_id`, generic transaction `source`,
import metadata, merchant matching, and account freshness are not producer authority. SimpleFIN
is an in-process scheduled sync without a connector heartbeat and remains
unmeasurable. A recurring group must resolve the complete set of contributing
transactions to exactly one producer; missing, unsupported, copied, mixed, or
unreadable provenance is unmeasurable.

The registered `track_subscription_fact` property-fact writer is outside current
Finance recurrence inputs: `subscription_audit()` and renewal jobs read the
dedicated subscription table. If a future consumer reads those property facts,
their caller-supplied message ID and metadata are unmeasurable until reserved
server producer and endpoint attestation is present.

`subscriptions.next_renewal` is a declared schedule, not evidence that a charge
was observed or missed. The existing forward-looking annual renewal reminder may
continue unchanged. No current Finance policy turns an elapsed expected signal
into missed-renewal, merchant-behavior, payment, cancellation, pause, or stopped
wording; a healthy elapsed signal may be `absent` in the ledger but has no implicit
candidate consumer.

Relationship stale-contact signals use the reviewed
`relationship-stale-contact-producer-mapping`: a reserved server attestation
binds one exact Gmail, Telegram user-client, or WhatsApp user-client endpoint,
or an authenticated owner observation. Missing, unsupported, legacy, tied,
mixed, conflicting, or unreadable provenance remains unmeasurable. Existing
stale-contact policy may consume only `absent`; this adoption adds no outreach
policy and never guesses a producer.

## Failure properties

- Liveness read failure fails closed to `unmeasurable`.
- Persistence failure aborts the detector; no unreceipted absence is emitted.
- Concurrent upserts converge on one key.
- An unavailable API query is not represented as an empty signal list.
- The ledger stores no measurement value or owner-facing message.
- The standard backup excludes the forced-RLS ledger rather than enabling row
  security or aborting the dump; source observations and liveness remain in the
  artifact, and detector evaluation rebuilds the projection after restore.

## Alternatives rejected

- Elapsed-time-only gaps: instrument failure impersonates owner behavior.
- Treating stale liveness as absent: absence of evidence is promoted to evidence.
- Guessing Relationship or Finance producers: their current provenance is not
  sufficient to bind one liveness source honestly.
