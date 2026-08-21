## MODIFIED Requirements

### Requirement: Scheduled Tasks

The education butler SHALL run five scheduled tasks: a nightly analytics job, a weekly progress digest prompt, a weekly stale-flow check, a daily spaced-repetition nudge, and a daily briefing contribution job.

The weekly stale-flow check is the only sweep that can reach an unfinished mind map, so its work list SHALL be the mind map table rather than teaching-flow state. Keying it off flow state left maps whose flow state was never written permanently unreachable by cleanup.

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
- **AND** the task MUST use `dispatch_mode = "prompt"` with a prompt that lists active mind maps, collects pending reviews per map, and sends a single Telegram summary only when at least one review is pending (sending nothing when there are zero pending reviews)

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

---

### Requirement: Skill Definitions

The education butler SHALL provide six domain-specific skills plus three shared skill symlinks.

#### Scenario: Domain skill inventory

- **WHEN** the `roster/education/.agents/skills/` directory is inspected
- **THEN** it MUST contain skill directories (each with a `SKILL.md`): `diagnostic-assessment`, `curriculum-planning`, `teaching-session`, `review-session`, `progress-digest`, and `stale-flow-cleanup`

#### Scenario: diagnostic-assessment skill purpose

- **WHEN** the `diagnostic-assessment` skill is loaded
- **THEN** its `SKILL.md` MUST describe the adaptive probe sequence protocol: generate a concept inventory, binary-search difficulty levels in 3-7 questions, and seed conservative mastery scores onto mind map nodes
- **AND** it MUST specify that the skill exits with flow state transitioned from DIAGNOSING to PLANNING

#### Scenario: curriculum-planning skill purpose

- **WHEN** the `curriculum-planning` skill is loaded
- **THEN** its `SKILL.md` MUST describe the two-phase curriculum generation process: LLM-driven concept decomposition producing nodes and prerequisite edges, followed by topological sort with depth and effort-weighting to produce a learning sequence
- **AND** it MUST specify the DAG validation constraint (no cycles; acyclicity checked before persisting edges)
- **AND** it MUST specify that the mind map is `draft` throughout decomposition and is activated only by `curriculum_generate()`, which refuses to activate an empty graph
- **AND** it MUST NOT instruct the session to clear the `pending_curriculum_request` lock at all; that release is owned by the dashboard API layer and is scoped to a request token the session does not hold, so a session-side `state_delete` would be an unsafe blind delete

#### Scenario: teaching-session skill purpose

- **WHEN** the `teaching-session` skill is loaded
- **THEN** its `SKILL.md` MUST describe the single-concept teaching loop: pick next frontier node, explain the concept, ask 1-3 comprehension questions, record quiz responses, update mastery via SM-2, and update flow state before exiting
- **AND** it MUST specify the token budget guideline (~2K output tokens per session)

#### Scenario: review-session skill purpose

- **WHEN** the `review-session` skill is loaded
- **THEN** its `SKILL.md` MUST describe the spaced repetition review flow: read due review nodes (up to 20), quiz the user, record SM-2 quality scores, reschedule next review intervals, and update flow state
- **AND** it MUST specify the token budget guideline (~500 output tokens per review session)

#### Scenario: progress-digest skill purpose

- **WHEN** the `progress-digest` skill is loaded
- **THEN** its `SKILL.md` MUST describe the weekly digest generation: read the last 7 analytics snapshots, compute trends (velocity, retention, struggling nodes), compose a structured digest, and deliver via `notify()`
- **AND** it MUST specify that the digest is sent via the owner contact's preferred channel, not hardcoded to Telegram

#### Scenario: stale-flow-cleanup skill purpose

- **WHEN** the `stale-flow-cleanup` skill is loaded
- **THEN** its `SKILL.md` MUST describe abandoning inactive teaching flows: identify flows whose `last_session_at` is older than 30 days and are not already completed or abandoned, transition them to abandoned, call `spaced_repetition_schedule_cleanup()` to remove pending review schedules, and record the abandonment as a memory fact
- **AND** it MUST describe the second sweep: `draft` mind maps with zero nodes older than 24 hours, including maps that have no teaching flow state at all, transitioned via `mind_map_update_status()`
- **AND** it MUST specify that the sweep enumerates `education.mind_maps` rows so that a map without flow state is reachable
- **AND** it MUST specify that this skill backs the `weekly-stale-flow-check` scheduled task

#### Scenario: Shared skill symlinks present

- **WHEN** the `roster/education/.agents/skills/` directory is inspected
- **THEN** it MUST contain symlinks `butler-memory`, `butler-notifications`, and `routed-message-safety` pointing into the shared skills directory (`../../../shared/skills/<name>`)
- **AND** these symlinks MUST resolve to valid SKILL.md files in the shared skills directory
