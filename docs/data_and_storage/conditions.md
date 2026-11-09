# Goal condition evaluator (dark)

`butlers.conditions` implements RFC 0038 S4 and `REQ-condition-evaluation-001/002`. It is
dark: no runner, scheduler, API or tool calls it yet. The goal runner (`bu-dhloi1.7`)
consumes `ConditionEvaluator.consume_fire`; it never evaluates predicates itself.

## Contract

- `predicate.validate` accepts only the closed AST: `decision`, `owner_input`,
  `effect_settled`, `at`, `transition`, and `all`/`any` (at most 16 leaves, depth two, 8 KiB).
  It mirrors `perception_private.valid_predicate`, so the dark `perception_bind` store and
  this evaluator accept the same predicates. There are no text expressions or model calls.
- `register` refuses before any write when a leaf names an identity or source outside the
  caller-derived `Grant`, or when expiry is not finite (at most 90 days). It evaluates the
  current `Snapshot`, so an already-true condition fires at registration (no lost wake).
- `evaluate` takes trusted `ConditionEvent`s and an explicit `now`. Leaves latch, so
  replay, duplicates and reordering are idempotent. Results are three-valued; an
  unmeasurable source is `unknown`, never `false`. Transition leaves ignore events that are
  late, retracted, retraction records, or older than 10 minutes. An `at` leaf crossed by a
  clock jump fires once (`fired:clock_late`) if the instance has not expired.
- One fire intent exists per instance generation (`Instance.fire_key`); the instance row, the
  receipt and the intent commit in one store transaction. `consume_fire` lets the first
  consumer claim it; the same consumer may re-read its claim after a crash.
- `resolve_clock` picks the owner clock, then the configured home zone, then UTC. There is no
  built-in zone default.
- A store outage raises `ConditionsUnavailable` and calls the optional `DependencyReporter`
  with `system.dep.conditions`; nothing is advanced or invented.

## Deferred

- The SQL `ConditionStore` (control-plane tables, durable cursors, outbox) is not part of this
  package. `InMemoryConditionStore` defines the transactional contract it must satisfy.
- Producers feeding events (`.9` perception reader, goal ledger), the scheduler tick
  delivery, and the next-generation rearm (S2.2) belong to their owners.
- Watches and advanced rules stay with `bu-4c7nvp.1.5`.
