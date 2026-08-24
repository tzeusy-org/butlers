## 1. Schema

- [x] 1.1 `core_186`: `public.domain_events`, `public.butler_subscriptions`, `public.domain_event_deliveries`.
- [x] 1.2 Seed the one concrete pair: Finance subscribed to `travel.trip_booked`.

## 2. Core modules

- [x] 2.1 `butlers.core.domain_events`: event log + subscription + delivery-ledger reader/writer.
- [x] 2.2 `butlers.core.domain_event_wake`: subscriber-local deterministic task reconciliation (fire-and-forget, mirrors `delegation_wake` without the answer/digest round trip).
- [x] 2.3 `butlers.core_tools._domain_events`: `publish_event`/`subscribe_to_event`/`unsubscribe_from_event`/`list_my_subscriptions`/`receive_domain_event` MCP tools, `fan_out_event`, `publish_domain_event` (deterministic-caller convenience wrapper).
- [x] 2.4 Wire into `core_tools/_dispatcher.py`; add `domain_events` to `KNOWN_CORE_GROUPS` and Finance's `core_groups`.

## 3. Slice 1: Travel -> Finance wiring

- [x] 3.1 `record_booking` tracks `trip_created`/`trip_event_payload`; the MCP wrapper publishes `travel.trip_booked` on a new trip, best-effort.
- [x] 3.2 Unit tests (mocked pool) for every MCP tool closure and `fan_out_event`'s branches (self-skip, already-delivered, dispatch failure, task conflict, success).
- [x] 3.3 Real-Postgres integration test: migration shape, seeded subscription, publish -> fan-out -> subscriber wake creates exactly one `scheduled_tasks` row end to end, fan-out idempotence on retry, atomic per-(event, subscriber) claim, conflicting deterministic name fails closed on both the subscriber reconciliation and the publisher-side delivery ledger.

## 4. Contract and verification

- [x] 4.1 Add the `domain-event-bus` capability spec delta.
- [x] 4.2 Run `openspec validate --strict` on the changed specs.
- [x] 4.3 Run backend lint/format/targeted tests and a full non-e2e pytest pass.

## 5. Slice 2: Health consumer + dashboard subscription visibility (bu-317s5)

- [x] 5.1 `core_189`: seed Health standing-subscribed to `travel.trip_active`.
- [x] 5.2 `run_travel_context_producer` best-effort publishes `travel.trip_active`, memoized once per trip via the new `publish_domain_event_once` (`butlers.core_tools._domain_events`).
- [x] 5.3 `roster/health/AGENTS.md` documents the wake behavior (front-load medication prep using Health's own tools; no new hardcoded business logic, mirrors slice 1's Finance paragraph).
- [x] 5.4 `GET /api/domain-events/subscriptions` + `GET /api/domain-events/deliveries` (new `butlers.core.domain_events.list_recent_deliveries` reader) + `ButlerDomainEventsPanel` on the butler-detail Overview tab (subscriptions + recent deliveries, each independently fetched so a degraded source renders its own note, never a fabricated empty list).
- [x] 5.5 Unit + integration test coverage (mocked-pool unit tests, real-Postgres integration roundtrip, API router tests, frontend component tests).

## 6. Slice 3: Derived TTL'd advisories (bu-317s5)

- [x] 6.1 `publish_domain_event_once` (state-store-memoized at-most-once-per-window publish) added to `butlers.core_tools._domain_events`.
- [x] 6.2 Finance's `insight_scan` publishes `finance.budget_pressure` on a budget-threshold crossing (same dedup identity as the owner-facing candidate).
- [x] 6.3 Health's `insight_scan` publishes `health.recovery_state` (`compute_recovery_state`, pure/unit-tested) from the existing severity-floor-crossing symptom rows.
- [x] 6.4 Spec delta: "Derived TTL'd Advisory Events" requirement added.

## 7. Slice 4: Retire redundant context-bus producers (bu-317s5)

- [x] 7.1 Investigated whether any of the four `context_producers.py` producers (calendar, home-presence, travel, sleep-window) are subsumed by the new subscriptions. Conclusion: NONE are -- they serve a durable state-query need (`get_active_context`/`is_user_in_context`, read by the spawner preamble, the notify quiet-hours gate, and the attention ledger) a fire-once domain event cannot replace without a broader redesign of those read paths. No producer removed; documented in `proposal.md`.

## 8. Deferred (still reported as a follow-up, not implemented here)

- [ ] 8.1 A shared `domain-event-bus` skill -- the bar ("a second manual publisher adopts the primitives") means a second *agent-authored* MCP-tool-driven publisher, not a deterministic Python job calling `publish_domain_event`/`publish_domain_event_once` directly. That bar is not yet met.

## 9. Route-failure classification reliability (bu-j9bc7)

- [x] 9.1 Preserve route error text while emitting a structured literal-boolean
  retry classification, consume it through domain-event delivery with
  fail-closed malformed-signal handling and legacy-prefix compatibility only
  for unstructured envelopes, and cover bounded ledger reconciliation plus
  terminal route/target-tool failures.

## 10. Advisory validity semantics (bu-ac4yc)

- [x] 10.1 Decide and pin `valid_until` semantics for derived advisories: descriptive-only, never a
  delivery predicate. `butlers.core.domain_event_wake` documents why the bus must not filter on
  payload content, the wake prompt carries the freshness-recheck caveat outside the untrusted-data
  fence, and `tests/core/test_domain_event_wake.py` fails if either drifts.

