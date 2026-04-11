# Lifestyle Butler

## Purpose

The Lifestyle butler owns taste, rhythm, and daily quality-of-life. It is the domain specialist for music, entertainment, food preferences, hobbies, and daily routines — the connective tissue between your other butlers' domains. It amplifies what you enjoy without ranking or judging it.

## Requirements

### Requirement: Butler Identity and Wire Contract

The Lifestyle butler SHALL expose a stable identity and wire contract that other components of the system depend on. Identity details that drift (model, cron minutes, module list, concurrency caps) are owned by `roster/lifestyle/butler.toml` per `about/heart-and-soul/vision.md` Rule 5 and are NOT mirrored here.

#### Scenario: Stable wire identity

- **WHEN** the butler is loaded from `roster/lifestyle/butler.toml`
- **THEN** it SHALL have `name = "lifestyle"` and `port = 41109`
- **AND** its database binding SHALL be `[butler.db]` with `name = "butlers"` and `schema = "lifestyle"`
- **AND** its switchboard binding SHALL be `[butler.switchboard]` with `url = "http://localhost:41100/mcp"`

#### Scenario: Runtime seed is operational-only

- **WHEN** `roster/lifestyle/butler.toml` declares a `[butler.runtime_seed]` table
- **THEN** it SHALL contain only operational tuning fields (concurrency, liveness TTL, route contract bounds)
- **AND** it SHALL NOT declare `model` or runtime adapter fields — those are resolved at session-spawn time from `public.model_catalog`
- **AND** the toml SHALL NOT contain a top-level `[runtime]` section; the loader rejects any reintroduction of one

### Requirement: Agent Type and Module Shape

The Lifestyle butler SHALL be a butler-typed agent (not a staffer) and SHALL expose a fixed shape of required module capabilities.

#### Scenario: Butler-typed specialist

- **WHEN** the daemon classifies the Lifestyle butler
- **THEN** it SHALL be a butler-typed agent, eligible for routing from the Switchboard as a domain specialist
- **AND** it SHALL NOT be a staffer (Switchboard, Messenger, QA)

#### Scenario: Required module capabilities

- **WHEN** the butler starts
- **THEN** it SHALL enable the `memory`, `calendar`, and `contacts` modules at minimum
- **AND** it MAY enable domain-specific integration modules (e.g. Spotify, Steam)
- **AND** the authoritative enabled-module list lives in `roster/lifestyle/butler.toml`

### Requirement: Domain Scope Boundary

The Lifestyle butler SHALL own a clearly defined domain that does not overlap with other butlers' primary responsibilities. These boundaries are routing invariants that other butlers rely on.

#### Scenario: Lifestyle domain ownership

- **WHEN** a message relates to music, listening habits, playlists, or music discovery
- **THEN** the Lifestyle butler SHALL be the primary routing target

#### Scenario: Entertainment domain ownership

- **WHEN** a message relates to movies, TV shows, books, games, podcasts, or general entertainment
- **THEN** the Lifestyle butler SHALL be the primary routing target

#### Scenario: Food preference domain ownership

- **WHEN** a message relates to food preferences, favorite restaurants, cuisines, recipes, or dining experiences
- **THEN** the Lifestyle butler SHALL be the primary routing target
- **AND** the message SHALL NOT be routed to Health unless it contains explicit nutritional data (calories, macros, diet tracking)

#### Scenario: Hobby and interest domain ownership

- **WHEN** a message relates to hobbies, personal interests, or leisure activities
- **THEN** the Lifestyle butler SHALL be the primary routing target
- **AND** the message SHALL NOT be routed to Education unless it involves systematic learning or curriculum

#### Scenario: Routine domain ownership

- **WHEN** a message relates to daily routines, morning/evening patterns, or focus/wind-down modes
- **THEN** the Lifestyle butler SHALL be the primary routing target
- **AND** the message SHALL NOT be routed to Health unless it contains explicit health metrics (sleep duration, exercise reps, vitals)

#### Scenario: Nutrition tracking refusal

- **WHEN** a request asks the Lifestyle butler to track calories, macros, or a nutrition plan
- **THEN** the butler SHALL refuse and defer to the Health butler

#### Scenario: Formal learning refusal

- **WHEN** a request asks the Lifestyle butler to manage curricula, study plans, or spaced-repetition learning
- **THEN** the butler SHALL refuse and defer to the Education butler

#### Scenario: Social event planning refusal

- **WHEN** a request asks the Lifestyle butler to coordinate social events or manage relationships
- **THEN** the butler SHALL refuse and defer to the Relationship butler

#### Scenario: Home automation refusal

- **WHEN** a request asks the Lifestyle butler to control home devices or automate the home
- **THEN** the butler SHALL refuse and defer to the Home butler

### Requirement: Memory Taxonomy Shape

The Lifestyle butler SHALL maintain a domain-specific memory taxonomy distinguishing enduring taste preferences from current consumption state. The full predicate inventory and example facts live in `roster/lifestyle/CLAUDE.md` under Memory Classification.

#### Scenario: Permanence contract

- **WHEN** the butler stores a lifestyle fact
- **THEN** enduring preferences (genre, artist, cuisine, favorite restaurants, recipes, hobbies, dietary patterns, routines) SHALL default to `stable` permanence
- **AND** current consumption state (what the user is currently watching, reading, playing, or listening to) SHALL default to `volatile` permanence

### Requirement: Scheduled Task Shape

The Lifestyle butler SHALL run the standard memory maintenance job set shared across butler-typed agents, plus at least one domain-specific periodic task that surfaces taste highlights.

#### Scenario: Standard maintenance plus domain digest

- **WHEN** the butler daemon is running
- **THEN** it SHALL schedule the standard memory maintenance jobs shared by butler-typed agents
- **AND** it SHALL schedule at least one domain-specific recurring task whose current shape is a weekly taste digest
- **AND** the exact cron expressions and dispatch modes live in `roster/lifestyle/butler.toml`

### Requirement: Cross-Butler Briefing Contribution

The Lifestyle butler SHALL participate in the canonical daily briefing as a specialist butler.

#### Scenario: Participates as canonical specialist

- **WHEN** the daily briefing aggregation runs
- **THEN** Lifestyle SHALL contribute via the contract defined in `openspec/specs/cross-butler-briefing-contribution/spec.md` as a member of the canonical specialist set
- **AND** when new consumption or taste facts were recorded in the last 24 hours, the contribution SHALL have `has_updates=true`
- **AND** the highlight schema, category labels, and cleanup rules are owned by the briefing contribution spec, not this role spec

### Requirement: Interactive Response Mode

The Lifestyle butler SHALL follow the shared Interactive Response Mode contract for user-facing ingests, and SHALL NOT treat background connector events as interactive.

#### Scenario: Interactive channel ingest

- **WHEN** the butler receives a routed message whose `source_channel` is an interactive channel (e.g. `telegram_bot`)
- **THEN** it SHALL follow the Interactive Response Mode contract defined in `roster/shared/AGENTS.md`

#### Scenario: Spotify connector events are non-interactive

- **WHEN** the butler receives a routed Spotify connector event (`spotify.track_change`, `spotify.session_summary`)
- **THEN** it SHALL treat the event as background knowledge-graph growth
- **AND** it SHALL NOT emit a user-facing reply, reaction, or Telegram notification in response to the event

### Requirement: Manifesto

The Lifestyle butler SHALL have a `MANIFESTO.md` under `roster/lifestyle/` that defines its identity and value proposition.

#### Scenario: Manifesto exists and declares scope

- **WHEN** the manifesto is read
- **THEN** it SHALL define the butler's scope, promises, refusals, and value to the user
- **AND** the authoritative content lives in `roster/lifestyle/MANIFESTO.md`
