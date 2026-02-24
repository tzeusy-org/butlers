# General Butler Role

## Purpose
The General butler (port 40101) is the flexible catch-all assistant for freeform data that does not belong to any specialist domain.

## ADDED Requirements

### Requirement: General Butler Identity and Runtime
The general butler handles ad-hoc user requests without specialist schema assumptions.

#### Scenario: Identity and port
- **WHEN** the general butler is running
- **THEN** it operates on port 40101 with description "Flexible catch-all assistant for freeform data"
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema is `general` within the consolidated `butlers` database

#### Scenario: Module profile
- **WHEN** the general butler starts
- **THEN** it loads modules: `calendar` (Google provider, suggest conflicts policy), `contacts` (Google provider, sync enabled, 15-minute interval, 6-day full sync), and `memory`

### Requirement: General Butler Tool Surface
The general butler provides collection and entity management tools for organizing freeform data.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the general butler
- **THEN** it has access to: `collection_create`, `collection_list`, `collection_delete`, `entity_create`, `entity_get`, `entity_update`, `entity_delete`, `entity_search`, `collection_export`, and calendar tools

### Requirement: General Butler Schedules
The general butler runs memory maintenance and a daily end-of-day preparation prompt.

#### Scenario: Scheduled task inventory
- **WHEN** the general butler daemon is running
- **THEN** it executes: `memory-consolidation` (0 */6 * * *, job), `memory-episode-cleanup` (0 4 * * *, job), and `eod-tomorrow-prep` (0 15 * * *, prompt-based: end-of-day briefing reviewing tomorrow's calendar and sending a preparation summary via Telegram)

### Requirement: General Butler Skills
The general butler has a data organization skill plus shared skills.

#### Scenario: Skill inventory
- **WHEN** the general butler operates
- **THEN** it has access to `data-organizer` (collection and entity organization patterns, JSONB query patterns, data hygiene) plus shared skills `butler-memory` and `butler-notifications`

### Requirement: General Memory Taxonomy
The general butler uses a flexible memory taxonomy for broad knowledge capture.

#### Scenario: Memory classification
- **WHEN** the general butler extracts facts
- **THEN** it uses flexible subjects like topic names or "user"; predicates like `goal`, `preference`, `resource`, `idea`, `note`, `deadline`, `status`; permanence `standard` for most general knowledge, `volatile` for temporary notes, `stable` for long-term preferences
