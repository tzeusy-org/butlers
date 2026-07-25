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
- [ ] 4.2 Run `openspec validate --strict` on the changed specs.
- [ ] 4.3 Run backend lint/format/targeted tests and a full non-e2e pytest pass.

## 5. Deferred (reported as follow-ups, not implemented here)

- [ ] 5.1 Second consumer: Health medication front-load on `travel.trip_booked`/an eventual trip-active event.
- [ ] 5.2 Subscription visibility on the dashboard.
- [ ] 5.3 Derived advisories (Finance `budget_pressure`, Health recovery-state) publishing as TTL'd events.
- [ ] 5.4 Retire redundant `context-bus` deterministic producers a subscription now subsumes.
