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
- Events at or below the instance's immutable per-source replay floor (the cursors given at
  registration, part of its boundary) are ignored: a rearm that passes the prior generation's
  cursors cannot re-fire on its event. The advancing `cursors` are high-water marks for
  receipts and resume only; within a generation latching makes reordering safe. Expiry wins: an
  instance at or past `expires_at` expires before any event or clock fire is considered.
- One fire intent exists per instance generation (`Instance.fire_key`); the instance row, the
  receipt and the intent commit in one store transaction. `FireIntent.event_id` is the
  causing event's UUID, or a stable synthetic UUID for registration and clock fires, so the
  runner builds `gen:<generation>:event:<uuid>` (`FireIntent.occurrence`) without inventing
  an id. `consume_fire` lets the first consumer claim it; the same consumer may re-read its
  claim after a crash.
- The store contract (`ConditionTx`): `insert_instance` returns `(row, inserted)`, never
  inferred from equality; `save_instance` is a version CAS; both and the unique `fire_key`
  raise or return the winner on a lost race (`StoreConflict`, retried by the service).
  `StoreContract` in `tests/conditions/test_conditions.py` is the suite a SQL store reuses.
- `resolve_clock` picks the owner clock, then the configured home zone, then UTC. There is no
  built-in zone default.
- A store outage raises `ConditionsUnavailable` and calls the optional `DependencyReporter`
  with `system.dep.conditions`; nothing is advanced or invented.

## SQL store (`butlers.conditions.sql`, core_323)

`PostgresConditionStore(pool)` implements `ConditionStore` over four `public` tables:
`condition_instances` (immutable boundary and replay floor, version CAS), `condition_cursors`
(high-water marks, never backward), `condition_receipts` (append-only) and
`condition_fire_intents` (unique `fire_key`, inserted only for a fired instance, claim set
once). `get_instance` is `SELECT .. FOR UPDATE`, so racing evaluators serialise and the loser
re-reads the winner's fired row; `insert_instance` is `ON CONFLICT DO NOTHING RETURNING`.

- Run it on a `butler_switchboard_rw` pool. Only that role is granted anything, and non-FORCE
  RLS (`current_user = 'butler_switchboard_rw'`) keeps every other role out even after an
  `init-db.sql` replay re-grants public DML. `db_authority/grants.py` records the grants.
- Serialisation failures, deadlocks and a lost CAS raise `StoreConflict` (the service retries);
  any other driver error raises `ConnectionError`, which the service reports as
  `system.dep.conditions` and surfaces as `ConditionsUnavailable`.
- JSON travels as text, so the store works with or without the repository jsonb codec.
- Rollback: `core_323` downgrade drops the tables only for the last installed schema chain and
  refuses while any instance exists, preserving receipts and intents.
- `tests/conditions/store_contract.py` is the one contract both stores pass; the PostgreSQL
  suite adds two-connection `FOR UPDATE`, crash/restart replay, the role fence and trigger
  guards.

## Deferred

- Producers feeding events (`.9` perception reader, goal ledger), the scheduler tick
  delivery, and the next-generation rearm (S2.2) belong to their owners.
- Watches and advanced rules stay with `bu-4c7nvp.1.5`.
