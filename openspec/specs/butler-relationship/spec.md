# Relationship Butler Role

## Purpose
The Relationship butler (port 40102) is a personal CRM that manages contacts, relationships, important dates, interactions, gifts, reminders, and loans.

## ADDED Requirements

### Requirement: Relationship Butler Identity and Runtime
The relationship butler maintains personal CRM context with 40+ domain tools.

#### Scenario: Identity and port
- **WHEN** the relationship butler is running
- **THEN** it operates on port 40102 with description "Personal CRM. Manages contacts, relationships, important dates, interactions, gifts, and reminders."
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema is `relationship` within the consolidated `butlers` database

#### Scenario: Module profile
- **WHEN** the relationship butler starts
- **THEN** it loads modules: `calendar` (Google provider, suggest conflicts policy), `contacts` (Google provider, sync enabled, 15-minute interval, 6-day full sync), and `memory`

### Requirement: Relationship Butler Tool Surface
The relationship butler exposes a comprehensive personal CRM tool set.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the relationship butler
- **THEN** it has access to 40+ tools including: contact CRUD (`contact_create`, `contact_update`, `contact_get`, `contact_search`, `contact_archive`, `contact_resolve`), relationship management (`relationship_add`, `relationship_list`, `relationship_remove`), date tracking (`date_add`, `date_list`, `upcoming_dates`), notes (`note_create`, `note_list`, `note_search`), interactions (`interaction_log`, `interaction_list`), reminders (`reminder_create`, `reminder_list`, `reminder_dismiss`), gifts (`gift_add`, `gift_update_status`, `gift_list`), loans (`loan_create`, `loan_settle`, `loan_list`), groups (`group_create`, `group_add_member`, `group_list`, `group_members`), labels (`label_create`, `label_assign`, `contact_search_by_label`), facts (`fact_set`, `fact_list`), feed (`feed_get`), entity resolution (`entity_resolve`, `entity_create`), memory (`memory_store_fact`), and calendar tools

### Requirement: Entity Resolution Pipeline
The relationship butler follows a 7-step entity resolution pipeline for person mentions.

#### Scenario: Entity resolution flow
- **WHEN** the relationship butler processes a message mentioning a person
- **THEN** it follows a 7-step pipeline: (1) identify person mentions, (2) resolve each via `entity_resolve` with context hints, (3) apply disambiguation policy (zero candidates: create entity; single candidate or top leads by 30+ points: use entity_id; multiple candidates with gap less than 30 points: ask user), (4) handle new people, (5) store facts with entity_id, (6) log interactions, (7) update domain records

### Requirement: Relationship Butler Schedules
The relationship butler runs date checks, maintenance sweeps, and memory jobs.

#### Scenario: Scheduled task inventory
- **WHEN** the relationship butler daemon is running
- **THEN** it executes: `upcoming-dates-check` (0 8 * * *, prompt-based: check birthdays/anniversaries in the next 7 days), `relationship-maintenance` (0 9 * * 1, prompt-based: review contacts not interacted with in 30+ days, suggest 3 reconnections), `memory-consolidation` (0 */6 * * *, job), and `memory-episode-cleanup` (0 4 * * *, job)

### Requirement: Relationship Butler Skills
The relationship butler has gift brainstorming and reconnection planning skills.

#### Scenario: Skill inventory
- **WHEN** the relationship butler operates
- **THEN** it has access to `gift-brainstorm` (personalized gift idea generation with budget tiers and gift pipeline integration) and `reconnect-planner` (stale contact identification and reconnection outreach planning), plus shared skills `butler-memory` and `butler-notifications`

### Requirement: Relationship Memory Taxonomy
The relationship butler uses a person-centric memory taxonomy.

#### Scenario: Memory classification
- **WHEN** the relationship butler extracts facts
- **THEN** it uses the person's human-readable name as subject (with entity_id as anchor); predicates like `relationship_to_user`, `birthday`, `preference`, `current_interest`, `workplace`, `lives_in`; permanence `permanent` for identity facts, `stable` for workplace/location, `standard` for interests, `volatile` for temporary states
