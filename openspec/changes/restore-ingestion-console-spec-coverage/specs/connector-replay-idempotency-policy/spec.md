# Connector Replay Idempotency Policy

## Purpose

Defines the per-channel replay-safety classification for ingestion events and
the batch, atomicity, and audit contract for the bulk-replay handler. Replay is
a privileged operator action that re-injects a previously filtered or errored
event back through the ingestion pipeline. Without a channel-aware safety gate,
replaying a non-idempotent channel (an inbound email that triggers a reply) can
cause user-visible side effects. This capability owns the safety
classification, the `replay_safe` registry flag, and the bulk-handler
contract. It complements `connector-replay-queue`, which owns the drain
mechanics, and `connector-filtered-events`, which owns the event rows.

## ADDED Requirements

### Requirement: Per-channel replay safety classification

The system SHALL classify each candidate event as replay-safe or
replay-unsafe, and SHALL fail closed: any condition under which safety cannot
be positively established SHALL classify the event as unsafe. The
classification SHALL be evaluated in this order, each step producing a block
reason a caller can act on:

1. `source_channel = 'email'` is unconditionally replay-unsafe, regardless of
   any registry flag.
2. A blank `endpoint_identity`, or an event whose connector resolves to no
   registry row, is unsafe — replay safety is not configured.
3. An event whose connector resolves to more than one registry row is unsafe —
   replay safety is ambiguous.
4. An event whose single matching registry row does not have `replay_safe` set
   to TRUE is unsafe. A NULL from an outer join counts as not-TRUE.
5. Otherwise the event is replay-safe.

Registry matching SHALL exclude soft-deleted and archived rows. The
classification SHALL be enforced twice — once in the pre-flight check that
gates the batch, and again inside the state-transition SQL — so a flag that
flips between the two cannot let an unsafe replay through. The transition SQL
SHALL take a share lock on the registry row so its value cannot change mid
transition. When the classification query itself fails, the surfaced
`replay_safe` value SHALL be `false` with a reason stating that safety could
not be confirmed.

#### Scenario: Email is replay-unsafe regardless of the registry

- **WHEN** an event with `source_channel = 'email'` is considered for replay
- **THEN** it is classified unsafe with a block reason naming email
- **AND** the connector's `replay_safe` flag is not consulted to override it

#### Scenario: Unresolvable connector is unsafe

- **WHEN** an event has a blank `endpoint_identity`, or its connector matches
  no live registry row
- **THEN** it is classified unsafe with a "not configured" reason

#### Scenario: Ambiguous connector match is unsafe

- **WHEN** an event's connector candidates match more than one live registry
  row
- **THEN** it is classified unsafe with an "ambiguous" reason

#### Scenario: Registry flag governs every other channel

- **WHEN** an event resolves to exactly one live registry row
- **THEN** it is replay-safe only if that row's `replay_safe` is TRUE

#### Scenario: Classification failure fails closed

- **WHEN** the replay-policy query raises
- **THEN** the surfaced `replay_safe` is `false` with a reason stating safety
  could not be confirmed
- **AND** no event is replayed on the strength of an unknown classification

#### Scenario: Policy is re-checked inside the transition

- **WHEN** a connector's `replay_safe` flips to FALSE between the pre-flight
  check and the state transition
- **THEN** the transition does not mark the event for replay
- **AND** the event is reported as a conflict rather than silently replayed

### Requirement: connector_registry.replay_safe column

The connector registry SHALL carry a column
`replay_safe BOOLEAN NOT NULL DEFAULT TRUE`. It SHALL be settable by migration
or by an operator update through the registry surface, and SHALL NOT be exposed
for end-user editing in the dashboard. The replay path SHALL read this flag for
every event it considers.

#### Scenario: Migration adds the column with a safe default

- **WHEN** the migration that introduces the column runs
- **THEN** `connector_registry.replay_safe` exists as `BOOLEAN NOT NULL
  DEFAULT TRUE`
- **AND** pre-existing rows carry `replay_safe = TRUE`

#### Scenario: Gmail is seeded to FALSE

- **WHEN** the follow-on seed migration runs
- **THEN** every `connector_registry` row with `connector_type = 'gmail'` and
  `replay_safe = TRUE` is set to FALSE
- **AND** re-running the migration is a no-op because the predicate no longer
  matches

#### Scenario: No other connector is seeded unsafe

- **WHEN** the migration chain completes
- **THEN** Gmail is the only connector type seeded to `replay_safe = FALSE`

### Requirement: Bulk replay concurrency contract

The bulk-replay handler SHALL cap a submission at 100 event ids and SHALL
reject an over-sized batch with HTTP 400 naming the submitted size and the
maximum, rather than silently truncating it — an operator must never believe
they replayed more than they did. Each accepted event SHALL be transitioned
individually with a guarded single-row `UPDATE` predicated on both the event's
current status and the replay-policy check, so a concurrent transition cannot
double-mark a row. The connector drain loop that consumes `replay_pending` rows
SHALL claim work with `FOR UPDATE SKIP LOCKED` within a single transaction, so
that it never blocks on, or steals, a row another drain is already handling.

#### Scenario: Over-sized batch is rejected, not truncated

- **WHEN** the handler receives more than 100 event ids
- **THEN** it returns HTTP 400 identifying the submitted count and the maximum
- **AND** no event is transitioned

#### Scenario: Batch at the cap is accepted

- **WHEN** the handler receives exactly 100 event ids
- **THEN** all 100 are considered, subject to the safety gate

#### Scenario: Malformed input is rejected

- **WHEN** `event_ids` is missing, not a list, empty, or contains a value that
  is not a UUID
- **THEN** the handler returns HTTP 400 and transitions nothing

#### Scenario: Drain claims rows without blocking

- **WHEN** the connector drain loop selects `replay_pending` rows
- **THEN** the select uses `FOR UPDATE SKIP LOCKED` inside one transaction
- **AND** rows locked by a concurrent drain are skipped rather than waited on

### Requirement: HTTP 409 on unsafe-channel replay attempt

The bulk-replay handler SHALL run a pre-flight classification over the whole
submitted batch before transitioning anything. If any event classifies as
replay-unsafe, the handler SHALL reject the entire batch with HTTP 409 and
SHALL NOT transition any event, so the operator's mental model of the batch
stays atomic. The 409 body SHALL identify each offending event by id, its
`source_channel`, and its block reason.

Once past pre-flight, per-event execution is not atomic: an event whose policy
changed since pre-flight, or whose current status is not replayable, SHALL be
reported as a per-event `conflict` inside an HTTP 200 response rather than
failing the batch.

#### Scenario: Mixed batch with one unsafe event

- **WHEN** a batch of ten events contains one classified unsafe
- **THEN** no event is transitioned
- **AND** the response is HTTP 409 identifying that event's id, channel, and
  reason

#### Scenario: All-safe batch proceeds

- **WHEN** every event in a batch classifies as replay-safe
- **THEN** each is transitioned toward `replay_pending` subject to its current
  status

#### Scenario: Late policy change reports a per-event conflict

- **WHEN** an event passes pre-flight but fails the in-transition policy check
- **THEN** the response is HTTP 200 with that event reported as `conflict`
- **AND** the other events in the batch are unaffected

#### Scenario: Missing event is reported, not fatal

- **WHEN** a submitted id matches no event
- **THEN** that entry is reported as `not_found` within the HTTP 200 response

### Requirement: Audit emission on bulk replay

Every replay decision SHALL be recorded in `public.audit_log`. Accepted events
SHALL be audited one entry per event with `action = 'ingestion.event.replay'`
and the event id as target — deliberately the same action string the
single-event endpoint writes, so bulk replays appear in replay history rather
than being invisible to it. A pre-flight batch rejection SHALL write one entry
with `action = 'ingestion.retry.bulk_reject'` whose target is the submitted id
list and whose note carries the unsafe-event detail. A per-event safety
rejection SHALL write `action = 'ingestion.event.replay_reject'` with the event
id and its channel. All audit writes SHALL be best-effort: a failure SHALL be
logged and SHALL NOT fail the request.

#### Scenario: Accepted event is audited under the replay action

- **WHEN** the bulk handler marks an event for replay
- **THEN** an audit entry is written with `action = 'ingestion.event.replay'`,
  the actor, the event id as target, and a note carrying the result and source

#### Scenario: Rejected batch is audited once

- **WHEN** the handler rejects a batch with HTTP 409
- **THEN** exactly one audit entry is written with
  `action = 'ingestion.retry.bulk_reject'` and a note naming the unsafe events

#### Scenario: Audit failure does not fail the replay

- **WHEN** an audit append raises
- **THEN** the failure is logged as a warning
- **AND** the replay result returned to the caller is unchanged

## Source References

- Non-Negotiable Rule 7 (transport is connector responsibility — replay safety
  is a property of the channel, decided at the ingestion boundary)
- RFC 0003 (Switchboard routing and ingestion)
- RFC 0017 (Owner routing safety and incident reconciliation)
