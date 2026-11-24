## MODIFIED Requirements

### Requirement: Tick Handler
The `tick()` function SHALL query all due tasks (`enabled=true AND next_run_at <= now()`) ordered by `next_run_at`, dispatch each serially, and update `next_run_at`, `last_run_at`, and `last_result` for every task regardless of success or failure. A telemetry span `butler.tick` is created with `tasks_due` and `tasks_run` attributes.

Additionally, `tick()` SHALL perform three new evaluation passes:

1. **Deadline evaluation**: For each enabled task with `task_type='deadline'`, compute `days_remaining` and evaluate alert thresholds. Dispatch deadline tasks whose thresholds are newly satisfied.
2. **Event chain trigger detection**: Check deadline status transitions and threshold matches. Fire matching deadline event chains by materializing their actions as one-shot scheduled tasks. Historical calendar-end chains remain readable and removable, without querying calendar projection, marking an event or materializing work.
3. **Deferred notification flush**: Query `deferred_notifications` where `status='pending' AND deliver_at <= now()`, pass a solo row's stored envelope verbatim to the standard notify pipeline, and update a row to delivered only after successful delivery. The flush SHALL NOT re-evaluate approvals-policy quiet hours or context for a stored envelope; `deliver_at` is the durable admission decision. Existing same-target coalescing and pending-row retry behavior remain unchanged.

The tick span attributes SHALL include `deadlines_evaluated`, `chains_fired`, and `deferred_flushed` in addition to the existing `tasks_due` and `tasks_run`.

#### Scenario: Due tasks dispatched serially
- **WHEN** `tick()` is called and multiple tasks are due
- **THEN** each task is dispatched one at a time in `next_run_at` order

#### Scenario: Dispatch failure does not block other tasks
- **WHEN** one task's dispatch raises an exception
- **THEN** the error is captured in `last_result` as `{"error": "..."}` and the remaining due tasks continue dispatching
- **AND** the failed task's `next_run_at` is still advanced to the next cron occurrence

#### Scenario: Deadline evaluation runs each tick
- **WHEN** `tick()` is called
- **THEN** all enabled `task_type='deadline'` tasks are evaluated for threshold crossing
- **AND** newly satisfied thresholds trigger dispatch with deadline context

#### Scenario: Event chain detection runs each tick
- **WHEN** `tick()` is called
- **THEN** deadline status transitions and threshold matches are checked
- **AND** matching deadline event chains with `status='active'` are fired
- **AND** historical calendar-end chains remain inert without calendar projection reads or event marking

#### Scenario: Deferred notification flush runs each tick
- **WHEN** `tick()` is called
- **THEN** pending deferred notifications with `deliver_at <= now()` are delivered

#### Scenario: Stored owner-default envelope flushes without re-gating
- **WHEN** a due row was parked by an owner-default policy or context hold
- **THEN** the scheduler supplies its stored full envelope to the notifier
  without a second policy/context lookup
- **AND** a transport failure leaves the row pending for the next tick

#### Scenario: Seasonal context injected during dispatch
- **WHEN** `tick()` dispatches any task (cron or deadline)
- **AND** `get_active_seasons()` returns non-empty results
- **THEN** the dispatch context includes `active_seasons` metadata

#### Scenario: Migrated scheduler schema is required
- **WHEN** `tick()` runs against storage missing a required migrated column or table
- **THEN** the actual database fault remains visible instead of a schema probe, NULL projection or silent pass skip
- **AND** the scheduler does not reinterpret missing `task_type`, `until_at`, `max_token_budget`, `continuity`, deadline, event-chain or deferred-notification storage as a usable legacy schema
- **AND** current-schema claims, CAS, dispatch ordering, attention, continuity, quiet-window decisions, retry/coalescing and expiry remain unchanged

### Requirement: Task Continuity Ledger with Role-Fit Registration (Opt-In)
A recurring PROMPT-mode scheduled task MAY opt in to carrying forward what its previous run concluded, via a `continuity` boolean field (TOML `[[butler.schedule]]` entry, or `schedule_create`/`schedule_update` — PROMPT mode only; setting `continuity=true` with `dispatch_mode="job"` SHALL raise a `ConfigError`). `sync_schedules()` SHALL persist the field to `scheduled_tasks.continuity` (migration `core_229`).

When a session concludes, it MAY call the `carry_forward(task_name, content)` core tool to record its own conclusion. `record_carry_forward()` SHALL write to `public.task_continuity` in a transaction that archives (sets `is_live=false` on) any other live row for that `(butler_name, task_name)` before inserting the new live row, so at most one row is live per butler+task at a time. A repeated call within the same session (same `session_id`) SHALL update the existing row for that session rather than creating a duplicate (`ON CONFLICT (butler_name, task_name, session_id) DO UPDATE`).

The `carry_forward` core tool SHALL NOT be registered for staffer-typed butlers (`ButlerType.STAFFER`: concierge, messenger, switchboard, qa), matching the existing non-STAFFER scoping already drawn around other narrative/dispatch-adjacent core tools (e.g. `notify`, the `temporal` group). `continuity=true` is restricted to PROMPT-mode schedules, and staffer butlers' schedules are exclusively job-mode deterministic handlers; the tool would have no reachable caller there. This is a role-fit and reachability constraint, not a registered-handler budget decision. RFC 0002 Amendment 1 and RFC 0027 apply the 30-50 target to full definitions initially loaded into model context, not to the canonical registered set; presentation discovery, admission, and initial-schema-byte evidence remain outside the scheduler contract.

`tick()` SHALL, only for tasks with `continuity=true`, read the current live row via `fetch_live_carry_forward()` after preparing the dispatched prompt and append a continuity block to it:
- naming the previous run's `session_id`, `recorded_at`, and age, plus the recorded `content`, when a live row exists;
- the literal text "the last run recorded no carry-forward" when no live row exists for that task (never fabricated content).

Continuity lookup SHALL be fail-open, not fail-closed: any exception during lookup SHALL be caught and logged, and the tick SHALL proceed by omitting the continuity block rather than failing the dispatch. This is deliberately the opposite of the blind-spot preamble's fail-closed contract (see core-spawner spec, Blind-Spot Preamble Injection) — continuity is an additive convenience, and a task that has always run without it must keep running unaffected by a lookup failure.

Tasks that do not opt in (`continuity=false`, the default) SHALL see no behavior change: `tick()` SHALL NOT query `task_continuity` for them and SHALL NOT alter their dispatched prompt.

ID: REQ-core-scheduler-002
Source: RFC 0038 D5, D6 and D7
Scope: v1-mandatory

#### Scenario: Opted-in task with no prior run gets an honest empty state
- **WHEN** a `continuity=true` task fires for the first time (no live row in `public.task_continuity`)
- **THEN** the dispatched prompt includes a continuity block reading "the last run recorded no carry-forward"

#### Scenario: Opted-in task with a prior conclusion carries it forward
- **WHEN** a `continuity=true` task's previous run called `carry_forward(task_name, content)`
- **AND** the task fires again
- **THEN** the dispatched prompt includes a continuity block naming the previous session_id, recorded_at, age, and content

#### Scenario: New session supersedes the previous live row
- **WHEN** a task calls `carry_forward()` from a new session
- **THEN** the prior live row for that `(butler_name, task_name)` is archived (`is_live=false`)
- **AND** the new row becomes the sole live row

#### Scenario: Same-session retry deduplicates instead of superseding
- **WHEN** a session calls `carry_forward()` more than once with the same `session_id`
- **THEN** the existing row for that session is updated in place
- **AND** no additional row is archived

#### Scenario: Non-opted-in tasks are unaffected
- **WHEN** a task has `continuity=false` (the default) or omits the field
- **THEN** `tick()` SHALL NOT call `fetch_live_carry_forward()` for that task
- **AND** its dispatched prompt is byte-identical to the pre-continuity behavior

#### Scenario: Continuity lookup failure does not block dispatch
- **WHEN** `fetch_live_carry_forward()` raises for an opted-in task
- **THEN** the exception is logged and the tick proceeds
- **AND** the dispatched prompt omits the continuity block entirely (not a fail-closed placeholder)

#### Scenario: Staffer butlers do not receive the carry_forward tool
- **WHEN** a staffer-typed butler (`ButlerType.STAFFER`) registers its core tools
- **THEN** `carry_forward` is NOT among them
- **AND** this does not affect any other butler's tool registration

#### Scenario: Missing migrated continuity column is a storage fault
- **WHEN** `tick()` runs against a `scheduled_tasks` table missing `continuity`
- **THEN** the database error remains visible without a column probe or implicit opt-out
- **AND** migrated rows with `continuity=false` still dispatch without a continuity block

#### Scenario: The ledger is excluded from the nightly backup, by design
- **WHEN** the nightly `pg_dump` backup runs
- **THEN** `public.task_continuity` is excluded (its RLS is FORCEd, like `public.expected_signals`, and pg_dump cannot read a FORCE-RLS table as the table owner without `--enable-row-security`, which this backup deliberately never sets)
- **AND** a restored database has no live carry-forward rows
- **AND** the next run of any opted-in task reports "the last run recorded no carry-forward" rather than failing or fabricating a conclusion
