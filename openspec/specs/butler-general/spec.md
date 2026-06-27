# General Butler Role

## Purpose
The General butler (port 41101) is the flexible catch-all assistant for freeform data that does not belong to any specialist domain.

## ADDED Requirements

### Requirement: General Butler Identity and Runtime
The general butler handles ad-hoc user requests without specialist schema assumptions.

#### Scenario: Identity and port
- **WHEN** the general butler is running
- **THEN** it operates on port 41101 with description "Flexible catch-all assistant for freeform data"
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema is `general` within the consolidated `butlers` database

#### Scenario: Module profile
- **WHEN** the general butler starts
- **THEN** it loads modules: `general` (the custom module that registers the collection and item management tools), `calendar` (Google provider, suggest conflicts policy), `contacts` (Google provider, sync enabled, 15-minute interval, 6-day full sync), `memory`, and `steam` (lifestyle gaming-activity capture available to the catch-all butler)

### Requirement: General Butler Tool Surface
The general butler provides collection and item management tools for organizing freeform data.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the general butler
- **THEN** it has access to: `collection_create`, `collection_list`, `collection_delete`, `item_create`, `item_get`, `item_update`, `item_delete`, `item_search`, `collection_export`, and calendar tools

### Requirement: General Butler Schedules
The general butler runs memory maintenance, briefing aggregation, and a daily end-of-day preparation prompt that incorporates cross-butler data.

#### Scenario: Scheduled task inventory
- **WHEN** the general butler daemon is running
- **THEN** it executes: `memory_consolidation` (0 */6 * * *, job), `memory_episode_cleanup` (0 4 * * *, job), `collect_briefing_contributions` (58 6 * * *, job: 14:58 SGT; aggregates specialist briefing contributions into combined payload), and `eod-tomorrow-prep` (0 15 * * *, prompt-based: 23:00 SGT end-of-day briefing reviewing tomorrow's calendar AND incorporating cross-butler specialist summaries, sent via Telegram)

#### Scenario: EOD briefing reads combined contributions
- **WHEN** the `eod-tomorrow-prep` prompt executes
- **THEN** it reads `state_get('briefing/combined/<today-SGT>')` to obtain the aggregated specialist data
- **AND** incorporates sections with `has_updates=true` into the briefing message
- **AND** omits sections with `has_updates=false` entirely

#### Scenario: EOD briefing without specialist data
- **WHEN** the `eod-tomorrow-prep` prompt executes and `briefing/combined/<today>` is absent or empty
- **THEN** the briefing degrades gracefully to calendar-only format (current behavior preserved)

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

### Requirement: EOD Briefing Multi-Domain Format
The EOD briefing message SHALL follow a structured multi-domain format when specialist contributions are available. The message SHALL be under 500 words for mobile readability.

#### Scenario: Full briefing with specialist sections
- **WHEN** the combined briefing payload has contributions with `has_updates=true`
- **THEN** the message includes a calendar timeline section followed by a "Today's Highlights" section grouping specialist summaries by domain — the domain label set is exactly: Learning (education), Finance, Health, Home, Lifestyle, Relationships, Travel (one label per butler in `SPECIALIST_BUTLERS`)
- **AND** only domains with `has_updates=true` appear in the output

#### Scenario: Cross-domain heads-up flags
- **WHEN** multiple specialist contributions contain high-priority highlights
- **THEN** the briefing MAY include a "Heads-up" section at the end highlighting cross-domain correlations (e.g., travel departure conflicting with a medical appointment)

#### Scenario: Calendar-only fallback
- **WHEN** no specialist contributions have `has_updates=true`
- **THEN** the briefing renders the calendar timeline only, matching the existing format
