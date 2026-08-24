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
- (bu-317s5, slice 2) Second consumer: `butlers.jobs.context_producers.
  run_travel_context_producer` best-effort publishes `travel.trip_active`
  (memoized once per trip via `publish_domain_event_once`) when a trip
  transitions into its active window; Health is seeded (`core_189`) as a
  standing subscriber and reacts via the existing generic wake
  reconciliation -- no new hardcoded business logic, per this change's own
  design. Dashboard subscription visibility ships as
  `GET /api/domain-events/{subscriptions,deliveries}` plus a
  `ButlerDomainEventsPanel` on the butler-detail Overview tab.
- (bu-317s5, slice 3) Derived TTL'd advisories: Finance's `insight_scan`
  publishes `finance.budget_pressure` on a budget-threshold crossing (same
  dedup identity as its owner-facing candidate); Health's `insight_scan`
  publishes `health.recovery_state` (`"recovering"`/`"depleted"`) from the
  same severity-floor-crossing symptom rows the symptom-trend insight
  already reads -- privacy-minimized like `medication_travel_snapshot`
  (state/counts only, never the specific symptom names). Both use
  `publish_domain_event_once` for at-most-once-per-window publishing --
  folds the "derived-advisory read layer" ecosystem idea into this bus
  (TTL info lives in the event payload, not a second `public.
  domain_advisories` table).
- (bu-317s5, slice 4) Retiring redundant `context-bus` deterministic
  producers: investigated and NOT done. The four producers in
  `context_producers.py` (calendar, home-presence, travel, sleep-window)
  serve a durable *state query* need (`get_active_context`/
  `is_user_in_context`, consumed by the spawner preamble, the notify
  quiet-hours gate, and the attention ledger) that a fire-once domain
  event cannot replace without a broader redesign of those read paths.
  None is subsumed by the new subscriptions; no producer was removed.
- (bu-j9bc7) Route-level failures retain the legacy `error` string and current
  Switchboard envelopes carry a literal boolean `retryable` classification
  while Switchboard still has the concrete exception hierarchy. Domain-event
  delivery consumes that signal through its existing bounded retry ledger;
  route/configuration and target-tool failures remain terminal.
- (bu-6jv4m.8) Contract versioning and reaction receipts. Publishers now own
  a versioned, minimized contract per event type, declared in git at
  `roster/<butler>/domain_events.toml` (schema version, required/optional
  fields, retention policy, permitted subscribers, reaction expectation and
  contract). Publishes and subscriptions are admitted against those
  declarations and fail closed; `core_206`'s
  `public.domain_event_contracts` is a startup-materialized read projection,
  never the permission check. Every wake now opens an append-only lifecycle
  in `public.domain_event_reactions` and is expected to close through the
  new `report_event_reaction` tool -- the only path to `acted`/`ignored`/
  `deferred`/`failed`. Nothing infers success: a per-butler sweep on the
  scheduler loop may record only `running` and `unreported`. The API and
  `ButlerDomainEventsPanel` label the wake (transport) and the reaction
  (domain outcome) as separate facts, with a keyboard-reachable trace. New
  core modules: `butlers.core.domain_event_contracts`,
  `butlers.core.domain_event_reactions`,
  `butlers.core.domain_event_reaction_sweep`. The live Travel-to-Finance
  `failed_permanent` shape is pinned as a read-only regression test; no
  replay or runtime recovery is performed here.
- Deferred (still reported as a follow-up): a shared `domain-event-bus`
  skill. Not added in this change -- the new publish call sites (Travel's
  context producer, Finance's and Health's insight-scan jobs) are
  deterministic Python producers calling `publish_domain_event`/
  `publish_domain_event_once` directly, not a second *agent-authored*
  (MCP-tool-driven) manual publisher, so the "second manual publisher
  adopts the primitives" bar for adding the skill is not yet met.
