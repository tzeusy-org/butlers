# Education Butler Identity and Roster Configuration

## Purpose

The Education butler (port 41107) is a personalized tutor with spaced repetition, mind maps, and adaptive learning. This spec covers the butler's roster configuration, system prompt, module profile, schedule, skills, switchboard registration, and database identity — the foundational layer on which all other education butler capabilities are built.

## ADDED Requirements

### Requirement: Education Butler Identity and Runtime

The education butler SHALL be configured with the correct port, description, complexity requirements, and concurrency settings matching the designed identity. The model catalog (`public.model_catalog` + `public.butler_model_overrides`) is the authoritative source for model selection; `butler.toml` contains only seed/fallback values.

#### Scenario: Identity and port

- **WHEN** the education butler is running
- **THEN** it operates on port 41107 with description "Personalized tutor with spaced repetition, mind maps, and adaptive learning"
- **AND** it permits a maximum of 3 concurrent sessions

#### Scenario: Complexity tier requirements

- **WHEN** the education butler spawns sessions for teaching, diagnostic, or curriculum work
- **THEN** it MUST request complexity tier `HIGH` or `EXTRA_HIGH` from the model catalog
- **AND** the model catalog MUST have at least one enabled entry at those tiers that can satisfy the request
- **AND** the actual model resolved is determined by the catalog's priority ranking, not hardcoded in config

#### Scenario: Runtime section contains operational tuning only

- **WHEN** the `butler.toml` file is parsed
- **THEN** `[butler.runtime_seed]` MUST specify `max_concurrent_sessions` and contain only operational seed fields (see `butler-base-spec` — Runtime configuration)
- **AND** model identity (runtime_type, model, args, session_timeout_s) is authoritative in `public.model_catalog`; it MUST NOT be pinned in `butler.toml`

---

### Requirement: Database Schema Configuration

The education butler SHALL use the consolidated `butlers` PostgreSQL database with an isolated `education` schema.

#### Scenario: Database name and schema assignment

- **WHEN** the education butler connects to the database
- **THEN** it uses database name `butlers` and schema `education`
- **AND** the `[butler.db]` section in `butler.toml` MUST specify `name = "butlers"` and `schema = "education"`

#### Scenario: Schema isolation enforced at query time

- **WHEN** the education butler executes SQL queries
- **THEN** the effective search path MUST be `education, public`
- **AND** education-specific tables (mind_maps, mind_map_nodes, mind_map_edges, quiz_responses, analytics_snapshots) MUST reside in the `education` schema
- **AND** shared identity tables (contacts, contact_info) MUST reside in the `public` schema

#### Scenario: Alembic migration file exists for the education schema

- **WHEN** database migrations are applied
- **THEN** a migration file MUST exist in `roster/education/migrations/` that creates the `education` schema and all butler-specific tables
- **AND** the migration MUST run without error against a clean `butlers` database

---

### Requirement: Module Profile

The education butler SHALL enable the memory and contacts modules, and SHALL NOT enable the telegram module.

#### Scenario: Required modules enabled

- **WHEN** the education butler starts
- **THEN** it loads the `memory` module and the `contacts` module
- **AND** both MUST appear as `[modules.memory]` and `[modules.contacts]` entries in `butler.toml`

#### Scenario: Telegram module is explicitly absent

- **WHEN** the education butler's module list is inspected
- **THEN** no telegram module entry SHALL appear in `butler.toml`
- **AND** outbound user notifications MUST be sent via `notify()` routed through the Messenger butler, not via direct Telegram API access

#### Scenario: Memory module stores learning-domain facts

- **WHEN** the memory module is loaded by the education butler
- **THEN** it provides the ephemeral session access to `memory_store()`, `memory_query()`, and related memory tools
- **AND** the education butler MUST use these tools to persist learning outcomes, struggle areas, and prerequisite knowledge across sessions

#### Scenario: Contacts module provides identity resolution

- **WHEN** the contacts module is loaded by the education butler
- **THEN** it enables `contact_lookup()` and `notify()` so the education butler can resolve the owner contact and deliver messages via the correct channel

---

### Requirement: Scheduled Tasks

The education butler SHALL run five scheduled tasks: a nightly analytics job, a weekly progress digest prompt, a weekly stale-flow check, a daily spaced-repetition nudge, and a daily briefing contribution job.

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
- **THEN** it includes a scheduled task (job or prompt dispatch) that checks for teaching flows with `last_session_at` older than 30 days
- **AND** the task MUST clean up associated pending review schedules for abandoned flows
- **AND** the task runs on a weekly cadence (cron expression MUST fire no more than once per day)

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
- **AND** it MUST write one row per active mind_map into `analytics_snapshots` with `snapshot_date = today`

#### Scenario: Analytics job is idempotent

- **WHEN** `compute_analytics_snapshots` is called twice on the same calendar date
- **THEN** the second invocation MUST upsert (not duplicate) snapshot rows, using the `UNIQUE` constraint on `(mind_map_id, snapshot_date)`

---

### Requirement: Switchboard Routing Registration

The Switchboard butler SHALL route education-domain requests to the education butler based on classified intent.

#### Scenario: Education routing rules in switchboard CLAUDE.md

- **WHEN** the Switchboard butler's `CLAUDE.md` is read
- **THEN** it MUST contain a routing rule directing the "education" butler to handle messages classified with learning intent
- **AND** the rule MUST list trigger phrases including: "teach me", "quiz me", "what do I know about", and general study or learning requests

#### Scenario: Classification keywords for education routing

- **WHEN** the Switchboard classifies an incoming message
- **THEN** messages matching keywords teach, learn, study, quiz, review, explain (in an educational context) MUST be routed to the education butler
- **AND** the Switchboard MUST distinguish educational "review" and "explain" requests from non-educational uses of those words (e.g., "review my calendar" MUST NOT route to education)

#### Scenario: Unambiguous learning intent routes to education butler

- **WHEN** an incoming message contains "teach me Python" or "quiz me on calculus"
- **THEN** the Switchboard MUST route the message to the education butler at port 41107
- **AND** it MUST NOT route the message to any other domain butler

#### Scenario: Ambiguous routing defers to user clarification

- **WHEN** a message is ambiguous between education and another domain (e.g., "explain this" with no topic context)
- **THEN** the Switchboard MAY request clarification before routing
- **AND** it MUST NOT silently route to an incorrect butler

---

### Requirement: Roster Directory Structure Compliance

The education butler directory at `roster/education/` SHALL contain all required configuration files in the correct layout.

#### Scenario: Required top-level files present

- **WHEN** the `roster/education/` directory is inspected
- **THEN** it MUST contain: `butler.toml`, `MANIFESTO.md`, `CLAUDE.md`, and `AGENTS.md`
- **AND** absence of any of these four files is a configuration error that MUST prevent butler startup

#### Scenario: Optional directories present when features are active

- **WHEN** the education butler is fully configured
- **THEN** the following directories MUST exist: `tools/`, `migrations/`, `api/`, `.agents/`, and `tests/`
- **AND** the `.agents/` directory MUST contain a `skills/` subdirectory holding the domain-specific skills, with a `.claude` symlink pointing to `.agents` for Claude Code discovery
- **AND** each directory MAY be empty initially but MUST be created to signal intent

#### Scenario: AGENTS.md initialized with notes-to-self header

- **WHEN** the `roster/education/AGENTS.md` file is created
- **THEN** it MUST begin with the header `# Notes to self`
- **AND** it MAY be otherwise empty at initialization time

---

### Requirement: MANIFESTO.md Content

The education butler's MANIFESTO.md SHALL define its value proposition, scope, and persona in a way that guides all future feature and tool decisions.

#### Scenario: Value proposition articulates the core offering

- **WHEN** the `roster/education/MANIFESTO.md` is read
- **THEN** it MUST articulate the butler's primary value: personalized learning through spaced repetition, adaptive mind maps, and expert-level pedagogical judgment
- **AND** it MUST name the intended user benefit (measurable mastery, not just exposure to content)

#### Scenario: Scope boundaries are defined

- **WHEN** the MANIFESTO.md is read
- **THEN** it MUST state what the butler does NOT do, including: video content, live tutoring, multi-user classrooms, certification/credentialing, and integration with external LMS platforms
- **AND** these non-goals MUST be explicit enough to prevent scope creep in future feature proposals

#### Scenario: Persona section conveys educator character

- **WHEN** the MANIFESTO.md is read
- **THEN** it MUST describe the butler's character as an expert adaptive tutor — knowledgeable, encouraging, patient, and focused on understanding over rote memorization
- **AND** the persona description MUST be consistent with the CLAUDE.md educator persona section

---

### Requirement: CLAUDE.md System Prompt

The education butler's `CLAUDE.md` SHALL provide a complete system prompt covering educator persona, Interactive Response Mode, memory classification taxonomy, tool listing, and teaching behavior guidelines.

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
- **THEN** it MUST contain a section listing the MCP tools available to the ephemeral session
- **AND** each tool entry MUST include the tool name and a one-line description of its purpose

#### Scenario: Teaching behavior guidelines present

- **WHEN** the CLAUDE.md is read
- **THEN** it MUST contain teaching behavior guidelines specifying: one concept per session, use of Socratic questioning before direct explanation, positive reinforcement on correct answers, and explicit exit criteria for each session phase
- **AND** these guidelines MUST instruct the session to update flow state before exiting

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
- **AND** it MUST specify that this skill backs the `weekly-stale-flow-check` scheduled task

#### Scenario: Shared skill symlinks present

- **WHEN** the `roster/education/.agents/skills/` directory is inspected
- **THEN** it MUST contain symlinks `butler-memory`, `butler-notifications`, and `routed-message-safety` pointing into the shared skills directory (`../../../shared/skills/<name>`)
- **AND** these symlinks MUST resolve to valid SKILL.md files in the shared skills directory

---

### Requirement: High-Tier Complexity Rationale

The education butler SHALL request HIGH or EXTRA_HIGH complexity tiers, justified by the pedagogical demands of the domain. The specific model fulfilling that tier is resolved by the model catalog at runtime.

#### Scenario: Complexity tier selection by task type

- **WHEN** the education butler spawns an ephemeral session
- **THEN** teaching sessions, diagnostic assessments, and curriculum planning MUST request `Complexity.HIGH` or `Complexity.EXTRA_HIGH`
- **AND** spaced repetition review sessions (shorter, more formulaic) MAY request `Complexity.MEDIUM`
- **AND** the spawner resolves the actual model via `resolve_model(pool, "education", complexity_tier)`

#### Scenario: Complexity justification is documented

- **WHEN** the education butler's complexity requirements are reviewed
- **THEN** the rationale MUST be traceable: expert domain knowledge, calibrated quiz generation, free-form answer evaluation, and Socratic questioning all require nuanced judgment that lower complexity tiers do not reliably provide

#### Scenario: Cost mitigation strategy complements complexity tier

- **WHEN** the education butler operates
- **THEN** cost is mitigated by: single-concept teaching sessions (~2K output tokens), batched spaced repetition reviews (~500 tokens per review session at MEDIUM tier), and nightly analytics computed as a Python job with zero LLM tokens
- **AND** the `max_concurrent_sessions = 3` cap in `butler.toml` limits parallel spend
- **AND** review sessions use a lower complexity tier than teaching sessions to further reduce cost
