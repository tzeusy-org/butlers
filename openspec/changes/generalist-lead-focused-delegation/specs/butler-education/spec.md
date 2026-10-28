## MODIFIED Requirements

### Requirement: Education Butler Identity and Runtime

The education butler SHALL be configured with the correct port, description, and runtime seed settings matching the designed identity. The education daemon SHALL NOT spawn reasoning sessions: teaching, diagnostic, curriculum, and review reasoning SHALL run as lead or child invocations of admitted goals, and each invocation's model SHALL be resolved per invocation from the model catalog by role and requested tier. The model catalog (`public.model_catalog` + `public.butler_model_overrides`) is the authoritative source for model selection; `butler.toml` contains only seed/fallback values.

Source: RFC 0038 D2, D16
Scope: v1-mandatory

#### Scenario: Identity and port

- **WHEN** the education butler is running
- **THEN** it operates on port 41107 with description "Personalized tutor with spaced repetition, mind maps, and adaptive learning"
- **AND** it registers its education capabilities for goal invocations and spawns no reasoning session itself

#### Scenario: Complexity tier requirements

- **WHEN** teaching, diagnostic, curriculum, or review work needs model judgment
- **THEN** it runs as a lead or depth-1 child invocation of an admitted goal, using education capabilities within that invocation's grant
- **AND** the model is resolved per invocation by role and requested tier, not by the education daemon
- **AND** the actual model resolved is determined by the catalog's priority ranking, not hardcoded in config

#### Scenario: Runtime section contains operational tuning only

- **WHEN** the `butler.toml` file is parsed
- **THEN** `[butler.runtime_seed]` MUST contain only operational seed fields (see `butler-base-spec` — Runtime configuration)
- **AND** model identity (runtime_type, model, args, session_timeout_s) is authoritative in `public.model_catalog`; it MUST NOT be pinned in `butler.toml`

### Requirement: Scheduled Tasks

The education butler SHALL run five scheduled tasks: a nightly analytics job, a weekly progress digest obligation, a weekly stale-flow check, a daily spaced-repetition nudge obligation, and a daily briefing contribution job. A task that needs model judgment SHALL submit an obligation admission to the control plane when due instead of spawning a session, and its capability set SHALL be declared in git.

Source: RFC 0038 D2, D5
Scope: v1-mandatory

The weekly stale-flow check is the only sweep that can reach an unfinished mind map, so its work list SHALL be the mind map table rather than teaching-flow state. Keying it off flow state left maps whose flow state was never written permanently unreachable by cleanup.

#### Scenario: Nightly analytics job configuration

- **WHEN** the education butler daemon is running
- **THEN** it executes a scheduled task named `nightly-analytics` on cron `0 3 * * *` (daily at 03:00)
- **AND** the task MUST use `dispatch_mode = "job"` with `job_name = "compute_analytics_snapshots"`
- **AND** this task MUST invoke a native Python function without spawning an LLM session, incurring no LLM token cost

#### Scenario: Weekly progress digest prompt configuration

- **WHEN** Sunday at 09:00 arrives
- **THEN** the education butler submits an obligation admission for the scheduled task named `weekly-progress-digest` on cron `0 9 * * 0`, whose goal input asks the lead to read analytics snapshots for the past 7 days, identify trends, highlight achievements, flag struggling areas, and deliver the digest to the owner through `notify`
- **AND** the obligation's capability set MUST be declared in git and SHALL NOT exceed education `read` and `record` plus owner `notify`
- **AND** no session is spawned by the education daemon

#### Scenario: Weekly stale-flow check configuration

- **WHEN** the education butler daemon is running
- **THEN** it includes a scheduled task (a deterministic job, or an obligation admission with a capability set declared in git) whose work list is drawn from rows in `education.mind_maps` with `status IN ('draft', 'active')`, not from the set of `flow:*` KV keys
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
- **THEN** the education butler submits an obligation admission for the scheduled task named `daily-spaced-repetition-nudge` on cron `0 17 * * *`, whose goal input asks the lead to list active mind maps, collect pending reviews per map, and send the owner a single Telegram summary through `notify` only when at least one review is pending
- **AND** nothing is sent when there are zero pending reviews
- **AND** the obligation's capability set MUST be declared in git and SHALL NOT exceed education `read` and `record` plus owner `notify`

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

### Requirement: CLAUDE.md System Prompt

The education butler's `CLAUDE.md` SHALL be versioned education domain guidance, searchable through `guidance.search` and loadable through `guidance.load`, covering educator persona, Interactive Response Mode, memory classification taxonomy, capability listing, and teaching behavior guidelines. It SHALL NOT serve as the system prompt of a session spawned by the education daemon, and it SHALL NOT grant any capability.

Source: RFC 0038 D2
Scope: v1-mandatory

#### Scenario: Educator persona section present

- **WHEN** the `roster/education/CLAUDE.md` is read
- **THEN** it MUST contain a section defining the educator persona
- **AND** the persona MUST describe the butler as an expert adaptive tutor who calibrates to the user's level, uses Socratic questioning, provides positive reinforcement, and focuses on one concept at a time

#### Scenario: Interactive Response Mode defined

- **WHEN** the CLAUDE.md is read
- **THEN** it MUST contain an "Interactive Response Mode" (IRM) section that defines the five response patterns for conversational interactions: React, Affirm, Follow-up, Answer, and React+Reply
- **AND** each pattern MUST have a clear description of when it applies (e.g., React+Reply for Telegram interactions where brevity and acknowledgment are both needed)

#### Scenario: Memory classification taxonomy defined

- **WHEN** the CLAUDE.md is read
- **THEN** it MUST contain a "Memory Classification" section defining the taxonomy the butler uses when calling memory tools
- **AND** the subjects dimension MUST include: topic names, concept names, and "user"
- **AND** the predicates dimension MUST include: `learning_outcome`, `struggle_area`, `prerequisite_mastered`, `learning_preference`, and `study_pattern`
- **AND** the permanence dimension MUST include: `stable` (long-term skills), `standard` (topic knowledge), and `volatile` (temporary struggles)

#### Scenario: Memory classification permanence mapping is correct

- **WHEN** the education butler records a memory fact
- **THEN** long-term transferable skills (e.g., "user has mastered recursion") MUST use permanence `stable`
- **AND** topic-specific knowledge in active study (e.g., "user knows Python list comprehensions") MUST use permanence `standard`
- **AND** temporary confusion or current struggle areas MUST use permanence `volatile`

#### Scenario: Tool listing section is present in CLAUDE.md

- **WHEN** the CLAUDE.md is read
- **THEN** it MUST contain a section listing the education capabilities a goal invocation may be granted
- **AND** each entry MUST include the capability name and a one-line description of its purpose
- **AND** the listing MUST NOT be treated as a grant; an invocation can call only what its broker grant contains

#### Scenario: Teaching behavior guidelines present

- **WHEN** the CLAUDE.md is read
- **THEN** it MUST contain teaching behavior guidelines specifying: one concept per session, use of Socratic questioning before direct explanation, positive reinforcement on correct answers, and explicit exit criteria for each session phase
- **AND** these guidelines MUST instruct the invocation to update flow state through education capabilities before it completes

## REMOVED Requirements

### Requirement: Switchboard Routing Registration

**Reason**: RFC 0038 D1 and the Hard cutover delete model classification and route dispatch, so no Switchboard routing rule or classification keyword directs learning requests to the education butler.
**Migration**: Owner learning requests are admitted as owner goals (REQ-goal-orchestration-008); the lead finds education capabilities through catalog search and education domain guidance, and calls them directly or through a depth-1 child.

### Requirement: High-Tier Complexity Rationale

**Reason**: RFC 0038 D2 and D16 remove per-butler session spawning and make model choice per invocation by role and requested tier; replay evaluation, not domain specs, sets those assignments (owner decision 4), and `max_concurrent_sessions` no longer bounds education spend.
**Migration**: Model assignment follows the `model_catalog` role and tier rules of RFC 0038 D16; spend is bounded by goal budgets (RFC 0038 D10). The pedagogical rationale may live in education domain guidance as advice for a lead choosing a child tier.
