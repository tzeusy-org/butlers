## MODIFIED Requirements

### Requirement: Scheduled Tasks

The education butler SHALL run five scheduled tasks: a nightly analytics job, a weekly progress digest prompt, a weekly stale-flow check, a daily spaced-repetition nudge, and a daily briefing contribution job.

The weekly stale-flow check is the only sweep that can reach an unfinished mind map, so its work list SHALL be the mind map table rather than teaching-flow state. Keying it off flow state left maps whose flow state was never written permanently unreachable by cleanup.

The education butler also owns one deterministic standing order, defined in its git-held configuration, that reconciles unscheduled review cards into one-shot review schedules (module-education-spaced-repetition "Review card schedule reconciliation"). It is a job, not a model session, and it is not a sixth user-facing prompt task. The daily spaced-repetition nudge and every review-related instruction SHALL be transport-neutral: none SHALL name Telegram or any other channel.

ID: REQ-butler-education-008
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89; education-mind-map-lifecycle-integrity
Scope: v1-mandatory

#### Scenario: Nightly analytics job configuration

- **WHEN** the education butler daemon is running
- **THEN** it executes a scheduled task named `nightly-analytics` on cron `0 3 * * *` (daily at 03:00)
- **AND** the task MUST use `dispatch_mode = "job"` with `job_name = "compute_analytics_snapshots"`
- **AND** this task MUST invoke a native Python function without spawning an LLM session, incurring no LLM token cost

#### Scenario: Weekly progress digest prompt configuration

- **WHEN** Sunday at 09:00 arrives
- **THEN** the education butler executes a scheduled task named `weekly-progress-digest` on cron `0 9 * * 0`
- **AND** the task MUST use `dispatch_mode = "prompt"` with a prompt that instructs the spawned LLM session to read analytics snapshots for the past 7 days, identify trends, highlight achievements, flag struggling areas, and deliver the digest via the user's preferred channel

#### Scenario: Weekly stale-flow check configuration

- **WHEN** the education butler daemon is running
- **THEN** it includes a scheduled task (job or prompt dispatch) whose work list is drawn from rows in `education.mind_maps` with `status IN ('draft', 'active')`, not from the set of `flow:*` KV keys
- **AND** the task MUST abandon a map whose teaching flow has `last_session_at` older than 30 days
- **AND** the task MUST abandon a `draft` map with zero nodes whose `created_at` is older than 24 hours, whether or not it has flow state
- **AND** the task MUST clean up associated pending review schedules for abandoned flows
- **AND** the task runs on a weekly cadence (cron expression MUST fire no more than once per day)

#### Scenario: Stale-flow check reaches mind maps with no flow state

- **WHEN** the weekly stale-flow check runs
- **AND** a `draft` mind map older than 24 hours has zero nodes and no `flow:{mind_map_id}` KV entry
- **THEN** the task MUST evaluate that map rather than skipping it
- **AND** it MUST transition the map to `abandoned` via `mind_map_update_status()`

#### Scenario: Daily spaced-repetition nudge configuration

- **WHEN** 17:00 arrives each day
- **THEN** the education butler executes a scheduled task named `daily-spaced-repetition-nudge` on cron `0 17 * * *`
- **AND** the task MUST use `dispatch_mode = "prompt"` with a prompt that lists active mind maps, collects pending reviews per map, and sends a single summary through `notify(intent="send", ...)` without naming a transport, only when at least one open review card is due (sending nothing when there are zero due cards)

#### Scenario: Daily briefing contribution job configuration

- **WHEN** 06:55 arrives each day
- **THEN** the education butler executes a scheduled task named `daily_briefing_contribution` on cron `55 6 * * *`
- **AND** the task MUST use `dispatch_mode = "job"` with `job_name = "daily_briefing_contribution"`

#### Scenario: Nightly analytics job does not spawn LLM

- **WHEN** `compute_analytics_snapshots` runs
- **THEN** it MUST execute as a Python coroutine/function within the butler daemon process
- **AND** it MUST NOT trigger the LLM CLI spawner
- **AND** it MUST write one row per `active` mind_map into `analytics_snapshots` with `snapshot_date = today`
- **AND** it MUST NOT write snapshot rows for `draft` mind maps, which have no nodes to measure

#### Scenario: Analytics job is idempotent

- **WHEN** `compute_analytics_snapshots` is called twice on the same calendar date
- **THEN** the second invocation MUST upsert (not duplicate) snapshot rows, using the `UNIQUE` constraint on `(mind_map_id, snapshot_date)`

