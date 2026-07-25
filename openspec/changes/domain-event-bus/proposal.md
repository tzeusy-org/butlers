## Why

Cross-butler interaction today is either one-shot pull (`delegate_ask`/
`delegate_answer`, the `cross-butler-delegation` capability -- one row per
question, answered once, pull-only per
`openspec/specs/cross-butler-delegation/spec.md:13-45`) or a frozen,
fixed-vocabulary read (`public.user_context`, the `context-bus` capability --
a closed 11-signal enum with hardcoded writers per
`openspec/specs/context-bus/spec.md:107-133`). Neither lets a butler say
"wake me when another butler's domain does X": Finance cannot stand a
subscription for "wake me when Travel books a trip"; Health cannot front-load
medication prep when a trip goes active. This move (2026-07-25 JARVIS
pursuit dossier, ranked move #10, `bu-ep4ks.10`) adds a durable publish/
subscribe log through the Switchboard, reusing the delegated-answer wake
plumbing that just landed (`butlers.core.delegation_wake`, bu-27dxl.5.2)
rather than opening a new side channel, so the fleet becomes reactive while
still honoring the MCP-only rule.

## What Changes

- Add `public.domain_events` (append-only publish log), `public.
  butler_subscriptions` (standing `(subscriber_butler, event_type)`
  registrations), and `public.domain_event_deliveries` (the atomic
  per-subscriber fan-out claim/outcome ledger, `UNIQUE (event_id,
  subscriber_butler)`) via `core_186`.
- `event_type` is an open, namespaced vocabulary (`"<butler>.<event>"`) with
  a light format check, not a fixed enum -- deliberately fixing the exact
  limitation `context-bus`'s `ContextSignal` enum has.
- Add fleet-wide (non-STAFFER) MCP tools: `publish_event`,
  `subscribe_to_event`, `unsubscribe_from_event`, `list_my_subscriptions`,
  `receive_domain_event`. Fan-out always goes through the Switchboard's
  existing `route()` primitive, mirroring `delegate_ask`/`delegate_receive`'s
  client-vs-self-delivery split.
- A subscriber reconciles its own bounded one-shot `scheduled_tasks` row per
  `(event_id, subscriber_butler)` (deterministic name, conflict-detected),
  mirroring `delegation_wake`'s reconciliation exactly but simpler: a domain
  event is fire-and-forget (no answer/digest round trip).
- Wire the one concrete pair end-to-end: Travel publishes
  `travel.trip_booked` when `record_booking` creates a brand-new trip
  container; Finance is seeded (migration data) as a standing subscriber.
  The subscriber's wake task hands its next session the event's fenced
  payload and lets the session decide the domain action (e.g. a pre-budget
  check) using its own existing tools -- no new hardcoded business logic.

## Capabilities

### New Capabilities

- `domain-event-bus`: the durable, open-vocabulary publish/subscribe log and
  its atomic per-subscriber fan-out/wake reconciliation.

## Impact

- New tables: `public.domain_events`, `public.butler_subscriptions`,
  `public.domain_event_deliveries` (`core_186`).
- New core modules: `butlers.core.domain_events`, `butlers.core.
  domain_event_wake`, `butlers.core_tools._domain_events`.
- Travel: `roster/travel/tools/bookings.py`'s `record_booking` gains a
  `trip_created`/`trip_event_payload` result field; the MCP wrapper
  (`roster/travel/modules/tools.py`) publishes `travel.trip_booked` on a new
  trip, best-effort (a bus hiccup never fails the booking itself).
- Finance: `roster/finance/butler.toml` gains the `domain_events` core
  group; Travel's `core_groups` is unset (all groups enabled), so no toml
  change was needed there.
- Deferred (reported as follow-ups, not implemented in this change): a
  second consumer (Health medication front-load on trip-active) and
  subscription visibility on the dashboard; derived advisories (Finance
  `budget_pressure`, Health recovery-state) publishing as TTL'd events;
  retiring redundant `context-bus` deterministic producers a subscription
  now subsumes.
