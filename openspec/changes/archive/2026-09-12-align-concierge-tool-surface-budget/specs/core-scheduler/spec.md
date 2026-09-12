## ADDED Requirements

### Requirement: Task Continuity Ledger with Role-Fit Registration (Opt-In)
A recurring PROMPT-mode scheduled task MAY opt in to carrying forward what its previous run concluded, via a `continuity` boolean field (TOML `[[butler.schedule]]` entry, or `schedule_create`/`schedule_update` — PROMPT mode only; setting `continuity=true` with `dispatch_mode="job"` SHALL raise a `ConfigError`). `sync_schedules()` SHALL persist the field to `scheduled_tasks.continuity` (migration `core_229`).

When a session concludes, it MAY call the `carry_forward(task_name, content)` core tool to record its own conclusion. `record_carry_forward()` SHALL write to `public.task_continuity` in a transaction that archives (sets `is_live=false` on) any other live row for that `(butler_name, task_name)` before inserting the new live row, so at most one row is live per butler+task at a time. A repeated call within the same session (same `session_id`) SHALL update the existing row for that session rather than creating a duplicate (`ON CONFLICT (butler_name, task_name, session_id) DO UPDATE`).

The `carry_forward` core tool SHALL NOT be registered for staffer-typed butlers (`ButlerType.STAFFER`: concierge, messenger, switchboard, qa), matching the existing non-STAFFER scoping already drawn around other narrative/dispatch-adjacent core tools (e.g. `notify`, the `temporal` group). `continuity=true` is restricted to PROMPT-mode schedules, and staffer butlers' schedules are exclusively job-mode deterministic handlers; the tool would have no reachable caller there. This is a role-fit and reachability constraint, not a registered-handler budget decision. RFC 0002 Amendment 1 and RFC 0027 apply the 30-50 target to full definitions initially loaded into model context, not to the canonical registered set; presentation discovery, admission, and initial-schema-byte evidence remain outside the scheduler contract and owned by `bu-ondtw`.

`tick()` SHALL, only for tasks with `continuity=true`, read the current live row via `fetch_live_carry_forward()` after preparing the dispatched prompt and append a continuity block to it:
- naming the previous run's `session_id`, `recorded_at`, and age, plus the recorded `content`, when a live row exists;
- the literal text "the last run recorded no carry-forward" when no live row exists for that task (never fabricated content).

Continuity lookup SHALL be fail-open, not fail-closed: any exception during lookup SHALL be caught and logged, and the tick SHALL proceed by omitting the continuity block rather than failing the dispatch. This is deliberately the opposite of the blind-spot preamble's fail-closed contract (see core-spawner spec, Blind-Spot Preamble Injection) — continuity is an additive convenience, and a task that has always run without it must keep running unaffected by a lookup failure.

Tasks that do not opt in (`continuity=false`, the default) SHALL see no behavior change: `tick()` SHALL NOT query `task_continuity` for them and SHALL NOT alter their dispatched prompt.

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

#### Scenario: Legacy schema without the continuity column
- **WHEN** `tick()` runs against a `scheduled_tasks` table that predates migration `core_229`
- **THEN** the column probe treats `continuity` as absent
- **AND** every task is treated as `continuity_enabled=False`, with no behavior change from before the ledger existed

#### Scenario: The ledger is excluded from the nightly backup, by design
- **WHEN** the nightly `pg_dump` backup runs
- **THEN** `public.task_continuity` is excluded (its RLS is FORCEd, like `public.expected_signals`, and pg_dump cannot read a FORCE-RLS table as the table owner without `--enable-row-security`, which this backup deliberately never sets)
- **AND** a restored database has no live carry-forward rows
- **AND** the next run of any opted-in task reports "the last run recorded no carry-forward" rather than failing or fabricating a conclusion

## REMOVED Requirements

### Requirement: Task Continuity Ledger (Opt-In)
**Reason**: The requirement's Concierge-specific rationale incorrectly treats RFC 0002's former 30-50 eager-presentation target as a registered-handler ceiling, contradicting accepted RFC 0002 Amendment 1 and RFC 0027.
**Migration**: Use Task Continuity Ledger with Role-Fit Registration (Opt-In) above, which preserves every existing continuity guarantee and scenario while grounding the staffer exclusion in role fit and reachability.
