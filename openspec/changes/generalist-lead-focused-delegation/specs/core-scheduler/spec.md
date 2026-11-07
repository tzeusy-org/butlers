## MODIFIED Requirements

### Requirement: Dispatch Modes
Scheduled tasks SHALL support two dispatch modes: `prompt` (an obligation: when due, the daemon submits an obligation admission to the control plane) and `job` (sends a structured job name and optional arguments to a native, deterministic job function). Mode-specific constraints are enforced: prompt mode requires non-empty `prompt` and forbids `job_name`/`job_args`; job mode requires non-empty `job_name` and forbids `prompt`.

A prompt-mode admission SHALL carry the task's prompt as goal input, the occurrence identity (task name and due time), and the task's capability set: for a TOML task, the set declared for it in git, defaulting to owner-domain `read` and `record` plus owner `notify`; for a runtime-created task, the set recorded at its creation. The same SHALL apply when a deadline threshold, an event chain or a manual `schedule_trigger` dispatches a prompt-mode task, except that a `schedule_trigger` call made by a goal invocation SHALL be a caused admission bounded as the Schedule CRUD API requirement states. Prompt-mode dispatch SHALL NOT call the Spawner, and a job function SHALL NOT start a reasoning session.

Source: RFC 0038 D2, D5 and D18
Scope: v1-mandatory

#### Scenario: Prompt mode dispatch
- **WHEN** a due task has `dispatch_mode='prompt'`
- **THEN** the daemon submits an obligation admission with the task's prompt as goal input, the occurrence identity for `schedule:<task-name>`, and the task's capability set
- **AND** no Spawner session starts in the daemon

#### Scenario: Repeated submission of one occurrence returns the same goal
- **WHEN** the daemon resubmits the admission for an occurrence after a crash or retry
- **THEN** admission returns the existing goal for that occurrence instead of admitting a second one

#### Scenario: Job mode dispatch
- **WHEN** a due task has `dispatch_mode='job'`
- **THEN** the dispatch function is called with `job_name=<name>`, `job_args=<dict|None>`, and `trigger_source="schedule:<task-name>"`

#### Scenario: Invalid dispatch mode combination
- **WHEN** a task is created with `dispatch_mode='prompt'` but no prompt text
- **THEN** a `ValueError` is raised requiring a non-empty prompt

### Requirement: Schedule CRUD API
Runtime schedule management SHALL be exposed via `schedule_create`, `schedule_update`, `schedule_delete`, and `schedule_list`.

The CRUD API SHALL accept `task_type` as a parameter. When `task_type='deadline'`, deadline-specific fields (`target_date`, `lead_time_days`, `alert_thresholds`, `deadline_status`) are required on create and accepted on update. When `task_type='cron'` (default), existing behavior is unchanged.

Creating a prompt-mode task at runtime creates an obligation and SHALL be an `administer` capability. The created row SHALL record the creating goal and invocation from the verified broker assertion, a capability set no larger than the creator's grant at creation, and whether the creating invocation was exposed; an obligation created under exposure SHALL start every occurrence exposed. Updating a prompt-mode task's prompt or capability set SHALL apply the same bounds and SHALL record the updater's lineage and exposure.

Every other model-reachable scheduling tool that can cause, change or suppress an obligation occurrence SHALL also be an `administer` capability: `schedule_trigger`, and `schedule_update`, `schedule_delete` and `schedule_toggle` whenever the task's dispatch mode before or after the call is `prompt`. A `schedule_update` that changes a job-mode task to prompt mode is obligation creation and SHALL apply the creation bounds above. Because one tool cannot be classed per call in the catalog, `schedule_create` and these four tools SHALL be catalogued as `administer` outright (RFC 0038 D4: a tool takes the most consequential class among the effects it can reach), so a child never holds them, a third-party goal's ceiling excludes them, and an exposed call needs per-event owner approval; the `remind` tool keeps its own contract. A `schedule_trigger` call from a goal invocation SHALL be admitted as a caused admission carrying `caused_by_goal` and `caused_by_effect` from the verified assertion: the occurrence's goal SHALL inherit the triggering goal's exposure, its ceiling SHALL be the task's capability set intersected with the triggering goal's origin ceiling, and it SHALL count toward the causal chain limit (`REQ-capability-authorization-013`). A trigger from the owner's dashboard is a service-plane call and admits an ordinary obligation occurrence.

Source: RFC 0038 D4, D5, D7 and D8
Scope: v1-mandatory

#### Scenario: Create runtime schedule
- **WHEN** `schedule_create()` is called with valid parameters
- **THEN** a new row is inserted with `source='db'`, `enabled=true`, and computed `next_run_at`
- **AND** the new task's UUID is returned

#### Scenario: Create deadline via schedule API
- **WHEN** `schedule_create(task_type="deadline", target_date="2026-08-15", lead_time_days=42, alert_thresholds=[...])` is called
- **THEN** a deadline task is created with appropriate metadata
- **AND** `next_run_at` is computed based on the first alert threshold

#### Scenario: Runtime obligation is bounded by its creator
- **WHEN** a goal invocation's approved `schedule_create` call creates a prompt-mode task
- **THEN** the row records the creating goal and invocation, a capability set no larger than the creator's grant, and the creator's exposure
- **AND** when the creator was exposed, every occurrence of the task is admitted exposed

#### Scenario: An update cannot widen an obligation
- **WHEN** `schedule_update()` requests a capability set for a prompt-mode task that is larger than the updating invocation's grant
- **THEN** the update is refused and the task is unchanged

#### Scenario: Duplicate name rejected
- **WHEN** `schedule_create()` is called with an existing task name
- **THEN** a `ValueError` is raised

#### Scenario: A goal triggers a prompt-mode task
- **WHEN** an unexposed owner goal's approved `schedule_trigger` call fires a prompt-mode task whose declared set includes a capability the triggering goal's origin ceiling lacks
- **THEN** the occurrence is admitted as a caused goal whose ceiling excludes that capability
- **AND** the goal records the trigger call as its cause and counts toward the causal chain limit

#### Scenario: Converting a job task to prompt mode is obligation creation
- **WHEN** a goal invocation calls `schedule_update` to change a job-mode task it created into a prompt-mode task
- **THEN** the call is an `administer` effect, parked for per-event owner approval while exposed and refused to a child
- **AND** once applied, the task records the updater's lineage and exposure and a capability set no larger than the updater's grant

#### Scenario: A child cannot trigger an obligation
- **WHEN** a lead asks to delegate a child holding `schedule_trigger`
- **THEN** the broker refuses the delegation, because `schedule_trigger` is `administer`

#### Scenario: An exposed goal triggers a task
- **WHEN** an exposed invocation calls `schedule_trigger`
- **THEN** the call is parked for per-event owner approval
- **AND** once approved, the occurrence's goal starts exposed

#### Scenario: Update schedule fields
- **WHEN** `schedule_update()` is called with allowed fields
- **THEN** the specified fields are updated atomically
- **AND** if `cron` changes, `next_run_at` is recomputed
- **AND** if `enabled` is set to False, `next_run_at` is set to NULL

#### Scenario: Delete runtime schedule
- **WHEN** `schedule_delete()` is called for a `source='db'` task
- **THEN** the row is removed

#### Scenario: Cannot delete TOML schedule
- **WHEN** `schedule_delete()` is called for a `source='toml'` task
- **THEN** a `ValueError` is raised

### Requirement: Remind Tool
The `remind` MCP tool SHALL create one-shot scheduled tasks by generating a cron expression for a target time and setting `until_at` to auto-disable after firing. It supports `delay_minutes` (relative) and `remind_at` (absolute) timing with mutual exclusivity. When due, the task SHALL deliver the stored message through `notify()` deterministically, without starting a reasoning session. The task SHALL record the creating goal's lineage and exposure from the verified broker assertion; delivery SHALL remain subject to `core-notify`'s owner-recipient and approval rules, and a reminder created under exposure SHALL be delivered with link previews disabled and URLs rendered inert.

Source: RFC 0038 D2, D4 and D6
Scope: v1-mandatory

#### Scenario: Reminder created with delay
- **WHEN** `remind(message, channel, delay_minutes=60)` is called
- **THEN** a scheduled task is created with a cron matching `now + 60 minutes` and `until_at = target + 1 minute`
- **AND** when it fires, the stored message is delivered through `notify()` without a session or goal being started

#### Scenario: Invalid timing parameters
- **WHEN** both `delay_minutes` and `remind_at` are provided
- **THEN** an error response is returned

#### Scenario: Reminder created under exposure
- **WHEN** an exposed invocation calls `remind`
- **THEN** the task records that exposure
- **AND** its delivery disables link previews and renders URLs inert

### Requirement: Scheduled Task Complexity Field
Scheduled tasks SHALL accept and validate an optional `complexity` field for configuration compatibility. The field SHALL NOT select the model of an obligation's goal: the goal runner resolves a lead's model by role and its escalation ladder.

Source: RFC 0038 D16
Scope: v1-mandatory

#### Scenario: Complexity in TOML schedule
- **WHEN** a `[[butler.schedule]]` entry includes `complexity = "high"`
- **THEN** the value is validated and persisted with the task
- **AND** the obligation's lead model is resolved by role, not by this value

#### Scenario: Complexity default
- **WHEN** a `[[butler.schedule]]` entry omits the `complexity` field
- **THEN** the complexity defaults to `medium`

#### Scenario: Invalid complexity value
- **WHEN** a `[[butler.schedule]]` entry includes `complexity = "invalid"`
- **THEN** a `ValueError` is raised listing valid complexity values

#### Scenario: Complexity in scheduled_tasks DB table
- **WHEN** the `scheduled_tasks` table schema is defined
- **THEN** it includes a `complexity` column (text, nullable, default `'medium'`)

#### Scenario: Complexity in schedule CRUD
- **WHEN** `schedule_create()` or `schedule_update()` is called
- **THEN** the `complexity` field is accepted as an allowed parameter
- **AND** valid values are: `trivial`, `medium`, `high`, `extra_high`

### Requirement: Task Continuity Ledger with Role-Fit Registration (Opt-In)
A recurring PROMPT-mode scheduled task MAY opt in to carrying forward what its previous occurrence concluded, via a `continuity` boolean field (TOML `[[butler.schedule]]` entry, or `schedule_create`/`schedule_update` — PROMPT mode only; setting `continuity=true` with `dispatch_mode="job"` SHALL raise a `ConfigError`). `sync_schedules()` SHALL persist the field to `scheduled_tasks.continuity` (migration `core_229`).

When an occurrence's goal concludes, its lead MAY call the `carry_forward(task_name, content)` core tool through the broker to record its conclusion. `record_carry_forward()` SHALL write to `public.task_continuity` in a transaction that archives (sets `is_live=false` on) any other live row for that `(butler_name, task_name)` before inserting the new live row, so at most one row is live per butler+task at a time. The row SHALL identify the writing goal and invocation from the call's verified broker assertion in place of a spawner session identifier, and SHALL be tainted when the writing invocation was exposed. A repeated call within the same invocation SHALL update the existing row for that invocation rather than creating a duplicate (`ON CONFLICT (butler_name, task_name, session_id) DO UPDATE`, keyed by the writing invocation).

The `carry_forward` core tool SHALL NOT be registered for staffer-typed butlers (`ButlerType.STAFFER`: concierge, messenger, switchboard, qa), matching the existing non-STAFFER scoping already drawn around other narrative/dispatch-adjacent core tools (e.g. `notify`, the `temporal` group). `continuity=true` is restricted to PROMPT-mode schedules, and staffer butlers' schedules are exclusively job-mode deterministic handlers; the tool would have no reachable caller there. This is a role-fit and reachability constraint, not a registered-handler budget decision. RFC 0002 Amendment 1 and RFC 0027 apply the 30-50 target to full definitions initially loaded into model context, not to the canonical registered set; presentation discovery, admission, and initial-schema-byte evidence remain outside the scheduler contract.

`tick()` SHALL, only for tasks with `continuity=true`, read the current live row via `fetch_live_carry_forward()` after preparing the obligation admission and include a continuity block in the admission's goal input as stamped content:
- naming the previous occurrence's goal, `recorded_at`, and age, plus the recorded `content`, when a live row exists;
- the literal text "the last run recorded no carry-forward" when no live row exists for that task (never fabricated content).

A tainted live row SHALL make the occurrence's goal start exposed.

Continuity lookup SHALL be fail-open, not fail-closed: any exception during lookup SHALL be caught and logged, and the tick SHALL proceed by omitting the continuity block rather than failing the admission. This is deliberately the opposite of the blind-spot layer's fail-closed contract (see core-spawner spec, Blind-Spot Preamble Injection) — continuity is an additive convenience, and a task that has always run without it must keep running unaffected by a lookup failure.

Tasks that do not opt in (`continuity=false`, the default) SHALL see no behavior change: `tick()` SHALL NOT query `task_continuity` for them and SHALL NOT alter their admission's goal input.

ID: REQ-core-scheduler-002
Source: RFC 0038 D5, D6 and D7
Scope: v1-mandatory

#### Scenario: Opted-in task with no prior run gets an honest empty state
- **WHEN** a `continuity=true` task fires for the first time (no live row in `public.task_continuity`)
- **THEN** the admission's goal input includes a continuity block reading "the last run recorded no carry-forward"

#### Scenario: Opted-in task with a prior conclusion carries it forward
- **WHEN** a `continuity=true` task's previous occurrence called `carry_forward(task_name, content)`
- **AND** the task fires again
- **THEN** the admission's goal input includes a continuity block naming the previous occurrence's goal, recorded_at, age, and content

#### Scenario: New session supersedes the previous live row
- **WHEN** a task's lead calls `carry_forward()` from a new occurrence's goal
- **THEN** the prior live row for that `(butler_name, task_name)` is archived (`is_live=false`)
- **AND** the new row becomes the sole live row

#### Scenario: Same-session retry deduplicates instead of superseding
- **WHEN** an invocation calls `carry_forward()` more than once
- **THEN** the existing row for that invocation is updated in place
- **AND** no additional row is archived

#### Scenario: Tainted carry-forward exposes the next occurrence
- **WHEN** an exposed occurrence's lead records a carry-forward
- **AND** the task fires again
- **THEN** the next occurrence's goal starts exposed and the continuity block carries the record's taint stamp

#### Scenario: Non-opted-in tasks are unaffected
- **WHEN** a task has `continuity=false` (the default) or omits the field
- **THEN** `tick()` SHALL NOT call `fetch_live_carry_forward()` for that task
- **AND** its admission's goal input is byte-identical to the pre-continuity behavior

#### Scenario: Continuity lookup failure does not block dispatch
- **WHEN** `fetch_live_carry_forward()` raises for an opted-in task
- **THEN** the exception is logged and the tick proceeds
- **AND** the admission's goal input omits the continuity block entirely (not a fail-closed placeholder)

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

## ADDED Requirements

### Requirement: Time occurrences feed the shared minimal evaluator
The scheduler SHALL publish trusted finite time occurrences to the one S4 evaluator and retain its existing obligation/continuity contracts. It SHALL NOT install a second condition language, implicit subscription, automatic model session or advanced automation engine.

ID: REQ-core-scheduler-001
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S4; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: A clock jumps over a bounded instant
- **WHEN** a registered at leaf is crossed before expiry
- **THEN** one late fire intent is emitted under the shared event/cursor fence, not one per missed tick

#### Scenario: A transition is too old for a real-time wake
- **WHEN** a replayed transition is more than10 minutes old
- **THEN** historical evidence remains but it cannot fire a real-time transition leaf

#### Scenario: Evaluator service is unavailable
- **WHEN** the scheduler cannot hand off a due trusted event
- **THEN** work/cursor is retained with an unavailable receipt and no guessed wake, empty success or direct Spawner call
