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
- **THEN** that subscriber's delivery row is recorded with `status =
  'failed'` and an `error_message`, the publish call itself still returns
  `{"status": "ok", ...}` for the event that was durably recorded, and the
  failure is reported in that subscriber's delivery outcome

#### Scenario: The publisher is excluded from its own fan-out

- **WHEN** the publishing butler is itself an active subscriber of the
  `event_type` it just published
- **THEN** no delivery row is created for that butler and it is not
  dispatched to

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
