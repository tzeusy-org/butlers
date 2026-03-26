# Lifestyle Butler

## Purpose

The Lifestyle butler owns taste, rhythm, and daily quality-of-life. It is the domain specialist for music, entertainment, food preferences, hobbies, and daily routines — the connective tissue between your other butlers' domains. It amplifies what you enjoy without ranking or judging it.

## ADDED Requirements

### Requirement: Butler Identity and Configuration

The Lifestyle butler SHALL be configured as a standard butler in `roster/lifestyle/`.

#### Scenario: Butler configuration

- **WHEN** the Lifestyle butler is loaded from `roster/lifestyle/butler.toml`
- **THEN** it SHALL have:
  - `name = "lifestyle"`
  - `port = 41109`
  - `description` summarizing its domain (taste, music, entertainment, food, hobbies, routines)
  - `[butler.db]` with `name = "butlers"` and `schema = "lifestyle"`
  - `[butler.switchboard]` with `url = "http://localhost:41100/mcp"`
  - `[butler.seed_configs]` with `runtime_type = "codex"`, `model = "gpt-5.1"`, `liveness_ttl_seconds = 300`, `route_contract_min = 1`, `route_contract_max = 1`
  - `[butler.runtime]` with `max_concurrent_sessions = 3`

### Requirement: Base Module Configuration

The Lifestyle butler SHALL enable standard utility modules and the spotify module when available.

#### Scenario: Enabled modules

- **WHEN** the butler starts
- **THEN** the following modules SHALL be enabled:
  - `memory` — taste profiles, consumption patterns, preference facts
  - `calendar` — routine scheduling, event-linked taste notes
  - `contacts` — entity resolution for shared-taste contexts
  - `spotify` — music tools (when the spotify-module change is implemented)

### Requirement: Domain Scope Boundary

The Lifestyle butler SHALL own a clearly defined domain that does not overlap with other butlers' primary responsibilities.

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

### Requirement: Memory Taxonomy

The Lifestyle butler SHALL maintain a domain-specific memory taxonomy for taste and consumption patterns.

#### Scenario: Taste preference facts

- **WHEN** the butler stores a taste preference
- **THEN** it SHALL use subject/predicate pairs from the following taxonomy:
  - `user | likes_genre` — music genre preferences
  - `user | likes_artist` — favorite artists
  - `user | likes_cuisine` — cuisine preferences
  - `user | favorite_restaurant` — preferred dining spots
  - `user | favorite_recipe` — beloved recipes
  - `user | hobby` — active hobbies and interests
  - `user | food_preference` — dietary patterns and preferences
  - `user | food_dislike` — foods to avoid (allergies, taste aversions)
  - `user | routine` — daily routine patterns
- **AND** taste preferences SHALL default to `DURABLE` permanence

#### Scenario: Consumption tracking facts

- **WHEN** the butler stores current consumption state
- **THEN** it SHALL use predicates:
  - `user | watches` — currently watching (TV, film)
  - `user | reads` — currently reading
  - `user | plays` — currently playing (games)
  - `user | listens_to` — current listening focus
- **AND** these SHALL default to `TRANSIENT` permanence

#### Scenario: Spotify-enriched memory

- **WHEN** the butler stores Spotify-derived insights
- **THEN** it SHALL use subject patterns:
  - `spotify:artist:{id} | listening_pattern` — rotation intensity over time
  - `spotify:playlist:{id} | purpose` — what the playlist is for
  - `spotify:playlist:{id} | context` — when/where/why it's used
- **AND** these SHALL default to `DURABLE` permanence

### Requirement: Scheduled Tasks

The Lifestyle butler SHALL run standard memory maintenance and domain-specific scheduled tasks.

#### Scenario: Memory maintenance tasks

- **WHEN** the butler is running
- **THEN** it SHALL schedule:
  - `memory_consolidation` at `0 */6 * * *`
  - `memory_episode_cleanup` at `0 4 * * *`
  - `memory_purge_superseded` at `10 4 * * *`

#### Scenario: Weekly taste digest

- **WHEN** Sunday at 21:00 (configured cron)
- **THEN** the butler SHALL generate a weekly digest summarizing:
  - Music: top artists, new discoveries, playlist activity, listening hours
  - Entertainment: what was watched/read/played
  - Food: notable meals or restaurant visits
  - Hobbies: activity highlights
- **AND** the digest SHALL be delivered via `notify(intent="send")`

#### Scenario: Daily briefing contribution

- **WHEN** the `daily_briefing_contribution` job runs at `55 6 * * *`
- **THEN** the butler SHALL contribute lifestyle highlights to the cross-butler morning briefing
- **AND** the contribution SHALL include: any notable listening patterns, entertainment milestones, or taste discoveries from the past 24 hours

### Requirement: Interactive Response Mode

The Lifestyle butler SHALL respond to interactive messaging channels with appropriate engagement.

#### Scenario: Taste capture from chat

- **WHEN** the user mentions a food preference, music opinion, or entertainment recommendation via an interactive channel
- **THEN** the butler SHALL store the fact in memory
- **AND** it SHALL respond with a brief acknowledgment (react or affirm mode)
- **AND** it MAY offer a follow-up ("Want me to add that to your playlist?" / "Should I remember that restaurant?")

#### Scenario: Taste query from chat

- **WHEN** the user asks about their preferences or consumption history via an interactive channel
- **THEN** the butler SHALL search memory and respond with a substantive answer
- **AND** it SHALL include relevant context (when the preference was recorded, related facts)

### Requirement: Manifesto

The Lifestyle butler SHALL have a MANIFESTO.md that defines its identity and value proposition.

#### Scenario: Manifesto content

- **WHEN** the manifesto is read
- **THEN** it SHALL communicate:
  - The butler celebrates what the user enjoys without judgment
  - It remembers taste preferences, tracks patterns, and surfaces discoveries
  - It is a knowledgeable friend who remembers your taste — not a critic or algorithm
  - The domain scope: music, entertainment, food preferences, hobbies, routines
  - Explicit refusals: does not track nutrition/calories (Health), does not manage formal learning (Education), does not plan social events (Relationship), does not automate the home (Home)
  - The value: over time, the butler becomes a rich map of what makes your life enjoyable

### Requirement: System Prompt Structure

The Lifestyle butler's CLAUDE.md SHALL follow the standard system prompt structure.

#### Scenario: Shared agent context

- **WHEN** the CLAUDE.md is loaded by the runtime
- **THEN** it SHALL begin with `@../shared/AGENTS.md` to include shared agent context
- **AND** it SHALL include sections for: tool inventory, guidelines, calendar usage, Interactive Response Mode, and Memory Classification

### Requirement: Shared Skills

The Lifestyle butler SHALL include shared skill symlinks for runtime instances.

#### Scenario: Shared skill availability

- **WHEN** the butler's `.agents/skills/` directory is created
- **THEN** it SHALL contain symlinks to:
  - `butler-memory` → `../../../shared/skills/butler-memory`
  - `butler-notifications` → `../../../shared/skills/butler-notifications`
- **AND** a `.claude` → `.agents` symlink SHALL exist at the butler root

### Requirement: Shutdown

The butler SHALL shut down gracefully following standard butler lifecycle.

#### Scenario: Graceful shutdown

- **WHEN** the butler receives a shutdown signal
- **THEN** it SHALL close all module connections and flush pending memory writes
