## ADDED Requirements

### Requirement: Durable Event Log

The system SHALL maintain `public.domain_events`, an append-only log of
every published domain event. `event_type` SHALL be an open, namespaced
string (`"<namespace>.<event>"`, conventionally the publishing butler's
name as the namespace) validated only against that shape -- not a fixed
enum -- so any butler can mint a new event type without a schema change.

#### Scenario: A valid publish is durably recorded

- **WHEN** a butler calls `publish_event` with `event_type="travel.
  trip_booked"` and a payload
- **THEN** exactly one `public.domain_events` row is inserted with that
  event type, the publishing butler's name, and the payload, and its `id`
  is returned as `event_id`

#### Scenario: An invalid event type is rejected before any write

- **WHEN** `publish_event` is called with an `event_type` that does not
  match `"<namespace>.<event>"` (e.g. missing the dot, uppercase characters)
- **THEN** the call returns `{"status": "error", ...}` and no row is
  inserted

### Requirement: Standing Subscriptions

The system SHALL maintain `public.butler_subscriptions`, one row per
`(subscriber_butler, event_type)` pair, with an `active` flag. A butler
manages only its own subscriptions via `subscribe_to_event` and
`unsubscribe_from_event`; there is no cross-butler subscription management.

#### Scenario: Subscribing is idempotent

- **WHEN** `subscribe_to_event` is called twice for the same
  `(subscriber_butler, event_type)` pair
- **THEN** exactly one row exists for that pair afterward, with
  `active = true`

#### Scenario: Unsubscribing deactivates rather than deletes

- **WHEN** `unsubscribe_from_event` is called for an active subscription
- **THEN** the row's `active` flag is set to `false` and the row is
  retained (not deleted)

#### Scenario: Unsubscribing from a subscription that never existed is a no-op

- **WHEN** `unsubscribe_from_event` is called for a `(subscriber_butler,
  event_type)` pair with no existing row
- **THEN** the call returns `{"status": "ok", "existed": false}` and no row
  is created

### Requirement: Atomic Per-Subscriber Fan-Out

Publishing an event SHALL dispatch it to every currently active subscriber
of its `event_type` via the Switchboard's `route()` primitive, recording
one `public.domain_event_deliveries` row per `(event_id, subscriber_
butler)` pair (`UNIQUE` constraint) as the atomic claim and outcome ledger.
A publishing butler SHALL NOT be dispatched to itself even if it is an
active subscriber of its own event type.

#### Scenario: Every active subscriber receives a delivery attempt

- **WHEN** `publish_event` is called for an `event_type` with two active
  subscribers
- **THEN** the fan-out result lists one delivery outcome per subscriber

#### Scenario: A subscriber is never fanned out to twice for the same event

- **WHEN** fan-out for one `(event_id, subscriber_butler)` pair is
  attempted more than once (a caller retry, or a future reconciliation
  sweep)
- **THEN** the second attempt observes the existing delivery row rather
  than inserting a second one, and a row already `delivered` is never
  re-dispatched

#### Scenario: A dispatch failure is recorded, not raised

- **WHEN** the Switchboard `route()` call for one subscriber's dispatch
  fails (unreachable, timeout, tool error)
- **THEN** that subscriber's delivery row records the original
  `error_message`, the publish call itself still returns `{"status": "ok",
  ...}` for the event that was durably recorded, and the failure is reported
  in that subscriber's delivery outcome according to the retry-classification
  requirement below

#### Scenario: The publisher is excluded from its own fan-out

- **WHEN** the publishing butler is itself an active subscriber of the
  `event_type` it just published
- **THEN** no delivery row is created for that butler and it is not
  dispatched to

### Requirement: Strict Delivery Retry Classification

The domain-event delivery ledger SHALL retry only a transient route failure.
Switchboard SHALL preserve the existing route-level `error` text and SHALL add
a literal boolean `retryable` classification to current route-error envelopes:
`true` only when it still has a concrete connection, transient OS, or timeout
exception, and `false` for every other current route error. The domain-event
route-result unwrap SHALL honor a literal boolean `retryable` signal when
present and treat a present non-boolean signal as terminal; for older route
envelopes without that signal, it SHALL retain compatibility only for the exact
legacy `ConnectionError:`, `OSError:`, and `TimeoutError:` prefixes.

Every other route-level failure, including unregistered/unknown tool,
registry lookup, authorization, validation/schema, configuration, and business
errors, SHALL be terminal. A target tool's own `{"status": "error", ...}`
response SHALL also be terminal, regardless of its message. Terminal failures
SHALL transition the delivery to `failed_permanent` without entering the
reconciliation retry selection. Retryable failures SHALL retain their original
error text, honor the existing backoff and maximum-attempt bound, and become
`failed_permanent` once that bound is exhausted.

#### Scenario: A structured nonlegacy transport error is retried

- **WHEN** Switchboard returns `{"error": "ClientConnectorError: connection
  refused", "retryable": true}` for a subscriber delivery
- **THEN** the original error text is stored, the delivery remains `failed`
  for the bounded reconciliation retry path, and a later eligible sweep
  redrives it

#### Scenario: A legacy transient envelope remains retryable

- **WHEN** an older Switchboard route envelope returns an error beginning with
  `OSError:` and carries no `retryable` field
- **THEN** the delivery remains retryable under the existing bounded retry
  policy

#### Scenario: Persistent route and target-tool failures are terminal

- **WHEN** a route envelope reports `RuntimeError` for an unknown tool,
  `LookupError` for a missing registry target, or a target tool returns
  `{"status": "error", ...}`
- **THEN** the delivery transitions to `failed_permanent` and is not selected
  for a reconciliation retry

#### Scenario: Retry exhaustion is terminal

- **WHEN** a retryable delivery reaches the configured maximum-attempt bound
- **THEN** the final failed attempt transitions it to `failed_permanent`, and
  later reconciliation sweeps do not dispatch it again

### Requirement: Subscriber-Local Wake Reconciliation

A butler receiving a fanned-out event via `receive_domain_event` SHALL
reconcile exactly one deterministically-named one-shot `scheduled_tasks`
row per `(event_id, subscriber_butler)` pair, in its own schema, using its
own connection pool. The task's prompt SHALL present the event's payload as
clearly fenced, untrusted reference data -- never as instructions -- and
instruct the receiving session to take whatever domain action applies or
exit silently.

#### Scenario: First delivery creates one task

- **WHEN** `receive_domain_event` is called for an `(event_id,
  subscriber_butler)` pair with no existing reconciled task
- **THEN** exactly one `scheduled_tasks` row is created, named
  deterministically from the event id and subscriber, and the result
  reports `state = "task_created"`

#### Scenario: Duplicate delivery reconciles to the same task

- **WHEN** `receive_domain_event` is called again for the same `(event_id,
  subscriber_butler)` pair after the task already exists with matching
  provenance
- **THEN** no second task is created; the same task id is returned and the
  result marks it `reconciled = true`

#### Scenario: A conflicting deterministic name fails closed

- **WHEN** a task already exists under the deterministic name for this
  `(event_id, subscriber_butler)` pair but its provenance footer is
  missing or names different provenance
- **THEN** the result reports `status = "conflict"`, the existing
  unrelated task is left untouched, and no second task is created

### Requirement: Dashboard Subscription Visibility

The dashboard API SHALL expose read-only discovery endpoints over
`public.butler_subscriptions` and `public.domain_event_deliveries` so a
butler's standing subscriptions and recent fan-out deliveries are
discoverable outside its own MCP session, mirroring
`GET /api/delegation/ledger`'s shape for `public.delegation_ledger`. A
query failure SHALL surface as an error response, never as a fabricated
empty result (the fleet-wide degraded-source honesty convention).

#### Scenario: Listing a butler's standing subscriptions

- **WHEN** `GET /api/domain-events/subscriptions?subscriber_butler=health`
  is called
- **THEN** the response lists every `(subscriber_butler, event_type)` row
  for `health`, active and inactive

#### Scenario: Listing recent deliveries to a butler

- **WHEN** `GET /api/domain-events/deliveries?subscriber_butler=finance`
  is called
- **THEN** the response lists `public.domain_event_deliveries` rows for
  `finance` joined with each delivery's `event_type`/`source_butler`/
  `occurred_at`, most-recent first, paginated

#### Scenario: A degraded read never renders as a truthful empty list

- **WHEN** the underlying query for either endpoint raises (a genuine
  failure, not "no rows")
- **THEN** the API returns an error response; the caller must not render
  this the same as a legitimately empty subscription or delivery list

### Requirement: Derived TTL'd Advisory Events

A deterministic, recurring producer publishing a derived advisory event (one carrying its own validity window in the payload, e.g. a `valid_until` field) SHALL publish at most once per distinct occurrence of the condition it advises on.

Not once per scan cycle for as long as the condition holds -- the producer uses the same state-store-memoized dedup-key discipline the deterministic context-bus producers already use for their own idempotence. This folds the "generalize the context bus to domain advisories" ecosystem idea into this bus instead of building a second parallel `public.domain_advisories` vocabulary/table alongside it.

#### Scenario: An ongoing condition is not re-published every scan cycle

- **WHEN** a deterministic producer re-evaluates a condition that already
  held during its previous run (same category/state, same window)
- **THEN** no new domain event is published for that run

#### Scenario: A crossing into a new window or a changed severity re-publishes

- **WHEN** the condition's dedup identity changes (e.g. a new budget
  period window, or an escalation from `"recovering"` to `"depleted"`)
- **THEN** a fresh domain event is published with the new payload

### Requirement: Descriptive-Only Advisory Validity

A validity window carried inside a domain event's payload (e.g. a `valid_until` timestamp on a derived advisory) SHALL be descriptive only. The fan-out, delivery-ledger, and subscriber-local wake paths SHALL NOT read it, and SHALL NOT skip, defer, expire, or otherwise vary a delivery because of it.

Such a field is a producer convention inside an open JSONB payload, not a bus column. Enforcing it would make a scheduling decision from publisher-supplied payload content -- exactly what the fenced, DATA-ONLY treatment of `payload` forbids -- and would silently strand deliveries for any butler that published an unrelated field of the same name. Delivery latency is in any case bounded by the retry ladder (stale-pending redrive, bounded failed-retry backoff) plus the ~1-minute wake, far inside the horizons these advisories declare.

Because the semantics are descriptive, the contract SHALL be stated where the consumer reads it: the subscriber-local wake prompt SHALL tell the waking session that the payload is a snapshot as of publication and that any validity window inside it must be re-checked against the current time before the session acts on it. That caveat is trusted bus text and SHALL sit outside the untrusted-payload fence.

#### Scenario: An advisory past its own validity window is still delivered

- **WHEN** a subscriber receives a fanned-out event whose payload carries a
  `valid_until` already in the past relative to the delivery clock
- **THEN** the wake task is reconciled and scheduled exactly as for any
  other event; no delivery is dropped, and the payload is embedded verbatim
  including the lapsed `valid_until`

#### Scenario: Payload content never steers the wake schedule

- **WHEN** two otherwise identical events are delivered, one carrying a
  lapsed validity window and one carrying a far-future window
- **THEN** both schedule the same wake, derived from the delivery clock
  alone

#### Scenario: The waking session is told to re-check freshness itself

- **WHEN** the wake task prompt is built for any fanned-out event
- **THEN** it states that the payload is a point-in-time snapshot and
  instructs the session to compare any validity window against the current
  time before acting, with that instruction placed outside the
  `<domain_event>` fence

### Requirement: Publisher-Owned Event Contracts

Every active event type SHALL have a contract owned by its publishing butler and declared in that butler's git configuration (`roster/<butler>/domain_events.toml`), carrying a schema version, a summary, the minimized set of payload fields (required and optional), a named retention policy, the exhaustive list of permitted subscriber butlers, and the reaction expectation plus the prose reaction contract the publisher intends.

The git declaration SHALL be the source of truth for admission. The `public.domain_event_contracts` table is a projection each butler materializes for its own declarations at startup so the fleet can read them; a failure to materialize SHALL narrow visibility only, never widen permission.

Admission SHALL fail closed in both directions. A publish of an undeclared event type, a publish under a namespace the caller does not own, a publish carrying a field the contract does not declare, and a publish missing a required field SHALL all be refused before any row is written. A subscription to an undeclared event type, or by a butler the publisher's `permitted_subscribers` does not name, SHALL be refused before any registration is written. An explicitly empty `permitted_subscribers` is a valid publisher policy meaning "no subscribers yet"; an omitted key is a malformed declaration and SHALL fail to load.

`reaction_expectation` and `reaction_contract` SHALL be infrastructure-only documentation of what the publisher hopes for. They SHALL NOT compel a subscriber to act: whether acting is correct remains the subscriber's own manifesto's decision, and Switchboard's amended contract stays infrastructure-only.

#### Scenario: An undeclared event type cannot be published

- **WHEN** a butler calls `publish_event` with a syntactically valid
  `event_type` that no `roster/<butler>/domain_events.toml` declares
- **THEN** the call returns `{"status": "error", ...}` naming the missing
  declaration, and no `public.domain_events` row is inserted

#### Scenario: An unpermitted subscriber cannot register

- **WHEN** a butler calls `subscribe_to_event` for an event type whose
  publisher's `permitted_subscribers` does not name it
- **THEN** the call returns `{"status": "error", ...}` and no
  `public.butler_subscriptions` row is written

#### Scenario: A payload outside the declared shape is refused

- **WHEN** a publish carries a field the contract declares neither required
  nor optional, or omits a required field
- **THEN** the publish is refused before any write, naming the offending
  field

#### Scenario: Admission does not depend on the projection

- **WHEN** `public.domain_event_contracts` is empty or stale for a
  publisher whose git declaration exists
- **THEN** admission still succeeds against the git declaration; the
  projection is a read surface, never the permission check

### Requirement: Reaction Lifecycle Receipts

Every delivery attempt SHALL be correlated with a reaction lifecycle in the append-only `public.domain_event_reactions` ledger, keyed by `(event_id, subscriber_butler)`, whose steps are `scheduled`, `running`, `acted`, `ignored`, `deferred`, `failed`, and `unreported`.

A lifecycle SHALL end in exactly one terminal step. That exactly-once property SHALL be a database invariant (a partial unique index over the terminal statuses), not a convention held only in application code.

A terminal step SHALL carry the runtime session id that produced it where one exists, and MAY carry typed evidence references (`task`, `session`, `event`, `delegation`, `memory`), each with a non-empty ref. Malformed evidence SHALL be refused before any write.

Success SHALL NOT be inferred. A wake task completing, an LLM process exiting zero, or a delivery reaching `delivered` SHALL NOT produce `acted`, `ignored`, or `deferred`: only the subscriber's own `report_event_reaction` call may claim those. The reconciliation sweep MAY write `running` for an in-flight wake and `unreported` for a wake that ended without a receipt, and SHALL write nothing else.

#### Scenario: A scheduled wake opens a lifecycle

- **WHEN** a subscriber-local wake task is created for a delivery
- **THEN** a `scheduled` reaction step is recorded for that
  `(event_id, subscriber_butler)` carrying the wake task name

#### Scenario: The subscriber closes its own loop

- **WHEN** a woken session calls `report_event_reaction` with `acted`,
  `ignored`, `deferred`, or `failed`
- **THEN** a terminal step is recorded carrying that session's id, and a
  second terminal step for the same `(event_id, subscriber_butler)` is
  refused

#### Scenario: A session cannot claim it was never asked

- **WHEN** a session calls `report_event_reaction` with `unreported`
- **THEN** the call is refused: `unreported` is a verdict the sweep records
  about a session, not a status a session may claim for itself

#### Scenario: A wake that ends silently is marked unreported, not acted

- **WHEN** a delivered wake's task has finished (or has aged past the
  orphan horizon) and no terminal receipt exists after the grace period
- **THEN** the sweep records `unreported`, and never `acted`

### Requirement: Transport And Domain Outcome Are Reported Separately

The delivery API and the dashboard SHALL present the transport status of a delivery and the domain outcome of the subscriber's reaction as separately labelled facts. `delivered` SHALL be presented as "a wake was scheduled", never as "the subscriber handled it".

A delivered wake with no terminal receipt SHALL be visibly distinguished from a delivery that has not yet been attempted. The full append-only trace for an event SHALL be reachable from the delivery surface by keyboard alone.

#### Scenario: A delivery row carries its reaction

- **WHEN** `GET /api/domain-events/deliveries` returns a delivery whose
  subscriber recorded a reaction
- **THEN** the response carries the latest reaction step (status, session
  id, note, timestamp) alongside, and distinct from, the delivery status

#### Scenario: A delivered wake with no receipt is not rendered as complete

- **WHEN** a delivery is `delivered` and no reaction step exists
- **THEN** the API reports no reaction and the panel labels it as
  unreported rather than leaving the row looking handled

#### Scenario: The trace is reachable without a pointer

- **WHEN** a reader tabs to a delivery row's trace control and activates it
  with the keyboard
- **THEN** the event's ordered reaction trace is revealed, with the control
  reporting its expanded state to assistive technology

### Requirement: Incomplete Receiver Responses Are Terminal, Not Silent

A fan-out whose target returns a structurally incomplete success payload (a `route()` result whose unwrapped body is not a mapping carrying a recognizable status) SHALL be classified as a non-retryable delivery failure with the receiver's response preserved verbatim in the error text, and SHALL NOT record any reaction receipt.

This is the shape observed in the live ledger for Travel to Finance: the receiver appeared present and configured, yet the delivery ended `failed_permanent`. Diagnosing it SHALL remain a read-only exercise; replaying, restarting, or otherwise mutating the live connector or runtime is a separate owner-authorized action and SHALL NOT be performed by any test, sweep, or worker.

#### Scenario: An incomplete success payload fails permanently and loudly

- **WHEN** `receive_domain_event` on the target returns a payload the
  unwrapper cannot read as a status
- **THEN** the delivery is marked `failed_permanent` with an error naming
  the target and the verbatim response, and no reaction row is written for
  that `(event_id, subscriber_butler)`

## Source References

- Non-Negotiable Rule 3 (MCP-only inter-butler communication through the Switchboard)
- RFC 0003 (Switchboard routing and ingestion)
