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
  current `Snapshot`, so an already-true condition fires at registration (no lost wake). A retry
  of the same instance (same boundary) evaluates its fresher snapshot too: a leaf the snapshot
  shows `true` latches, the instance may fire once (`fired:registration`, same one intent), and
  nothing is ever downgraded or re-floored. A different boundary still refuses.
- `evaluate` takes trusted `ConditionEvent`s and an explicit `now`. Leaves latch, so
  replay, duplicates and reordering are idempotent. Results are three-valued; an
  unmeasurable source is `unknown`, never `false`. Transition leaves ignore events that are
  late, retracted, retraction records, or older than 10 minutes. A transition event stamped more
  than 60 seconds ahead of `now` is ignored, not refused, and does not move the cursor: a
  batch of trusted events cannot be rejected whole, and a redelivery counts once the clock
  reaches it. An `at` leaf crossed by a
  clock jump fires once (`fired:clock_late`) if the instance has not expired.
- Events at or below the instance's immutable per-source replay floor (the cursors given at
  registration, part of its boundary) are ignored: a rearm that passes the prior generation's
  cursors cannot re-fire on its event. The advancing `cursors` are high-water marks for
  receipts and resume only; within a generation latching makes reordering safe. Expiry wins: an
  instance at or past `expires_at` expires before any event or clock fire is considered.
- Labels (RFC 0038 S1/S4): `register` takes the wait envelope's `Label` snapshot (required; a
  non-`Label` refuses). It is stored with the instance (`core_324`), is part of the immutable
  boundary. A fire joins labels (S4): the intent and its receipt carry the registered label
  joined with its evidence when the cause is the registration snapshot or an event
  (bu-dhloi1.305 PR5a). An event fire's evidence is the labels of the batch's events that hold
  each true non-clock leaf (`ConditionEvent.label`); a registration fire's is `Snapshot.label`.
  A true leaf held by no event, or by an unlabelled one, and an unlabelled snapshot join the
  unknown label (`{world}` in I and D, all six categories in C): S1 makes missing provenance
  unknown, so an unlabelled producer's fire is labelled as before. A clock (`at`) fire whose
  only true leaves are clock leaves conveys no source data and carries the registered label
  alone. A clock fire that also needs a data leaf latched earlier joins that leaf's evidence
  the same way, from the batch's events, else `unknown`, so the clock never launders an earlier
  batch's taint (`all(owner_input, at(T))` fed a forwarded reply fires exposed at T). No fire
  is ever narrower.
  The runner joins the intent's label into the epoch and inbox row it creates. In PostgreSQL the
  label is CHECK-validated (`public.condition_label_valid`) and immutable once written, and a
  receipt or fire intent whose label does not cover its instance's is refused (23514, an
  outage-path bug, never `RegistrationRefused`). Rows older than `core_324` took the unknown
  label.
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
- Instance and goal ids must be 1..128 characters; `register` refuses others for both stores. If
  the insert still trips one of those two id-length CHECKs, `RegistrationRefused` is raised, never
  an outage report. Every other constraint trip (intent fence, FK, unique) is a bug and stays on
  the `ConnectionError` outage path.
- JSON travels as text, so the store works with or without the repository jsonb codec.
- Rollback: `core_323` downgrade drops the tables only for the last installed schema chain and
  refuses while any instance exists, preserving receipts and intents.
- `tests/conditions/store_contract.py` is the one contract both stores pass; the PostgreSQL
  suite adds two-connection `FOR UPDATE`, crash/restart replay, the role fence and trigger
  guards.

## Transition events from the perception timeline (`butlers.conditions.transitions`, dark)

`transition_event(row)` maps one `public.perception_timeline` row (bu-dhloi1.9) to the
`transition` event a leaf on its kind waits for (bu-dhloi1.305 PR5c). Nothing calls it yet:
reading the timeline, feeding instances and lifting the `policy` refusal of `transition`
leaves wait on bu-jv1moa.27.

- Only a `class = 'transition'` row is an event; a record, occurrence or point is none. The
  event id is the row id, its source `perception:<kind>`, its time the row's `at`, its position
  the row's write time (`created_at`) in microseconds, and its data
  `{kind, subject, from, to}` from `subject_entity`, `from_state` and `to_state`.
- `late`, `retracted` and a retraction (`operation = 'retract'`) pass through as flags, which
  the evaluator never lets set a leaf.
- The event carries the row's stored label (`timeline_label`). Perception stores `world` or an
  opaque resolved principal reference (a UUID) in I and D; a reference becomes the S1 principal
  `external:<uuid>`. A row with no label, or a malformed or non-version-1 one, gives an
  unlabelled event, whose fire joins `unknown`, never `owner`.
- It accepts the timeline function's JSON (string times) and a driver row (datetimes, UUIDs,
  jsonb as text) alike.

## Deferred

- Feeding the perception reader's events through `transition_event`, and the scheduler tick
  delivery, belong to their owners. The goal ledger feed and the rearm are
  `GoalRunner.feed_conditions` and `goal__wait_again` (see `docs/runtime/goal-runner.md`).
- Watches and advanced rules stay with `bu-4c7nvp.1.5`.
