# Goal condition evaluator (dark)

`butlers.conditions` implements RFC 0038 S4 and `REQ-condition-evaluation-001/002`. It is
dark: no runner, scheduler, API or tool calls it yet. The goal runner (`bu-dhloi1.7`)
consumes `ConditionEvaluator.consume_fire`; it never evaluates predicates itself.

## Contract

- `predicate.validate` accepts only the closed AST: `decision`, `owner_input`,
  `effect_settled`, `at`, `transition`, the M7 leaves `expectation`, `routine_deviation`,
  `condition`, `mode`, `read_check` and `bundle` (REQ-owner-watches-001), and `all`/`any` (at
  most 16 leaves in total, depth two, 8 KiB of compact JSON). An `expectation_kind` must be a
  registered `expect.*` perception kind, a UUID must be canonical lowercase and hyphenated, an `at`
  instant must be RFC 3339 UTC (`Z` or `+00:00`, no leap second), and a `read_check` number must be zero (any
  spelling) or satisfy 10^-6 <= |v| <= 10^15 on its exact decimal value, and its jsonb text must be at
  most 40 characters. Effect statuses are distinct. The size bound is one measure in both
  validators: compact canonical JSON with each number as jsonb prints it (plain decimal, scale
  kept, no sign on zero). A string with a NUL or a lone surrogate is refused, a cyclic document is
  refused (not hung), and `predicate.loads(text)` reads numbers as `Decimal`, so a literal such as
  `9e-1000` is seen as the non-zero value it is. The validator raises only its own error, whatever the document holds.
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
  unmeasurable source is `unknown`, never `false`. Transition leaves and the M7 leaves (each an
  occurrence of a typed row, never snapshot-witnessed) ignore events that are
  late, retracted, retraction records, or older than 10 minutes. A transition event stamped more
  than 60 seconds ahead of `now` is ignored, not refused, and does not move the cursor: a
  batch of trusted events cannot be rejected whole, and a redelivery counts once the clock
  reaches it. A `read_check` leaf compares one declared field of a trusted typed read against a
  literal (`eq`/`ne` on a number, boolean or string, `lt`/`le`/`gt`/`ge` on a number); a value of
  another type, a non-finite number, an unmeasurable read or an event whose `trusted` flag is not
  set (the default) is `unknown`, never `true`, and a read
  result never authors a watch or rule. An `at` leaf crossed by a
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

## Owner watches (dark)

`butlers.watches` (REQ-owner-watches-003/004) builds the notify-only owner watch on this
evaluator and adds no second one. A watch is one evaluator instance (its own `goal_id`) approved on an
exact `WatchCard` whose digest binds the predicate, source and category boundary, grant digest,
delivery mode, lifetime (or current expiry) and version. Only a model proposes a card; mail,
source text and read-check output are evidence only. A spec has exactly `predicate`, `delivery` and `categories`; a
recipient, URL, template or action refuses. At most 50 watches are active, each expires 90
days after approval unless the owner approves a renewal card in the last seven (a renewal registers the next
generation, then cancels the old one), delivery defaults to `digest`, and `pause` (`never`,
disable) cancels the instance without deleting the watch, its receipts or anything it observed.
Every transition of one watch is serialized by a per-watch lock held across the read, the
condition side effects and the CAS write, in an undoable order: a new generation registers
before ACTIVE commits (and is cancelled if the write loses), and a pause or delete commits before
it cancels. A replayed approval repairs an interrupted transition (it never re-inserts or re-registers a
create, and an active watch's replay also stops the generation a crashed renewal left open), so ACTIVE implies a live
condition and PAUSED or DELETED implies none. A create, renew or resume approval is refused unless the live grant still has the digest and
sources on the card. A resume, an update or a delete needs a card; a replayed approval returns the committed watch.
Two stores implement `WatchStore`: `InMemoryWatchStore` and `PostgresWatchStore` over
`public.owner_watches` (core_364). The table is Switchboard-only behind row-level security (the
core_323 posture), never deleted or truncated, with an immutable boundary, a version that rises
by one, expiry that moves forward only with a new generation, closed rows frozen, and a trigger
that backstops the cap of 50 active under one advisory lock. `WatchRunner` (`butlers.watches.runner`) is the loop an evaluator instance's single fire needs: a
tick claims a fired instance's intent, delivers one notice, then registers the next generation with
the fired one's cursors as its floor. Delivery is `InsightDelivery`, a proposal to the insight
broker keyed `owner_watch:<watch>:g<generation>` with a fixed, content-free line; `right_away` raises
the candidate's priority (70, against 40 for the digest) and never interrupts quiet hours. A failed
delivery leaves the watch fired and retried, never rearmed past a lost notice; a crash after delivery
replays to the same key and the same next generation. Each tick step holds the watch's transition
lock and re-reads the committed row and the live grant first, so a paused, deleted, expired or
re-granted watch sends nothing. Generation 1 may fire on the current snapshot; every later one (a
re-arm, a renewal, a resume or a repair) is registered with seeding off and the watch's last fire
cursor as its floor, so a condition that stays true fires again only on a new event past the
cursor, and an event that arrived during a pause still counts on resume. Delivery runs its duplicate check and insert on one
connection under a session advisory lock keyed by the dedup key, so racing runners queue one
candidate. Pause, delete and expiry withdraw the watch's pending, unclaimed candidates
(`InsightDelivery.withdraw`); a replayed close finishes an interrupted withdrawal, and every tick
(so also the first one after a restart) sweeps the pending candidates of all paused, deleted and
expired watches. The owner approval card rides the approvals spine (`butlers.watches.approval`): a proposed card parks
one approval-gated pending action for the Switchboard tool `owner_watch_apply_card`, whose call is the
card's canonical JSON and digest. The action id derives from the digest, so a replayed proposal parks
once. Approve replays the call, which refuses anything that is not byte-for-byte the card Switchboard
parked (stored in the Switchboard `state` table in the same transaction) and then approves that exact
card as the owner through `WatchService.approve`. Reject or expiry changes nothing. Applying needs a
service and a snapshot provider that production does not wire yet, so until the goal-runtime cutover
the apply refuses with `owner_watches_unavailable`. The `never` reply and the scheduled tick are a
later slice.
